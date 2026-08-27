"""Tests for connect-time key validation probes (read-only, never log keys)."""

from __future__ import annotations

import json

import pytest
from aiohttp import web

from collie_core.catalog import CatalogueStore
from collie_core.providers.validation import (
    detect_local_ollama,
    detect_models_for_base_url,
    detect_provider_for_key,
    probe_api_key,
)


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _fake_openai_app(*, models_status: int = 200, chat_status: int = 200) -> web.Application:
    async def models(request: web.Request) -> web.Response:
        auth = request.headers.get("Authorization", "")
        if models_status in (401, 403):
            return web.json_response({"error": "bad key"}, status=models_status)
        if models_status != 200:
            return web.json_response({"error": "nope"}, status=models_status)
        if auth != "Bearer sk-good":
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response(
            {
                "data": [
                    {"id": "deepseek-v4-flash", "object": "model"},
                    {"id": "deepseek-reasoner", "object": "model"},
                ]
            }
        )

    async def chat(request: web.Request) -> web.Response:
        if chat_status != 200:
            return web.json_response({"error": "nope"}, status=chat_status)
        body = json.loads(await request.text())
        return web.json_response(
            {
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "model": body.get("model"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "pong"},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    app = web.Application()
    app.router.add_get("/v1/models", models)
    app.router.add_post("/v1/chat/completions", chat)
    return app


@pytest.fixture()
async def openai_server():
    runner = web.AppRunner(_fake_openai_app())
    await runner.setup()
    port = _free_port()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    yield f"http://127.0.0.1:{port}/v1"
    await runner.cleanup()


@pytest.mark.asyncio
async def test_probe_api_key_success_via_models_endpoint(openai_server: str) -> None:
    result = await probe_api_key(
        provider_id="deepseek",
        api_key="sk-good",
        api_base=openai_server,
        protocol="openai",
        model="deepseek-v4-flash",
        catalogue=CatalogueStore(),
    )
    assert result["ok"] is True
    assert result["model"] == "deepseek-v4-flash"
    assert result["model_label"] == "DeepSeek V4 Flash"
    assert "deepseek-reasoner" in result["models"]


@pytest.mark.asyncio
async def test_probe_api_key_accepts_requested_model_in_list(openai_server: str) -> None:
    result = await probe_api_key(
        provider_id="deepseek",
        api_key="sk-good",
        api_base=openai_server,
        protocol="openai",
        model="deepseek-v4-flash",
        catalogue=CatalogueStore(),
    )
    assert result["ok"] is True
    assert result["model"] == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_probe_api_key_rejects_explicit_unknown_model(openai_server: str) -> None:
    # The model list advertises deepseek-v4-flash / deepseek-reasoner. An
    # explicitly chosen model the provider doesn't advertise must be a
    # definitive "model not found" — never silently re-mapped, and never ok.
    result = await probe_api_key(
        provider_id="deepseek",
        api_key="sk-good",
        api_base=openai_server,
        protocol="openai",
        model="deepseek-ghost",
        explicit_model=True,
        catalogue=CatalogueStore(),
    )
    assert result["ok"] is False
    assert result["error"] == "model"
    assert result["models"] == ["deepseek-v4-flash", "deepseek-reasoner"]


@pytest.mark.asyncio
async def test_probe_api_key_keeps_curated_default_when_not_explicit(openai_server: str) -> None:
    # Collie's curated default may not be advertised for some endpoints; when no
    # model was explicitly typed we keep it (lenient) instead of rejecting it.
    result = await probe_api_key(
        provider_id="deepseek",
        api_key="sk-good",
        api_base=openai_server,
        protocol="openai",
        model="deepseek-ghost",
        explicit_model=False,
        catalogue=CatalogueStore(),
    )
    assert result["ok"] is True
    assert result["model"] == "deepseek-ghost"


@pytest.mark.asyncio
async def test_probe_api_key_rejects_bad_key(openai_server: str) -> None:
    result = await probe_api_key(
        provider_id="deepseek",
        api_key="sk-wrong",
        api_base=openai_server,
        protocol="openai",
        model="deepseek-v4-flash",
    )
    assert result["ok"] is False
    assert result["error"] == "auth"


@pytest.mark.asyncio
async def test_probe_api_key_falls_back_to_one_token_completion() -> None:
    app = web.Application()

    async def models(request: web.Request) -> web.Response:
        return web.json_response({"error": "not found"}, status=404)

    async def chat(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "pong"},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    app.router.add_get("/v1/models", models)
    app.router.add_post("/v1/chat/completions", chat)
    runner = web.AppRunner(app)
    await runner.setup()
    port = _free_port()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    try:
        result = await probe_api_key(
            provider_id="deepseek",
            api_key="sk-good",
            api_base=f"http://127.0.0.1:{port}/v1",
            protocol="openai",
            model="deepseek-v4-flash",
            catalogue=CatalogueStore(),
        )
    finally:
        await runner.cleanup()
    assert result["ok"] is True
    assert result["model"] == "deepseek-v4-flash"
    assert result["model_label"] == "DeepSeek V4 Flash"


@pytest.mark.asyncio
async def test_probe_api_key_network_error_is_not_auth() -> None:
    result = await probe_api_key(
        provider_id="deepseek",
        api_key="sk-good",
        api_base="http://127.0.0.1:9/v1",  # nothing listens here
        protocol="openai",
        model="deepseek-v4-flash",
    )
    assert result["ok"] is False
    assert result["error"] == "network"


@pytest.mark.asyncio
async def test_detect_provider_for_key_uses_prefix_for_unambiguous(openai_server: str) -> None:
    # gsk_ is unambiguous → instant, no probing needed.
    result = await detect_provider_for_key("gsk_abc", catalogue=CatalogueStore())
    assert result == {"detected": True, "provider_id": "groq", "reason": "prefix"}


@pytest.mark.asyncio
async def test_detect_provider_for_key_ambiguous_sk_never_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collie_core.providers import validation

    async def fake_probe(**kwargs: object) -> dict[str, object]:  # pragma: no cover
        raise AssertionError("ambiguous sk- keys must never be probed")

    monkeypatch.setattr(validation, "probe_api_key", fake_probe)
    result = await detect_provider_for_key("sk-abc", catalogue=CatalogueStore())
    # The key must not be shipped to candidate providers to guess which one
    # owns it — the candidates come back and the UI asks the user to pick.
    assert result == {
        "detected": False,
        "provider_id": None,
        "reason": "ambiguous",
        "candidates": ["openai", "deepseek"],
    }


@pytest.mark.asyncio
async def test_detect_provider_for_key_nothing_matches() -> None:
    result = await detect_provider_for_key("no-match-xyz", catalogue=CatalogueStore())
    assert result["detected"] is False
    assert result["reason"] == "no_prefix_match"


@pytest.mark.asyncio
async def test_detect_models_for_custom_base_url(openai_server: str) -> None:
    result = await detect_models_for_base_url(openai_server, protocol="openai", api_key="sk-good")
    assert result["detected"] is True
    assert result["models"] == ["deepseek-v4-flash", "deepseek-reasoner"]


@pytest.mark.asyncio
async def test_detect_models_handles_missing_base_url() -> None:
    result = await detect_models_for_base_url("", protocol="openai")
    assert result["detected"] is False
    assert result["error"] == "missing_base_url"


@pytest.mark.asyncio
async def test_detect_local_ollama_empty_when_not_running() -> None:
    # Nothing listens on an unlikely port; OLLAMA_HOST forces the probe there.
    import os

    os.environ["OLLAMA_HOST"] = "http://127.0.0.1:9"
    try:
        result = await detect_local_ollama()
    finally:
        os.environ.pop("OLLAMA_HOST", None)
    assert result == {"available": False, "models": []}


@pytest.mark.asyncio
async def test_detect_provider_for_key_unambiguous_prefix_resolves_instantly() -> None:
    result = await detect_provider_for_key("gsk_abc123", catalogue=CatalogueStore())
    assert result == {"detected": True, "provider_id": "groq", "reason": "prefix"}


@pytest.mark.asyncio
async def test_detect_provider_for_key_no_match() -> None:
    result = await detect_provider_for_key("zzz-unknown-format", catalogue=CatalogueStore())
    assert result["detected"] is False
    assert result["reason"] == "no_prefix_match"
