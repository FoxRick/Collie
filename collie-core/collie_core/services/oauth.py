"""Generic OAuth 2.0 flow for service connections (F038, Step 34).

Authorization code + PKCE with a localhost callback:

1. Open the user's browser to the service's consent page.
2. A tiny local HTTP server catches the redirect on 127.0.0.1.
3. Exchange the code for tokens; the manager stores them per service.

Synchronous by design (blocks on the browser round trip) — the IPC server
runs connect calls in a worker thread, same as provider sign-in.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable

from loguru import logger

from collie_core.services.catalog import ServiceOAuth

__all__ = ["OAuthError", "ensure_fresh_tokens", "resolve_client", "run_oauth_flow"]

_CALLBACK_HOST = "127.0.0.1"
_DEFAULT_CALLBACK_PORT = 3719

_SUCCESS_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Collie</title></head>
<body style="font-family: system-ui; background: #FAFAFA; color: #0D0D0D;
             display: flex; align-items: center; justify-content: center;
             height: 100vh; margin: 0;">
<div style="text-align: center;">
  <div style="font-size: 64px;">&#128021;</div>
  <h1 style="margin: 8px 0;">Got it!</h1>
  <p style="color: #8C8C8C;">{service} is connected. You can close this tab
  and head back to Collie.</p>
</div></body></html>"""

_FAILURE_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Collie</title></head>
<body style="font-family: system-ui; background: #FAFAFA; color: #0D0D0D;
             display: flex; align-items: center; justify-content: center;
             height: 100vh; margin: 0;">
<div style="text-align: center;">
  <div style="font-size: 64px;">&#128021;</div>
  <h1 style="margin: 8px 0;">Uh oh...</h1>
  <p style="color: #8C8C8C;">That didn't go through. Head back to Collie and
  try connecting again.</p>
</div></body></html>"""


class OAuthError(RuntimeError):
    """Raised when a service OAuth flow cannot complete."""


def _callback_port() -> int:
    return int(os.environ.get("COLLIE_SERVICE_OAUTH_PORT", _DEFAULT_CALLBACK_PORT))


def resolve_client(spec: ServiceOAuth) -> tuple[str, str]:
    """Resolve (client_id, client_secret) from env with catalog defaults."""
    client_id = ""
    if spec.client_id_env:
        client_id = os.environ.get(spec.client_id_env, "")
    client_id = client_id or spec.default_client_id
    client_secret = ""
    if spec.client_secret_env:
        client_secret = os.environ.get(spec.client_secret_env, "")
    return client_id, client_secret


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


class _CallbackHandler(BaseHTTPRequestHandler):
    result: dict[str, str] = {}
    done: threading.Event = threading.Event()
    service_name: str = "The service"

    def do_GET(self) -> None:  # noqa: N802 - stdlib API name
        parsed = urllib.parse.urlparse(self.path)
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        type(self).result = params
        ok = "code" in params and "error" not in params
        body = (_SUCCESS_PAGE if ok else _FAILURE_PAGE).format(
            service=type(self).service_name
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        type(self).done.set()

    def log_message(self, *args: Any) -> None:  # silence stdlib logging
        return


def _post_token(token_url: str, payload: dict[str, str]) -> dict[str, Any]:
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(
        token_url,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "Collie/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()[:300]
        except Exception:
            pass
        raise OAuthError(f"token exchange failed ({e.code}): {detail}") from e
    except (urllib.error.URLError, ValueError) as e:
        raise OAuthError(f"token exchange failed: {e}") from e
    if not isinstance(body, dict) or not body.get("access_token"):
        raise OAuthError("token exchange returned no access token")
    return body


def _normalize_tokens(body: dict[str, Any], old: dict[str, Any] | None = None) -> dict[str, Any]:
    tokens: dict[str, Any] = dict(old or {})
    tokens["access_token"] = body["access_token"]
    if body.get("refresh_token"):
        tokens["refresh_token"] = body["refresh_token"]
    if body.get("expires_in"):
        tokens["expires_at"] = time.time() + float(body["expires_in"])
    if body.get("scope"):
        tokens["scope"] = body["scope"]
    return tokens


def run_oauth_flow(
    spec: ServiceOAuth,
    *,
    service_name: str = "The service",
    timeout: float = 300.0,
    open_browser: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Run the browser consent flow and return the token dict."""
    client_id, client_secret = resolve_client(spec)
    if not client_id:
        raise OAuthError(
            f"{service_name} isn't wired up in this build yet — "
            "check for updates and I'll fetch it as soon as it lands!"
        )

    port = _callback_port()
    redirect_uri = f"http://{_CALLBACK_HOST}:{port}/callback"
    state = secrets.token_urlsafe(24)
    verifier, challenge = _pkce_pair()

    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    if spec.scopes:
        params["scope"] = " ".join(spec.scopes)
    if spec.pkce:
        params["code_challenge"] = challenge
        params["code_challenge_method"] = "S256"
    params.update(dict(spec.extra_auth_params))
    auth_url = f"{spec.auth_url}?{urllib.parse.urlencode(params)}"

    handler = type(
        "_Handler",
        (_CallbackHandler,),
        {"result": {}, "done": threading.Event(), "service_name": service_name},
    )
    server = HTTPServer((_CALLBACK_HOST, port), handler)
    server.timeout = 1.0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        (open_browser or webbrowser.open)(auth_url)
        if not handler.done.wait(timeout):
            raise OAuthError("Sign-in didn't finish in time. Give it another go?")
    finally:
        server.shutdown()
        server.server_close()

    result = handler.result
    if result.get("error"):
        raise OAuthError(f"sign-in was declined: {result['error']}")
    if result.get("state") != state:
        raise OAuthError("sign-in state mismatch — try again")
    code = result.get("code")
    if not code:
        raise OAuthError("no authorization code returned")

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
    }
    if spec.pkce:
        payload["code_verifier"] = verifier
    if client_secret:
        payload["client_secret"] = client_secret

    body = _post_token(spec.token_url, payload)
    logger.info("Service OAuth complete for {}", service_name)
    return _normalize_tokens(body)


def ensure_fresh_tokens(
    spec: ServiceOAuth,
    tokens: dict[str, Any],
    *,
    skew: float = 60.0,
) -> tuple[dict[str, Any], bool]:
    """Refresh *tokens* if expired. Returns (tokens, refreshed)."""
    expires_at = tokens.get("expires_at")
    refresh = tokens.get("refresh_token")
    if not refresh or not expires_at or time.time() < float(expires_at) - skew:
        return tokens, False
    client_id, client_secret = resolve_client(spec)
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": str(refresh),
        "client_id": client_id,
    }
    if client_secret:
        payload["client_secret"] = client_secret
    body = _post_token(spec.token_url, payload)
    return _normalize_tokens(body, tokens), True
