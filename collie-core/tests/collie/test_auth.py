"""Tests for Collie auth: OAuth login/logout/status + provider override wiring."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from collie_core.providers import auth as collie_auth


class FakeToken(SimpleNamespace):
    pass


@pytest.fixture()
def fake_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point both providers' token storage at tmp files."""

    class _Storage:
        def __init__(self, name: str):
            self._path = tmp_path / name

        def get_token_path(self) -> Path:
            return self._path

    codex = _Storage("codex.json")
    claude = _Storage("claude.json")
    monkeypatch.setattr(
        collie_auth,
        "_codex_provider_and_storage",
        lambda: (SimpleNamespace(token_filename="codex.json"), codex),
    )
    monkeypatch.setattr(
        collie_auth,
        "_claude_provider_and_storage",
        lambda: (SimpleNamespace(token_filename="claude.json"), claude),
    )
    return {"codex": codex, "claude": claude}


def test_spec_aliases(fake_storage) -> None:
    assert collie_auth._spec("ChatGPT")[0] == "chatgpt"
    assert collie_auth._spec("openai_codex")[0] == "chatgpt"
    assert collie_auth._spec("claude")[0] == "claude"
    assert collie_auth._spec("anthropic")[0] == "claude"
    with pytest.raises(ValueError):
        collie_auth._spec("gemini")


def test_login_uses_cached_token(fake_storage, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"interactive": 0}

    def fake_get_token(provider=None, storage=None, **kwargs):
        return FakeToken(access="tok-cached", account_id="acct-1")

    def fake_interactive(_config, storage, _cancelled):
        calls["interactive"] += 1
        return FakeToken(access="tok-new", account_id="acct-1")

    import oauth_cli_kit

    monkeypatch.setattr(oauth_cli_kit, "get_token", fake_get_token)
    monkeypatch.setattr(collie_auth, "_login_oauth_cancellable", fake_interactive)

    result = collie_auth.login_provider("chatgpt")
    assert result == {"provider": "chatgpt", "signed_in": True, "account_id": "acct-1"}
    assert calls["interactive"] == 0


def test_login_falls_back_to_oauth_flow(fake_storage, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_token(provider=None, storage=None, **kwargs):
        raise RuntimeError("no token")

    def fake_interactive(_config, storage, _cancelled):
        return FakeToken(access="tok-new", account_id="acct-2")

    import oauth_cli_kit

    monkeypatch.setattr(oauth_cli_kit, "get_token", fake_get_token)
    monkeypatch.setattr(collie_auth, "_login_oauth_cancellable", fake_interactive)

    result = collie_auth.login_provider("claude")
    assert result["provider"] == "claude"
    assert result["signed_in"] is True


def test_login_failure_is_friendly(fake_storage, monkeypatch: pytest.MonkeyPatch) -> None:
    import oauth_cli_kit

    monkeypatch.setattr(
        oauth_cli_kit,
        "get_token",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        collie_auth,
        "_login_oauth_cancellable",
        lambda _config, _storage, _cancelled: FakeToken(access=None),
    )
    with pytest.raises(ValueError, match="another go"):
        collie_auth.login_provider("chatgpt")


def test_logout_removes_token_files(fake_storage) -> None:
    path = fake_storage["claude"].get_token_path()
    path.write_text("{}", encoding="utf-8")
    result = collie_auth.logout_provider("claude")
    assert result == {"provider": "claude", "signed_in": False}
    assert not path.exists()


def test_oauth_status(fake_storage, monkeypatch: pytest.MonkeyPatch) -> None:
    import oauth_cli_kit

    monkeypatch.setattr(
        oauth_cli_kit,
        "get_token",
        lambda **kwargs: FakeToken(access="tok", account_id="a-9"),
    )
    status = collie_auth.oauth_status("chatgpt")
    assert status == {"provider": "chatgpt", "signed_in": True, "account_id": "a-9"}

    monkeypatch.setattr(oauth_cli_kit, "get_token", lambda **kwargs: None)
    status = collie_auth.oauth_status("claude")
    assert status["signed_in"] is False


def test_claude_oauth_provider_config() -> None:
    from collie_core.providers.claude_oauth import ANTHROPIC_OAUTH_PROVIDER

    assert ANTHROPIC_OAUTH_PROVIDER.authorize_url.startswith("https://console.anthropic.com/")
    assert ANTHROPIC_OAUTH_PROVIDER.scope == "org:create_api_key user:profile"
    assert ANTHROPIC_OAUTH_PROVIDER.redirect_uri == "http://localhost:54545/callback"
    assert ANTHROPIC_OAUTH_PROVIDER.token_filename == "claude.json"


def test_callback_server_derives_from_redirect_uri() -> None:
    import urllib.error
    import urllib.request

    from collie_core.providers.callback_server import start_callback_server

    received: list[str] = []
    server, err = start_callback_server(
        "http://localhost:54545/callback", "st-1", on_code=received.append
    )
    assert server is not None and err is None
    host = str(server.server_address[0])
    port = int(server.server_address[1])
    base = f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"{base}/wrong-path", timeout=5)
        assert exc.value.code == 404

        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"{base}/callback?code=c1&state=bad-state", timeout=5)
        assert exc.value.code == 400

        resp = urllib.request.urlopen(f"{base}/callback?code=c1&state=st-1", timeout=5)
        assert resp.status == 200
        assert received == ["c1"]
    finally:
        server.shutdown()
        server.server_close()


def test_oauth_authorize_url_params_per_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Claude requests carry no Codex-only params; Codex requests keep them."""
    import webbrowser

    import oauth_cli_kit.flow as oauth_flow

    import collie_core.providers.callback_server as callback_server_mod
    from collie_core.providers.claude_oauth import ANTHROPIC_OAUTH_PROVIDER

    opened: list[str] = []
    exchanged: list[str] = []
    saved: list[FakeToken] = []

    class FakeServer:
        def __init__(self, redirect_uri, state, on_code=None):
            self.on_code = on_code

        def shutdown(self):
            pass

        def server_close(self):
            pass

    class Storage:
        def save(self, token) -> None:
            saved.append(token)

    def fake_start(redirect_uri, state, on_code=None):
        server = FakeServer(redirect_uri, state, on_code)
        if on_code is not None:
            on_code("srv-code")  # complete the flow immediately
        return server, None

    def fake_exchange(code, verifier, provider):
        exchanged.append(code)

        async def _run():
            return FakeToken(access="tok", refresh="ref", expires=1, account_id="a")

        return _run

    monkeypatch.setattr(callback_server_mod, "start_callback_server", fake_start)
    monkeypatch.setattr(oauth_flow, "_exchange_code_for_token_async", fake_exchange)
    monkeypatch.setattr(oauth_flow, "_should_open_browser", lambda: True)
    monkeypatch.setattr(webbrowser, "open", opened.append)

    token = collie_auth._login_oauth_cancellable(
        ANTHROPIC_OAUTH_PROVIDER, Storage(), threading.Event()
    )
    assert token.access == "tok"
    assert exchanged == ["srv-code"]
    assert len(opened) == 1
    claude_url = opened[0]
    assert claude_url.startswith("https://console.anthropic.com/oauth/authorize?")
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A54545%2Fcallback" in claude_url
    assert "scope=org%3Acreate_api_key+user%3Aprofile" in claude_url
    assert "codex_cli_simplified_flow" not in claude_url
    assert "originator" not in claude_url

    opened.clear()
    from oauth_cli_kit.providers import OPENAI_CODEX_PROVIDER

    token = collie_auth._login_oauth_cancellable(
        OPENAI_CODEX_PROVIDER, Storage(), threading.Event()
    )
    assert token.access == "tok"
    assert len(opened) == 1
    codex_url = opened[0]
    assert codex_url.startswith("https://auth.openai.com/oauth/authorize?")
    assert "originator=nanobot" in codex_url
    assert "codex_cli_simplified_flow=true" in codex_url


def test_oauth_attempt_stages_token_until_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    persisted: list[FakeToken] = []

    class Storage:
        def get_token_path(self) -> Path:
            return tmp_path / "oauth.bin"

        def load(self):
            return None

        def save(self, token) -> None:
            persisted.append(token)

    config = SimpleNamespace(token_filename="oauth.json")
    monkeypatch.setattr(collie_auth, "_spec", lambda _provider: ("chatgpt", (config, Storage())))

    import oauth_cli_kit

    monkeypatch.setattr(oauth_cli_kit, "get_token", lambda **_kwargs: None)

    def interactive(_config, storage, _cancelled):
        token = FakeToken(access="new-token", account_id="acct")
        storage.save(token)
        return token

    monkeypatch.setattr(collie_auth, "_login_oauth_cancellable", interactive)

    attempt = collie_auth.OAuthLoginAttempt("chatgpt")
    assert attempt.run()["signed_in"] is True
    assert persisted == []
    assert attempt.commit() is True
    assert [token.access for token in persisted] == ["new-token"]


def test_cancelled_oauth_attempt_discards_late_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    started = threading.Event()
    release = threading.Event()
    persisted: list[FakeToken] = []

    class Storage:
        def get_token_path(self) -> Path:
            return tmp_path / "oauth.bin"

        def load(self):
            return None

        def save(self, token) -> None:
            persisted.append(token)

    config = SimpleNamespace(token_filename="oauth.json")
    monkeypatch.setattr(collie_auth, "_spec", lambda _provider: ("chatgpt", (config, Storage())))

    import oauth_cli_kit

    monkeypatch.setattr(oauth_cli_kit, "get_token", lambda **_kwargs: None)

    def interactive(_config, storage, _cancelled):
        started.set()
        assert release.wait(2)
        token = FakeToken(access="late-token", account_id="acct")
        storage.save(token)
        return token

    monkeypatch.setattr(collie_auth, "_login_oauth_cancellable", interactive)

    attempt = collie_auth.OAuthLoginAttempt("chatgpt")
    errors: list[BaseException] = []

    def run() -> None:
        try:
            attempt.run()
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    assert started.wait(2)
    attempt.cancel()
    release.set()
    worker.join(2)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], collie_auth.OAuthLoginCancelledError)
    assert attempt.commit() is False
    assert persisted == []


def test_cancelled_oauth_attempt_closes_callback_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from oauth_cli_kit import flow as oauth_flow
    from oauth_cli_kit.providers import OPENAI_CODEX_PROVIDER

    import collie_core.providers.callback_server as callback_server_mod

    started = threading.Event()
    closed: list[str] = []

    class Storage:
        def get_token_path(self) -> Path:
            return tmp_path / "oauth.bin"

        def load(self):
            return None

        def save(self, _token) -> None:
            raise AssertionError("cancelled callback must not save a token")

    class Server:
        def shutdown(self) -> None:
            closed.append("shutdown")

        def server_close(self) -> None:
            closed.append("close")

    def start_server(_redirect_uri, _state, on_code=None):
        del on_code
        started.set()
        return Server(), None

    monkeypatch.setattr(
        collie_auth,
        "_spec",
        lambda _provider: ("chatgpt", (OPENAI_CODEX_PROVIDER, Storage())),
    )
    monkeypatch.setattr(callback_server_mod, "start_callback_server", start_server)
    monkeypatch.setattr(oauth_flow, "_should_open_browser", lambda: False)

    import oauth_cli_kit

    monkeypatch.setattr(oauth_cli_kit, "get_token", lambda **_kwargs: None)
    attempt = collie_auth.OAuthLoginAttempt("chatgpt")
    errors: list[BaseException] = []

    def run() -> None:
        try:
            attempt.run()
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    assert started.wait(2)
    attempt.cancel()
    worker.join(2)

    assert not worker.is_alive()
    assert isinstance(errors[0], collie_auth.OAuthLoginCancelledError)
    assert closed == ["shutdown", "close"]


def test_claude_oauth_provider_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from collie_core.providers import claude_oauth

    monkeypatch.setattr(claude_oauth, "_current_access_token", lambda: None)
    with pytest.raises(RuntimeError, match="Not signed in"):
        claude_oauth.ClaudeOAuthProvider()


def test_claude_oauth_provider_builds_bearer_client(monkeypatch: pytest.MonkeyPatch) -> None:
    from collie_core.providers import claude_oauth

    monkeypatch.setattr(claude_oauth, "_current_access_token", lambda: "tok-abc")
    provider = claude_oauth.ClaudeOAuthProvider(default_model="claude-sonnet-4-6")
    assert provider.get_default_model() == "claude-sonnet-4-6"
    assert provider.extra_headers["anthropic-beta"] == "oauth-2025-04-20"
    assert provider._client.auth_token == "tok-abc"

    monkeypatch.setattr(claude_oauth, "_current_access_token", lambda: "tok-refreshed")
    assert provider.refresh_auth() is True
    assert provider._client.auth_token == "tok-refreshed"


def test_runtime_provider_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLLIE_HOME", str(tmp_path / ".collie"))
    from collie_core.db import CollieDB
    from collie_core.runtime import CollieRuntime

    runtime = CollieRuntime(port=0, db=CollieDB(tmp_path / "c.db"))

    assert runtime._provider_override() is None

    runtime.db.set_setting("provider.auth", "claude-oauth")
    from collie_core.providers import claude_oauth

    monkeypatch.setattr(claude_oauth, "_current_access_token", lambda: "tok")
    provider = runtime._provider_override()
    assert type(provider).__name__ == "ClaudeOAuthProvider"

    runtime.db.set_setting("provider.auth", "chatgpt-oauth")
    provider = runtime._provider_override()
    assert type(provider).__name__ == "OpenAICodexProvider"
    runtime.db.close()


# -- B3: OAuth tokens encrypted at rest (DPAPI) ---------------------------------


def _fake_protect(data: bytes) -> bytes:
    return b"FAKE:" + data


def _fake_unprotect(data: bytes) -> bytes:
    assert data.startswith(b"FAKE:")
    return data[len(b"FAKE:") :]


@pytest.fixture()
def fake_dpapi_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the provider storage at a temp dir with a reversible fake DPAPI."""
    import collie_core.providers.storage as storage_mod
    import collie_core.services.credentials as credentials_mod

    class FakeCredentialStore(credentials_mod.CredentialStore):
        def __init__(self) -> None:  # type: ignore[override]
            super().__init__(
                tmp_path / "credentials",
                protect=_fake_protect,
                unprotect=_fake_unprotect,
            )

    monkeypatch.setattr(storage_mod, "CredentialStore", FakeCredentialStore)
    return tmp_path


def test_dpapi_token_storage_round_trip(fake_dpapi_store) -> None:
    from collie_core.providers.storage import DpapiTokenStorage

    storage = DpapiTokenStorage(token_filename="claude.json")
    storage.save(FakeToken(access="acc", refresh="ref", expires=1234, account_id="a1"))

    blob = storage.get_token_path()
    assert blob.exists()
    assert blob.read_bytes().startswith(b"COLLIE-DPAPI\x00FAKE:")

    loaded = storage.load()
    assert loaded.access == "acc"
    assert loaded.refresh == "ref"
    assert loaded.expires == 1234
    assert loaded.account_id == "a1"


def test_legacy_oauth_root_honors_isolation_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collie_core.providers.storage import legacy_oauth_data_root

    isolated = tmp_path / "oauth-root"
    monkeypatch.setenv("COLLIE_OAUTH_ROOT", str(isolated))
    monkeypatch.setenv("COLLIE_LEGACY_OAUTH_ROOT", str(tmp_path / "ignored-alias"))

    assert legacy_oauth_data_root() == isolated


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows DPAPI")
def test_dpapi_token_storage_real_encryption(tmp_path: Path, monkeypatch) -> None:
    """On Windows the stored blob must never contain the token in plaintext."""
    monkeypatch.setenv("COLLIE_HOME", str(tmp_path))
    from collie_core.providers.storage import DpapiTokenStorage

    storage = DpapiTokenStorage(token_filename="codex.json")
    storage.save(
        FakeToken(
            access="super-secret-access",
            refresh="super-secret-refresh",
            expires=1234,
            account_id="a1",
        )
    )
    blob = storage.get_token_path()
    raw = blob.read_bytes()
    assert raw.startswith(b"COLLIE-DPAPI\x00")
    assert b"super-secret-access" not in raw
    assert b"super-secret-refresh" not in raw
    assert storage.load().access == "super-secret-access"


def test_dpapi_token_storage_migrates_plaintext_legacy(
    fake_dpapi_store, tmp_path, monkeypatch
) -> None:
    from oauth_cli_kit.models import OAuthToken

    from collie_core.providers.storage import DpapiTokenStorage

    legacy_path = tmp_path / "legacy" / "auth" / "codex.json"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text(
        json.dumps({"access": "old", "refresh": "old-refresh", "expires": 99, "account_id": "a9"}),
        encoding="utf-8",
    )

    class LegacyStorage:
        def get_token_path(self) -> Path:
            return legacy_path

        def load(self):
            from oauth_cli_kit.storage import FileTokenStorage

            return FileTokenStorage(
                token_filename="codex.json",
                data_dir=tmp_path / "legacy",
                import_codex_cli=False,
            ).load()

        def save(self, token: OAuthToken) -> None:
            from oauth_cli_kit.storage import FileTokenStorage

            FileTokenStorage(
                token_filename="codex.json",
                data_dir=tmp_path / "legacy",
                import_codex_cli=False,
            ).save(token)

    storage = DpapiTokenStorage(token_filename="codex.json")
    monkeypatch.setattr(storage, "_plain_storage", lambda: LegacyStorage())

    loaded = storage.load()
    assert loaded.access == "old"
    # The legacy token was migrated into the (fake-)encrypted blob.
    blob = storage.get_token_path()
    assert blob.exists()
    assert blob.read_bytes().startswith(b"COLLIE-DPAPI\x00FAKE:")
    assert storage.load().refresh == "old-refresh"


def test_clear_all_wipes_credentials_oauth_and_pairing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """clear_all_data must remove DPAPI blobs, legacy tokens, and pairing."""
    import socket

    import websockets

    from collie_core.db import CollieDB
    from collie_core.ipc.server import CollieIPCServer

    home = tmp_path / "home"
    monkeypatch.setenv("COLLIE_HOME", str(home))
    (home / "credentials").mkdir(parents=True)
    (home / "credentials" / "oauth-claude.bin").write_bytes(b"FAKE:blob")
    (home / "pairing.json").write_text('{"approved": {}}', encoding="utf-8")
    legacy = tmp_path / "appdata" / "oauth-cli-kit" / "auth"
    legacy.mkdir(parents=True)
    (legacy / "claude.json").write_text("{}", encoding="utf-8")

    db = CollieDB(home / "collie.db")
    clear_result: dict = {}

    async def run() -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        srv = CollieIPCServer(
            db,
            port=port,
            legacy_oauth_root=tmp_path / "appdata" / "oauth-cli-kit",
        )
        await srv.start()
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                ready = json.loads(await ws.recv())
                assert ready["type"] == "ready"
                await ws.send(json.dumps({"type": "clear_all_data", "confirm": True, "id": "1"}))
                while True:
                    frame = json.loads(await ws.recv())
                    if frame.get("type") == "ok":
                        clear_result.update(frame["data"])
                        break
        finally:
            await srv.stop()

    asyncio.run(run())
    db.close()

    assert not (home / "credentials").exists()
    assert not (home / "pairing.json").exists()
    assert not (legacy / "claude.json").exists()
    assert clear_result == {
        "cleared": True,
        "partial": False,
        "database_cleared": True,
        "filesystem_cleared": True,
        "warnings": [],
    }


@pytest.mark.asyncio
async def test_clear_all_reports_permission_error_after_database_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collie_core.db import CollieDB
    from collie_core.ipc.server import CollieIPCServer

    home = tmp_path / "home"
    monkeypatch.setenv("COLLIE_HOME", str(home))
    pairing_path = home / "pairing.json"
    pairing_path.parent.mkdir(parents=True)
    pairing_path.write_text("{}", encoding="utf-8")

    db = CollieDB(home / "collie.db")
    db.create_conversation("must be cleared")
    server = CollieIPCServer(db, legacy_oauth_root=tmp_path / "legacy-oauth")
    original_unlink = Path.unlink

    def guarded_unlink(path: Path, *args, **kwargs) -> None:
        if path == pairing_path:
            raise PermissionError("pairing file is locked")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", guarded_unlink)
    try:
        result = await server._cmd_clear_all_data(None, {"confirm": True})

        assert result["cleared"] is False
        assert result["partial"] is True
        assert result["database_cleared"] is True
        assert result["filesystem_cleared"] is False
        assert result["warnings"] == [
            {
                "scope": "filesystem",
                "target": str(pairing_path),
                "error": "PermissionError: pairing file is locked",
            }
        ]
        assert db.list_conversations(include_archived=True) == []
        assert pairing_path.exists()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_clear_all_database_failure_skips_filesystem_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collie_core.db import CollieDB
    from collie_core.ipc.server import CollieIPCServer

    home = tmp_path / "home"
    monkeypatch.setenv("COLLIE_HOME", str(home))
    credential = home / "credentials" / "oauth-claude.bin"
    credential.parent.mkdir(parents=True)
    credential.write_bytes(b"still-here")
    legacy_root = tmp_path / "legacy-oauth"
    legacy_token = legacy_root / "auth" / "claude.json"
    legacy_token.parent.mkdir(parents=True)
    legacy_token.write_text("{}", encoding="utf-8")

    db = CollieDB(home / "collie.db")
    db.create_conversation("must remain")
    server = CollieIPCServer(db, legacy_oauth_root=legacy_root)

    def fail_clear() -> None:
        raise RuntimeError("database is busy")

    monkeypatch.setattr(db, "clear_all", fail_clear)
    try:
        result = await server._cmd_clear_all_data(None, {"confirm": True})

        assert result["cleared"] is False
        assert result["partial"] is False
        assert result["database_cleared"] is False
        assert result["filesystem_cleared"] is False
        assert result["warnings"] == [
            {
                "scope": "database",
                "target": str(db.path),
                "error": "RuntimeError: database is busy",
            }
        ]
        assert len(db.list_conversations(include_archived=True)) == 1
        assert credential.exists()
        assert legacy_token.exists()
    finally:
        db.close()
