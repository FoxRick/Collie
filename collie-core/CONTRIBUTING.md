# Contributing to Collie Core

Thanks for helping improve Collie. `collie-core` is the Python runtime used by
the Collie desktop app. It contains Collie-owned code in `collie_core/` and
adapted MIT-licensed engine code in the `nanobot/` namespace.

## Before you start

- Use [GitHub issues](https://github.com/FoxRick/Collie/issues) for focused bug
  reports and feature proposals. Search first and describe the user-facing
  problem, expected behavior, and a reproducible path.
- For a vulnerability, follow [SECURITY.md](./SECURITY.md); do not open a
  public issue.
- For a larger change, open an issue or draft pull request early so the
  intended scope can be agreed before implementation grows.

## Development setup

From `collie-core/`, use Python 3.11 or newer and install the development
dependencies in a virtual environment:

```powershell
python -m pip install -e ".[dev]"
python -m pytest tests -q
python -m ruff check nanobot collie_core tests
```

The Electron shell is in `../collie-ui`. Its typecheck and build should be run
when a core change affects the IPC contract.

## Change guidelines

- Keep patches focused and include tests for behavior changes.
- Preserve the `nanobot` package namespace and make surgical changes to
  vendored engine code. Put new Collie behavior in `collie_core/` where
  practical.
- Do not commit credentials, test tokens, user data, installers, or generated
  release artifacts.
- Follow the existing Python 3.11+, async, and Ruff conventions. Avoid broad
  formatting churn in a functional change.
- Update user-facing documentation when the supported product behavior changes.

## Pull requests

Explain the problem, solution, tests run, and any behavior or migration risk.
Keep unrelated local changes out of the pull request. Maintainers may request
additional tests or a smaller scope before merging.

By contributing, you confirm that you have the right to submit the change and
license it under the repository's MIT License.

## Attribution

Collie is a fork that retains adapted components from
[HKUDS/nanobot](https://github.com/HKUDS/nanobot). Keep required copyright and
third-party notices intact; see `LICENSE` and `THIRD_PARTY_NOTICES.md`.
