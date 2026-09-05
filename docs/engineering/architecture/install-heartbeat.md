# Install heartbeat

**Status:** implemented; deploy the migration before releasing the desktop change.

Packaged Collie sends a presence ping on launch and every four minutes while
running, independently of sign-in, cloud backup, onboarding, and the Python
core. Development and unconfigured builds do not ping. Failed requests time
out after 15 seconds and retry on the next interval without blocking the UI.

`collie-ui/src/main/install-heartbeat.ts` persists a random UUID in
`userData/install-id` before sending anything. It survives app upgrades,
sign-in/out, and scheduler restarts. Clearing userData creates a new identity.
If it cannot persist the ID, it does not send a temporary identity. The ID is
separate from cloud backup device/account identities.

The request contains only that ID, version, and platform. It sends the public
API key, never an account access token, device name, email, or user content.
Account settings disclose this default behavior; turning backup off does not
disable presence. Network infrastructure still sees the originating IP address;
the presence table does not store it.

## Storage and access

`record_install_heartbeat` is an intentionally unauthenticated RPC. Its
invoker wrapper calls a narrow definer function in the unexposed `install_presence_private`
schema, with an empty search path and explicit grants. It can only upsert
presence fields in `install_heartbeats`; it cannot read presence or access
account backups. RLS is enabled, with no client table privileges/policies.
The service role can read reports. Timestamps are assigned by the server;
first_seen is preserved on repeat pings. Version/platform inputs are bounded.

Anonymous clients can fabricate IDs, so counts are operational estimates, not
verified people or billing/security signals. Protect the endpoint with gateway
rate limits if abuse occurs. Never expose the install_presence_private schema through the Data API.

## Counting and rollout

1. Apply `supabase/migrations/20260905060526_install_heartbeat.sql` to the same
   Supabase project used by the release's public account configuration.
2. Run `supabase/tests/install_heartbeat.sql` against a test database with the
   migration applied. It rolls back its fixtures and checks anonymous writes,
   deduplication, timestamps, validation, and denied direct access.
3. Verify a POST to `/rest/v1/rpc/record_install_heartbeat` with the release
   publishable key in `apikey`, and no Authorization header. Confirm the row
   through an owner connection. Do not use a publishable key as a Bearer JWT.
4. Release the desktop app. Existing installations only start reporting once
   they update and launch; no historical counts can be recovered from this fix.
5. Run `python tools/device_liveness.py` with owner credentials as documented
   in that tool. It pages through all install rows, including server-limited
   short pages, without downloading backups or account information.

Total = launched install IDs ever received; live = seen within ten minutes;
active today = seen within 24 hours. One person can have several installs.
An installer that is downloaded but never launched cannot report, and offline
or blocked requests are absent until a later successful ping. Legacy snapshot
presence is not added to the new totals because that would double count.
