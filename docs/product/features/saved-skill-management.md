# Saved skill management

The Skills screen supports editing saved instructions, enable/disable,
recoverable deletion of custom skills, restoration of deleted bundles, and
restoration of instructions from before a recorded edit. Creation continues
through the existing approved `create_skill` chat tool.

`collie_core.skills.SkillManager` is the shared implementation for the desktop
`manage_skill` / `get_skill_editor` commands and approved `manage_skill` agent
tool. `inspect_skills` exposes current revisions, instruction history, deleted
names, pending changes, and activation problems. Every agent-origin change
requires fresh central approval, including enablement and version restoration.
The authenticated desktop controls represent direct owner actions.

Built-in instructions and supporting assets are never written in `nanobot/`.
Editing a built-in copies its bundle into workspace `skills/`; reset moves that
override to recovery storage and reveals the built-in again. Deletion/reset
moves entire custom folders into workspace `.skill-trash/`. Recovery refuses
to overwrite a newer workspace directory. Linked directories/assets cannot be
modified through this manager. No package installation or script execution is
introduced.

Instruction edits use the existing `artifact_versions` store under
`skill_instruction`, with no database migration. The editor shows a bounded
history of the latest 50 edits; restoration can select the instructions before
one of those edits. Every modification supplies the current content revision;
stale saves/restores fail and require reopening the skill. Restored instructions
are themselves recorded as a new edit, preserving newer history. Supporting
assets are preserved, but instruction history does not version their contents.

Changes requested during tracked main/child agent runs are queued in local
settings. They activate after all those runs reach their finalization hook,
including cancellation; pending changes survive a restart and revalidate their
revision before activation. Conflicting changes appear in the library's status
instead of overwriting owner edits. Refresh the library to see queued results.
This does not cancel/rebuild the provider loop. The manager updates the public
loader enablement configuration at that boundary; settings also initialize new
loops. Disabled skills disappear from model summaries, explicit `load_skill`,
`/skills` and `/skill` selection, and always-skill context for subsequent turns.
Instructions already present in conversation history are not retroactively erased.

The focused offline acceptance scenario covers creation/editing, discoverability
and explicit/always loading after disable, overlapping-run deferral, bundled-byte
and asset preservation, recovery, and refusing stale owner-content writes.
Packaged desktop interaction and live-provider acceptance remain unverified.
