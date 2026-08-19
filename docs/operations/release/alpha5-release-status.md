# Alpha 0.1.0-alpha.5 — release status

**Status (2026-08-19):** all fixes merged (desktop + website), website
re-deployed from `main` and verified live, desktop release gates green.
Remaining: the tag and the owner's Windows acceptance pass.

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
| #54 `fix/storage-hardening` | Linux `basic_text` rejected unless a real keyring backend is selected; OAuth connectors gated off Windows (`coming_soon` on macOS/Linux); `smoke:packaged-core` auto-detects unpacked resources per OS + release smoke runs on all 3 OSes | merged `0a08e43`; branch pre-check: 688 core tests, 21/21 UI tests, ruff + typecheck clean |

## Website (heycollie.com)

- Both fix branches (`fix/mfa-guard-and-waitlist-hardening`,
  `fix/deps-audit-upgrades`) are **merged to `collie-webiste` main**
  (`1087e52`) and **re-deployed from main** (worker `6746f19f`) on
  2026-08-19. `deploy/live-fixes` is redundant (main tree == live tree).
- Post-merge verification: all 6 pages 200 on **both** hosts
  (heycollie.com + workers.dev), waitlist probe healthy
  (`patrickfuchs@live.at` → ok, no duplicate), `/account` serving.
- Live **Playwright MFA e2e passed** 2026-08-18 against the same tree
  (password sign-in with a verified TOTP factor → 2FA challenge → dashboard
  at AAL2) — still the live build.
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

1. ~~Merge PR #54~~ — **done** (`0a08e43`).
2. ~~Merge the two `collie-webiste` PRs + re-deploy from main~~ — **done**
   (`1087e52`, worker `6746f19f`); sweep green on both hosts.
3. ~~Full release gates on desktop main~~ — **done 2026-08-19** (main
   `0a08e43` + docs branch): ruff check + format ✅ · core pytest
   3570 passed / 1 known load-flake (`test_watchdog_exits_when_parent_is_killed`,
   passes in isolation + at file level) / 1 skipped / 5 deselected ·
   coverage 76.0% (floor 42) · UI typecheck ✅ · vitest 352/352 ✅ ·
   packaged-core smoke ✅ (Linux). Windows-specific tests verified on the
   `collie-core-windows` / `collie-ui-windows` CI jobs.
4. **Tag `v0.1.0-alpha.5`** → `release.yml` runs qualify → builds Windows
   NSIS x64, macOS arm64 dmg/zip, Linux AppImage x64 → **draft GitHub
   Release** with combined `SHA256SUMS.txt`.
5. **Owner's Windows acceptance pass** on the installer = final gate.

## Known items (tracked, not blocking)

- **`npm audit` (desktop, 4 advisories — pre-existing, unchanged since
  alpha.4):** js-yaml 4.3.0 (high, runtime via `electron-updater`; no fixed
  4.x — fix needs an override to 5.x with a compat check), nanoid 3.3.16
  (high, dev chain), postcss 8.5.19 + undici 6.27.0 (moderate, dev chain).
  Update feed is maintainer-controlled → low practical exposure. Queued as a
  `fix/deps-audit-hardening` PR after the Windows pass (CI has no audit
  gate; local `npm audit fix` blocked by npm 12 EALLOWREMOTE on this VM).
- **macOS x64/universal2** — per-arch staged Python follow-up, documented in
  `release.yml`.
