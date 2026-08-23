"""Tests for the Phase 3 service catalog, OAuth flow, and ServiceManager."""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from collie_core.db import CollieDB
from collie_core.services import oauth as service_oauth
from collie_core.services.catalog import (
    SERVICE_CATALOG,
    CredentialField,
    McpTemplate,
    ServiceOAuth,
    platform_supported,
    service_def,
)
from collie_core.services.credentials import CredentialStore
from collie_core.services.manager import (
    ServiceManager,
    bind_service_manager,
    get_service_manager,
)

# -- catalog -------------------------------------------------------------------


def test_catalog_is_curated_for_weekend_alpha() -> None:
    expected = {
        "gmail",
        "google-calendar",
        "outlook",
        "notion",
        "todoist",
        "spotify",
        "google-drive",
        "dropbox",
    }
    assert {s.id for s in SERVICE_CATALOG} == expected
    assert not any(service.available for service in SERVICE_CATALOG)
    assert not any(service.mcp.command == "npx" for service in SERVICE_CATALOG)


def test_service_def_lookup_is_case_insensitive() -> None:
    assert service_def("GMail") is not None
    assert service_def("nope") is None


def test_platform_gating() -> None:
    assert service_def("apple-notes") is None
    gmail = service_def("gmail")
    assert gmail is not None
    assert platform_supported(gmail, "win32")


# -- credential store ------------------------------------------------------------


_FAKE_PROTECTION_PREFIX = b"TEST-PROTECTED\x00"


def _fake_protect(data: bytes) -> bytes:
    return _FAKE_PROTECTION_PREFIX + data[::-1]


def _fake_unprotect(data: bytes) -> bytes:
    assert data.startswith(_FAKE_PROTECTION_PREFIX)
    return data[len(_FAKE_PROTECTION_PREFIX) :][::-1]


def _portable_credential_store(path: Path) -> CredentialStore:
    return CredentialStore(path, protect=_fake_protect, unprotect=_fake_unprotect)


def test_credential_store_roundtrip(tmp_path: Path) -> None:
    store = _portable_credential_store(tmp_path / "creds")
    assert store.load("todoist") is None
    store.save("todoist", {"todoist_token": "tok-123"})
    assert store.load("todoist") == {"todoist_token": "tok-123"}
    assert b"tok-123" not in (tmp_path / "creds" / "todoist.bin").read_bytes()
    store.delete("todoist")
    assert store.load("todoist") is None
    store.delete("todoist")  # idempotent


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows DPAPI")
def test_credential_store_real_dpapi_roundtrip(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path / "creds")
    store.save("todoist", {"todoist_token": "tok-123"})

    blob = (tmp_path / "creds" / "todoist.bin").read_bytes()
    assert blob.startswith(b"COLLIE-DPAPI\x00")
    assert b"tok-123" not in blob
    assert store.load("todoist") == {"todoist_token": "tok-123"}


# -- fake OAuth service ------------------------------------------------------------


class _FakeTokenEndpoint(BaseHTTPRequestHandler):
    requests: list[dict[str, str]] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode()
        params = {k: v[0] for k, v in urllib.parse.parse_qs(body).items()}
        type(self).requests.append(params)
        payload = json.dumps(
            {
                "access_token": f"at-{params.get('grant_type')}",
                "refresh_token": "rt-1",
                "expires_in": 3600,
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:
        return


@pytest.fixture()
def fake_token_server():
    handler = type("_Tok", (_FakeTokenEndpoint,), {"requests": []})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/token", handler
    server.shutdown()
    server.server_close()


def _fake_spec(token_url: str) -> ServiceOAuth:
    return ServiceOAuth(
        auth_url="http://127.0.0.1:1/never-opened",
        token_url=token_url,
        scopes=("scope.a",),
        default_client_id="collie-test",
    )


def _browser_that_approves(auth_url: str) -> None:
    """Simulate the user approving: hit the callback with code + state."""
    parsed = urllib.parse.urlparse(auth_url)
    params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
    redirect = params["redirect_uri"]
    query = urllib.parse.urlencode({"code": "auth-code-1", "state": params["state"]})

    def _hit() -> None:
        urllib.request.urlopen(f"{redirect}?{query}", timeout=5).read()

    threading.Thread(target=_hit, daemon=True).start()


def test_oauth_flow_end_to_end(fake_token_server, monkeypatch) -> None:
    token_url, handler = fake_token_server
    monkeypatch.setenv("COLLIE_SERVICE_OAUTH_PORT", "3799")
    tokens = service_oauth.run_oauth_flow(
        _fake_spec(token_url),
        service_name="Test Service",
        timeout=10,
        open_browser=_browser_that_approves,
    )
    assert tokens["access_token"] == "at-authorization_code"
    assert tokens["refresh_token"] == "rt-1"
    assert tokens["expires_at"] > time.time()
    exchange = handler.requests[0]
    assert exchange["code"] == "auth-code-1"
    assert exchange["client_id"] == "collie-test"
    assert "code_verifier" in exchange


def test_oauth_flow_requires_client_id() -> None:
    spec = ServiceOAuth(
        auth_url="http://127.0.0.1:1/a",
        token_url="http://127.0.0.1:1/t",
        client_id_env="COLLIE_MISSING_CLIENT_ID_FOR_TEST",
    )
    with pytest.raises(service_oauth.OAuthError):
        service_oauth.run_oauth_flow(spec, service_name="X", timeout=1)


def test_ensure_fresh_tokens_refreshes_expired(fake_token_server) -> None:
    token_url, handler = fake_token_server
    spec = _fake_spec(token_url)
    stale = {"access_token": "old", "refresh_token": "rt-0", "expires_at": time.time() - 10}
    fresh, refreshed = service_oauth.ensure_fresh_tokens(spec, stale)
    assert refreshed is True
    assert fresh["access_token"] == "at-refresh_token"
    assert handler.requests[0]["grant_type"] == "refresh_token"

    keep, refreshed = service_oauth.ensure_fresh_tokens(
        spec, {"access_token": "ok", "refresh_token": "rt", "expires_at": time.time() + 3600}
    )
    assert refreshed is False
    assert keep["access_token"] == "ok"


# -- manager ------------------------------------------------------------------------


@pytest.fixture()
def manager(tmp_path: Path):
    db = CollieDB(tmp_path / "collie.db")
    mgr = ServiceManager(
        db,
        credentials=_portable_credential_store(tmp_path / "creds"),
        oauth_runner=lambda spec, service_name: {
            "access_token": "at-fake",
            "refresh_token": "rt-fake",
            "expires_at": time.time() + 3600,
        },
        platform="win32",
    )
    yield mgr
    db.close()


def test_catalog_view_defaults(manager: ServiceManager) -> None:
    view = manager.catalog_view()
    assert len(view) == len(SERVICE_CATALOG)
    by_id = {s["id"]: s for s in view}
    assert by_id["gmail"]["status"] == "coming_soon"
    assert by_id["gmail"]["release_status"] == "coming_soon"
    assert by_id["todoist"]["available"] is False


def _enable_for_unit_test(monkeypatch, service_id: str, **changes):
    from collie_core.services import catalog

    service = service_def(service_id)
    assert service is not None
    enabled = replace(service, available=True, release_status="alpha", **changes)
    monkeypatch.setitem(catalog._BY_ID, service_id, enabled)
    return enabled


def test_connect_api_key_service(manager: ServiceManager, monkeypatch) -> None:
    _enable_for_unit_test(
        monkeypatch,
        "todoist",
        fields=(CredentialField("todoist_token", "Todoist API token"),),
        mcp=McpTemplate(
            command="node.exe",
            args=("server.mjs",),
            env=(("TODOIST_API_TOKEN", "{todoist_token}"),),
        ),
    )
    result = manager.connect("todoist", {"todoist_token": "tok-1"})
    assert result["status"] == "connected"
    assert manager.is_connected("todoist")
    servers = manager.mcp_servers_for_config()
    assert servers["todoist"]["command"] == "node.exe"
    assert servers["todoist"]["env"]["TODOIST_API_TOKEN"] == "tok-1"


def test_connect_api_key_missing_field(manager: ServiceManager, monkeypatch) -> None:
    _enable_for_unit_test(
        monkeypatch,
        "todoist",
        fields=(CredentialField("todoist_token", "Todoist API token"),),
    )
    with pytest.raises(ValueError, match="Todoist API token"):
        manager.connect("todoist", {})
    row = manager.db.get_service("todoist")
    assert row is not None and row["status"] == "failed"
    assert not manager.is_connected("todoist")


def test_connect_oauth_service(manager: ServiceManager, monkeypatch) -> None:
    _enable_for_unit_test(
        monkeypatch,
        "google-calendar",
        mcp=McpTemplate(
            command="node.exe",
            args=("server.mjs",),
            env=(
                ("GOOGLE_OAUTH_ACCESS_TOKEN", "{access_token}"),
                ("GOOGLE_OAUTH_REFRESH_TOKEN", "{refresh_token}"),
                ("GOOGLE_OAUTH_CLIENT_ID", "{client_id}"),
                ("GOOGLE_OAUTH_CLIENT_SECRET", "{client_secret}"),
            ),
        ),
    )
    monkeypatch.setenv("COLLIE_GOOGLE_CLIENT_ID", "cid-1")
    monkeypatch.setenv("COLLIE_GOOGLE_CLIENT_SECRET", "cs-1")
    result = manager.connect("google-calendar")
    assert result["status"] == "connected"
    servers = manager.mcp_servers_for_config()
    env = servers["google-calendar"]["env"]
    assert env["GOOGLE_OAUTH_ACCESS_TOKEN"] == "at-fake"
    assert env["GOOGLE_OAUTH_REFRESH_TOKEN"] == "rt-fake"
    assert env["GOOGLE_OAUTH_CLIENT_ID"] == "cid-1"
    assert env["GOOGLE_OAUTH_CLIENT_SECRET"] == "cs-1"


def test_connect_unknown_and_unavailable(manager: ServiceManager) -> None:
    with pytest.raises(ValueError):
        manager.connect("myspace")
    with pytest.raises(ValueError):
        manager.connect("gmail")


def test_disconnect_service(manager: ServiceManager, monkeypatch) -> None:
    _enable_for_unit_test(
        monkeypatch,
        "todoist",
        fields=(CredentialField("todoist_token", "Todoist API token"),),
        mcp=McpTemplate(command="node.exe", args=("server.mjs",)),
    )
    manager.connect("todoist", {"todoist_token": "tok-1"})
    result = manager.disconnect("todoist")
    assert result["status"] == "disconnected"
    assert not manager.is_connected("todoist")
    assert manager.mcp_servers_for_config() == {}
    assert manager.credentials.load("todoist") is None
    view = {s["id"]: s for s in manager.catalog_view()}
    assert view["todoist"]["status"] == "disconnected"


def test_bind_service_manager(manager: ServiceManager) -> None:
    bind_service_manager(manager)
    try:
        assert get_service_manager() is manager
    finally:
        bind_service_manager(None)


# -- config injection --------------------------------------------------------------


def test_build_config_includes_mcp_servers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("COLLIE_HOME", str(tmp_path / "home"))
    from collie_core import settings as collie_settings

    db = CollieDB(tmp_path / "collie.db")
    try:
        collie_settings.set_api_key("openai", "sk-test")
        config = collie_settings.build_config(
            db,
            mcp_servers={
                "todoist": {
                    "command": "npx",
                    "args": ["-y", "@abhiz123/todoist-mcp-server"],
                    "env": {"TODOIST_API_TOKEN": "tok"},
                    "toolTimeout": 60,
                }
            },
        )
        server = config.tools.mcp_servers["todoist"]
        assert server.command == "npx"
        assert server.env["TODOIST_API_TOKEN"] == "tok"
        assert server.tool_timeout == 60
    finally:
        db.close()
