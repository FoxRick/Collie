# Cross-session context and independent handoff

Collie can explicitly search committed personal desktop chat messages with
`read_previous_chats`, or read a timestamped snapshot by conversation ID.
Responses carry real database message IDs and clickable source links. Links open
the personal chat and reveal the source message, including older transcript windows.
No all-chat context is automatically added to prompts. Returned text is untrusted
evidence, never instructions, approval, or authority.

Search scans at most 200 recent unarchived chats and their 100 most recent
messages, returning at most 20 literal substring matches. A snapshot includes at
most 12 recent committed messages, current IPC activity, and the persisted active
task goal/progress when available. Each message is capped at 2,000 characters;
progress fields are capped at 2,000 characters. Outputs are committed assistant
messages; attachments, file contents, raw tool results, hidden reasoning and
uncommitted streaming tokens are excluded. Engine goal state is not inspected;
if there is no persisted checklist/plan goal, the goal field is empty.

The access boundary requires a durable personal conversation row. Temporary
in-memory chats cannot search, be retrieved, or hand off. Explicit temporary/shared
markers and reserved ID prefixes are also rejected. Shared history lives in a
separate store which this module never reads; shared execution cannot use these
tools. A local privacy marker `cross_session.private.<conversation_id>` set to
true excludes that persisted chat as both requester and source. This initial
version has no dedicated privacy-marker editor.

When explicitly asked to start work in a **new chat** or **new session**, the
`start_new_chat` tool submits a minimal explicit brief through the normal IPC chat
execution path and returns a visible link and the actual title. The optional
`title` argument preserves a requested chat name. Handoff turns are explicitly
identified as already running in the new chat, with `start_new_chat` omitted
from their per-turn tool registry. Other chats keep their normal tools.
Approval cards appear only in their owning chat; a notice links to a different
chat when it needs approval. The central authorizer evaluates
`cross_session.start` as a reversible local write. The target has its own ID and
normal streaming, cancellation, model-provider selection and approvals. It receives
no source transcript, attachments, project folder, file grants, or approval grants;
it starts with the normal general-chat selected-folder scope (runtime workspace).
The tool does not parse arbitrary languages deterministically: natural-language
intent is interpreted by the model, while the originating request must contain
“new chat” or “new session” for this initial guard.

A durable reservation keyed by source chat and original message/turn ID prevents
retries from executing the task again, including after restart. One source request
can start one target. Handoff targets cannot automatically hand off again, and
handoff intake stops when three IPC chats are working. If dispatch fails or the
process exits after reservation, the target remains available but is never
replayed automatically: open it and explicitly resume manually. Reservations and
child lineage use the existing local settings store, with no schema migration.
Deleting a target does not reset its reservation or silently restart its work.

Validation is one focused acceptance scenario with real temporary SQLite storage,
public agent tool entry points and the normal IPC execution adapter, using a fake
model. It covers two-chat committed recall, source IDs, private/temp/shared
exclusion, source immutability, independent target execution, concurrent retries,
restart deduplication, recursion rejection and ambiguous-dispatch recovery.
Packaged UI navigation and real-provider execution still require manual acceptance.
