"""Local OAuth callback server derived from a provider's redirect_uri.

``oauth_cli_kit``'s built-in callback server is hardcoded to the Codex
callback (``localhost:1455/auth/callback``). Collie's Claude flow uses the
public Claude Code OAuth client, whose registered redirect is
``http://localhost:54545/callback``, so the local server must follow the
provider's ``redirect_uri`` instead of a fixed port and path.
"""

from __future__ import annotations

import socket
import threading
import urllib.parse
from collections.abc import Callable
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from oauth_cli_kit.constants import SUCCESS_HTML


class _OAuthHandler(BaseHTTPRequestHandler):
    """Callback handler that accepts only the redirect path + expected state."""

    server_version = "CollieOAuth/1.0"
    protocol_version = "HTTP/1.1"

    def _send_body(
        self, status: int, body: bytes, content_type: str = "text/plain; charset=utf-8"
    ) -> None:
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        with suppress(Exception):
            self.wfile.flush()

    def do_GET(self) -> None:  # noqa: N802
        try:
            url = urllib.parse.urlparse(self.path)
            if url.path != self.server.callback_path:
                self._send_body(404, b"Not found")
                return

            qs = urllib.parse.parse_qs(url.query)
            code = qs.get("code", [None])[0]
            state = qs.get("state", [None])[0]

            if state != self.server.expected_state:
                self._send_body(400, b"State mismatch")
                return
            if not code:
                self._send_body(400, b"Missing code")
                return

            self.server.code = code
            try:
                if getattr(self.server, "on_code", None):
                    self.server.on_code(code)
            except Exception:
                pass
            self._send_body(200, SUCCESS_HTML.encode("utf-8"), "text/html; charset=utf-8")
        except Exception:
            self._send_body(500, b"Internal error")

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Suppress default logs to avoid noisy output.
        return


class _OAuthServer(ThreadingHTTPServer):
    """Callback server bound to one address family with expected-state checks."""

    daemon_threads = True
    block_on_close = False

    def __init__(
        self,
        server_address: tuple[str, int],
        expected_state: str,
        callback_path: str,
        on_code: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(server_address, _OAuthHandler)
        self.expected_state = expected_state
        self.callback_path = callback_path
        self.code: str | None = None
        self.on_code = on_code


def start_callback_server(
    redirect_uri: str,
    state: str,
    on_code: Callable[[str], None] | None = None,
) -> tuple[_OAuthServer | None, str | None]:
    """Start a localhost callback server matching ``redirect_uri``.

    The port and path are taken from the redirect URI so both the Codex
    (``localhost:1455/auth/callback``) and Claude (``localhost:54545/callback``)
    flows work. Returns ``(server, None)`` on success or ``(None, message)``.
    """
    parsed = urllib.parse.urlsplit(redirect_uri)
    try:
        port = parsed.port
    except ValueError as exc:
        return None, f"Invalid redirect_uri port: {exc}"
    if not port:
        return None, f"redirect_uri has no port: {redirect_uri}"

    host = parsed.hostname or "localhost"
    callback_path = parsed.path or "/auth/callback"

    try:
        addrinfos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        return None, f"Failed to resolve {host}: {exc}"

    last_error: OSError | None = None
    for family, _socktype, _proto, _canonname, sockaddr in addrinfos:
        try:
            # Support IPv4/IPv6 to avoid missing callbacks when localhost
            # resolves to ::1.
            class _AddrOAuthServer(_OAuthServer):
                address_family = family

            server = _AddrOAuthServer(sockaddr, state, callback_path, on_code=on_code)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            return server, None
        except OSError as exc:
            last_error = exc
            continue

    if last_error:
        return None, f"Local callback server failed to start: {last_error}"
    return None, "Local callback server failed to start: unknown error"
