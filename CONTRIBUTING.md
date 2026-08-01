# Contributing to Collie

Thanks for helping improve Collie. It is a Windows desktop application with a
Python runtime in `collie-core/` and an Electron/React shell in `collie-ui/`.

## Before you start

- Use GitHub Issues for focused bugs and feature proposals. Search for an
  existing report first and include the user-visible problem and reproduction
  steps.
- Do not report vulnerabilities in public issues. Follow [SECURITY.md](SECURITY.md).
- For a larger change, open an issue or draft pull request early so the scope
  can be agreed before implementation grows.

## Development and checks

The supported alpha platform is Windows 11 x64. Use Python 3.12, Node.js, and
npm. The root README has the standard setup and checks. At minimum, run the
focused tests for the component you change; run the relevant full test, lint,
typecheck, or build gate when the change affects that boundary.

Do not commit credentials, test tokens, user data, installers, generated
release artifacts, or build output. Keep unrelated local changes out of a pull
request.

## Change guidelines

- Keep patches focused and include tests for behavior changes.
- Preserve the `nanobot` namespace and make surgical changes to adapted
  upstream code. Put new Collie behavior in `collie_core/` where practical.
- Keep permission and approval behavior explicit. Friendly user-facing copy
  must not weaken controls for consequential actions.
- Update public documentation when verified user-visible behavior changes.
- Do not claim a provider, connector, installer, or release is available until
  the corresponding verified acceptance checks pass.

## Pull requests

Explain the problem, solution, tests run, and any behavior, privacy, or
migration risk. Maintainers may ask for a smaller scope or additional evidence
before merging.

By contributing, you confirm that you have the right to submit the change and
license its code and documentation under the repository's MIT License. Do not
submit Collie branding, artwork, or third-party marks unless you have explicit
permission and the accompanying provenance information required by
[NOTICE.md](NOTICE.md).

## Attribution

Collie includes adapted components from
[HKUDS/nanobot](https://github.com/HKUDS/nanobot). Keep the required
attribution and third-party notices intact; see [LICENSE](LICENSE) and
[NOTICE.md](NOTICE.md), as well as
[collie-core/THIRD_PARTY_NOTICES.md](collie-core/THIRD_PARTY_NOTICES.md).
