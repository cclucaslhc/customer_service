from dataclasses import dataclass

import httpx
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.database import connect, utc_now
from app.security import SecretStore
from app.settings import MODEL_HTTP_MAX_CONNECTIONS, MODEL_HTTP_MAX_KEEPALIVE, MODEL_RUN_TIMEOUT_SECONDS


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
    api_key: str


class ModelGateway:
    def __init__(self, secrets: SecretStore) -> None:
        self.secrets = secrets
        self._http_client: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
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

    def list_configs(self) -> dict:
        with connect() as db:
            active = db.execute("SELECT value FROM app_settings WHERE key='active_provider'").fetchone()[0]
            rows = db.execute("SELECT * FROM provider_configs ORDER BY provider").fetchall()
        return {
            "active_provider": active,
            "providers": [
                {
                    "provider": row["provider"],
                    "model": row["model"],
                    "configured": bool(row["encrypted_key"]),
                    "updated_at": row["updated_at"],
                    "last_test_status": row["last_test_status"],
                    "last_test_message": row["last_test_message"],
                    "last_test_at": row["last_test_at"],
                }
                for row in rows
            ],
        }

    def update(self, provider: str, model: str, api_key: str | None) -> None:
        if provider not in PROVIDER_BASE_URLS:
            raise ProviderError("unsupported_provider", "不支持的模型供应商")
        with connect() as db:
            if api_key:
                encrypted, nonce = self.secrets.encrypt(api_key.strip(), provider)
                db.execute(
                    "UPDATE provider_configs SET model=?, encrypted_key=?, nonce=?, updated_at=? WHERE provider=?",
                    (model.strip(), encrypted, nonce, utc_now(), provider),
                )
            else:
                db.execute(
                    "UPDATE provider_configs SET model=?, updated_at=? WHERE provider=?",
                    (model.strip(), utc_now(), provider),
                )

    def delete_key(self, provider: str) -> None:
        with connect() as db:
            db.execute(
                "UPDATE provider_configs SET encrypted_key=NULL, nonce=NULL, updated_at=? WHERE provider=?",
                (utc_now(), provider),
            )

    def activate(self, provider: str) -> None:
        if provider not in PROVIDER_BASE_URLS:
            raise ProviderError("unsupported_provider", "不支持的模型供应商")
        with connect() as db:
            row = db.execute("SELECT encrypted_key FROM provider_configs WHERE provider=?", (provider,)).fetchone()
            if not row or not row["encrypted_key"]:
                raise ProviderError("provider_not_configured", "请先保存该供应商的 API Key")
            db.execute("UPDATE app_settings SET value=? WHERE key='active_provider'", (provider,))

    def snapshot(self) -> ProviderSnapshot:
        with connect() as db:
            active = db.execute("SELECT value FROM app_settings WHERE key='active_provider'").fetchone()[0]
            row = db.execute("SELECT * FROM provider_configs WHERE provider=?", (active,)).fetchone()
        if not row or not row["encrypted_key"]:
            raise ProviderError("provider_not_configured", f"请先在管理页配置 {active} API Key")
        try:
            key = self.secrets.decrypt(row["encrypted_key"], row["nonce"], active)
        except Exception as exc:
            raise ProviderError("secret_decryption_failed", "API Key 无法解密，请重新录入") from exc
        return ProviderSnapshot(active, row["model"], key)

    def build_model(self, snapshot: ProviderSnapshot) -> OpenAIChatModel:
        provider = OpenAIProvider(
            base_url=PROVIDER_BASE_URLS[snapshot.provider],
            api_key=snapshot.api_key,
            http_client=self._client(),
        )
        return OpenAIChatModel(snapshot.model, provider=provider)

    async def test(self, provider: str, model: str | None, candidate_key: str | None) -> dict:
        if provider not in PROVIDER_BASE_URLS:
            raise ProviderError("unsupported_provider", "不支持的模型供应商")
        with connect() as db:
            row = db.execute("SELECT * FROM provider_configs WHERE provider=?", (provider,)).fetchone()
        chosen_model = (model or row["model"]).strip()
        key = candidate_key.strip() if candidate_key else None
        if not key and row["encrypted_key"]:
            key = self.secrets.decrypt(row["encrypted_key"], row["nonce"], provider)
        if not key:
            raise ProviderError("provider_not_configured", "请先输入或保存 API Key")
        url = PROVIDER_BASE_URLS[provider].rstrip("/") + "/chat/completions"
        payload = {"model": chosen_model, "messages": [{"role": "user", "content": "Reply OK"}], "max_tokens": 4}
        status, message = "success", "连接成功"
        try:
            response = await self._client().post(url, headers={"Authorization": f"Bearer {key}"}, json=payload, timeout=20)
            if response.status_code >= 400:
                status, message = "error", f"供应商返回 HTTP {response.status_code}"
        except httpx.TimeoutException:
            status, message = "error", "连接超时"
        except httpx.HTTPError:
            status, message = "error", "网络连接失败"
        with connect() as db:
            db.execute(
                "UPDATE provider_configs SET last_test_status=?, last_test_message=?, last_test_at=? WHERE provider=?",
                (status, message, utc_now(), provider),
            )
        return {"status": status, "message": message, "model": chosen_model}
