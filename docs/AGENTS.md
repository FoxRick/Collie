# Documentation instructions

These rules apply under `docs/`.

- Prefer one canonical document per decision. Link to it instead of copying
  the same status into several plans.
- Keep `VISION.md`, `PROJECT_MAP.md`, and `WORKFLOW.md` concise and durable.
- Put active material in `product/`, `engineering/`, or `operations/`.
- Move superseded plans, completed reviews, and one-time handoffs to
  `archive/`. Preserve their contents unless correction is needed for safety.
- Never use an archived file as proof of current implementation or release
  state without verifying code, tests, and the active status document.
- Use repository-relative links and stable descriptive filenames.
- State a document's status near the top when its lifecycle is not obvious.
- Do not hand-edit `generated/REPOSITORY_SNAPSHOT.md`; run
  `collie-core/.venv/Scripts/python.exe tools/update_project_snapshot.py`.
- A code-only task does not require documentation churn. Update docs only when
  canonical truth or user-visible behavior changed.
