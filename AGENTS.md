# Collie workspace instructions

These instructions apply to the whole public Collie repository. More specific
`AGENTS.md` files add component-level rules.

## Start here

Read only the context needed for the task, in this order:

1. `docs/VISION.md` for durable product intent.
2. `docs/PROJECT_MAP.md` for boundaries, data flow, and current paths.
3. The relevant active specification under `docs/product/`,
   `docs/engineering/`, or `docs/operations/`.
4. The nearest component `AGENTS.md` before editing that component.

Generated output, runtime state, unrelated repositories, and broad market
research are not source of truth.

## Repository boundaries

- The root repository owns `collie-core/`, `collie-ui/`, `docs/`, and `tools/`.
- The public website is maintained separately from the desktop application.
- User data, credentials, internal investigations, private business material,
  recovery artifacts, runtime logs, caches, virtual environments, build output,
  and screenshots must remain outside source control.

Always inspect Git status before editing. Preserve unrelated and uncommitted
work, and keep migrations separate from feature or bug-fix changes.

## Working method

1. Restate the outcome and identify the smallest relevant component.
2. Inspect current behavior and the nearest instructions before changing it.
3. Keep delegated work bounded and avoid overlapping file ownership.
4. Make the smallest coherent change and test it at the narrowest useful level.
5. Perform the documentation-impact check in `docs/WORKFLOW.md`.
6. Run `collie-core/.venv/Scripts/python.exe tools/update_project_snapshot.py`
   when project shape, dependencies, or canonical docs change, then run it with
   `--check`.

## Documentation rules

- `docs/VISION.md` changes only when product intent changes.
- `docs/PROJECT_MAP.md` changes with component ownership, paths, interfaces, or
  architectural invariants.
- Active feature decisions belong in `docs/product/`; durable technical
  decisions in `docs/engineering/`; public release policy in
  `docs/operations/`.
- Internal investigations, one-time handoffs, user data, and private operational
  evidence do not belong in the public repository.

## Product invariants

- Collie is local-first, chat-first, and designed for nontechnical Windows
  users.
- Friendly language never weakens permissions. Consequential, destructive,
  financial, external-write, send, or publish actions remain centrally gated.
- Secrets stay in OS-protected storage and must never enter logs, source,
  fixtures, screenshots, or documentation.
- Product claims must reflect verified behavior, especially provider,
  connector, release, and data-handling claims.
