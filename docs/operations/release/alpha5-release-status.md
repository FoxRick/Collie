# Alpha 0.1.0-alpha.5 — release status

**Status (2026-08-18):** fixes shipped, release not yet tagged. The remaining
work is a merge, a re-deploy, the tag, and the owner's Windows acceptance
pass. See the handoff section at the bottom for the exact sequence.

This document is the "where we stand" record for the alpha.5 release. It
summarizes the pre-release security review, what was verified and fixed, and
what is still open. Source of truth for behavior is the code; this page is
the index.

---

## The review, challenged

A pre-release review (Codex, 2026-08-18, commit `885b38e`) produced a
no-go verdict with release blockers. Every claim was verified against the
code and, where claims were behavioral, against the live services. Verdicts:

| Claim | Verdict | Evidence / fix |
| --- | --- | --- |
| Desktop Supabase config empty → sign-in broken in installers | ✅ Real | `account-config.ts` read empty env; fixed by baking public client values via `define` in `electron.vite.config.ts` + env in `release.yml` (PR #55, verified in `out/main/index.js`) |
| Website 2FA bypass after password sign-in | ✅ Real — **confirmed live** | Password grant returns an AAL1 session for verified-TOTP users; the page reloaded past the challenge. Fixed in `collie-webiste` (AAL guard in `AccountPageClient.tsx`), proven by Playwright e2e against heycollie.com |
| Ruff I001 blocks release qualify | ✅ Real | `test_reminders.py:267` import order + 5 files of pre-existing format drift; fixed (PR #52) |
| `sk-` key auto-detect ships keys to candidate providers | ✅ Real | `detect_provider_for_key` probed each candidate with the full key; now ambiguous prefixes return candidates without any probe, UI asks the user to pick (PR #55) |
| Renderer receives decrypted secrets + core bearer token | ✅ Real — understated | Renderer actively pulled all stored secrets (`collie:load-secrets`); fixed by main-side push (`core-client.ts`, PR #53) — see [renderer-trust-boundary.md](../../engineering/security/renderer-trust-boundary.md) |
| macOS/Linux credential storage not release-ready | ⚠️ Partially | Linux `basic_text` accepted (real) — now rejected (PR #54). "Plaintext fallback" was wrong: no plaintext fallback exists; macOS uses Keychain; non-Windows connector OAuth *fails* (DPAPI-only) → connectors gated off-platform (PR #54) |
| Website audit 21 advisories (16 high) | ✅ Real | Runtime advisories cleared via dependency upgrades (next/react-server-dom-webpack/vite/wrangler…); 2 remaining high are build-chain (`vinext` → `image-size`), deferred to vinext 1.0 beta |
| Asset provenance incomplete | ✅ Real (self-declared) | Register completed by owner confirmation + SHA-256 manifest (PR #56) |
| Sessions never refresh | ✅ Real | Access tokens live ~1h; `getAccountState()` now silently refreshes via the `refresh_token` grant (PR #55) |
| Waitlist RLS `with check (true)` | ✅ Real, low severity | Policy now mirrors the Worker's insert shape (email/source/status constraints); applied live, probed from outside (PR in `collie-webiste`) |
| Magic-link "abandons PKCE" | ⚠️ Framing unfair | Deliberate, documented, e2e-verified trade-off (`@supabase/ssr` force-PKCE pins links to one browser; challenge-less REST makes links work cross-device) |
| "No successful status history on main" | ❌ Wrong | 885b38e had 1 green + 3 red checks; the review missed that main was red (3/4 jobs) — including a Windows trusted-ID test failure it never mentioned |
| macOS arm64-only / unsigned | ✅ Facts, not findings | Documented alpha posture (release.yml header, electron-builder.yml) |

**Net verdict:** "no-go" was right, partly for the wrong reasons. All
confirmed blockers are fixed and tested.

---

## What shipped (desktop, all merged to main)

| PR | Fix | Proof |
| --- | --- | --- |
| #52 `fix/alpha5-qualify-unblock` | ruff lint + format drift (5 files), Windows `things-files` trusted-ID tests, README copy | CI green on all 4 jobs |
| #53 `fix/renderer-secret-boundary` | decrypted secrets never reach the renderer; main pushes them to the core once per spawn | 349 UI tests (incl. 5 new core-client tests) |
| #55 `fix/alpha5-account-signin` | Supabase client config baked into builds; session auto-refresh; ambiguous API keys never probed | values verified in bundle; 345 UI tests + 12 validation tests |
| #56 `docs/asset-provenance-clear` | provenance records complete; installer blocker cleared | `asset-provenance.sha256` (67 files) |

**Open:** PR #54 `fix/storage-hardening` (Linux `basic_text` rejection,
OAuth connectors gated off macOS/Linux, 3-OS packaged smoke test) — checks
completed, merge pending.

## Website (heycollie.com)

- `fix/mfa-guard-and-waitlist-hardening` and `fix/deps-audit-upgrades` are
  **deployed live** (integration branch `deploy/live-fixes`, worker version
  46ba2a92) but **not merged to `collie-webiste` main** (still `72e1bd9`).
- Live verification (2026-08-18): all pages 200 on both hosts, waitlist API
  healthy, and a **live Playwright MFA e2e** passed (password sign-in with a
  verified TOTP factor → 2FA challenge → dashboard at AAL2).
- The waitlist RLS hardening migration (`db/20260818-waitlist-hardening.sql`)
  is applied to the live Supabase project.

## Decisions locked for alpha.5

- **Windows + Linux installers + macOS arm64 unsigned developer preview**
  (owner's launch set). x64/Intel macOS and signing/notarization are
  follow-ups, documented in `release.yml`.
- **OAuth connectors are Windows-only for alpha** — their token store is
  DPAPI-only (`CredentialStore`); on macOS/Linux they surface as
  coming_soon.
- **Provider/messenger logos are nominative trademark use** — recorded in
  the asset register, not redistributable standalone.
- **Account sign-in ships in alpha.5** (Supabase identity; chats/files stay
  local; README updated accordingly).

## Remaining sequence (handoff for next session)

1. **Merge PR #54** (`fix/storage-hardening`) — checks are green.
2. **Merge the two `collie-webiste` PRs** (compare URLs were handed to the
   owner) so website main == live; then **re-deploy from main** and re-run
   the sweep (all pages 200, waitlist probe, title fingerprint).
3. **Full release gates** on desktop main: `ruff check` + `ruff format
   --check`, full core pytest, UI typecheck + vitest (watch the Windows
   job), `npm audit` (desktop), packaged smoke.
4. **Tag `v0.1.0-alpha.5`** → `release.yml` runs qualify → builds Windows
   NSIS x64, macOS arm64 dmg/zip, Linux AppImage x64 → **draft GitHub
   Release** with combined `SHA256SUMS.txt`.
5. **Owner's Windows acceptance pass** on the installer = final gate.
