# Shared session authority and recovery

**Status:** implemented behind disabled release flags; two-computer and Slack pilot gates remain open.

The [implementation plan](../../product/features/shared-sessions-implementation-plan.md)
defines the acceptance contract. The desktop remains usable without an account.
Opting into a shared session publishes selected content to a managed coordination
service; this is separate from personal cloud snapshot backup.

## Ownership

- `supabase/migrations/` owns collaboration authority, membership, ordered events,
  quota reservations, device leases, archive receipts, and Slack coordination.
- `supabase/functions/` owns signed Slack intake, encrypted installation credentials,
  OAuth callbacks, bounded delivery workers, and object purge orchestration.
- `collie-core/collie_core/collaboration/` owns local outbox and materialization,
  stable archives, manifest verification, and requester execution isolation.
- Electron main owns authenticated transport and protected account credentials.
  Preload exposes specific product operations. Renderer input is never evidence
  of authenticated account identity, run ownership, or a verified local archive.

## Authority and publication

Organization membership permits directory discovery. Only accepted session
membership permits transcript access. Author identity comes from authentication;
membership, expected revision, quota, and lifecycle checks run on every command.
An immutable event ID can be retried with the same payload. Reusing an ID with a
different payload fails. Polling/realtime wakeups initiate durable cursor reads;
they never replace ordered history.

A request belongs to its author. Session ownership grants no use of another
person's model, tools, approvals, or credentials. A device claim carries a fence,
audience revision, and published context cutoff. Private computation and permission
prompts remain local. Reading private data and publishing its result are separate
decisions. Offline execution is shown as waiting, never delegated to a participant
whose credentials happen to be available.

## Feature coverage and validation boundaries

Shared execution uses the requester's installed model, credentials, permissions,
and tools. The desktop starts private-result work and requires separate review
before publishing its result. Shared-safe context is also available in core; it
uses canonical history and excludes personal context sources.

| Capability | Implemented path | Validation boundary |
|---|---|---|
| Models and payer | Requester-owned execution, credential-owner context, server-derived requester identity. | Hosted provider/device acceptance remains required. |
| Tools, connectors, files and skills | Private-result execution retains the requester's registry and permission broker; tool traces are not published. | Individual provider/connector credentials are not exercised by synthetic tests. |
| Memory and context | Shared-safe prompts omit private bootstrap/history/memory; private-result prompts may use requester context. Automatic shared-history ingestion is excluded. | Local sentinel tests cover context separation; two-device acceptance remains required. |
| Subagents and continuations | Requester/audience metadata propagates to child execution. Children are cancelled and drained when the parent is fenced. | Local context and regression tests; hosted interruption acceptance remains required. |
| Plans and approvals | Existing local approval UI remains requester-private; core validates identity, action and local lease deadline. | Actual device permission and approval flows remain pilot gates. |
| Routines | Optional creator-bound delivery stores the approved audience revision, queues immutable result events, and publishes through authenticated quota checks. The UI separately authorizes future result publication. | Offline/revocation delivery behavior is covered locally; real scheduled delivery remains a pilot gate. |
| Versioned shared files | Native explicit upload, reserved byte limits, server-verified hashes, private member downloads, and manifest inclusion. | Local SQL/Edge tests; hosted Storage acceptance remains required. |
| Stop, retry and result review | Leased requester pickup, cancellation, durable outbox retries, encrypted recoverable drafts, and explicit publication. | Local tests and static review; two-computer lease/reconnect acceptance remains required. |
| Archives | Atomic durable save, read-back/hash verification, import/export, receipt-gated purge, and retryable object cleanup. | Local corruption/SQL/worker checks; hosted purge acceptance remains required. |

A routine remains owned by its creator. Shared delivery does not grant other
participants access to its tools or credentials. Audience changes reject delivery
until reauthorized; delivery status is separate from private routine execution.

## Archive invariants

Archival progresses through `active`, `closing`, `archive_pending`, `purging`, and
`archived_local`. Closing rejects new submissions and waits for accepted work.
The final manifest freezes event order, membership revision, eligible recipients,
and file hashes. A durable atomic write followed by reopening and verification
precedes an account/device receipt. An evictable cache, browser download click, or
JSON parse is not sufficient evidence.

Every remaining eligible participant must have a verified copy. Offline people
block purge. Membership changes use audited revocation and invalidate the old
manifest revision; quota pressure never removes people automatically. Purge checks
the manifest and receipts again under a lease, checkpoints object deletion, and
retains a minimal tombstone. Old clients cannot resurrect a purged session.

Local archives survive sign-out and cache eviction. Losing all copies after purge
cannot be repaired from Supabase. Recovery uses a validated export or a copy from
an authorized participant. Continuing starts a new session and publishes only a
reviewed selection; it never silently uploads the archive. Slack retains its own
messages independently of Collie's archive and purge lifecycle.

## Release gates and evidence

Enable shared text, shared execution, archive purge, and Slack independently per
organization/user. Rollback pauses new work while retaining read, export, archive,
and recovery. It does not drop tables or relax authorization.

Before enabling a gate, record:

1. Actual project database, object, egress, and connection use; isolated synthetic
   40/200/1,000-message measurements including indexes, edits, and UTF-8 payloads.
2. Isolated hosted RLS/function tests across organizations, revoked participants,
   idempotent retry, atomic quota exhaustion, and run fencing.
3. Two-computer offline/reconnect, requester permissions, and private-context
   sentinel acceptance, including a sleeping participant during archive purge.
4. Corrupt/missing archive objects, interrupted durable save, stale manifests,
   object-deletion retry, and purge-worker crash recovery.
5. Real Slack test-workspace acceptance: two linked authors, selected channel
   audience, duplicate events, bot echo, uninstall, rate limits, uncertain outbound
   delivery, and an explicit continuation after archive.

Local test execution does not establish two-computer acceptance. Hosted schema
validation uses the existing Collie project inside a transaction that rolls back
all schema and fixture changes. No deployment is implied.

### Implementation evidence snapshot

The isolated SQL harness, desktop build, and typecheck pass. The full Python
suite passed 3,707 tests with six skipped; the full desktop suite passed 510
tests with one skipped. Eight Edge tests pass. Hosted rollback validation passed
real authorization/RLS, quota, consent, notifications, requester leases, closing
completion, and receipt-gated archive checks. A real hosted manifest containing
the accepted closing completion also passed Windows durable save, readback,
export, and import. The rollback-only
capacity fixture measured approximately 104 KiB, 312 KiB, and 1.58 MiB of added
physical event-table/index storage for 40, 200, and 1,000 messages with retained
revisions and UTF-8 content. This is a synthetic event-table calibration, not a
full production load model. Two-computer and real Slack workspace acceptance
remain open; these results do not open any release gate.

Reproduce the rollback-only hosted check with
`node tools/build_hosted_collaboration_validation.mjs`, then run the generated
`.tmp/hosted-collaboration-validation.sql` with the linked Supabase CLI's
`db query --file` command. Save its JSON output and pass the file to
`python tools/verify_hosted_archive.py` to validate the desktop round trip.
The hosted script must retain its final `ROLLBACK`; it is not a deployment script.

## Edge function configuration

Deploy the SQL migration separately from desktop/function code. Configure
`SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY` only in the
appropriate server environment. Slack functions additionally require
`SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, `SLACK_SIGNING_SECRET`,
`SLACK_REDIRECT_URI`, and `SLACK_TOKEN_ENCRYPTION_KEY` (base64-encoded 32 random
bytes). Worker invocation requires `COLLABORATION_WORKER_SECRET`. Never include
these secrets in renderer configuration, logs, screenshots, or source control.

Slack signature verification uses the exact raw request body and a five-minute
timestamp window. OAuth state is random, hashed at rest, short-lived, and consumed
once. Bot tokens use AES-GCM with the Slack team ID as authenticated associated
data. Scheduled workers acknowledge only persisted work and use bounded batches.
Unknown outbound outcomes require read-only reconciliation rather than a blind
repeat of the external write.

After deploying the functions, configure the two named Vault entries and run
[`install_collaboration_workers.sql`](../../../tools/install_collaboration_workers.sql)
as the project administrator. It schedules bounded archive/Slack workers and
six-hour physical capacity snapshots without embedding credentials in job text.
The desktop reconciles periodically while running, including in the tray, and
automatically verifies and acknowledges eligible pending archives. This deployment
setup has not been executed against the hosted project. The scheduler follows
[Supabase's scheduled Edge function pattern](https://supabase.com/docs/guides/functions/schedule-functions).
