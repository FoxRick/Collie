# Collie Core

Collie Core is the internal Python runtime for the Collie Windows desktop app.
It is launched and managed by the Electron shell in `../collie-ui`; it is not a
standalone public install, CLI, hosted service, or PyPI distribution.

For the product overview and supported capabilities, see the
[repository root README](../README.md).

## Development

Use Python 3.11 or newer. From this directory:

```powershell
python -m pip install -e ".[dev]"
python -m pytest tests -q
python -m ruff check nanobot collie_core tests
python -m collie_core.runtime --port 3818
```

The Electron/React shell lives in `../collie-ui` and communicates with this
runtime over localhost WebSocket. When a change affects the IPC contract, also
run its typecheck and production build:

```powershell
cd ../collie-ui
npm run typecheck
npm run build
```

## Architecture

- `collie_core/` contains Collie-owned runtime features: SQLite-backed
  settings and memory, IPC, permissions, providers, services, automations,
  messengers, and the desktop pet.
- `nanobot/` is adapted vendored engine code for the agent loop, providers,
  tools, MCP client, WebSocket transport, and messenger integrations. Keep
  changes surgical and preserve its namespace for compatibility.
- Secrets are stored by the Electron shell using OS-protected storage and are
  passed to the managed core process only when required. Do not add tokens to
  source, logs, tests, or documentation.

## Security and contributions

Do not open a public issue for a vulnerability. See the repository-level
[security policy](../SECURITY.md) and [contribution guide](../CONTRIBUTING.md).

## Attribution and license

Collie Core is a stripped and adapted fork of
[HKUDS/nanobot](https://github.com/HKUDS/nanobot). The vendored `nanobot`
namespace remains in this repository, and its MIT attribution is retained.
Collie Core is distributed under the MIT License; see `LICENSE` and
`THIRD_PARTY_NOTICES.md` for license and third-party notices.
