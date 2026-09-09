# Shared sessions: implementation plan

**Status:** proposed implementation plan; no feature implementation in this PR.
**Date:** 2026-09-09
**Scope:** Python core, Electron/React desktop, managed Supabase coordination, then Slack.
**Source baseline:** `b54311aaae24d12f92832fcfc68167c8fe2c81c6`.

## Outcome and decisions

People in an organization can find each other with `@`, invite one another into a
conversation, and continue the same conversation from different computers. Only
published session content is shared. Accounts, credentials, personal memory,
permissions, and private execution remain individual. Personal use stays local
and account-optional; shared sessions require sign-in.

Use the existing Supabase account system as a managed mailbox, membership store,
and ordering authority. Do not deploy a dedicated AI server or expose localhost
IPC. Each request runs on its author's enrolled desktop using that person's
configured provider and permissions. A sleeping executor means waiting, never
execution on the creator's or another participant's account.

Start with in-app shared conversations; add Slack over the same records later.
Use cloud limits and automatic local archiving to bound storage. Archived content
is removed from Supabase only after verified local copies exist for all remaining
participants. This means storage cannot be reclaimed immediately if a participant
is offline; new admissions may need to pause. There is no silent expiry of unread
history to make a free tier appear unlimited.

This direction supersedes the no-account, Telegram-owner-execution defaults in
[the parked Share Collie proposal](share-collie.md). It does not activate that
proposal or change existing Telegram behavior. The present PR adds a plan only:
no migrations, runtime changes, credentials, deployments, or feature flags enabled.

## Existing code to preserve and extend

| Area | Current source | Planned responsibility |
|---|---|---|
| Runtime | `collie-core/collie_core/runtime.py` | Reuse composition and tools; introduce a requester-bound shared entry. |
| Local data | `collie-core/collie_core/db.py` | Keep personal tables; add sync outbox/cache and local archive records separately. |
| Model context | `collie-core/nanobot/agent/context.py` | Separate published-session inputs from personal bootstrap, memory, skills, and history. |
| Agent execution | `collie-core/nanobot/agent/loop.py`, `collie-core/collie_core/session_identity.py` | Preserve local locks; carry shared request and context identity through continuations. |
| Permissions | `collie-core/collie_core/permissions/` | Authorize actor, resource owner, action, and publication audience. |
| Memory | `collie-core/collie_core/memory/`, `tools/memory.py`, Gardener hooks | Prevent automatic ingestion of shared history into private memory. |
| Accounts | `collie-ui/src/main/account-auth.ts` | Reuse protected Supabase identity; add narrow authenticated collaboration commands. |
| Desktop transport | `collie-ui/src/renderer/src/lib/ipc.ts`, `src/preload/`, `src/main/` | Separate local/private frames from published shared frames. |
| Chat | `ChatScreen.tsx`, `Sidebar.tsx`, `MessageList.tsx`, `MessageBubble.tsx` under renderer | Add membership, authorship, sync state, storage, archive and requester status. |
| Messenger | `collie-core/collie_core/messengers/manager.py`, `nanobot/channels/slack.py` | New exact Slack-thread mapping; keep Telegram mirror compatibility. |

Current messages have no human author/workspace membership; the local IPC
broadcast is not a multi-user authorization mechanism. The current context builder
loads personal bootstrap files, memory and skills. Tools/profile stores include
process-level bindings: do not put several people's runtimes in one process.
Cloud snapshot backup excludes conversations and must remain a separate feature.

## Initial limits and retention contract

These are proposed pilot defaults, configurable and enforced by the backend.
Step 1 calibrates them against actual database usage before rollout. MiB values
below are binary application limits; provider quotas are measured independently.

| Limit | Pilot default | Behavior |
|---|---|---|
| Participants | 5 accepted or reserved invitations per session | Reject additional invitations with a clear explanation. |
| Concurrent cloud sessions | 5 per organization, including archive-pending sessions | Offer archive or continuation when capacity is available. |
| Conversation length | 200 published human/assistant messages | Reserve a response slot before accepting a request; then close new submissions and archive. |
| Shared text/event payload | 2 MiB per session | Warn/stop new requests at 1.5 MiB; remaining budget covers reserved bounded responses and finalization. |
| Organization payload | 10 MiB | Count messages, retained revisions/events, reservations and pending archives, not only visible text. |
| Project collaboration payload | Initially at most 100 MiB | Also enforce measured physical collaboration budget of at most 250 MB, reduced for existing use. |
| Total database thresholds | Warn at 350 MB; stop new sessions before 400 MB | Reserve space for accepted runs, archive receipts, accounts and other writes; use a lower threshold when growth requires it. |
| Inactivity archive | 14 days without new published content | Begin archive if no active run, pending approval or accepted outbound job. Warn participants in advance. |
| Shared files | Initially 5 MiB/file, 20 MiB/session, 100 MiB project-wide | Separate object-storage quota; reserve before upload; include files in archive verification. |

Quota reservations must be atomic across simultaneous submissions. Bound generated
public answers to their reserved allowance; an oversized draft stays private and
offers selected publication or a continuation. Never accept a run and then drop
its result due to a predictable quota failure. Count each cloud session once,
not once per member. Connection/egress limits need separate monitoring.

Every message is stored once in canonical shared history; do not persist full
transcript snapshots after each turn or every streamed token. Save only meaningful
public progress. Diagnostic/tool logs and private drafts remain local. An
organization allowance is a byte limit, not a guarantee that all five sessions
can be filled regardless of indexes or other project usage.

## Auto-archive state machine

`active -> closing -> archive_pending -> purging -> archived_local`

1. Trigger on inactivity, a session limit, or explicit Archive. In a transaction,
   stop new submissions, settle or safely cancel in-flight work, and freeze a
   final sequence, membership revision, and recipient set. Never purge a running
   turn or an unresolved publication. A continuation is a new linked session.
2. Produce a versioned archive manifest of published messages, authors, edits,
   deletion state, public run results and shared files. Include canonical digest,
   file hashes, byte lengths, final sequence and session ID. Personal memory,
   secrets, approvals' private payloads and private tool state are excluded.
3. Every remaining participant downloads to a stable app-managed local archive,
   distinct from an evictable cache. Verify hashes and final sequence, write
   durably, reopen successfully, then send an authenticated device/account receipt.
   An existing incomplete cache or an unverified UI acknowledgement is insufficient.
4. Require at least one verified local archive for each frozen eligible participant,
   including the owner. Offline participants leave the session archive-pending.
   Show who is still pending. Retries are idempotent. Revocation during archiving
   invalidates the manifest revision and recomputes eligibility through the normal
   audited membership flow; never auto-remove people to free storage.
5. Recheck receipts, manifest, no active jobs, membership and lease before claiming
   purge. Delete cloud content and session-exclusive file objects with an idempotent
   job. Do not delete referenced objects used by other sessions. Database and object
   deletion are not one transaction: record progress and retry either independently.
6. Retain a small access-controlled tombstone and minimal sync/archive metadata
   (session ID, final sequence/digest, archive revision/status, eligible identities,
   receipt evidence, operation timestamps) so stale clients cannot resurrect old
   content. Remove content-bearing titles, excerpts and file names. Account these
   records in the quota and define eventual cleanup separately.
7. Mark archived only after cloud content removal succeeds. Postgres deletion may
   not immediately shrink physical usage; normal vacuum can make space reusable.
   Do not trigger blocking `VACUUM FULL` from user requests or promise immediate
   provider-metric reductions. Backend admission waits for measured safe capacity.

Archived sessions remain readable locally but are not live shared chats. Starting
again creates a new session; optionally publish a reviewed summary or selected
messages under the new audience and quota. Never silently reupload the full archive.
On a new computer, recover from the user's archive export or an available authorized
participant; do not claim cloud recovery after purge. Warn before deleting a local
archive that may be the last copy. Verified receipt proves a successful save at that
time, not protection against future disk loss or a malicious client.

Slack's own history is outside Supabase: local archive/cloud purge does not delete
Slack messages. Require linked desktop participants for the first Slack pilot so
archive receipt requirements are satisfiable. Explain this in Slack-facing help.

## Step-by-step implementation batches

Each numbered batch should be a separate reviewable implementation PR, after this
plan is accepted. Migration PRs remain separate from runtime/UI changes. Steps
that expose shared execution cannot ship until the context/privacy gates pass.

### 1. Measure capacity and specify contracts

**Owners:** backend/core. Read the current project's database/object/realtime usage;
do not assume an empty 500 MB quota. In an isolated test database, load synthetic
40-, 200-, and 1,000-message histories, run edits/deletes, and measure tables,
indexes, retained events and physical growth. Test long text and multilingual UTF-8.
Establish a conservative bytes-to-physical-size factor and admission reserve.

Define versioned DTOs for Session, Member, MessageEvent, Run, QuotaReservation,
ArchiveManifest and ArchiveReceipt. Specify error codes for quota, stale membership,
revision conflicts, archived sessions and offline execution.

**Gate:** reproducible capacity report, calibrated configuration and documented
fixtures. No production writes or infrastructure upgrades implied by this plan.

### 2. Add shared schema and access policies

**Owner:** managed backend. Proposed new root `supabase/migrations/` owns schema and
RLS; `supabase/functions/` will own edge handlers. Do not duplicate these migrations
in the separately maintained website repository.

Add organizations/memberships/invites; sessions/session members; attributed message
events; runs/leases; devices; quota counters/reservations; archive manifests/receipts;
inbox/outbox and tombstones. Store shared files in a private bucket with matching
authorization. Add composite constraints preventing cross-organization references.

Use existing account IDs. Organization membership permits directory discovery,
not every chat. Enforce accepted session membership on content and realtime. Keep
privileged fields, generated messages, run claims and archive receipts out of
unrestricted client writes. Use explicit database grants plus RLS. Never expose
service credentials through preload or renderer.

**Gate:** two organizations and multiple users cannot cross-read, forge authors,
grant membership, alter quota counters, publish results, or bypass archive receipts.

### 3. Implement transactional sync commands

**Owner:** backend. Provide authenticated commands for create/invite/accept,
append/edit, read-after-cursor, claim/complete run, and request/ack archive.
Atomically allocate per-session commit order, deduplicate immutable event IDs and
reserve quota. Reject reused IDs with different payloads. Enforce expected revision
on edits; preserve deletions as events/tombstones. Count the response budget before
admitting a request. Derive requester identity from verified authentication.

Publish realtime notifications after durable acceptance; notifications are hints,
not the authoritative history. Add snapshot/high-water/cursor semantics that cannot
miss writes during subscription. Rotate authorized topics on membership changes so
old connections receive no new data; revalidate on commands and publication.

**Gate:** lost acknowledgements and retries create one event/run; concurrent messages
are retained; malicious old clients cannot append to archived or revoked sessions.

### 4. Add local collaboration storage and transport

**Owners:** core and Electron main. Add `collie_core/collaboration/` with local
outbox, materializer, cursor store and archive manager. Keep personal SQL untouched
except additive migrations. Persist local message plus outbox in one transaction;
apply remote page plus cursor in one transaction. Reconnect/foreground reconciliation
downloads missing events; do not send full histories repeatedly.

Use narrow typed preload commands for authenticated shared operations. Keep account
tokens in Electron protected storage. Separate private local events from shared
publication so a generic existing IPC broadcast cannot leak private state.

**Gate:** crash mid-download, lost response, duplicate notification and several days
offline all converge to the same published state. Existing personal chat still works
without sign-in or network. Show local-save versus cloud-acknowledged status.

### 5. Build organization discovery and shared chat UI

**Owner:** frontend. Add organization selection, invitation acceptance, participant
avatars and a directory-backed `@` picker. Existing-member mentions notify; new-member
mentions offer an explicit invitation. Only authorized members may invite. Show the
history audience before sharing an existing personal conversation; publish an approved
copy, not its private tool logs. Other contributors must consent to wider publication
unless they accepted a clearly disclosed owner-managed audience policy.

Reuse Sidebar, ChatScreen, MessageList and MessageBubble; extract the local/shared
transport interface rather than forking the whole UI. Add names, requested-by labels,
unread state, syncing/error state and view-only/archive states. Invitations carry stable
IDs, not usernames as authority. Keep subgroup/team management optional for later.

**Gate:** two people share one attributed transcript; a directory match alone grants
no access; pending invitees receive no history or file URLs.

### 6. Isolate shared and private model context

**Owner:** core. Add an explicit audience/context mode at runtime entry. Shared turns
use canonical published history through a recorded sequence and public product
instructions; exclude personal bootstrap files, memories, private skill contents,
private session summaries and tool traces. Disabling recent history alone is insufficient.

Private-resource tasks run privately and publish only an explicitly authorized result.
Prevent private token deltas, progress details, arguments, errors and attachment paths
from reaching the shared publisher. Exclude shared history from automatic profile,
Dream and Gardener ingestion. Explicit Remember for me remains requester-scoped.

**Gate:** sentinel private data in every context source never appears in shared
prompts/events; private tools remain usable through the private-result path.

### 7. Bind execution and approvals to the requester

**Owners:** core/backend. Add authenticated device enrollment, outbound job pickup,
expiry, revocation, and executor-specific run leases/fencing. Only the requester's
device may execute; its local permission engine revalidates every job. Do not accept
remote permission overrides or caller-provided unrestricted local paths.

Extend ExecutionContext with requester, credential owner, executor, session/audience
revision and context cutoff. Session ownership manages membership, not tool authority.
Approvals bind actor, exact action/version, expiry and resource. Publication is a
separate authorization from reading a private resource. Keep one started run per
shared session; only the requester can steer their authority-bearing run. An owner
can stop without viewing private details. Resource conflicts need version checks.

**Gate:** Alice cannot use Rick's tools, approve his action, or redirect his ongoing
run. Two devices cannot execute one request. Offline/cloud-outage UI queues honestly;
new shared runs wait for coordination, while private work can continue separately.

### 8. Preserve feature parity through shared execution

**Owner:** core/frontend. Propagate identity and audience through subagents,
continuations, retries, skills, connector calls, local files, task cards and routines.
Routines remain creator-owned, with shared delivery separately authorized. Private
model/connector settings never become session-wide settings. Record provider payer
per run without uploading credentials.

Add a private approval/result drawer available only to the requesting person; shared
participants receive a generic waiting state. Shared files are explicit versioned
uploads, not another computer's path. Concurrent edits reject stale versions.

**Gate:** a feature matrix proves tools, models, memory, subagents, plans, routines,
artifacts, stop/retry and approvals work either directly with shared-safe inputs or
through private publication. No claim of full parity based solely on text chat.

### 9. Implement byte reservations and quota UX

**Owners:** backend/frontend. Enforce all limits from the table, including message
slots, events/revisions, attachments and in-flight reservations. Reconcile logical
counters with physical database measurement; daily provider metrics alone are not
an admission mechanism. Release unused reservations on completion/cancellation with
crash recovery. Rate-limit transient progress and cap notification fan-out.

Show used/remaining storage, participant/message limits and warnings before closing.
Offer Start next conversation using a reviewed summary only when cloud capacity
exists. Quota denial preserves the user's local draft and says it has not synced.

**Gate:** simultaneous writers cannot overspend; maximum-size accepted responses can
finish; quota failure cannot masquerade as delivery or erase pending local work.

### 10. Implement durable local archives and acknowledgements

**Owner:** core. Create versioned manifests, paginated downloads, file hashing,
durable atomic writes, read-back verification and per-account device receipts.
Add archive export/import and recovery validation. Archives survive sign-out,
cache eviction and app restart, with access protected on the user's computer.

**Gate:** interrupted writes, corrupt files, missing attachments and stale manifests
never receive a successful archive acknowledgement. Valid archives render offline.

### 11. Implement archive orchestration and cloud purge

**Owner:** backend. Implement the state machine above using short scheduled/function
batches with leases, retry checkpoints and audit records. Archive-limit triggers
must not force completion of a run by deleting its state. Wait for receipts from
all eligible participants, then idempotently remove payload and file objects.
Keep minimal tombstones; block old clients from re-uploading deleted history.

**Gate:** Alice offline blocks purge; after her verified copy, purge completes even
if Rick is then offline. Test membership changes, partially deleted objects,
worker crashes, quota pressure and duplicate acknowledgements. No archive-bypass
fallback or automatic participant eviction is permitted.

### 12. Add archive and continuation UI

**Owner:** frontend. Add Local archives in the sidebar, read-only archive viewer,
progress and waiting-for-participants status, export/import, local-copy location,
storage-reclaimed state and Start next conversation. Explain that archived content
is no longer recoverable from Supabase and existing Slack messages remain in Slack.

**Gate:** users can distinguish closing, saved locally, waiting for others, purging
and archived. A new computer sees an archive marker/recovery path, not an empty chat
misrepresented as fully synchronized history.

### 13. Add Slack over canonical shared sessions

**Owners:** backend/core/frontend. Add install/callback/events/interactions handlers
under `supabase/functions/`, with encrypted installation bot credentials. Validate
OAuth state and signed raw-body events, durably deduplicate and acknowledge promptly.
Map `(installation, channel, root_thread_ts)` to one session, including the first
mention. Link Slack senders to their own verified Collie account/executor.

Add Add to Slack and selected-channel settings, reciprocal links and visible channel
audience. Do not mirror private desktop history into a wider channel automatically.
Defer Slack Connect and unlinked/guest personal execution. Use rate-aware context
backfill, an outbound outbox and bot-echo suppression. An archived Slack mapping
must request an explicit new continuation, not recreate old content from Slack history.

**Gate:** two Slack authors plus desktop share the same canonical transcript and
request ownership. Revocation/uninstall stops execution; retries do not duplicate
work; private responses remain private; archive receipt policy is satisfiable.

### 14. Run fault-injection, regression and pilot rollout

**Owners:** core/frontend/backend. Add end-to-end tests for concurrent/offline devices,
lost acknowledgements, cursor gaps, run lease loss, duplicate Slack delivery, quota
exhaustion, stale edits, permission revocation and archive corruption/purge recovery.
External writes use operation-specific idempotency/reconciliation; exactly-once
message insertion does not guarantee exactly-once email delivery.

Run relevant Python IPC/permissions/memory/subagent/routine/Telegram tests, desktop
tests/typecheck/build, packaged-core checks for changed packaging, and hosted RLS/
function tests in an isolated project. Run a real two-computer acceptance pass and
then a real Slack test-workspace pass. Use per-user/organization feature flags with
separate shared-text, shared-execution, archive and Slack release gates.

Start with a controlled group. Measure database/objects/egress, active connections,
sync lag, dedup retries, archive-pending bytes, oldest unsynced member, and purge
failures using content-free telemetry. Pause new admissions if capacity is unsafe.
Rollback disables new shared work while preserving read/export/archive recovery;
do not roll back by dropping data tables. Never disable authorization to recover
availability. Preserve local-only behavior throughout rollout.

## Documentation and release responsibilities

The monorepo owns core, desktop and proposed Supabase collaboration definitions.
Website-only routing/invitation landing pages, if needed, are a later separate PR;
they must reuse this authority model. At implementation time update PROJECT_MAP
when ownership changes, the account/cloud-backup docs with the opt-in shared-session
exception, and the nearest component instructions as needed. Do not rewrite current
VISION/release claims to describe an unimplemented feature. Regenerate the repository
snapshot when structure or canonical documentation changes.

## Platform references and evidence limits

- [Supabase database size](https://supabase.com/docs/guides/platform/database-size):
  database usage includes indexes; new projects occupy baseline space; free projects
  can become read-only above 500 MB; deletion does not necessarily shrink usage immediately.
- [Supabase pricing](https://supabase.com/pricing): Free currently includes 500 MB
  database, 1 GB file storage and limited egress/realtime; recheck before deployment.
- [Realtime authorization](https://supabase.com/docs/guides/realtime/authorization):
  use private authorized channels in addition to durable, authorized history reads.
- [Slack events](https://docs.slack.dev/apis/events-api/),
  [OAuth](https://docs.slack.dev/tools/bolt-js/concepts/authenticating-oauth/), and
  [history limits](https://docs.slack.dev/reference/methods/conversations.replies/):
  installation, acknowledgements, retries and commercial-distribution constraints.

Storage averages are estimates until step 1. No live customer data has been sampled
for this plan. Free-tier operation is a bounded pilot, not an availability or
unlimited-retention promise. Archived local copies cannot be recovered after every
holder loses them, and cloud deletion cannot revoke previously downloaded copies.
