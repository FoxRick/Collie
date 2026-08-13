"""Phase 5 provider candidate transaction regression tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from collie_core import settings as collie_settings
from collie_core.db import CollieDB
from collie_core.runtime import CollieRuntime


def _candidate(
    name: str = "new-provider",
    *,
    key: str = "new-secret",
    model: str = "new-model",
    api_base: str | None = None,
) -> dict[str, Any]:
    return {
        "provider_id": f"api-{name}",
        "name": name,
        "auth_type": "api-key",
        "model": model,
        "runtime_name": name,
        "protocol": "openai",
        "api_base": api_base,
        "secret_name": name,
        "api_key": key,
    }


@pytest.fixture()
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COLLIE_HOME", str(tmp_path / "home"))
    instance = CollieRuntime(port=0, db=CollieDB(tmp_path / "collie.db"))
    yield instance
    for name in ("old-provider", "new-provider", "first", "second", "custom"):
        collie_settings.delete_api_key(name)
    instance.db.close()


def _seed_working_provider(runtime: CollieRuntime) -> dict[str, Any]:
    provider = runtime.db.configure_provider_candidate_record(
        "api-old-provider",
        name="old-provider",
        auth_type="api-key",
        model="old-model",
        runtime_name="old-provider",
        protocol="openai",
        api_base=None,
        secret_name="old-provider",
    )
    collie_settings.set_api_key("old-provider", "old-secret")
    runtime.loop = object()
    return provider


@pytest.mark.asyncio
async def test_invalid_model_is_rejected_before_mutation(runtime: CollieRuntime) -> None:
    old = _seed_working_provider(runtime)
    before = runtime.db.all_settings()

    result = await runtime._configure_provider_candidate(_candidate(model="model with whitespace"))

    assert result["configured"] is False
    assert "model ID" in result["error"]
    assert runtime.db.default_provider()["id"] == old["id"]
    assert runtime.db.all_settings() == before
    assert collie_settings.get_api_key("new-provider") is None


@pytest.mark.asyncio
async def test_unreachable_custom_endpoint_rolls_back_every_layer(
    runtime: CollieRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = _seed_working_provider(runtime)
    calls: list[str | None] = []

    async def configure_locked(*, probe_api_base: str | None = None) -> dict[str, Any]:
        calls.append(probe_api_base)
        if probe_api_base:
            return {"configured": False, "error": "endpoint unreachable"}
        return {"configured": True, "model": "old-model"}

    monkeypatch.setattr(runtime, "_configure_locked", configure_locked)
    result = await runtime._configure_provider_candidate(
        _candidate("custom", api_base="http://127.0.0.1:9/v1")
    )

    assert result == {
        "configured": False,
        "error": "endpoint unreachable",
        "rolled_back": True,
        "rollback_error": None,
    }
    assert calls == ["http://127.0.0.1:9/v1", None]
    assert runtime.db.default_provider()["id"] == old["id"]
    assert runtime.db.get_provider("api-custom") is None
    assert collie_settings.get_api_key("custom") is None


@pytest.mark.asyncio
async def test_endpoint_probe_checks_real_tcp_reachability(runtime: CollieRuntime) -> None:
    server = await asyncio.start_server(lambda _reader, writer: writer.close(), "127.0.0.1", 0)
    port = int(server.sockets[0].getsockname()[1])
    await runtime._probe_provider_endpoint(f"http://127.0.0.1:{port}/v1")
    server.close()
    await server.wait_closed()

    with pytest.raises(ValueError, match="couldn't reach"):
        await runtime._probe_provider_endpoint(f"http://127.0.0.1:{port}/v1")


@pytest.mark.asyncio
async def test_replacing_working_provider_restores_row_settings_and_key(
    runtime: CollieRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_working_provider(runtime)
    original = runtime.db.get_provider("api-old-provider")
    original_settings = runtime.db.all_settings()
    calls = 0

    async def configure_locked(*, probe_api_base: str | None = None) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"configured": False, "error": "replacement rejected"}
        return {"configured": True, "model": "old-model"}

    monkeypatch.setattr(runtime, "_configure_locked", configure_locked)
    replacement = _candidate("old-provider", key="replacement-secret", model="bad-model")
    replacement["provider_id"] = "api-old-provider"
    result = await runtime._configure_provider_candidate(replacement)

    assert result["configured"] is False
    assert result["rolled_back"] is True
    assert runtime.db.get_provider("api-old-provider") == original
    assert runtime.db.all_settings() == original_settings
    assert collie_settings.get_api_key("old-provider") == "old-secret"
    assert calls == 2


@pytest.mark.asyncio
async def test_failed_rollback_is_reported_honestly(
    runtime: CollieRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_working_provider(runtime)

    async def configure_locked(*, probe_api_base: str | None = None) -> dict[str, Any]:
        return {"configured": False, "error": "candidate failed"}

    def fail_restore(_snapshot: dict[str, Any]) -> None:
        raise OSError("disk stayed locked")

    monkeypatch.setattr(runtime, "_configure_locked", configure_locked)
    monkeypatch.setattr(runtime.db, "restore_provider_configuration", fail_restore)
    result = await runtime._configure_provider_candidate(_candidate())

    assert result["configured"] is False
    assert result["rolled_back"] is False
    assert "database restore failed" in result["rollback_error"]
    assert "previous runtime rebuild failed" in result["rollback_error"]
    assert collie_settings.get_api_key("new-provider") is None


@pytest.mark.asyncio
async def test_successful_retry_after_failed_candidate(
    runtime: CollieRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_working_provider(runtime)
    candidate_attempts = 0

    async def configure_locked(*, probe_api_base: str | None = None) -> dict[str, Any]:
        nonlocal candidate_attempts
        if runtime.db.get_setting("provider.name") == "new-provider":
            candidate_attempts += 1
            if candidate_attempts == 1:
                return {"configured": False, "error": "temporary failure"}
            return {"configured": True, "model": "new-model"}
        return {"configured": True, "model": "old-model"}

    monkeypatch.setattr(runtime, "_configure_locked", configure_locked)
    first = await runtime._configure_provider_candidate(_candidate())
    second = await runtime._configure_provider_candidate(_candidate())

    assert first["configured"] is False and first["rolled_back"] is True
    assert second["configured"] is True
    assert collie_settings.get_api_key("new-provider") == "new-secret"
    assert runtime.db.default_provider()["id"] == "api-new-provider"
    assert (await runtime._finalize_provider_candidate(second["transaction_id"]))["finalized"]


@pytest.mark.asyncio
async def test_post_success_compensation_restores_previous_runtime_and_key(
    runtime: CollieRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = _seed_working_provider(runtime)

    async def configure_locked(*, probe_api_base: str | None = None) -> dict[str, Any]:
        return {
            "configured": True,
            "model": runtime.db.get_setting("provider.model"),
        }

    monkeypatch.setattr(runtime, "_configure_locked", configure_locked)
    configured = await runtime._configure_provider_candidate(_candidate())
    rolled_back = await runtime._rollback_provider_candidate(configured["transaction_id"])

    assert rolled_back == {"rolled_back": True, "rollback_error": None}
    assert runtime.db.default_provider()["id"] == old["id"]
    assert runtime.db.get_provider("api-new-provider") is None
    assert collie_settings.get_api_key("new-provider") is None


@pytest.mark.asyncio
async def test_concurrent_candidates_are_serialized(
    runtime: CollieRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    maximum_active = 0

    async def configure_locked(*, probe_api_base: str | None = None) -> dict[str, Any]:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return {"configured": True, "model": runtime.db.get_setting("provider.model")}

    monkeypatch.setattr(runtime, "_configure_locked", configure_locked)
    first, second = await asyncio.gather(
        runtime._configure_provider_candidate(_candidate("first", model="model-one")),
        runtime._configure_provider_candidate(_candidate("second", model="model-two")),
    )

    assert first["configured"] is True
    assert second["configured"] is True
    assert maximum_active == 1
    assert runtime.db.default_provider()["id"] == "api-second"
    await runtime._finalize_provider_candidate(first["transaction_id"])
    await runtime._finalize_provider_candidate(second["transaction_id"])


@pytest.mark.asyncio
async def test_probe_auth_failure_rolls_back_with_warm_error_copy(
    runtime: CollieRuntime,
) -> None:
    """P2 wiring seam: a 401 from the connect probe must roll back and show
    the warm "That key didn't work" copy, not a network-style error."""
    from aiohttp import web

    # Seed a working provider WITH an api_base: the real rebuild path
    # validates it (nanobot provider factory), unlike the mocked-path tests
    # that use _seed_working_provider()'s api_base=None.
    runtime.db.configure_provider_candidate_record(
        "api-old-provider",
        name="old-provider",
        auth_type="api-key",
        model="old-model",
        runtime_name="old-provider",
        protocol="openai",
        api_base="http://127.0.0.1:9/v1",
        secret_name="old-provider",
    )
    collie_settings.set_api_key("old-provider", "old-secret")
    runtime.loop = object()
    old = runtime.db.get_provider("api-old-provider")

    async def models(request: web.Request) -> web.Response:
        return web.json_response({"error": "unauthorized"}, status=401)

    async def chat(request: web.Request) -> web.Response:
        return web.json_response({"error": "unauthorized"}, status=401)

    app = web.Application()
    app.router.add_get("/v1/models", models)
    app.router.add_post("/v1/chat/completions", chat)
    runner = web.AppRunner(app)
    await runner.setup()

    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    api_base = f"http://127.0.0.1:{port}/v1"

    try:
        result = await runtime._configure_provider_candidate(
            _candidate("probe-fail", key="sk-wrong", model="deepseek-v4-flash", api_base=api_base)
        )
    finally:
        await runner.cleanup()

    assert result["configured"] is False
    assert result["validated"] is False
    assert result["error_kind"] == "auth"
    assert "That key didn't work" in result["error"]
    assert "get-started" in result["error"]
    assert result["rolled_back"] is True
    assert runtime.db.default_provider()["id"] == old["id"]
    assert runtime.db.get_provider("api-probe-fail") is None
    assert collie_settings.get_api_key("probe-fail") is None
