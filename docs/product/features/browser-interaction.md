# Visible browser interaction

Status: development preview; controlled local Windows Edge acceptance verified;
staged runtime verified; installed-release acceptance remains unverified.

Collie's browser uses local Playwright and an already installed Microsoft Edge.
It needs no automation-service account, subscription, cloud browser, relay,
hosted OCR, or downloaded model. Installation supplies the Python/driver runtime;
starting or using it never installs a browser or model. Loopback/local web apps
work offline. Remote sites need their normal network connection. Fully offline
AI additionally needs a configured, compatible local model through Collie's
existing provider settings. Hosted model inference is not made free by this
feature, and local model requirements depend on the chosen model and computer.

## Ordinary use

Ask Collie to open a page, read it, prepare fields, choose options, check boxes,
follow links, scroll, or use another tab. The Browser panel uses the same
controller and permission broker as the agent tool. Its page-control picker uses
ordinary labels; the user never writes selectors, JSON, code, or terminal commands.
A screenshot is an optional masked capture for the user's inspection. This
implementation does **not** deliver screenshots to an image-capable model.
Canvas/image-only controls, closed shadow roots and iframe contents are therefore
unsupported; exposed text/DOM/ARIA controls work without a visual model.

Each session has a disposable context in its own visible Edge process, up to
three sessions and five tabs each. It never attaches to a personal profile,
existing debugging port or external browser service. Sessions belong to one
private local conversation and task. Other tasks cannot silently adopt them;
shared conversations remain excluded. The owning conversation's UI can inspect,
act, take over or stop through the same serialized controller. A later agent
task must explicitly Resume and receive a new central approval to bind the
session to that task. Listing and Close / stop are local recovery controls.

Read page returns at most 6,000 text characters, 60 exposed controls and 30 option
labels per select. Traversal visits at most 1,600 nodes to depth 24, including
open shadow roots. Labels use separate bounded traversal (60 nodes, depth 8,
120 characters). Large pages are marked truncated. `observe` with a current
region/control reference reads that subtree. No idle observations or captures
run; the worker only dispatches browser events between commands.

Actions use random opaque references and the latest observation ID, never
model-authored CSS. References bind live element handles, exact document URL,
a 60-second lifetime, a fingerprint of bounded observed page/control state, and
target visibility/enabled state. The worker checks both before approval and
again after approval. Every observation and effect invalidates old references;
tab changes and resume also require fresh observations. Changes outside the
bounded observed tree are not a full-page consistency guarantee. Page content
is untrusted data and cannot grant destinations, permissions, or file access.

## Authority and network policy

All browser actions, including read/capture, pass the central broker with hard,
once-only approval. Plan mode and task-wide auto-approval cannot bypass it.
Request resources include chat, owning task, session and destination. Approval
records contain origin/action/control metadata, never field text, page labels,
selected file paths or screenshots. Tool results in persistent chats retain the
normal transcript behavior; bounded site text can itself contain sensitive data.
Review a page before approving observation. Browser exceptions and driver stderr
are suppressed; no page or typed content enters diagnostic logs or metrics.

The initial destination is approved at Open. Approve destination adds an exact
HTTP(S) origin (up to eight) for this task before deliberate navigation, new tabs or external
site dependencies can use it. Credentials in addresses and explicit URL queries/
fragments are refused; searches/redirects can produce queries inside the page,
which observations redact. Scripts, styles and GET/HEAD traffic can use approved
origins. Cross-origin redirects/dependencies need their own prior approval;
blocked requests are never automatically replayed. Retry only by deliberately
navigating/acting after inspecting the result.

Unlike the original read-only preview, a centrally approved fill/click/select/
check/navigation-key/upload action briefly enables non-GET/HEAD requests from
the active tab to approved origins while executing (at most 8 seconds, normally
closed after a fixed 150 ms event-dispatch period). Requests from background tabs
cannot inherit that write lease. Delayed writes outside the lease are blocked.
Consequential controls detected as submit/reset or with likely final labels need
the separate Submit / final action operation and explicit central authorization.
These heuristics cannot establish site intent: a harmless-looking control,
autosave or even GET can have effects. Approvals explicitly state that uncertainty;
this is a destination/action boundary, not a semantic firewall for arbitrary sites.
WebSockets and service workers remain blocked. It is not an OS-level network
sandbox or a claim that Microsoft Edge itself never makes background connections.

## User sign-in and recovery

Take over invalidates automation references and pauses model interaction.
Its approval explicitly hands the visible browser to the user: HTTP(S)
navigation, redirects, popups and submissions can proceed during user control.
Passwords and payment details are entered by the user, never filled by the model
or exported from browser storage. Downloads remain canceled. Resume requires an
explicit approval for the visible origin, verifies that it still matches, clears
previous destination grants, closes other-origin tabs and returns a fresh bounded
observation. A denied/stale resume leaves the session paused. This supports
continuing ordinary signed-in pages without exporting cookies or credentials.
A separate real-Edge fixture emulates the user's manual side of sign-in,
sets an HTTP-only fixture session and verifies signed-in continuation after
resume without exposing the fixture password/cookie. It uses a test harness,
not a model credential tool. Site-specific login providers and real-account
journeys are not acceptance-tested.

Close / stop stays available during approval and action execution. It cancels
pending browser approval and closes the worker tree. Take over cancels a pending
approval before obtaining its own approval; an already executing action may need
to finish before takeover (use Stop for immediate shutdown). Queued operations
recheck session/task ownership inside the lock, so Stop cannot restart a worker.
After a timeout, crash, cancellation or uncertain failure the action is never
retried automatically. Verify the visible/business result before trying again.
Fill/select/check compare their local postcondition; click/final actions return
fresh observations and require task-specific verification, not inferred success.

## User-selected files

Only the Browser panel's native file picker supplies paths. The model tool has
no upload/download/path parameter. Both transfer directions additionally require
central approval for the observed control and approved destinations. Local
absolute paths only; network shares, device/alternate-stream paths and executable
extensions are excluded. Transfers permit one document/image of at most 6 MB
(`txt`, `csv`, `pdf`, `png`, `jpg`, `jpeg`, `webp`, `docx`, `xlsx`, `pptx`).
Upload supplies bytes to the selected file control and checks file selection;
site confirmation remains a separate task postcondition. Files are not opened.

Download supports an observed **public HTTP(S) link**, with bounded streamed GET,
up to three redirects within approved origins, a 10-second I/O timeout and a
20-second checked time budget (one final I/O may finish later). It exports no
cookies or credentials, ignores environment proxies, and does not support
signed-in/blob downloads. Automatic page downloads are canceled. A complete file
is committed without overwriting an existing destination; the filesystem must
support hard links. Normal failures clean partial files. Forced worker termination
can leave a bounded `.collie-download-*.partial` file beside the selected output;
it is never executed or automatically resumed. No profile/storage export exists.

## Runtime, notices and verification

Playwright Python is pinned to `1.64.1` (Apache-2.0). No new runtime service or
AI/OCR dependency was added. Its installed Python dependencies are pyee (MIT)
and greenlet (MIT AND PSF-2.0). Staging preserves Playwright/Node licenses and
notices and writes `browser-runtime-inventory.json` with actual package versions,
embedded driver version, file sizes and SHA-256 hashes. The inspected wheel
reports embedded driver `1.64.0-beta-1791568591000`; this prerelease marker must
remain visible in release review. Edge is user-installed, independently licensed,
and not redistributed. See `collie-core/THIRD_PARTY_NOTICES.md`.

Run the real controller/tool/broker fixture explicitly on Windows with Edge:

```powershell
$env:COLLIE_BROWSER_ACCEPTANCE = '1'
$env:PYTHONPATH = (Resolve-Path collie-core).Path
collie-core/.venv/Scripts/python.exe -m pytest collie-core/tests/collie/test_browser_acceptance.py -q -s
```

The fixture uses only controlled loopback pages and no external model API.
It covers dynamic forms and POST, final-action denial/approval, stale IDs,
ordinary controls, navigation/tabs/popups, approved/denied destinations, selected
upload/public download, masking, handoff/resume reauthorization, task/chat/shared
isolation, and Stop during approval or a wedged executing action. Policy tests cover schema/path/size limits
and Stop/ownership races; UI checks cover reference-based controls, chat changes,
file-picker cancellation and available Stop/Take over during approval.
On Windows Edge 156.0.4314.42, the staged fixture completed in 17.19 seconds.
Ordinary observations were 2,175–2,361 serialized bytes; most interactions spent
218–250 ms in the worker. The truncated large-page observation was 6,299 bytes;
one optional masked capture was 21,745 bytes. These are controlled-fixture
measurements, not site/model performance guarantees. Content-free results report
elapsed milliseconds, serialized observation bytes
and blocked-request count. They do not estimate model tokens: no provider runs
in the fixture. General provider token reporting remains unchanged.

Local and staged-runtime checks are distinct from a signed installed app.
Installed-release acceptance, real-account login, broader site compatibility,
non-Windows lifecycle, authenticated downloads and model image delivery remain
unverified or unsupported. Native desktop control has a separate owner and
shares no unmerged dependency with this browser implementation.
