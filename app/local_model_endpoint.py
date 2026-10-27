"""Strict per-host endpoint boundary for a local OpenAI-compatible model.

Only a literal IPv4 loopback address is accepted. A model server is an
untrusted HTTP peer: redirects and proxy environment variables must not turn
its requests into arbitrary outbound traffic.
"""

from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True)
class LocalModelEndpoint:
    base_url: str

    def __post_init__(self) -> None:
        try:
            parsed = urlsplit(self.base_url)
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Invalid local model endpoint") from exc
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                or port is None or not 1 <= port <= 65535
                or parsed.username is not None or parsed.password is not None
                or parsed.path != "/v1" or parsed.query or parsed.fragment
                or parsed.netloc != f"127.0.0.1:{port}"):
            raise ValueError("Local model endpoint must be http://127.0.0.1:<port>/v1")

    def client(self, *, timeout: float = 30) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            max_redirects=0,
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10)),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
