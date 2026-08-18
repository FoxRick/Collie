# Renderer trust boundary — decrypted secrets never reach the renderer

**Decision (2026-08-18, PR #53):** stored secrets are decrypted and pushed
to the Python core by the **main process only**. The renderer receives a
bare count, never the values. This document records the design and the
rationale so the boundary stays intact.

## Threat model

The renderer (React) is the least-trusted layer of the Electron shell. It
renders chat content that originates from model providers and connectors,
so it must be treated as potentially compromised (XSS) even though the app
enforces strict CSP, trusted-sender checks (`renderer-security.ts`), and
URL-based renderer validation. The documented invariant, restated:
**the renderer must never receive decrypted long-lived secrets.**

## The previous flow (the bug)

```
renderer (App.tsx) --'collie:load-secrets'--> main (decrypts ALL stored secrets)
  --> renderer holds every provider/messenger key in memory
  --> renderer pushes them to the core over its own WebSocket
```

The "one-shot" guard in `secrets.ts` (`secretsConsumed`) was meant to stop
a compromised renderer from repeatedly pulling plaintext keys — but the
renderer was the *only* consumer, so the guard was defeated by design: the
renderer pulled everything once per boot, which was the whole leak.

## The current flow

```
core ready (python.ts onCoreReady)
  --> main: core-client.ts pushStoredSecretsToCore()
        loadSecrets()             # one-shot decrypt, main process only
        own WebSocket (token)     # same collie-<token> subprotocol
        set_api_key / set_messenger_secret frames
  --> renderer: window.collie.storedSecretCount()   # count ONLY
```

Key files:

- `collie-ui/src/main/core-client.ts` — main-process core client; the only
  channel over which decrypted stored secrets travel. Opens a WebSocket to
  `ws://127.0.0.1:<port>` with the `collie-<token>` subprotocol, sends the
  secret-bearing frames with request/response ids, closes. A failed push
  degrades to "re-enter keys in Settings" — never a boot crash.
- `collie-ui/src/main/python.ts` — `onCoreReady()` listener fired on the
  first `ready` of each core spawn (re-armed on respawn), so every new core
  process gets exactly one secret push.
- `collie-ui/src/main/secrets.ts` — `loadSecrets()` stays one-shot per
  spawn (`resetSecretsConsumption()` at spawn); `secureStorageAvailable()`
  also rejects Linux `basic_text` (see below).
- `collie-ui/src/main/index.ts` — `collie:load-secrets` handler removed;
  replaced by `collie:stored-secret-count` (a count for boot decisions).
- `collie-ui/src/preload/index.ts` + `App.tsx` — bridge/UI updated; the
  renderer's boot probe uses the count only.

## Why the renderer still talks to the core directly

The renderer keeps its own WebSocket to the core, authenticated with the
per-boot bearer token (`collie:core-state` → port+token). This is
intentional:

- The token is **ephemeral** (random per launch), not a long-lived secret.
- Its purpose is **localhost confinement against other local processes** —
  the renderer is already the full-featured UI client and can issue
  everything the UI can.
- Moving the renderer behind a main-process proxy would change every chat
  and streaming path for no additional security (a compromised renderer can
  already click "approve"); scoping the token doesn't reduce what a
  compromised renderer can do either.

Defense-in-depth stays in place: strict CSP, no remote content,
trusted-sender IPC guards, and now **no stored secrets in renderer memory
at all**.

## User-entered keys

Keys the user types fresh in Settings still travel
renderer → main (`saveSecret`, encrypted at rest) and are also handed to
the core. That is user input the user typed in the UI — unavoidable and not
a stored-secret exposure.

## Related hardening (PR #54)

- Linux `basic_text` backend is rejected in `secureStorageAvailable()`
  (`getSelectedStorageBackend()` check): without a keyring daemon Electron
  "encrypts" with a hardcoded password, which is no protection. Secrets and
  the account session refuse to persist behind it, with a clear keyring
  hint in the error.
- Connector OAuth tokens stay Windows-only (`CredentialStore` is
  DPAPI-only); OAuth connectors surface as coming_soon on macOS/Linux.
