# Maintenance pass report — `maint/v4pro-stability`

**Date:** 2026-08-13
**Scope:** `/home/rick/collie-latest` (collie-core + collie-ui)
**Model:** deepseek-v4-pro
**Branch:** `maint/v4pro-stability` (6 commits, not pushed — owner reviews)

---

## What changed, grouped by goal

### Stability fixes (provable bugs, zero user-visible behavior change)

- **Event-loop stalls on blocking I/O** — four code paths ran blocking work directly on the asyncio loop, freezing streamed chat deltas and every IPC connection during the operation:
  - `_cmd_export_data` (`collie_core/ipc/server.py`): full DB export + zip walk
  - `WeatherTool` (`tools/weather.py`): blocking `urlopen` geocode + forecast
  - `NewsTool` (`tools/news.py`): blocking `urlopen` RSS fetch
  - `RecipesTool` (`tools/recipes.py`): up to two blocking TheMealDB requests per call
  - All now run through `asyncio.to_thread`, matching the pattern already used by the engine's own web tools and the rest of the IPC layer. Same endpoints, timeouts, and error copy.

### Dead code removed (each verified with repo-wide greps + vulture cross-check)

**`collie_core/db.py`:**
- `archive_conversation` — no caller; nothing ever sets `archived=1`
- `clear_conversation_review_gate`, `fail_task_checklist` — checklist-finalize paths in `ipc/server.py` use `update/complete/cancel_task_checklist`
- `touch_provider`
- `check_shopping_item` / `delete_shopping_item` (by-id variants; the by-name variants are the live path)
- `expenses_for_month`
- `mark_automation_run` — superseded by `mark_routine_result`
- `delete_service` — disconnect uses `upsert_service`
- `delete_message`

**Other core modules:**
- `collie_core/runtime.py`: `recent_subagents_for_conversation` (superseded by `subagent_activity`, zero callers)
- `collie_core/undo/journal.py`: `pending_entries` (test-only; tests now assert via `_safe_conversation_id`)
- `collie_core/gardener/propose.py`: `_artifact_text` (orphaned; `build_prompt` inlines the reads)
- `collie_core/connectors/drivers/base.py`: entire file deleted — the `ConnectorDriver` protocol no driver implements or imports
- `collie_core/pet/sprites.py`: `generate_jump`/`generate_wag`/`generate_run`, `generate_spritesheet`, `generate_all_sprites` (never registered in `STATE_GENERATORS`, no callers), plus orphan palette constants `DARK_GREY`, `TONGUE_PINK`
- `collie_core/voice.py`: unused `MODEL_NAME`

**`collie-ui`:**
- `components/settings/AutomationsTab.tsx`: deleted — orphaned (no Settings tab routes to it; RoutinesScreen owns that surface)
- `renderer/src/lib/ipc.ts`: removed uncalled `CollieClient.setSetting`; the `set_setting` server handler stays for backend tests and moved to the IPC contract allowlist with its reason

**Generated docs:**
- `docs/generated/REPOSITORY_SNAPSHOT.md` refreshed via the repo's own `tools/update_project_snapshot.py` (`--check` passes)

---

## Files touched

**Core (14):**
- `collie-core/collie_core/db.py`
- `collie-core/collie_core/runtime.py`
- `collie-core/collie_core/ipc/server.py`
- `collie-core/collie_core/undo/journal.py`
- `collie-core/collie_core/gardener/propose.py`
- `collie-core/collie_core/connectors/drivers/base.py` (deleted)
- `collie-core/collie_core/pet/sprites.py`
- `collie-core/collie_core/voice.py`
- `collie-core/collie_core/tools/weather.py`
- `collie-core/collie_core/tools/news.py`
- `collie-core/collie_core/tools/recipes.py`
- `collie-core/tests/collie/test_db.py`
- `collie-core/tests/collie/test_life_tools_phase3.py`
- `collie-core/tests/collie/test_undo_journal.py`
- `collie-core/tests/collie/test_ipc_contract.py`

**UI (2):**
- `collie-ui/src/renderer/src/components/settings/AutomationsTab.tsx` (deleted)
- `collie-ui/src/renderer/src/lib/ipc.ts`

**Docs (1):**
- `docs/generated/REPOSITORY_SNAPSHOT.md`

Commits: 2 dead-code, 3 threading fixes, 1 snapshot refresh. Tree clean apart from the pre-existing untracked `.commandcode/` (taste files — intentionally left alone).

---

## Test + lint results

| Gate | Baseline | Final |
|---|---|---|
| Core pytest (CI-equivalent Linux, 5 documented deselections) | 3549 passed, 1 skipped | **3549 passed, 1 skipped** |
| Core ruff check + ruff format --check | clean | **clean** |
| Core phase gates (e2e 1–4, IPC contract, headless, prompt hashes) | passing | **passing** |
| UI vitest (NODE_ENV unset — see note) | 323 passed | **323 passed** |
| UI typecheck + electron-vite build | clean | **clean** |
| Snapshot tool `--check` | stale | **current** |

**Environment note:** the agent shell exported `NODE_ENV=production`, which strips React's `act` export and produced 89 vitest failures. With `env -u NODE_ENV`, all 323 UI tests pass. CI does not set NODE_ENV, so this is a local environment artifact, not a repo bug.

---

## Skipped-but-recommended fixes (need a behavior/design decision)

- **Stale server-only IPC handlers** — `get_routine`, `update_routine`, `delete_routine`, `test_routine` have no UI caller (flagged "verify or remove" in `tests/collie/test_ipc_contract.py`). RoutinesScreen only uses list/create/pause/resume/run/retry. Removing them is an IPC surface change; the allowlist documents them as a conscious decision to review.
- **`collie_core/services/` legacy shim** — AGENTS.md explicitly says not to remove it before the migration fixtures pass. Left untouched.
- **`AnimationSnapshot.state_changed_at`** (`pet/v2.py`) — never read (only `.state`/`.direction` are), but the v2 pet snapshot shape is a v2 contract. Recommend removal when that contract is next reviewed.
- **Blocking DB reads in `_cmd_get_status` / `_cmd_get_subagent_activity`** — small bounded queries today; wrap in `to_thread` if the DB grows. Not a bug at current scale.
- **`.commandcode/` is untracked** — recommend the owner add it to `.gitignore` (or a global ignore) so it stops showing in `git status`.

---

## Manual QA needed on the desktop app (`~/collie-workspace/apps/collie-desktop`)

- Weather, news, and recipe tools still return correct cards (only the threading changed).
- Data export still produces the zip with the same reply shape.
- Settings screen tabs unchanged — AutomationsTab had no route, so nothing visible was removed.
- Standard smoke: chat a turn, stop mid-turn, mid-turn steering, an automation firing, and the desktop pet (sprites cleanup only touched unregistered generators).

---

## Risks / follow-ups

- Branch must be reviewed and merged by the owner; nothing is pushed.
- The 5 Linux-deselected core tests remain deselected locally (Windows DPAPI + Windows path semantics); the Windows CI job is the authority for those.
- The `CollieDB` method removals are API-surface changes for any downstream consumer outside this repo (none exist in-repo).
