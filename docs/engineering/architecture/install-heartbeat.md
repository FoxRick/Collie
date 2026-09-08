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

The presence request contains only that ID, version, and platform. It sends the public
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

## Weekly product metrics

**Status:** implemented; requires the separate
`20260907063336_weekly_product_metrics.sql` migration before the desktop release.

The migration preserves each successful heartbeat as one row per UTC day and
install in `install_activity_daily`. It works with the existing presence RPC,
including older heartbeat-capable builds. It does not infer historical daily
activity from `first_seen`/`last_seen`. Active means observed running, including
idle tray time; it does not mean the person interacted with Collie.

The packaged desktop sets `COLLIE_PRODUCT_METRICS=1` on its core process.
SQLite schema V15 keeps cumulative daily counters, incremented atomically only
when a new telemetry event is inserted. Completion updates do not increment
them. Collection uses the existing asynchronous recorder, so a full recorder
queue, crashes before persistence, or storage failures can undercount.
Development launches explicitly disable collection; standalone headless
benchmarks are outside this reporting population.

The four report rows have these definitions:

| Metric | Definition |
| --- | --- |
| Distinct observed active installations | Distinct random install IDs with a successful presence ping during the week, deduplicated across days. |
| Agent runs started | Recorded top-level agent run starts, including chat, plan, routine, cron, and automation; excludes subagent runs. A follow-up message that starts another run counts again. |
| Interactive agent runs started | Chat and plan run starts, including messenger chats; excludes routine, cron, and automation runs. This is Collie's counterpart to excluding server mode. |
| Recorded tool calls | New local tool records, including failed/blocked attempts and subagent tools; completion updates do not count twice. |

On core readiness and every four minutes, Electron requests
`get_product_metrics` and sends a separate `record_install_metrics` RPC using
the existing public key and install ID. A separate in-flight guard keeps core
failures or slow metrics uploads from blocking presence. The payload contains
only a random counter-source UUID, UTC dates, and three numeric counts.
Electron constructs an allowlisted payload; no conversation/session identifiers,
tool names, parameters, results, provider information, or content are uploaded.
Account settings disclose these counters alongside presence.

The server stores the maximum received value for each counter per
`(day, install_id, source_id)`. Retries, response loss, and out-of-order delivery
cannot double-count or reduce accepted totals. Local counters persist across
restarts and raw telemetry deletion. Clearing all local data deletes the
counters and rotates their source UUID, allowing fresh counts that day without
colliding with previous maxima. These installation-specific counters and their
source UUID are excluded from account backup/export, so restoring a backup
does not replay another installation's counters.

Only today and the preceding 34 UTC dates are buffered/uploaded. Older local
counter rows are pruned when the desktop reads the snapshot. Counts retained
offline can arrive on a subsequent launch, changing a past report; longer
offline gaps are lost. Server presence days use server time; metric days use
the client's event time, and future/expired dates are rejected. Clock skew can
therefore undercount. Buffered run counts do not fabricate historical online
presence. There is no scheduled server deletion in this migration: daily
aggregates remain available for historical reports.

Tables have RLS enabled and no client privileges. The anonymous write-only RPC
uses the existing unexposed `install_presence_private` schema, explicit grants,
an empty search path, and bounded dates, batches, and counters. Anonymous
install/source IDs remain forgeable; all counts are operational estimates.
The owner-only `weekly_product_metrics` RPC runs as invoker and aggregates in
Postgres, avoiding API page limits and downloads of per-install records.

### Rollout and reporting

1. Apply the heartbeat migration if absent, then the weekly metrics migration
   to Collie's release-configured Supabase project.
2. Run `supabase/tests/install_heartbeat.sql` and
   `supabase/tests/weekly_product_metrics.sql` on an isolated test database.
   These scripts temporarily alter test rows and roll back their fixtures.
3. Verify the two public write RPCs using the release publishable key and the
   owner report using owner credentials; then release the desktop build.
4. Run `python tools/weekly_product_metrics.py`, using the same owner-only
   environment or `~/.collie-supabase.env` as `device_liveness.py`.
   `--week-start YYYY-MM-DD` selects an earlier complete Sunday-starting week.

The report compares complete Sunday–Saturday UTC weeks and excludes the current
partial week. It displays collection start time and suppresses percentage
growth when the comparison overlaps migration rollout or has a zero baseline.
Run/tool coverage additionally depends on adoption of the new desktop build;
even after two full weeks, growth can reflect adoption rather than usage.
This feature PR does not apply production migrations or release a build.
