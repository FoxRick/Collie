"""Subagent loader: .md files on disk, mirrored to SQLite (Step 36, F049).

File format (adapted from the engine's SKILL.md pattern):

    ---
    name: Trip Planner
    description: Plans travel from start to finish.
    ---
    You are a travel planning specialist. ...

The Markdown body is the subagent's system prompt. Files win over DB rows:
``sync()`` reconciles the folder into the ``subagents`` table so edits made
in any text editor show up in Settings → Subagents.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from collie_core.db import CollieDB
from collie_core.versions import VersionStore

__all__ = [
    "STARTERS",
    "SubagentLoader",
    "bind_subagent_loader",
    "draft_system_prompt",
    "get_subagent_loader",
]

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "subagent"


def draft_system_prompt(name: str, description: str) -> str:
    """Deterministic fallback prompt when no LLM is available to write one."""
    clean = description.strip().rstrip(".")
    return (
        f"You are {name}, a specialized assistant.\n\n"
        f"Your specialty: {clean}.\n\n"
        "How you work:\n"
        "1. Understand exactly what the user needs before diving in.\n"
        "2. Break the job into clear steps and work through them.\n"
        "3. Use the tools available to you when they help.\n"
        "4. Be thorough but concise — results first, caveats second.\n"
        "5. Speak warmly and plainly; no jargon, no corporate language."
    )


STARTERS: tuple[dict[str, str], ...] = (
    {
        "name": "Researcher",
        "description": "Researches the web and connected sources with citations.",
        "execution_posture": "read_only",
        "system_prompt": (
            "You are Collie's research specialist.\n\n"
            "1. Search more than one reliable source when available.\n"
            "2. Separate evidence from inference.\n"
            "3. Cite sources with direct links.\n"
            "4. Return a compact synthesis, not raw search traces.\n"
            "5. Say plainly when evidence is missing or uncertain."
        ),
    },
    {
        "name": "Analyst",
        "description": "Handles calculations, comparisons, tables, and findings.",
        "execution_posture": "read_only",
        "system_prompt": (
            "You are Collie's analysis specialist.\n\n"
            "1. State assumptions before calculating.\n"
            "2. Check arithmetic and units.\n"
            "3. Use compact tables when they improve comparison.\n"
            "4. Separate findings from recommendations.\n"
            "5. Return the useful conclusion first."
        ),
    },
    {
        "name": "Reviewer",
        "description": "Independently checks accuracy, omissions, and requirements.",
        "execution_posture": "read_only",
        "system_prompt": (
            "You are Collie's independent reviewer.\n\n"
            "1. Check the result against the user's explicit requirements.\n"
            "2. Find factual errors, omissions, unsafe assumptions, and unclear wording.\n"
            "3. Do not rewrite the artifact or mutate anything.\n"
            "4. Return only a short prioritized list of actionable issues.\n"
            "5. Say when no material issue was found."
        ),
    },
    {
        "name": "Operator",
        "description": "Carries out multi-step actions with normal approvals.",
        "execution_posture": "inherit",
        "system_prompt": (
            "You are Collie's action specialist.\n\n"
            "1. Follow the requested scope exactly.\n"
            "2. Prefer reversible steps and verify each result.\n"
            "3. Never treat a previous approval as approval for a new action.\n"
            "4. Stop and report a blocker instead of improvising risky changes.\n"
            "5. Return a concise record of what changed."
        ),
    },
)


class SubagentLoader:
    """Discover, create, edit, and delete subagent .md files."""

    def __init__(self, workspace: Path, db: CollieDB) -> None:
        self.dir = workspace / "subagents"
        self.db = db

    # -- versioning (Gardener rollback rail) --------------------------------

    def _snapshot(
        self, filename: str, before: str, after: str, *, source: str = "user"
    ) -> str | None:
        """Snapshot a subagent file edit; never breaks the write on failure."""
        try:
            return VersionStore(self.db).snapshot(
                "subagent", filename, before, after, source=source
            )
        except Exception:
            logger.exception("subagent version snapshot failed (swallowed)")
            return None

    def latest_version_id(self, filename: str) -> str | None:
        try:
            return VersionStore(self.db).latest_version_id("subagent", filename)
        except Exception:
            return None

    # -- disk <-> db sync ----------------------------------------------------

    def sync(self) -> list[dict[str, Any]]:
        """Reconcile the subagents folder into the DB; return current rows."""
        self.dir.mkdir(parents=True, exist_ok=True)
        rows = {str(r.get("filename") or ""): r for r in self.db.list_subagents()}
        seen: set[str] = set()
        for path in sorted(self.dir.glob("*.md")):
            # A file that exists — even unparseable or briefly unreadable —
            # must not delete its database row.
            seen.add(path.name)
            parsed = self._parse(path)
            if parsed is None:
                continue
            name, description, prompt, posture = parsed
            existing = rows.get(path.name)
            if (
                existing is None
                or existing.get("name") != name
                or existing.get("description") != description
                or existing.get("system_prompt") != prompt
                or existing.get("execution_posture") != posture
            ):
                self.db.upsert_subagent(
                    name,
                    description=description,
                    system_prompt=prompt,
                    filename=path.name,
                    execution_posture=posture,
                    subagent_id=(existing or {}).get("id"),
                )
        for filename, row in rows.items():
            if filename and filename not in seen:
                self.db.delete_subagent(str(row["id"]))
        return self.db.list_subagents()

    def seed_bundled_once(self) -> None:
        """Install bundled Markdown specialists once without restoring deletions."""
        if self.db.get_setting("subagents.bundled_seeded", False):
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        for starter in STARTERS:
            path = self.dir / f"{_slugify(starter['name'])}.md"
            if path.exists():
                continue
            path.write_text(
                self._render(
                    starter["name"],
                    starter["description"],
                    starter["system_prompt"],
                    starter["execution_posture"],
                ),
                encoding="utf-8",
            )
        self.db.set_setting("subagents.bundled_seeded", True)

    def _parse(self, path: Path) -> tuple[str, str, str, str] | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return None
        if raw.startswith("\ufeff"):  # UTF-8 BOM breaks the frontmatter match
            raw = raw[1:]
        name = path.stem.replace("-", " ").title()
        description = ""
        posture = "read_only"
        body = raw
        match = _FRONTMATTER.match(raw)
        if match:
            body = raw[match.end():]
            try:
                meta = yaml.safe_load(match.group(1)) or {}
            except yaml.YAMLError:
                meta = {}
            if isinstance(meta, dict):
                name = str(meta.get("name") or name)
                description = str(meta.get("description") or "")
                posture = str(meta.get("execution_posture") or "read_only")
                if posture not in {"read_only", "inherit"}:
                    posture = "read_only"
        prompt = body.strip()
        if not prompt:
            return None
        return name, description, prompt, posture

    # -- CRUD ------------------------------------------------------------------

    def create(
        self,
        name: str,
        description: str = "",
        system_prompt: str = "",
        execution_posture: str = "read_only",
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("Every helper needs a name!")
        prompt = (system_prompt or "").strip() or draft_system_prompt(name, description)
        self.dir.mkdir(parents=True, exist_ok=True)
        filename = f"{_slugify(name)}.md"
        path = self.dir / filename
        if path.exists():
            raise ValueError(
                f"You already have a helper called '{name}'. "
                "Edit it, or pick another name!"
            )
        path.write_text(
            self._render(name, description, prompt, execution_posture),
            encoding="utf-8",
        )
        row = self.db.upsert_subagent(
            name,
            description=description,
            system_prompt=prompt,
            filename=filename,
            execution_posture=execution_posture,
        )
        self._snapshot(filename, "", path.read_text(encoding="utf-8"))
        logger.info("Subagent created: {}", filename)
        return row

    def update(
        self,
        subagent_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        system_prompt: str | None = None,
        execution_posture: str | None = None,
    ) -> dict[str, Any]:
        rows = {r["id"]: r for r in self.db.list_subagents()}
        row = rows.get(subagent_id)
        if row is None:
            raise ValueError("I can't find that helper — was it already deleted?")
        new_name = (name if name is not None else row["name"]).strip()
        new_desc = description if description is not None else (row.get("description") or "")
        new_prompt = (
            system_prompt if system_prompt is not None else (row.get("system_prompt") or "")
        ).strip()
        posture = str(
            execution_posture
            if execution_posture is not None
            else row.get("execution_posture") or "read_only"
        )
        if posture not in {"read_only", "inherit"}:
            raise ValueError("Agent access must be read_only or inherit.")
        if not new_prompt:
            raise ValueError("A helper needs its instructions — the prompt can't be empty.")
        filename = str(row.get("filename") or f"{_slugify(new_name)}.md")
        self.dir.mkdir(parents=True, exist_ok=True)
        new_filename = f"{_slugify(new_name)}.md"
        if new_filename != filename:
            # Keep the on-disk file in step with the name (slug drift).
            if (self.dir / new_filename).exists():
                raise ValueError(
                    f"You already have a helper called '{new_name}'. Pick another name!"
                )
            old_path = self.dir / filename
            if old_path.exists():
                old_path.rename(self.dir / new_filename)
            filename = new_filename
        before = ""
        target_path = self.dir / filename
        if target_path.exists():
            before = target_path.read_text(encoding="utf-8")
        target_path.write_text(
            self._render(new_name, new_desc, new_prompt, posture), encoding="utf-8"
        )
        self._snapshot(filename, before, target_path.read_text(encoding="utf-8"))
        return self.db.upsert_subagent(
            new_name,
            description=new_desc,
            system_prompt=new_prompt,
            filename=filename,
            execution_posture=posture,
            subagent_id=subagent_id,
        )

    def delete(self, subagent_id: str) -> None:
        rows = {r["id"]: r for r in self.db.list_subagents()}
        row = rows.get(subagent_id)
        if row is None:
            return
        filename = str(row.get("filename") or "")
        if filename:
            file_path = self.dir / filename
            if file_path.exists():
                before = file_path.read_text(encoding="utf-8")
                file_path.unlink()
                self._snapshot(filename, before, "")
        self.db.delete_subagent(subagent_id)
        logger.info("Subagent deleted: {}", filename or subagent_id)

    # -- lookup ------------------------------------------------------------------

    def find(self, name: str) -> dict[str, Any] | None:
        """Fuzzy lookup: exact, then prefix, then substring (case-insensitive)."""
        wanted = (name or "").strip().lower()
        if not wanted:
            return None
        rows = self.db.list_subagents()
        for row in rows:
            if str(row["name"]).lower() == wanted:
                return row
        for row in rows:
            if str(row["name"]).lower().startswith(wanted):
                return row
        for row in rows:
            if wanted in str(row["name"]).lower():
                return row
        return None

    @staticmethod
    def _render(
        name: str,
        description: str,
        prompt: str,
        execution_posture: str = "read_only",
    ) -> str:
        meta = yaml.safe_dump(
            {
                "name": name,
                "description": description,
                "execution_posture": execution_posture,
            },
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        ).strip()
        return f"---\n{meta}\n---\n\n{prompt.strip()}\n"


_loader: SubagentLoader | None = None


def bind_subagent_loader(loader: SubagentLoader | None) -> None:
    global _loader
    _loader = loader


def get_subagent_loader() -> SubagentLoader | None:
    return _loader
