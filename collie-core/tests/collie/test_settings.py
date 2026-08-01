"""Tests for building the engine Config from SQLite settings."""

from pathlib import Path

import pytest

from collie_core import settings as collie_settings
from collie_core.db import CollieDB


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CollieDB:
    monkeypatch.setenv("COLLIE_HOME", str(tmp_path / ".collie"))
    d = CollieDB(tmp_path / ".collie" / "collie.db")
    yield d
    d.close()
    collie_settings._runtime_api_keys.clear()


def test_ensure_workspace_creates_defaults(db: CollieDB) -> None:
    ws = collie_settings.ensure_workspace()
    assert (ws / "VISION.md").exists()
    assert (ws / "AGENTS.md").exists()
    assert (ws / "subagents").is_dir()
    vision_text = (ws / "VISION.md").read_text(encoding="utf-8")
    assert "Collie" in vision_text
    # Never overwrite user edits
    (ws / "VISION.md").write_text("my custom personality", encoding="utf-8")
    collie_settings.ensure_workspace()
    assert (ws / "VISION.md").read_text(encoding="utf-8") == "my custom personality"


def test_build_config_defaults(db: CollieDB) -> None:
    config = collie_settings.build_config(db)
    assert config.agents.defaults.provider == "openai"
    assert config.agents.defaults.bot_name == "Collie"
    assert str(collie_settings.workspace_path()) in config.agents.defaults.workspace


def test_build_config_from_settings(db: CollieDB) -> None:
    db.set_setting("provider.name", "openrouter")
    db.set_setting("provider.model", "anthropic/claude-sonnet-5")
    db.set_setting("agent.timezone", "Europe/Berlin")
    collie_settings.set_api_key("openrouter", "sk-test-123")

    config = collie_settings.build_config(db)
    assert config.agents.defaults.provider == "openrouter"
    assert config.agents.defaults.model == "anthropic/claude-sonnet-5"
    assert config.agents.defaults.timezone == "Europe/Berlin"
    assert config.providers.openrouter.api_key == "sk-test-123"


def test_build_config_custom_endpoint(db: CollieDB) -> None:
    db.set_setting("provider.name", "custom")
    db.set_setting("provider.api_base", "http://localhost:11434/v1")
    collie_settings.set_api_key("custom", "ollama")

    config = collie_settings.build_config(db)
    assert config.providers.custom.api_base == "http://localhost:11434/v1"
    assert config.providers.custom.api_key == "ollama"


def test_build_config_defaults_model_per_provider(db: CollieDB) -> None:
    db.set_setting("provider.name", "deepseek")
    collie_settings.set_api_key("deepseek", "sk-ds")
    config = collie_settings.build_config(db)
    assert config.agents.defaults.model == "deepseek-v4-pro"

    db.set_setting("provider.name", "anthropic")
    config = collie_settings.build_config(db)
    assert config.agents.defaults.model == "claude-sonnet-4-6"

    # Explicit user choice always wins over the default map
    db.set_setting("provider.model", "deepseek-v4-flash")
    db.set_setting("provider.name", "deepseek")
    config = collie_settings.build_config(db)
    assert config.agents.defaults.model == "deepseek-v4-flash"

    # Clearing the choice (null) falls back to the map again
    db.set_setting("provider.model", None)
    config = collie_settings.build_config(db)
    assert config.agents.defaults.model == "deepseek-v4-pro"


def test_api_key_from_env(db: CollieDB, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLLIE_OPENAI_API_KEY", "sk-env-key")
    config = collie_settings.build_config(db)
    assert config.providers.openai.api_key == "sk-env-key"
