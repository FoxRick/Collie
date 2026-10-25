"""Collie-owned skill lifecycle, recovery, and turn-boundary activation."""

from __future__ import annotations

import hashlib
import re
import shutil
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from collie_core.versions import VersionStore
from nanobot.agent.hook import AgentHook
from nanobot.agent.skills import BUILTIN_SKILLS_DIR, SkillsLoader


class SkillManager:
    """One authority for UI and approved tools; bundled bytes are read-only.

    Changes requested during runs are reviewed now and applied after all tracked
    runs finish. Pending changes survive a restart and re-check their revision.
    """

    def __init__(self, workspace: Path, db: Any, *, builtin: Path | None = None):
        self.workspace, self.db = workspace, db
        self.builtin = builtin or BUILTIN_SKILLS_DIR
        self.versions = VersionStore(db)
        self._lock = threading.RLock()
        self._runs = 0
        self.on_activate: Callable[[set[str]], None] | None = None
        self.flush()

    def loader(self, *, include_disabled: bool = False) -> SkillsLoader:
        return SkillsLoader(
            self.workspace,
            self.builtin,
            disabled_skills=(
                set() if include_disabled else set(self.db.get_setting("agent.disabled_skills", []))
            ),
        )

    @staticmethod
    def _name(name: str) -> str:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64:
            raise ValueError("Pick a skill by its exact lowercase name.")
        return name

    def _directory(self, name: str, *, bundled: bool = False) -> Path:
        root = self.builtin if bundled else self.workspace / "skills"
        target = root / self._name(name)
        # Reject junctions/symlinks, including parents and supporting assets.
        if not bundled and root.resolve() != self.workspace.resolve() / "skills":
            raise ValueError("Linked skill roots cannot be changed here.")
        if target.resolve() != root.resolve() / name:
            raise ValueError("Linked skill folders cannot be changed here.")
        if target.exists() and any(
            p.is_symlink() or getattr(p, "is_junction", lambda: False)()
            for p in [target, *target.rglob("*")]
        ):
            raise ValueError("Linked skill assets cannot be changed here.")
        return target

    def _trash_root(self) -> Path:
        root = self.workspace / ".skill-trash"
        if root.resolve() != self.workspace.resolve() / ".skill-trash":
            raise ValueError("Linked recovery folders cannot be changed here.")
        return root

    def detail(self, name: str) -> dict[str, Any]:
        loader = self.loader(include_disabled=True)
        entry = next((s for s in loader.list_skills(False) if s["name"] == name), None)
        if entry is None:
            raise ValueError("That skill is no longer in the library.")
        content = loader.load_skill(name) or ""
        meta = loader.get_skill_metadata(name) or {}
        available, reason = loader.get_skill_availability(name)
        return {
            "name": name,
            "description": str(meta.get("description") or name),
            "source": entry["source"],
            "available": available,
            "unavailable_reason": reason,
            "requirements": loader.get_skill_requirements(name),
            "content": content,
            "revision": hashlib.sha256(content.encode()).hexdigest(),
            "enabled": name not in set(self.db.get_setting("agent.disabled_skills", [])),
            "overridden": entry["source"] == "workspace"
            and (self.builtin / name / "SKILL.md").is_file(),
            "versions": [
                {"version": v["version"], "source": v["source"]}
                for v in self.db.list_artifact_versions(
                    artifact_type="skill_instruction", artifact_key=name, limit=50
                )
            ],
        }

    def inventory(self) -> dict[str, Any]:
        return {
            "skills": [
                self.detail(e["name"])
                for e in self.loader(include_disabled=True).list_skills(False)
            ],
            "deleted": sorted(self.db.get_setting("skills.trash", {}).keys()),
            "pending": self.db.get_setting("skills.pending", []),
            "problems": self.db.get_setting("skills.problems", []),
        }

    def change(
        self,
        *,
        action: str,
        name: str,
        revision: str = "",
        content: str = "",
        version: int | None = None,
        source: str = "user",
    ) -> dict[str, Any]:
        self._name(name)
        if action not in {"edit", "delete", "reset", "enable", "disable", "restore", "version"}:
            raise ValueError("Choose a supported skill change.")
        operation = dict(
            action=action,
            name=name,
            revision=revision,
            content=content,
            version=version,
            source=source,
        )
        with self._lock:
            if self._runs:
                pending = self.db.get_setting("skills.pending", [])
                if any(p["name"] == name for p in pending):
                    raise ValueError(
                        "That skill already has a change waiting for this task to finish."
                    )
                self._validate(operation)
                self.db.set_setting("skills.pending", [*pending, operation])
                return {"pending": True}
            self._apply(operation)
            return {"pending": False}

    def _validate(self, op: dict[str, Any]) -> dict[str, Any] | None:
        name, action = op["name"], op["action"]
        self._directory(name)
        if action == "restore":
            if name not in self.db.get_setting("skills.trash", {}):
                raise ValueError("There is no deleted copy to restore.")
            if (self.workspace / "skills" / name).exists():
                raise ValueError("A newer workspace skill exists. I won't overwrite it.")
            return None
        detail = self.detail(name)
        if op["revision"] != detail["revision"]:
            raise ValueError("This skill changed since you opened it. Reopen it before saving.")
        if action in {"delete", "reset"} and detail["source"] != "workspace":
            raise ValueError("Built-in skills can be disabled or edited as a custom copy.")
        if action == "delete" and detail["overridden"]:
            raise ValueError("Use Reset built-in to remove this override.")
        if action == "reset" and not detail["overridden"]:
            raise ValueError("Only built-in overrides can be reset.")
        if action == "edit":
            text = op["content"]
            if not text.strip() or len(text) > 32_000:
                raise ValueError("Skill instructions must contain 1 to 32,000 characters.")
            match = re.match(r"^---\s*\n(.*?)\n---(?:\s*\n|$)", text.replace("\r\n", "\n"), re.S)
            try:
                meta = yaml.safe_load(match.group(1)) if match else None
            except yaml.YAMLError as exc:
                raise ValueError("Check the skill's YAML header before saving.") from exc
            if (
                not isinstance(meta, dict)
                or meta.get("name") != name
                or not meta.get("description")
            ):
                raise ValueError("Keep the same name and a description in the skill's YAML header.")
        return detail

    def _apply(self, op: dict[str, Any]) -> None:
        detail = self._validate(op)
        name, action = op["name"], op["action"]
        target = self._directory(name)
        trash = dict(self.db.get_setting("skills.trash", {}))
        if action in {"enable", "disable"}:
            disabled = set(self.db.get_setting("agent.disabled_skills", []))
            (disabled.add if action == "disable" else disabled.discard)(name)
            self.db.set_setting("agent.disabled_skills", sorted(disabled))
            if self.on_activate:
                self.on_activate(disabled)
        elif action in {"delete", "reset"}:
            archive = self._trash_root() / uuid.uuid4().hex / name
            archive.parent.mkdir(parents=True, exist_ok=True)
            target.rename(archive)  # Move the entire bundle, including supporting assets.
            trash[name] = str(archive.relative_to(self.workspace))
            self.db.set_setting("skills.trash", trash)
        elif action == "restore":
            archive = self.workspace / trash[name]
            if not archive.resolve().is_relative_to(self._trash_root().resolve()):
                raise ValueError("The deleted skill location is invalid.")
            target.parent.mkdir(parents=True, exist_ok=True)
            archive.rename(target)
            del trash[name]
            self.db.set_setting("skills.trash", trash)
        else:
            text = op["content"]
            if action == "version":
                rows = self.db.list_artifact_versions(
                    artifact_type="skill_instruction", artifact_key=name, limit=200
                )
                selected = next((r for r in rows if r["version"] == op["version"]), None)
                if selected is None:
                    raise ValueError("That instruction version is no longer available.")
                text = selected["before_text"]
            if detail["source"] == "builtin":
                shutil.copytree(self._directory(name, bundled=True), target)
            temporary = target / (".SKILL-" + uuid.uuid4().hex + ".tmp")
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(target / "SKILL.md")
            self.versions.snapshot(
                "skill_instruction", name, detail["content"], text, source=op["source"]
            )

    def flush(self) -> None:
        with self._lock:
            if self._runs:
                return
            problems = []
            for op in self.db.get_setting("skills.pending", []):
                try:
                    self._apply(op)
                except (OSError, ValueError) as exc:
                    problems.append({"name": op["name"], "message": str(exc)})
            self.db.set_setting("skills.pending", [])
            if problems:
                self.db.set_setting("skills.problems", problems)

    def turn_hook(self, _context: Any) -> AgentHook:
        manager = self
        with self._lock:
            self._runs += 1

        class SkillBoundaryHook(AgentHook):
            async def on_finally(self, context: Any) -> None:
                with manager._lock:
                    manager._runs -= 1
                    manager.flush()

        return SkillBoundaryHook()
