import asyncio
import json
from dataclasses import dataclass, field

import httpx
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.database import utc_now
from app.persistence.config_repository import LocalConfigurationRepository
from app.security import SecretStore
from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID
from app.settings import MODEL_HTTP_MAX_CONNECTIONS, MODEL_HTTP_MAX_KEEPALIVE, MODEL_RUN_TIMEOUT_SECONDS
from app.model_observability import observe_model, ObservedModel
from app.local_model_endpoint import LocalModelEndpoint


PROVIDER_BASE_URLS = {
    "deepseek": "https://api.deepseek.com",
    "openai": "https://api.openai.com/v1",
}


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class ProviderSnapshot:
    provider: str
    model: str
    api_key: str = field(repr=False)
    tenant_id: str | None = None


class ModelGateway:
    def __init__(self, secrets: SecretStore, *, repository=None, client_factory=None,
                 local_endpoint: LocalModelEndpoint | None = None, local_client_factory=None) -> None:
        self.secrets = secrets
        self.repository = repository or LocalConfigurationRepository()
        self._http_client: httpx.AsyncClient | None = None
        self._client_factory = client_factory
        self.local_endpoint = local_endpoint
        self._local_http_client: httpx.AsyncClient | None = None
        self._local_client_factory = local_client_factory

    def _local_client(self) -> httpx.AsyncClient:
        if self.local_endpoint is None:
            raise ProviderError("local_endpoint_unavailable", "本机模型服务未配置", 503)
        if self._local_client_factory is not None:
            return self._local_client_factory()
        if self._local_http_client is None or self._local_http_client.is_closed:
            self._local_http_client = self.local_endpoint.client(timeout=MODEL_RUN_TIMEOUT_SECONDS)
        return self._local_http_client

    def _client(self) -> httpx.AsyncClient:
        if self._client_factory is not None:
            return self._client_factory()
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                limits=httpx.Limits(
                    max_connections=MODEL_HTTP_MAX_CONNECTIONS,
                    max_keepalive_connections=MODEL_HTTP_MAX_KEEPALIVE,
                    keepalive_expiry=30,
                ),
                timeout=httpx.Timeout(MODEL_RUN_TIMEOUT_SECONDS, connect=10),
            )
        return self._http_client

    async def close(self) -> None:
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
        if self._local_http_client is not None and not self._local_http_client.is_closed:
            await self._local_http_client.aclose()

    def list_configs(self) -> dict:
        active, rows = self.repository.provider_configs()
        if rows and not any(row["provider"] == "openai" for row in rows):
            # Listing does not provision a provider or import a historical key.
            rows.append({"provider": "openai", "model": "gpt-5-mini", "encrypted_key": None,
                         "updated_at": None, "last_test_status": None,
                         "last_test_message": None, "last_test_at": None})
        if rows and not any(row["provider"] == "local" for row in rows):
            # Existing PostgreSQL tenants predate the optional local provider.
            # Listing remains read-only; saving creates the tenant-scoped row.
            rows.append({"provider": "local", "model": "local-model", "encrypted_key": None,
                         "updated_at": None, "last_test_status": None,
                         "last_test_message": None, "last_test_at": None})
        rows.sort(key=lambda row: {"deepseek": 0, "openai": 1, "local": 2}.get(row["provider"], 3))
        return {
            "active_provider": active,
            "providers": [
                {
                    "provider": row["provider"],
                    "model": row["model"],
                    "configured": bool(row["encrypted_key"]) if row["provider"] != "local" else self.local_endpoint is not None,
                    "updated_at": row["updated_at"],
                    "last_test_status": row["last_test_status"],
                    "last_test_message": row["last_test_message"],
                    "last_test_at": row["last_test_at"],
                }
                for row in rows
            ],
        }

    def update(self, provider: str, model: str, api_key: str | None) -> None:
        self.repository.require_manage()
        if provider not in (*PROVIDER_BASE_URLS, "local"):
            raise ProviderError("unsupported_provider", "不支持的模型供应商")
        if provider == "local" and api_key:
            raise ProviderError("local_key_not_allowed", "本机模型不接受 API Key")
        fields = {"model": model.strip(), "now": utc_now()}
        if api_key:
            namespace = self.repository.credential_namespace(permission="config.manage")
            encrypted, nonce = self.secrets.encrypt(api_key.strip(), provider, tenant_id=namespace)
            fields.update(encrypted_key=encrypted, nonce=nonce, replace_key=True)
        self.repository.update_provider(provider, **fields)

    def delete_key(self, provider: str) -> None:
        if provider == "local":
            raise ProviderError("local_key_not_allowed", "本机模型没有可删除的 API Key")
        self.repository.delete_provider_key(provider, utc_now())

    def activate(self, provider: str) -> None:
        if provider not in (*PROVIDER_BASE_URLS, "local"):
            raise ProviderError("unsupported_provider", "不支持的模型供应商")
        if provider == "local" and self.local_endpoint is None:
            raise ProviderError("local_endpoint_unavailable", "本机模型服务未配置", 503)
        if not self.repository.activate_provider(provider, require_key=provider != "local"):
            message = "请先保存本机模型名称" if provider == "local" else "请先保存该供应商的 API Key"
            raise ProviderError("provider_not_configured", message)

    def snapshot(self) -> ProviderSnapshot:
        row = self.repository.active_provider()
        active = row["provider"] if row else "deepseek"
        return self._snapshot_row(active, row)

    def fallback_snapshot(self, provider: str) -> ProviderSnapshot:
        """Load one tenant-scoped standby without changing the active provider.

        This is credential acquisition, not permission to send a request. The
        fenced attempt gate must still recheck consent and budget immediately
        before any alternate Provider call.
        """
        if provider not in {"openai", "local"}:
            raise ProviderError("unsupported_provider", "不支持的备用模型供应商")
        row = self.repository.provider(provider)
        if not row or row["provider"] != provider:
            raise ProviderError("provider_not_configured", "备用模型未配置", 503)
        return self._snapshot_row(provider, row)

    def _snapshot_row(self, active: str, row: dict | None) -> ProviderSnapshot:
        if active == "local":
            if self.local_endpoint is None or not row.get("model"):
                raise ProviderError("local_endpoint_unavailable", "本机模型服务未配置", 503)
            return ProviderSnapshot("local", row["model"], "local-only", self._credential_namespace(row))
        if not row or not row["encrypted_key"]:
            raise ProviderError("provider_not_configured", f"请先在管理页配置 {active} API Key")
        try:
            namespace = self._credential_namespace(row)
            key = self.secrets.decrypt(row["encrypted_key"], row["nonce"], active, tenant_id=namespace)
        except Exception as exc:
            raise ProviderError("secret_decryption_failed", "API Key 无法解密，请重新录入") from exc
        return ProviderSnapshot(active, row["model"], key, namespace if namespace != LEGACY_TENANT_ID else None)

    @staticmethod
    def _credential_namespace(row):
        tenant = row.get("tenant_id")
        if tenant and str(tenant) != LEGACY_TENANT_ID:
            if row.get("key_aad_version") != 1:
                raise ValueError("Tenant credential binding is invalid")
            return str(tenant)
        return str(tenant) if tenant and row.get("key_aad_version") == 1 else None

    def build_model(self, snapshot: ProviderSnapshot) -> ObservedModel:
        if self.repository.credential_namespace() != snapshot.tenant_id:
            raise ProviderError("tenant_mismatch", "模型快照不属于当前租户", 403)
        if snapshot.provider == "local":
            if self.local_endpoint is None:
                raise ProviderError("local_endpoint_unavailable", "本机模型服务未配置", 503)
            provider = OpenAIProvider(base_url=self.local_endpoint.base_url,
                api_key="local-only", http_client=self._local_client())
            provider = OpenAIProvider(openai_client=provider.client.with_options(max_retries=0))
            return observe_model(OpenAIChatModel(snapshot.model, provider=provider), provider="local")
        provider = OpenAIProvider(
            base_url=PROVIDER_BASE_URLS[snapshot.provider],
            api_key=snapshot.api_key,
            http_client=self._client(),
        )
        # Centralize future retries in the gateway, where every paid attempt can
        # be journaled. Do not hide multiple HTTP charges in one SDK usage row.
        provider = OpenAIProvider(openai_client=provider.client.with_options(max_retries=0))
        return observe_model(OpenAIChatModel(snapshot.model, provider=provider), provider=snapshot.provider)

    async def ensure_local_tool_capability(self, snapshot: ProviderSnapshot) -> None:
        """Probe the loopback peer with synthetic data before fallback user content.

        A model directory entry is not evidence that the selected model can
        produce the function calls required by this Agent. This is a bounded
        protocol check, not a full context-length or quality certification.
        """
        if snapshot.provider != "local" or self.local_endpoint is None:
            raise ProviderError("local_endpoint_unavailable", "本机备用模型不可用", 503)
        body = {
            "model": snapshot.model,
            "messages": [{"role": "user", "content": "Call cs_capability_probe with ok=true."}],
            "tools": [{"type": "function", "function": {"name": "cs_capability_probe",
                       "description": "Synthetic protocol check", "parameters": {"type": "object",
                       "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
                       "additionalProperties": False}}}],
            "tool_choice": {"type": "function", "function": {"name": "cs_capability_probe"}},
            "max_tokens": 64,
            "temperature": 0,
        }
        try:
            async def probe():
                async with self._local_client().stream("POST", self.local_endpoint.base_url + "/chat/completions",
                        json=body, timeout=8) as response:
                    if response.status_code != 200:
                        raise ProviderError("local_capability_unverified", "本机模型工具能力未通过验证", 503)
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 262144:
                            raise ValueError("Capability response exceeds limit")
                    return json.loads(raw)
            payload = await asyncio.wait_for(probe(), timeout=8)
            choices = payload.get("choices") if isinstance(payload, dict) else None
            message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
            calls = message.get("tool_calls") if isinstance(message, dict) else None
            if not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict):
                raise ValueError("Expected one tool call")
            function = calls[0].get("function")
            if not isinstance(function, dict) or function.get("name") != "cs_capability_probe":
                raise ValueError("Unexpected tool name")
            arguments = function.get("arguments")
            parsed = json.loads(arguments) if isinstance(arguments, str) else None
            if parsed != {"ok": True}:
                raise ValueError("Unexpected tool arguments")
        except (asyncio.TimeoutError, httpx.HTTPError, ValueError, UnicodeError, TypeError) as exc:
            raise ProviderError("local_capability_unverified", "本机模型工具能力未通过验证", 503) from exc

    async def test(self, provider: str, model: str | None, candidate_key: str | None) -> dict:
        if provider not in (*PROVIDER_BASE_URLS, "local"):
            raise ProviderError("unsupported_provider", "不支持的模型供应商")
        row = await asyncio.to_thread(self.repository.provider, provider)
        if not row:
            raise ProviderError("provider_not_configured", "请先保存供应商配置")
        chosen_model = (model or row["model"]).strip()
        if provider == "local":
            if candidate_key:
                raise ProviderError("local_key_not_allowed", "本机模型不接受 API Key")
            if self.local_endpoint is None:
                raise ProviderError("local_endpoint_unavailable", "本机模型服务未配置", 503)
            try:
                async with self._local_client().stream("GET", self.local_endpoint.base_url + "/models",
                                                       timeout=10) as response:
                    if response.status_code != 200:
                        status, message = "error", f"本机模型返回 HTTP {response.status_code}"
                    else:
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > 262144:
                                raise ValueError("Model directory exceeds limit")
                        directory = json.loads(body)
                        entries = directory.get("data") if isinstance(directory, dict) else None
                        listed = (isinstance(entries, list) and any(
                            isinstance(entry, dict) and entry.get("id") == chosen_model
                            for entry in entries))
                        status = "success" if listed else "error"
                        message = ("本机已列出所选模型（生成及工具能力未验证）" if listed
                                   else "本机模型目录未列出所选模型")
            except (ValueError, UnicodeError):
                status, message = "error", "本机模型目录响应无效"
            except httpx.TimeoutException:
                status, message = "error", "连接超时"
            except httpx.HTTPError:
                status, message = "error", "网络连接失败"
            await asyncio.to_thread(self.repository.record_provider_test, provider,
                status=status, message=message, now=utc_now())
            return {"status": status, "message": message, "model": chosen_model}
        key = candidate_key.strip() if candidate_key else None
        if not key and row["encrypted_key"]:
            key = self.secrets.decrypt(row["encrypted_key"], row["nonce"], provider, tenant_id=self._credential_namespace(row))
        if not key:
            raise ProviderError("provider_not_configured", "请先输入或保存 API Key")
        # Connection checks must not create an unmetered generation charge or
        # transmit even a synthetic chat prompt. This verifies API access only.
        url = PROVIDER_BASE_URLS[provider].rstrip("/") + "/models"
        status, message = "error", "供应商模型目录响应无效"
        try:
            async with self._client().stream("GET", url,
                    headers={"Authorization": f"Bearer {key}"}, timeout=20,
                    follow_redirects=False) as response:
                if response.status_code != 200:
                    status, message = "error", f"供应商返回 HTTP {response.status_code}"
                else:
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 262144:
                            raise ValueError("Model directory exceeds limit")
                    try:
                        directory = json.loads(body)
                        entries = directory.get("data") if isinstance(directory, dict) else None
                        listed = isinstance(entries, list) and any(
                            isinstance(entry, dict) and entry.get("id") == chosen_model
                            for entry in entries)
                        status = "success" if listed else "error"
                        message = ("API 连接正常且模型目录包含所选模型（生成及工具能力未验证）"
                                   if listed else "供应商模型目录未列出所选模型")
                    except ValueError:
                        pass
        except (ValueError, UnicodeError):
            status, message = "error", "供应商模型目录响应无效"
        except httpx.TimeoutException:
            status, message = "error", "连接超时"
        except httpx.HTTPError:
            status, message = "error", "网络连接失败"
        await asyncio.to_thread(self.repository.record_provider_test, provider, status=status, message=message, now=utc_now())
        return {"status": status, "message": message, "model": chosen_model}
