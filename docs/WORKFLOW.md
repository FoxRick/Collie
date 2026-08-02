# Collie delivery workflow

**Status:** canonical

## Start a task

1. Read `VISION.md`, `PROJECT_MAP.md`, the nearest `AGENTS.md`, and only the
   active specification relevant to the request.
2. Check Git status and define the outcome, owned paths, acceptance checks, and
   constraints.
3. Use a reviewable plan before broad, destructive, cross-repository, or
   materially irreversible work.

## Repository discipline

- Use a focused branch and, when useful, a dedicated Git worktree. Keep `main`
  ready to integrate reviewed work.
- Preserve unrelated changes and never stage them silently.
- Before a release build, use a clean tagged source commit and a new,
  version-specific output directory. Never release from mutable `dist/` output.
- Remove a worktree only after its changes are merged, intentionally preserved,
  or abandoned by its owner.

## Make and verify changes

- Keep features, refactors, migrations, and generated output separate when
  practical.
- Treat `nanobot/` as adapted vendor code and keep changes surgical.
- Add focused tests for behavior changes, then run the component gate
  appropriate to the affected boundary.
- Preserve permission, privacy, secret-storage, and attribution invariants.

## Documentation-impact check

Before finishing, ask:

1. Did product intent change? Update `VISION.md` only if yes.
2. Did ownership, paths, interfaces, or an invariant change? Update
   `PROJECT_MAP.md` and the nearest `AGENTS.md` only if yes.
3. Did active feature behavior change? Update one canonical file under
   `product/`.
4. Did a durable technical decision change? Update one file under
   `engineering/`.
5. Did verified public release truth change? Update `operations/release/`.

When project structure or canonical documentation changes, run:

```powershell
.\collie-core\.venv\Scripts\python.exe tools\update_project_snapshot.py
.\collie-core\.venv\Scripts\python.exe tools\update_project_snapshot.py --check
```

## Finish a task

Report the user-visible outcome, important files changed, validation performed,
remaining owner-controlled blockers, and any canonical document whose truth
changed. Do not claim completion based only on generated code or mutable build
output.
