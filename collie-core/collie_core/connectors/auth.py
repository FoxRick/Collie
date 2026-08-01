"""MCP OAuth 2.1 support backed by Collie's encrypted credential store."""

from __future__ import annotations

import asyncio
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from collie_core.services.credentials import CredentialStore

_SUCCESS = b"""<!doctype html><meta charset="utf-8"><title>Collie</title>
<body style="font-family:system-ui;text-align:center;padding:12vh 2rem">
<div style="font-size:64px">&#128021;</div><h1>You're connected!</h1>
<p>You can close this tab and head back to Collie.</p></body>"""


class CredentialStoreTokenStorage:
    """Adapt the MCP SDK TokenStorage protocol to encrypted per-connection blobs."""

    def __init__(self, store: CredentialStore, connection_id: str) -> None:
        self.store = store
        self.key = f"connector:{connection_id}"

    def _load(self) -> dict[str, Any]:
        return self.store.load(self.key) or {}

    def _save_part(self, key: str, value: Any) -> None:
        data = self._load()
        data[key] = value.model_dump(mode="json", exclude_none=True)
        self.store.save(self.key, data)

    async def get_tokens(self) -> Any:
        from mcp.shared.auth import OAuthToken

        data = self._load().get("tokens")
        return OAuthToken.model_validate(data) if isinstance(data, dict) else None

    async def set_tokens(self, tokens: Any) -> None:
        self._save_part("tokens", tokens)

    async def get_client_info(self) -> Any:
        from mcp.shared.auth import OAuthClientInformationFull

        data = self._load().get("client_info")
        return (
            OAuthClientInformationFull.model_validate(data)
            if isinstance(data, dict)
            else None
        )

    async def set_client_info(self, client_info: Any) -> None:
        self._save_part("client_info", client_info)


class _CallbackHandler(BaseHTTPRequestHandler):
    result: dict[str, str]
    done: threading.Event

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        type(self).result = {
            key: values[0]
            for key, values in urllib.parse.parse_qs(parsed.query).items()
        }
        body = _SUCCESS
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        type(self).done.set()

    def log_message(self, *args: Any) -> None:
        return


class LoopbackOAuthReceiver:
    """Single-use loopback receiver with an OS-assigned random port."""

    def __init__(self) -> None:
        handler = type(
            "_ConnectorCallback",
            (_CallbackHandler,),
            {"result": {}, "done": threading.Event()},
        )
        self.handler = handler
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.redirect_uri = (
            f"http://127.0.0.1:{self.server.server_port}/callback"
        )
        self._started = False

    async def redirect(self, authorization_url: str) -> None:
        if not self._started:
            self._started = True
            threading.Thread(
                target=self.server.serve_forever, daemon=True
            ).start()
        webbrowser.open(authorization_url)

    async def callback(self) -> tuple[str, str | None]:
        try:
            completed = await asyncio.to_thread(self.handler.done.wait, 300)
            if not completed:
                raise TimeoutError("Provider sign-in did not finish in time.")
            result = self.handler.result
            if result.get("error"):
                raise RuntimeError(f"Provider declined sign-in: {result['error']}")
            code = result.get("code")
            if not code:
                raise RuntimeError("Provider returned no authorization code.")
            return code, result.get("state")
        finally:
            self.server.shutdown()
            self.server.server_close()


def build_oauth_provider(
    connection_id: str,
    server_url: str,
    store: CredentialStore,
    *,
    scopes: tuple[str, ...] = (),
    interactive: bool,
) -> Any:
    """Build the Python MCP SDK OAuth provider.

    Interactive providers use a random loopback callback. Runtime providers
    reuse encrypted tokens and may refresh them, but never surprise-open a
    browser during an agent turn.
    """
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata

    storage = CredentialStoreTokenStorage(store, connection_id)
    if interactive:
        receiver = LoopbackOAuthReceiver()
        redirect_uris = [receiver.redirect_uri]
        redirect_handler = receiver.redirect
        callback_handler = receiver.callback
    else:
        redirect_uris = ["http://127.0.0.1:3719/callback"]
        redirect_handler = None
        callback_handler = None
    metadata = OAuthClientMetadata(
        client_name="Collie",
        redirect_uris=redirect_uris,
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        scope=" ".join(scopes) or None,
    )
    return OAuthClientProvider(
        server_url,
        metadata,
        storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
        timeout=300,
    )
