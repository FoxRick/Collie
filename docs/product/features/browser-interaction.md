# Visible browser interaction

Status: development preview; local Windows acceptance verified, packaged app unverified.

Ask Collie to open a web page, inspect it, prepare ordinary fields, or click a
visible element. The chat's Browser panel offers the same actions, captures,
visible takeover, and Close / stop. Read page returns bounded text and selectable
elements. Page content is untrusted and never grants permissions.

The first scope is one visible tab per session, up to three sessions. Each
session has a disposable context in a new Microsoft Edge process controlled by
Playwright, never a personal browser profile or existing debugging endpoint.
The session belongs to one local conversation and task. Another task cannot
interact with it; the owning conversation's UI may inspect, capture, close, or
take over. Shared-session execution is unavailable pending separate audience
authority. Browser data is not added to conversation retrieval or SQLite; tool
results in normal persistent chats retain the existing transcript behavior.
No browser artifact or profile export is created by this module.

Every page action, including reading/capturing, goes through the central broker
with fresh approval and a resource scoped to conversation, task, session and
origin. Task-wide auto-approval cannot authorize these actions. Close and local
session listing are recovery controls. Plan mode cannot start/interact with a
browser. A fill may trigger site scripts or autosave; even an ordinary click or
GET request may have effects. Approval copy explicitly states this uncertainty.
The action name and heuristic checks do not establish that a site is safe.

Automated traffic is limited to GET/HEAD on the initially approved exact origin;
service workers, WebSockets, other origins and popups are blocked. Password and
payment-autocomplete fields, submit/reset controls and likely final-action
labels are refused even after generic click approval. There is no arbitrary JS,
keyboard/Enter, file-upload, cookie/storage, credential, or profile API.
These limits break some sites intentionally; they are not a general guarantee
against arbitrary site effects. Scripts and GET-based writes still require the
user's judgment at the explicit approval gate.

For sign-in, payment, send, delete, or another final action, choose Take over.
This pauses automation permanently for that session and permits the user to
navigate HTTP(S) origins and submit visibly. Cross-origin sign-in redirects and
popups are allowed only after takeover; WebSockets and downloads remain blocked.
There is no resume or automated credential filling. Close when finished and
start a fresh session for later automated work. This narrow scope does not
promise signed-in autonomous workflows.

Page observations exclude field values, hidden text, cookies/storage, URL paths,
queries and fragments. Captures mask input/textarea/editable fields and iframes.
Site-rendered text can itself be sensitive: review the site before approving an
observation or capture. Playwright exceptions and process stderr never enter
tool output or logs. A failure closes the worker and warns that an action may
already have happened; no action is automatically retried. Stop, loop shutdown,
and app shutdown close the owned workers. A stopped or manually closed session
cannot silently restart itself.

## Runtime and verification

Developer setup from `collie-core/`: install the `browser` Python extra
(`python -m pip install -e ".[browser]"`) into the same Python environment that
runs Collie. Microsoft Edge must already be installed. No browser download,
account, subscription or service purchase runs automatically. The optional
extra uses maintained Playwright (`>=1.64.1,<2`); its bundled browser driver
launches Edge with a fresh disposable context. See the official
[browser installation guide](https://playwright.dev/python/docs/browsers) and
[isolated contexts API](https://playwright.dev/python/docs/api/class-browsercontext).
The current installer does not stage this extra, and packaged execution has not
been validated. Missing runtime returns an explicit setup message. Do not claim
this preview is ready in the distributed installer.

One explicit acceptance test (Windows with the extra and Edge installed):

```powershell
$env:COLLIE_BROWSER_ACCEPTANCE = '1'
python -m pytest tests/collie/test_browser_acceptance.py -q
```

It uses only a controlled loopback page and the real broker, supervisor and
browser. It covers navigation, fill, click using observed selectors, observation,
capture, rejected/unapproved submit, generic-approved submit still refused,
password refusal, blocked POST, cross-task/chat/shared boundaries, irreversible
handoff, close and worker failure. Default CI skips the opt-in visible test.
This is one focused acceptance flow, not broad production acceptance.

Managed downloads, multiple tabs per session, cross-origin automated journeys,
resume after sign-in, credential vaults, packaged browser installation and
non-Windows lifecycle verification are deferred. Native desktop control is a
separate module; this feature grants no native app authority.
