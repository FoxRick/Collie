"""Tests for the bundled provider catalogue (models.dev snapshot + curated layer)."""

from __future__ import annotations

from pathlib import Path

import pytest

from collie_core.catalog import BUNDLED_SNAPSHOT_PATH, CatalogueStore
from collie_core.catalog.snapshot_util import (
    trim_live_catalogue,
    validate_catalogue_schema,
)


@pytest.fixture()
def store(tmp_path: Path) -> CatalogueStore:
    return CatalogueStore(settings=None, snapshot_path=tmp_path / "missing.json")


def test_bundled_snapshot_exists_and_is_valid() -> None:
    assert BUNDLED_SNAPSHOT_PATH.is_file(), "run tools/update_catalogue_snapshot.py"
    store = CatalogueStore(settings=None)
    providers = store.providers()
    assert len(providers) >= 10
    ids = [p["id"] for p in providers]
    assert "deepseek" in ids and "openai" in ids and "anthropic" in ids


def test_bundled_snapshot_is_trimmed() -> None:
    size_kb = BUNDLED_SNAPSHOT_PATH.stat().st_size / 1024
    assert size_kb < 500, f"bundled snapshot too large: {size_kb:.0f} KB"


def test_curated_defaults_and_labels() -> None:
    store = CatalogueStore(settings=None)
    deepseek = store.get_provider("deepseek")
    assert deepseek is not None
    assert deepseek["name"] == "DeepSeek"
    assert deepseek["protocol"] == "openai"
    assert deepseek["api_base"] == "https://api.deepseek.com"
    assert deepseek["default_model"] == "deepseek-v4-flash"
    assert deepseek["tested"] is True
    assert store.model_label("deepseek", "deepseek-v4-flash") == "DeepSeek V4 Flash"
    # A model id missing from the snapshot falls back to the raw id.
    assert store.model_label("deepseek", "unknown-model") == "unknown-model"


def test_prefix_detection_longest_match_wins() -> None:
    store = CatalogueStore(settings=None)
    assert store.detect_provider_for_key("gsk_abc123") == ["groq"]
    assert store.detect_provider_for_key("sk-ant-abc") == ["anthropic"]
    assert store.detect_provider_for_key("sk-or-v1-abc") == ["openrouter"]
    assert store.detect_provider_for_key("AIzaSyB-xyz") == ["google"]
    assert store.detect_provider_for_key("xai-abc") == ["xai"]
    assert store.detect_provider_for_key("pplx-abc") == ["perplexity"]
    # sk- is shared by OpenAI and DeepSeek → ambiguous, both candidates kept.
    assert set(store.detect_provider_for_key("sk-abc")) == {"openai", "deepseek"}
    assert store.detect_provider_for_key("") == []
    assert store.detect_provider_for_key("totally-unknown") == []


def test_trim_live_catalogue_builds_curated_snapshot() -> None:
    raw = {
        "deepseek": {
            "name": "DeepSeek",
            "api": "https://api.deepseek.com",
            "doc": "https://api-docs.deepseek.com",
            "models": {
                "deepseek-v4-flash": {"name": "DeepSeek V4 Flash"},
                "deepseek-reasoner": {"name": "DeepSeek Reasoner"},
            },
        },
        "some-unknown-provider": {
            "name": "Other",
            "models": {"x": {"name": "X"}},
        },
    }
    trimmed = trim_live_catalogue(raw, generated_at="2026-08-05T00:00:00Z")
    assert trimmed is not None
    assert validate_catalogue_schema(trimmed)
    ids = [p["id"] for p in trimmed["providers"]]
    # Unknown providers are dropped; curated overrides win over the feed.
    assert ids == ["deepseek"]
    assert trimmed["providers"][0]["api_base"] == "https://api.deepseek.com"
    assert trimmed["providers"][0]["default_model"] == "deepseek-v4-flash"
    assert trimmed["source"]["providers_count"] == 2
    assert trimmed["source"]["sha256"]


def test_trim_rejects_garbage() -> None:
    assert trim_live_catalogue({}, "2026-08-05T00:00:00Z") is None
    assert trim_live_catalogue([1, 2, 3], "2026-08-05T00:00:00Z") is None  # type: ignore[arg-type]
    assert trim_live_catalogue({"providers": "nope"}, "2026-08-05T00:00:00Z") is None


def test_validate_catalogue_schema_rejects_bad_shapes() -> None:
    good = trim_live_catalogue(
        {
            "deepseek": {
                "name": "DeepSeek",
                "models": {"m": {"name": "M"}},
            }
        },
        "2026-08-05T00:00:00Z",
    )
    assert good is not None and validate_catalogue_schema(good)
    assert not validate_catalogue_schema({"schema_version": 999, "providers": [], "source": {}})
    assert not validate_catalogue_schema({"schema_version": 1, "providers": "nope", "source": {}})
    assert not validate_catalogue_schema(
        {"schema_version": 1, "providers": [{"id": ""}], "source": {}}
    )
    assert not validate_catalogue_schema(None)
    assert not validate_catalogue_schema("html error page")


class _FakeSettings:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get_setting(self, key: str, default: object = None) -> object:
        return self.data.get(key, default)

    def set_setting(self, key: str, value: object) -> None:
        self.data[key] = str(value)

    def delete_setting(self, key: str) -> None:
        self.data.pop(key, None)


@pytest.mark.asyncio
async def test_refresh_persists_overlay_and_rollback_restores_bundled(tmp_path: Path) -> None:
    settings = _FakeSettings()
    store = CatalogueStore(settings=settings, snapshot_path=tmp_path / "missing.json")
    # No bundled file → empty; after a successful refresh the overlay serves.
    assert store.providers() == []

    from collie_core.catalog import refresh as refresh_module

    raw = {
        "deepseek": {
            "name": "DeepSeek",
            "models": {"deepseek-v4-flash": {"name": "DeepSeek V4 Flash"}},
        },
        "openai": {"name": "OpenAI", "models": {"gpt-5.5": {"name": "GPT-5.5"}}},
    }
    calls: list[str] = []

    async def _fake_fetch(url: str) -> bytes:
        calls.append(url)
        if len(calls) == 1:
            raise TimeoutError("offline")
        import json

        return json.dumps(raw).encode("utf-8")

    original = refresh_module._fetch_async
    refresh_module._fetch_async = _fake_fetch  # type: ignore[assignment]
    try:
        failed = await store.refresh(url="https://models.dev/api.json")
        assert failed["refreshed"] is False  # network failure keeps previous state
        assert store.providers() == []

        refreshed = await store.refresh(url="https://models.dev/api.json")
    finally:
        refresh_module._fetch_async = original  # type: ignore[assignment]
    assert refreshed["refreshed"] is True
    assert store.providers() != []
    assert store.refresh_state()["refreshed_at"]

    rollback = store.rollback()
    assert rollback["rolled_back"] is True
    assert store.providers() == []
