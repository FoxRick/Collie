from unittest.mock import patch

import pytest

from collie_core.db import CollieDB
from collie_core.ipc.server import CollieIPCServer
from collie_core.providers.managed import MODEL, managed_provider, managed_transport


def test_transport_requires_desktop_credentials(monkeypatch):
    monkeypatch.delenv("COLLIE_KEYCHAIN_TOKEN", raising=False)
    monkeypatch.setenv("COLLIE_KEYCHAIN_PORT", "999999")
    with pytest.raises(ValueError, match="desktop app"):
        managed_transport()


async def test_fixed_model_output_cap_and_no_retries(monkeypatch):
    monkeypatch.setenv("COLLIE_KEYCHAIN_PORT", "12345")
    monkeypatch.setenv("COLLIE_KEYCHAIN_TOKEN", "test-bridge-token")
    provider = managed_provider()
    kwargs = provider._build_kwargs(
        [{"role": "user", "content": "Hello"}],
        None,
        "expensive-model",
        4096,
        0.7,
        "high",
        None,
    )
    assert kwargs["model"] == MODEL
    assert kwargs.get("max_tokens", kwargs.get("max_completion_tokens")) == 1024
    assert not provider._should_use_responses_api("gpt-5", "high")
    assert provider._handle_error(TimeoutError()).error_should_retry is False
    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI") as client:
        await provider._ensure_client()
    assert client.call_args.kwargs["max_retries"] == 0
    assert client.call_args.kwargs["api_key"] == "test-bridge-token"
    assert client.call_args.kwargs["base_url"] == "http://127.0.0.1:12345/inference/v1"


@pytest.mark.parametrize("success", [True, False])
async def test_activation_preserves_personal_provider_and_rolls_back(
    tmp_path, monkeypatch, success
):
    monkeypatch.setenv("COLLIE_KEYCHAIN_PORT", "12345")
    monkeypatch.setenv("COLLIE_KEYCHAIN_TOKEN", "test-bridge-token")
    db = CollieDB(tmp_path / "collie.db")
    db.upsert_provider(
        "personal", name="openai", auth_type="api-key", model="personal-model", is_default=True
    )

    async def configure():
        return {"configured": success}

    server = CollieIPCServer(db, on_configure=configure)
    try:
        result = await server._cmd_activate_managed_provider(None, {})
        assert result["configured"] is success
        assert db.get_provider("personal")["model"] == "personal-model"
        assert db.default_provider()["id"] == ("collie-managed" if success else "personal")
        if success:
            assert db.get_setting("provider.auth") == "collie-managed"
            # Re-activation cannot unset the existing default while upserting.
            await server._cmd_activate_managed_provider(None, {})
            assert db.default_provider()["id"] == "collie-managed"
        else:
            assert db.get_provider("collie-managed") is None
    finally:
        db.close()


async def test_failed_first_activation_leaves_no_managed_default(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLIE_KEYCHAIN_PORT", "12345")
    monkeypatch.setenv("COLLIE_KEYCHAIN_TOKEN", "test-bridge-token")
    db = CollieDB(tmp_path / "collie.db")

    async def configure():
        return {"configured": False}

    server = CollieIPCServer(db, on_configure=configure)
    try:
        await server._cmd_activate_managed_provider(None, {})
        assert db.default_provider() is None
        assert not db.get_setting("provider.auth")
    finally:
        db.close()
