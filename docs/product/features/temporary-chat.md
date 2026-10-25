# Temporary chat

**Status:** implemented text-only subset; draft pending desktop review

The ghost button next to New chat opens a separate temporary conversation.
Closing it, selecting another chat/view, losing the core connection, or quitting
discards its active history. Stop cancels a reply while keeping the open chat.
There is no conversion, export, recovery, or handoff into a saved conversation.

History stays in Python and renderer memory. The `DesktopSessionManager` is
injected into the existing engine; its registered `ghost:` sessions never call
the durable manager's save method, including early user saves and checkpoints.
Normal sessions delegate to the existing session manager. Durable listing,
file retrieval and forks cannot expose or copy ghost history. No conversation,
message, plan, approval, artifact or telemetry rows are created by this path.
Consequently regular search, backup/export, Dream and Gardener have no temporary
transcript to consume. Ghost events are sent only to their owning IPC connection
and carry a session ID; the renderer ignores other IDs and unsubscribes on close.

Turns use the same configured engine/provider and supported `process_direct`
options: ephemeral execution, an explicitly truthy empty tool registry, and no
durable hooks. All tools are unavailable, including personal-memory writes,
goals, plans, routines, file access, external actions, helpers and connected MCP
tools. Attachments, dictation, sharing and cross-chat handoffs are unavailable in
the temporary panel. This deliberate restriction avoids persisting content
through helper files, approval descriptions, artifacts or external accounts.
Ordinary chats retain their existing tools, plans and approval behavior.

Engine/provider Loguru records from the temporary task and inherited child
tasks are redacted before reaching sinks, including exception payloads. There
is no content telemetry/crash-report submission in this path. This is an
application storage promise, not secure RAM erasure, protection from OS swap,
user screenshots, debugging tools or operating-system crash dumps. Existing
workspace/profile context may be read. The configured model provider receives
the request and its own retention policy applies; Collie makes no server
zero-retention claim.

## Focused acceptance

`tests/collie/test_temporary_chat.py` runs the real runtime, IPC socket, engine,
SQLite and file-backed session manager, substituting only the model provider.
It completes a streamed temporary turn whose model attempts `remember`, cancels
a second turn, closes the session, rejects a fork, and confirms ordinary chat
still persists. It scans the isolated home (including database/WAL, JSONL,
memory, diagnostics, versions and artifact paths) and SQLite dump for the secret
markers. This narrow test does not establish packaged desktop acceptance or
third-party provider data handling.
