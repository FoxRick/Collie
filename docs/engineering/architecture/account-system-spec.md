# Account System Spec — Collie v1

> Status: **spec** (2026-08-17) · Owner: Rick · Scope: website + Supabase backend + Electron app + email
> Decision history: supersedes the 08-11 "no-account v1" decision (waitlist = email-only). Accounts now exist, but **local-first stays the product story** — the account never becomes a data hostage.

## 1. Goals / Non-goals

**Goals**
- Sign in on the website with **email+password** and **magic link** (both, one identity).
- Account tab on the website: download Collie, change email, set password, enable 2FA (TOTP), delete account, see early-access status.
- App sign-in via the system browser, "and get back to the app" (Codex/Claude pattern) — **no stack change**, Electron stays.
- Transactional emails via **Resend** (hello@heycollie.com domain, already live with MX).
- Download stays **open (not gated)** but **bot-safe** so bots can't burn bandwidth/limits.

**Non-goals (v1)**
- No cross-device sync of conversations/settings — nothing user-data syncs across accounts.
- No download gating / entitlement enforcement.
- No subscriptions, no billing.
- No mobile app, no CLI device flow (fallback later if a CLI appears).

## 2. Current state

| Piece | Today |
|---|---|
| Website | Cloudflare Worker `collie-website` (vinext), `/home/rick/collie-webiste` |
| Waitlist | `POST /api/early-access` → Supabase table `early_access` (email UNIQUE, source, created_at), per-IP rate-limit via KV (`early-access:<ip>`) |
| Supabase | Already connected (anon key in Worker env) |
| App | Electron desktop + Python `collie-core`, local SQLite settings, no auth, no network identity |
| Emails | **None sent today** — the waitlist API only inserts a row |

## 3. Auth model

Supabase Auth on the **existing project** (one backend serves web + app).

**When is which used:**
| Method | Used when | Notes |
|---|---|---|
| **Magic link** | Primary path — app sign-in flow, and "sign in without password" on the web | Zero password friction, ideal for non-coders. One email click |
| **Email + password** | Users who want it; required for **2FA** | Password is the second factor's anchor. Set password from account tab |

- Both produce the same `auth.users` identity keyed by email. Password users can still use magic links.
- 2FA (TOTP, authenticator app) is available **only on password accounts** — a magic-link-only account has no password to protect.
- Web sessions: JWT in httpOnly cookies (`@supabase/ssr` pattern, works on the Worker).
- App sessions: tokens stored via Electron `safeStorage` (OS keychain-backed).

## 4. Data model

```
auth.users            (Supabase managed — email, password hash, MFA, sessions)
early_access          (exists — add column)
  id, email UNIQUE, source, created_at
  + status    text  default 'waiting'   -- 'waiting' | 'granted'
  + granted_at timestamp null
```

- **RLS on `early_access`**: anon → INSERT only (as today, rate-limited); authenticated → SELECT only own row (`email = auth.jwt()->>'email'`); no anon SELECT.
- **`delete_my_account()`** — `SECURITY DEFINER` SQL function: deletes `early_access` row for `auth.uid()` then deletes the user from `auth.users` where `id = auth.uid()`. Called from the account tab with the user's JWT. **No service-role key ever touches the Worker.**
- Entitlement lookup = `early_access` row by auth email (status shows "you're on the list" / "access granted" in the account tab).

## 5. Website

### Routes (all inside the existing Worker)
| Route | Auth | Purpose |
|---|---|---|
| `/` | public | Landing (unchanged) |
| `/download` | public | Open download page — bot-safe link reveal (see §7) |
| `/api/early-access` | public | Waitlist (unchanged) + fires welcome email |
| `/account` | any | Single route: renders sign-in form if no session, dashboard if authed |
| `/auth/callback` | public | PKCE/magic-link redirect target — exchanges code, sets cookie, bounces to `/account` |

### Account tab contents
| Feature | Supabase mechanism |
|---|---|
| Download Collie | Bot-safe link (same as `/download`), plus platform hint |
| Early-access status | `early_access.status` by auth email |
| Change email | `auth.updateUser({ email })` → confirmation email to new address, re-verify |
| Set password | `auth.updateUser({ password })` (enables 2FA path) |
| Enable 2FA | `auth.mfa.enroll` (TOTP) → challenge → `auth.mfa.challenge/verify`; disable = unenroll after verify |
| Delete account | RPC `delete_my_account()` + confirm dialog (destructive, typed confirmation) |
| Sign out | `auth.signOut()` |

Sign-in page (on `/account` when logged out): two tabs — **Email + password** and **Magic link**. No Google OAuth in v1 (keep surface small; add later if the data says so).

## 6. App ↔ web sign-in (Electron, localhost callback)

Claude-Desktop pattern, zero typing for the user:

1. Settings → **Sign in** → app generates PKCE verifier, starts a local HTTP listener on `127.0.0.1:<random-port>`.
2. App opens the system browser (`shell.openExternal`) to the Supabase authorize URL with `redirect_to=http://127.0.0.1:<port>/callback` (plus `collie://auth/callback` custom protocol as fallback on macOS/Windows).
3. User signs in with password **or** magic link in the browser.
4. Browser redirects to `127.0.0.1:<port>/callback?code=...` → app exchanges code + verifier → session tokens.
5. App stores tokens with `safeStorage`, Settings now shows signed-in state (email, Sign out). No data leaves the machine — the session is just identity.
6. Timeout after ~5 min → "Sign-in didn't finish, try again."

**App-side changes (Windows machine, later phase):** settings UI (sign in/out), localhost listener + PKCE, secure token storage, session refresh. No app-stack change — **Tauri is not on the table**; the pattern is identical either way, so this spec is stack-neutral.

## 7. Bot-safe download (open, not gated)

- Installer files on **Cloudflare R2 (private bucket)**; never a public bucket listing or guessable static URL.
- Worker route `/api/download-link` (called from `/download` page): per-IP rate limit (KV counter, same pattern as early-access) + optional invisible **Cloudflare Turnstile** → returns a short-lived signed redirect to R2.
- Cost: a human clicks once per visit; bots burn a KV increment, not bandwidth. Deterministic humans (CI, etc.) unaffected.
- Fallback if R2 setup is unwanted: GitHub Releases URLs, rate-limited at the page level only (accepting the leak).

## 8. Emails (Resend)

Provider: **Resend**, domain `heycollie.com`, senders: `hello@heycollie.com` (human-ish) / `no-reply@heycollie.com` (auth). Add Resend SPF + DKIM records to the zone (MX already exists).

| Email | Trigger | Via | Priority |
|---|---|---|---|
| Thanks for signing up | `POST /api/early-access` success | Worker → Resend API | **Missing today — add** |
| You're in — download Collie | `early_access.status` → `granted` (admin action/script) | Worker/edge fn → Resend | Add (launch tooling) |
| Magic link | Auth request | Supabase template (SMTP → Resend) | Configure + brand |
| Password reset | "Forgot password" | Supabase template | Configure + brand |
| Email change confirmation | Change email | Supabase template | Configure + brand |
| Security alert (2FA disabled, new sign-in) | Account events | Supabase template | Optional |

Worker env additions: `RESEND_API_KEY`, `TURNSTILE_SECRET` (plus `TURNSTILE_SITE_KEY` public).

## 9. Security checklist

- [ ] Service-role key stays off the Worker (anon key only, as today)
- [ ] RLS on `early_access` (anon INSERT, authed SELECT-own; no anon SELECT)
- [ ] `delete_my_account()` SECURITY DEFINER, invoked with user JWT only
- [ ] httpOnly + Secure + SameSite=Lax cookies; PKCE everywhere (no implicit flow)
- [ ] Rate limits: early-access (exists), download link, Supabase auth has built-in limits + optional Turnstile on sign-in
- [ ] Email deliverability: SPF/DKIM via Resend; test to Gmail/Outlook
- [ ] Supabase Auth "Confirm email" requirement decision: magic-link-only accounts need no confirmation; password signup should confirm email before first sign-in (reduce spam accounts)

## 10. Implementation phases

**Phase 1 — Supabase config (backend, ~1 session)**
Enable Auth (email+password + magic link), add `early_access.status/granted_at` migration, RLS policies, `delete_my_account()` RPC, Resend SMTP on auth templates, Turnstile site keys, create R2 bucket + signed-URL helper.

**Phase 2 — Website account (this repo, ~2–3 sessions)**
`/account` (sign-in tabs + dashboard), `/auth/callback`, `@supabase/ssr` session wiring, change email / set password / 2FA / delete account UI, download section + `/api/download-link` rate limit + Turnstile, welcome email hook in early-access API. Follow collie-website workflow: branch → lint/test/build → show locally → PR → merge → deploy → verify both hosts.

**Phase 3 — App sign-in (Windows machine)**
Electron localhost-callback PKCE flow, Settings UI, safeStorage token storage, sign-out. Matches this spec's §6; implementation happens in the main repo on Windows (VM clone can't run the app).

**Phase 4 — Launch emails**
Access-granted tooling (mark granted → email), template branding, security alerts, deliverability test.

**Phase 5 — Verification matrix**
Password sign-in, magic-link sign-in, 2FA enroll/login/disable, email change, password set/reset, delete account (user gone from auth + early_access), app flow both methods + timeout, download rate limit hit, welcome email lands in spam-free inbox.

## 11. Open items

1. Installer hosting: **R2 + worker redirect** (recommended) vs GitHub Releases — needs R2 bucket creation or decision.
2. Supabase project admin access on this VM: needed for Phase 1 (SQL + Auth config). Where are the project keys/access? (The Worker already has SUPABASE_URL + anon key in env.)
3. Turnstile: invisible vs checkbox on sign-in + download — pick when implementing (checkbox = zero-config UX cost).
4. Confirm-email requirement for password signups (recommend: on).
