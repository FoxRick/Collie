"""One focused acceptance scenario for the shared skill lifecycle boundary."""

from pathlib import Path

import pytest

from collie_core.db import CollieDB
from collie_core.settings import build_config
from collie_core.skills import SkillManager
from collie_core.tools.capabilities import LoadSkillTool, create_workspace_skill
from collie_core.tools.skill_management import ManageSkillTool, bind_skill_manager
from nanobot.agent.context import ContextBuilder


@pytest.mark.asyncio
async def test_saved_skill_lifecycle_and_protection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("COLLIE_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    builtin = tmp_path / "bundled"
    original = builtin / "weekly-review"
    original.mkdir(parents=True)
    bundled_text = "---\nname: weekly-review\ndescription: Review a week\nalways: true\n---\nOriginal instructions.\n"
    (original / "SKILL.md").write_text(bundled_text, encoding="utf-8")
    (original / "reference.txt").write_bytes(b"supporting asset\r\n")
    original_bytes = (original / "SKILL.md").read_bytes()
    db = CollieDB(tmp_path / "collie.db")
    manager = SkillManager(workspace, db, builtin=builtin)
    bind_skill_manager(manager)
    create_workspace_skill(
        workspace,
        name="daily-review",
        description="Review a day",
        instructions="First saved instructions.",
    )
    assert "daily-review" in manager.loader().build_skills_summary()
    initial = manager.detail("daily-review")
    edited = initial["content"].replace("First saved instructions.", "Second instructions.")
    manager.change(action="edit", name="daily-review", revision=initial["revision"], content=edited)
    assert "Second instructions." in await LoadSkillTool(workspace).execute(name="daily-review")
    with pytest.raises(ValueError, match="changed since"):
        manager.change(
            action="version", name="daily-review", revision=initial["revision"], version=1
        )
    current = manager.detail("daily-review")
    manager.change(action="version", name="daily-review", revision=current["revision"], version=1)
    assert manager.detail("daily-review")["content"] == initial["content"]

    # A main run and child run keep the same active skill set until both finish,
    # including cancellation's on_finally path. No provider or live account used.
    main, child = manager.turn_hook(None), manager.turn_hook(None)
    builtin_detail = manager.detail("weekly-review")
    result = manager.change(
        action="disable", name="weekly-review", revision=builtin_detail["revision"]
    )
    assert result["pending"]
    assert "weekly-review" in manager.loader().get_always_skills()
    await main.on_finally(None)
    assert "weekly-review" in manager.loader().build_skills_summary()
    await child.on_finally(None)
    assert "weekly-review" not in manager.loader().build_skills_summary()
    assert manager.loader().load_skill("weekly-review") is None
    assert "weekly-review" not in manager.loader().get_always_skills()
    disabled_result = await LoadSkillTool(workspace).execute(name="weekly-review")
    assert "was not found" in disabled_result
    config = build_config(db)
    context = ContextBuilder(workspace, disabled_skills=config.agents.defaults.disabled_skills)
    assert "weekly-review" in config.agents.defaults.disabled_skills
    assert "weekly-review" not in context.skills.build_skills_summary()
    request = ManageSkillTool(manager).permission_request(
        {"name": "weekly-review", "action": "enable"}
    )
    assert request.hard_approval and request.reversible
    manager.change(action="enable", name="weekly-review", revision=builtin_detail["revision"])
    manager.change(
        action="edit",
        name="weekly-review",
        revision=builtin_detail["revision"],
        content=bundled_text.replace("Original instructions.", "Custom instructions."),
    )
    assert (
        workspace / "skills/weekly-review/reference.txt"
    ).read_bytes() == b"supporting asset\r\n"
    assert (original / "SKILL.md").read_bytes() == original_bytes
    override = manager.detail("weekly-review")
    manager.change(action="reset", name="weekly-review", revision=override["revision"])
    assert manager.detail("weekly-review")["source"] == "builtin"
    with pytest.raises(ValueError, match="Built-in skills"):
        manager.change(action="delete", name="weekly-review", revision=builtin_detail["revision"])

    asset = workspace / "skills/daily-review/notes.txt"
    asset.write_bytes(b"keep my notes")
    manager.change(action="delete", name="daily-review", revision=initial["revision"])
    assert manager.loader().load_skill("daily-review") is None
    assert "daily-review" in manager.inventory()["deleted"]
    create_workspace_skill(
        workspace,
        name="daily-review",
        description="New owner version",
        instructions="Do not overwrite me.",
    )
    with pytest.raises(ValueError, match="newer workspace"):
        manager.change(action="restore", name="daily-review")
    newer = manager.detail("daily-review")
    assert "Do not overwrite me." in newer["content"]
    # Remove only the test-created newer owner copy, then recover the full archive.
    (workspace / "skills/daily-review/SKILL.md").unlink()
    (workspace / "skills/daily-review").rmdir()
    manager.change(action="restore", name="daily-review")
    assert asset.read_bytes() == b"keep my notes"
    assert manager.detail("daily-review")["content"] == initial["content"]

    # A pending edit must not replace an owner edit made before it can activate.
    active = manager.turn_hook(None)
    manager.change(action="edit", name="daily-review", revision=initial["revision"], content=edited)
    skill_file = workspace / "skills/daily-review/SKILL.md"
    owner_text = initial["content"].replace("First saved instructions.", "New owner edit.")
    skill_file.write_text(owner_text, encoding="utf-8")
    await active.on_finally(None)
    assert skill_file.read_text(encoding="utf-8") == owner_text
    assert manager.inventory()["problems"]
    assert (original / "SKILL.md").read_bytes() == original_bytes
    db.close()
