/**
 * Public Supabase client configuration for the Collie account flow
 * (docs/engineering/architecture/account-system-spec.md §6).
 *
 * These values are PUBLIC client config (the anon key is designed to be
 * shipped in clients) — never secrets. They are wired at build time via
 * `COLLIE_SUPABASE_URL` / `COLLIE_SUPABASE_ANON_KEY` (set by the launcher or
 * baked into the build), so an unconfigured build degrades to empty strings
 * and the sign-in flow reports "not configured" instead of failing oddly.
 */
export const SUPABASE_URL = (process.env.COLLIE_SUPABASE_URL ?? '').trim()
export const SUPABASE_ANON_KEY = (process.env.COLLIE_SUPABASE_ANON_KEY ?? '').trim()
