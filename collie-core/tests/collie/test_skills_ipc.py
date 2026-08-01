from pathlib import Path

import pytest

from collie_core.db import CollieDB
from collie_core.ipc.server import CollieIPCServer


@pytest.mark.asyncio
async def test_skills_payload_is_safe_and_reports_availability(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    skill_dir = workspace / "skills" / "weekly-planner"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: weekly-planner\ndescription: Plans a realistic week.\n---\nDo the work.",
        encoding="utf-8",
    )
    server = CollieIPCServer(CollieDB(tmp_path / "collie.db"), skills_workspace=workspace)

    payload = await server._cmd_list_skills(None, {})

    custom = next(skill for skill in payload["skills"] if skill["name"] == "weekly-planner")
    assert custom["description"] == "Plans a realistic week."
    assert custom["source"] == "workspace"
    assert "path" not in custom


@pytest.mark.asyncio
async def test_skill_detail_omits_raw_instructions(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    skill_dir = workspace / "skills" / "weekly-planner"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: weekly-planner\ndescription: Plans a realistic week.\n---\nSecret instructions.",
        encoding="utf-8",
    )
    server = CollieIPCServer(CollieDB(tmp_path / "collie.db"), skills_workspace=workspace)

    payload = await server._cmd_get_skill(None, {"name": "weekly-planner"})

    assert payload["skill"]["name"] == "weekly-planner"
    assert "raw_markdown" not in payload["skill"]
    assert "requirements" in payload["skill"]
