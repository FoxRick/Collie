# Share Collie

**Status:** parked — deferred, not in the current plan; spec kept for future reference
**Date:** 2026-08-01
**Parked:** 2026-08-03
**Applies to:** Post-alpha multiplayer and shared-chat work

## Outcome

Collie will support simple multiplayer use without becoming an organization
administration product. A user can add their Collie to a Telegram group, approve the
people who may use it, and collaborate with those people in a shared conversation.

The experience should feel like adding a helpful dog to a group chat. Internally,
Collie will use explicit identities, shared scopes, membership, capability grants,
and attributed approvals. Those implementation terms must not become the normal
user-facing language.

The first version does **not** require Collie user accounts. The Windows installation
remains locally owned and is the source of truth. Telegram provides external identity
for invited participants, and the owner's configured model or local model performs
the work.

## Product principles

1. **Local owner, shared conversations.** The desktop user remains the owner and
   administrator of their Collie. Sharing does not move personal state to a Collie
   cloud service.
2. **Private by default.** A shared chat cannot see personal memory, email, calendar,
   contacts, private files, connector credentials, or other conversations unless the
   owner grants a clearly described capability.
3. **Identity is not an account.** Telegram's stable numeric user ID identifies a
   participant. Usernames and display names are presentation only.
4. **Permissions remain behavioral.** A group member cannot approve a consequential
   use of the owner's data or credentials. The existing central permission engine and
   owner approval remain authoritative.
5. **Attribution is durable.** Collie records who requested an action, which shared
   conversation initiated it, who approved it, what ran, and where the result went.
6. **The normal path stays simple.** People, shared chats, and "what this group can
   use" are acceptable product language. Principals, scopes, ACLs, grants, tenants,
   and harnesses are not.
7. **No hidden cost promise.** Telegram sharing avoids a Collie collaboration-server
   cost, but cloud model calls still consume the owner's provider allowance. Local AI
   can provide an optional zero-per-call path on capable hardware.

## First-release user experience

### Owner setup

Settings evolves from **Phone** to **Phone & Sharing**. It includes one primary action:

> Share Collie in Telegram

The action opens Telegram's add-to-group flow. After Collie receives a message from
the selected group, the desktop shows a friendly confirmation with:

- the group name;
- who requested access;
- the safe capabilities available initially;
- the statement that personal memory and connections remain private; and
- **Let them use Collie** / **Not now** actions.

Telegram privacy mode remains enabled and Collie responds in a group only when
mentioned or when somebody replies to it. It must not ingest unrelated group
conversation merely to appear more aware.

### Participant setup

A participant does not create a Collie password or provide an email address. Their
first interaction creates a pending local membership request using their Telegram
numeric user ID. The owner approves or rejects it in Collie.

Once approved, the participant can use Collie in that shared chat. Approval for one
group does not authorize the same person in another group or in a private chat.

### Shared conversations

Each Telegram group, and each Telegram topic when present, maps to its own Collie
conversation. The conversation list may show entries such as:

```text
🏠 Family
✈️ Japan Trip
🎓 Study Group
```

Messages in the desktop chat show the requesting participant's name. Desktop replies
return to the exact Telegram group or topic. No two Telegram sessions are merged into
one desktop transcript.

## Account and identity decision

### No mandatory Collie accounts

The first release uses three locally stored identity types:

| Identity | Authority |
| --- | --- |
| Local owner | Manages members, capabilities, models, connections, and approvals. |
| Telegram participant | May act only in explicitly approved shared chats. |
| Telegram group/topic | Owns its conversation, shared memory, files, routines, and grants. |

The owner is an implicit local principal. A Telegram participant is keyed by provider
plus stable external user ID. A shared chat is keyed by provider, chat ID, and optional
topic ID. Bot tokens, usernames, display names, and message text must never be treated
as identity authority.

### Retention, migration, and token decisions

The owner-facing setup must disclose that Collie stores approved participant
identity (provider, stable numeric ID, display name when supplied), membership
decisions, group/topic routing, shared transcripts, and attributed approvals
locally for as long as that shared space remains active. Removing a participant
revokes access immediately; deleting a shared space removes its local shared
memory, routing, and membership records according to the product's local-data
deletion flow, while preserving only the minimum audit record required for an
owner-visible past action. The UI must state the exact effect before deletion.

Telegram may migrate a basic group to a supergroup, changing its chat ID.
Collie may carry routing forward only from Telegram's authenticated migration
metadata, preserving the same topic isolation and pausing the space for owner
review if identity, members, or destination cannot be verified. It must never
join groups merely because names match.

The bot token stays only in OS-protected credential storage. Rotation is an
owner-controlled replacement flow: pause shared delivery, install and verify
the new token, invalidate the old local credential, and record no token value
in transcripts, diagnostics, logs, or approvals. A failed rotation leaves
sharing paused rather than falling back to an old or unknown token.

### When an optional account may be justified later

A Collie account may be offered for an optional feature that genuinely requires a
Collie-operated service, such as:

- synchronization between several computers;
- hosted browser access independent of Telegram;
- public or expiring share links;
- account-backed recovery after loss of local state;
- included model credits, billing, or organization administration; or
- a relay that works while the owner's computer is offline.

Those features must not make an account a prerequisite for the local desktop product.

## Current architecture fit

The existing path remains intact:

```text
TelegramChannel
  → MessageBus
  → identity and shared-space resolver (new)
  → AgentLoop
  → central permission broker
  → scoped tools and connectors
  → SQLite and session history
  → authenticated desktop IPC
  → Electron UI
```

The Telegram channel already supplies sender ID, username, chat ID, group status,
reply context, and topic ID. It already supports mention-only group handling, ordered
per-session processing, attachments, streaming, and topic-specific session keys.

The largest current mismatch is desktop mirroring: messenger sessions are associated
with a messenger-level conversation. Sharing requires an authoritative mapping from
each exact messenger session key to its own Collie conversation.

## Backend design

### Shared data model

Add the following concepts in the next appropriate SQLite migration. Names may be
adjusted during implementation, but the ownership boundaries are normative.

```text
principals
  id, kind, provider, external_id, display_name, status, created_at, updated_at

shared_spaces
  id, kind, provider, external_chat_id, external_topic_id,
  name, owner_principal_id, conversation_id, status, created_at, updated_at

space_members
  space_id, principal_id, role, status, approved_by, approved_at, revoked_at

space_grants
  id, space_id, capability, access_level, resource_pattern,
  granted_by, created_at, expires_at, revoked_at

space_memory
  id, space_id, key, value, created_by, created_at, updated_at
```

Requirements:

- Provider and external ID pairs are unique within their identity type.
- A Telegram group/topic maps to exactly one active shared space and conversation.
- Membership revocation takes effect before another model call or tool call.
- Deleted or archived desktop conversations cannot silently retain active routing.
- Connector tokens remain in the existing protected credential path; grants contain
  references and permissions, never secret values.

### Execution context

Extend the existing execution and permission context with:

```text
principal_id
principal_display_name
space_id
space_kind
owner_principal_id
reply_destination
```

The identity and shared-space resolver binds these values before the agent loop builds
context or exposes tools. A participant removed during a turn cannot begin another
tool call. Child agents inherit the same shared-space ceiling and may only narrow it.

### Tool exposure and authorization

Tool visibility is filtered before schemas reach the model, and the permission
evaluator remains the final authority at execution time. Hiding a tool is a usability
and prompt-surface optimization, not the security boundary.

Default shared capabilities:

- web research, weather, and news;
- reading files posted directly into that shared conversation;
- group-specific memory;
- shared reminders, checklists, and safe routines; and
- read-only Researcher, Analyst, and Reviewer specialists.

Web research is an external egress even when it is a default shared capability.
Before the first use, the group-facing disclosure says that the request wording
needed for research may be sent to the selected search/research provider. Collie
must not send the whole transcript, private resources, or uploaded files; any
such expansion requires a separate owner approval that names the destination
and data.

Default blocked capabilities:

- the owner's structured personal profile and personal memory;
- email, calendar, contacts, health, shopping, budgets, and private notes;
- arbitrary local filesystem access;
- connector creation, removal, or credential management;
- purchases, payments, deletion, publishing, and external sends; and
- any other conversation's messages, files, memory, or approvals.

Unknown capabilities fail closed. A grant may tighten or expose an eligible
capability; it can never bypass hard approval or the owner's policy floor.

### Private-resource requests

If a participant asks for a capability that could be sensibly granted, Collie does not
pretend it is unavailable and does not reveal private data. It tells the group that
the owner must decide and sends a private approval to the desktop.

The approval must show:

- who requested the action;
- which shared conversation will receive the result;
- the private resource or connection involved;
- what data is expected to leave the device;
- whether the access is once-only or retained for that group; and
- any additional action approval that will still be required.

An owner approval to read data is not approval to send, publish, delete, purchase, or
perform another mutation. Cross-connector egress remains separately approved.

### Memory separation

The existing personal profile remains owner-only. It must never be included in a
shared prompt by default.

Shared memory is stored separately and belongs to one shared space. Only information
explicitly remembered in that space, or deliberately promoted there by the owner, is
available to the group. Every entry records who created it. The owner can inspect,
edit, clear, or disable memory for a shared chat.

### Routines and delivery

A shared routine belongs to one shared space and runs with that space's current
membership and grants. It delivers only to the recorded group/topic destination.
Revoked capabilities are re-evaluated when the routine fires; approval captured when
the routine was created cannot authorize a newly risky action indefinitely.

The existing alpha limitation remains until separately changed: Collie must be open
for Telegram and routines to run. Missed shared routines must not execute late merely
because the app reopened.

### Usage controls

Shared participants consume the owner's configured provider allowance unless local
AI is selected. The owner gets understandable controls:

- maximum shared requests per person per day;
- maximum simultaneous shared turns;
- pause all sharing;
- choose local AI or a connected provider for shared chats; and
- see aggregate usage without exposing technical token controls by default.

The quota unit is a **shared model turn**: one accepted participant request
that starts model execution, regardless of how many messages or tool calls it
contains. The owner may set per-person turns per rolling 24 hours and a maximum
number of simultaneous shared turns. Retries caused by Collie's transport or
provider failure do not consume another turn; deliberate participant resubmits
do. The UI shows remaining turns and a plain-language reason when a request is
limited.

Rate limits and repeated-message detection apply before model execution.

## Frontend design

### Phone & Sharing

Keep sharing inside Settings rather than adding another primary navigation section.
The page contains:

- **Share Collie in Telegram**;
- pending people with Allow/Reject;
- approved people with the groups they can use;
- shared groups with Pause/Remove;
- a short "What this group can use" list;
- model choice and a friendly usage limit; and
- the reminder that Collie must be open.

Do not render a generic permission matrix. Begin with safe defaults. Show a named
toggle only when the owner intentionally grants a private resource, for example:

> Let Family see events from Shared Family Calendar

### Conversation UI

Shared conversations use the normal chat screen with small additions:

- sender name and optional avatar on participant messages;
- a compact **Shared through Telegram** source label;
- the exact group/topic name;
- a visible paused or disconnected state; and
- owner replies routed back to the correct external destination.

The primary transcript remains conversational. It does not show raw identity IDs,
grant records, tool schemas, or permission traces.

### Approval UI

The existing approval sheet adds requester, shared destination, data source, and
data-egress details. If an approval will create retained group access, the sheet states
that separately from the immediate action and provides a direct way to remove it.

## Free and local operation

### Collaboration transport

Telegram long polling lets the owner's computer receive group messages without a
Collie-hosted ingress service or public port. Telegram privacy mode and mention/reply
interaction remain the default.

This avoids an additional Collie server bill, but it is not an offline relay: the
owner's computer and Collie process must be running.

### Optional local AI

Collie may offer **Use AI on this computer** when it detects a compatible local model
runtime. The first supported path should be an automatically discovered, tested
OpenAI-compatible Ollama endpoint on localhost.

The setup must state hardware and storage requirements honestly, test tool calling,
and fall back gracefully. Users should not need to type endpoints, model IDs, or
terminal commands in the normal flow. A connected cloud provider remains available
for harder work when the owner chooses it.

References:

- [Ollama on Windows](https://docs.ollama.com/windows)
- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
- [Telegram bot privacy mode](https://core.telegram.org/bots/features#privacy-mode)

## QM patterns incorporated

[QM](https://github.com/yc-software/qm) is a multiplayer agent harness for
organizations. Collie may adapt its MIT-licensed architectural patterns while keeping
Collie's Python authority, local-first storage, consumer interface, and product
invariants.

Patterns worth porting selectively:

- stable principal and scope identities;
- scope-owned memory, files, skills, routines, and credential views;
- monotonic membership and capability grants;
- layered memory visibility;
- attributed audit records;
- declarative capability metadata; and
- harness and sandbox interfaces that fail closed on unsupported capabilities.

Do not make QM a runtime dependency and do not fork Collie onto QM's cloud stack.
Do not adopt its Dangerous posture, generic shell-first experience, Postgres/AWS/Fly
deployment model, admin content access, or public capability links for this feature.
Any adapted code must retain the required upstream license notice.

## Delivery sequence

1. **Identity and routing foundation**
   - Add principals, shared spaces, memberships, and exact session-to-conversation
     mapping.
   - Preserve private Telegram behavior and topic isolation.
   - Test identity spoofing, renamed users, revoked members, deleted conversations,
     and reconnect restoration.

2. **Shared safety boundary**
   - Bind shared identity into execution context.
   - Filter tool exposure and enforce shared-space denial in the evaluator.
   - Add requester/destination attribution to approval and audit records.
   - Prove that personal memory, connectors, and other conversations are unavailable.

3. **Shared memory and safe capabilities**
   - Add group memory, posted-file access, shared reminders/checklists, and read-only
     specialists.
   - Add inspect, edit, disable, and clear controls for the owner.

4. **Phone & Sharing experience**
   - Add the group invite, member review, group list, capability summary, pause, and
     removal flows.
   - Render sender attribution and exact group routing in Chat.

5. **Granted private capabilities**
   - Add named, resource-specific owner grants.
   - Add private approvals and cross-destination egress disclosure.
   - Test revoke-during-turn and revoke-before-routine behavior.

6. **Optional local AI and quotas**
   - Detect and test Ollama without requiring terminal setup.
   - Add shared-provider selection, per-person limits, concurrency limits, and clear
     usage reporting.

## Acceptance criteria

- The owner can add Collie to a Telegram group without creating a Collie account.
- An unknown participant cannot trigger a model call or tool call before approval.
- Each group/topic has an isolated conversation, transcript, memory, files, routines,
  and grants.
- A shared chat cannot read personal memory, private files, connectors, credentials,
  or another conversation by default.
- The owner can see who requested every consequential action and where its result will
  be delivered.
- A group participant cannot approve the owner's sensitive or external action.
- Revoking a member or grant prevents subsequent model/tool work immediately and
  invalidates queued background work before execution.
- A desktop reply reaches the exact originating group/topic and never another chat.
- Shared routines use current permissions and do not run late after Collie was closed.
- Sharing can be paused or removed without deleting the owner's personal state.
- The owner sees retention and deletion consequences before removing a person
  or shared space; deletion removes the stated shared data without touching
  personal state.
- Research requests disclose their external destination and do not egress
  transcripts, private resources, or uploads without separate owner approval.
- A verified Telegram group-to-supergroup migration preserves only its exact
  routed shared space; ambiguous migration pauses for owner review.
- Bot-token rotation is protected, auditable without secret values, and leaves
  sharing paused if verification fails.
- Shared limits count model turns as defined above and do not charge an
  additional turn for Collie/provider retry failures.
- The normal interface uses plain language and does not expose account, tenant,
  principal, scope, ACL, MCP, or harness terminology.
- When local AI is selected, Collie verifies the model and tool-call path before
  claiming that shared chat is ready.

## Non-goals for the first release

- Mandatory Collie accounts
- A Collie-hosted collaboration cloud
- Organization or enterprise administration
- Public anonymous access or share links
- Cross-device state synchronization
- Independent operation while the owner's computer is off
- Allowing participants to attach their own model providers or connectors
- Sharing the owner's whole memory or entire connector account
- A generic roles-and-permissions editor
- Agent-to-agent group conversation or autonomous swarms
- Slack, Discord, WhatsApp, or a new mobile app in the same delivery change
- Generic shell or filesystem execution for shared participants
