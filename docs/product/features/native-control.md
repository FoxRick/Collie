# Native editor control (experimental)

Private desktop chats can select a supported unsaved classic Notepad window in
the chat's **Use a local Notepad window** panel. Selection authorizes only that
window's editor and stays in process memory. It expires on Stop, window identity
failure, worker failure, core restart, or loss of all desktop IPC connections.
No account, database migration, extra package, or engine change is required.

## Supported scope

- Discover English windows titled `Untitled - Notepad`, process `notepad`,
  window class `Notepad`, with exactly one enabled non-password classic `Edit`
  control. Modern windows without that editor are excluded before selection.
  Select an exact returned identity in a private chat.
- Observe returns editor class and availability only, without reading document
  text, descendant names, clipboard, password values or screenshots.
- Focus and semantic `click` call the sole classic `Edit` control's `SetFocus`.
  This does not promise foreground visibility or a physical pointer click.
- `type` replaces the entire value, up to 16,000 characters, only when the
  editor exposes writable `ValuePattern`. No append or keystroke fallback.
- Scroll one small vertical increment only when `ScrollPattern` is exposed.
- Stop releases selection and cancels the chat/action and pending approvals.

Modern Notepad, localized titles, saved documents, menus, dialogs, other apps,
arbitrary buttons, save/send/publish/delete, shortcuts and coordinates are
unsupported. A missing accessibility pattern produces an unavailable message.
The user handles unsupported or consequential actions manually.

## Authority and lifecycle

`collie_core/native_control.py` is the single implementation behind the typed
`native_control` IPC family and agent tool. Every observation and input action
passes through `ApprovalBroker` with a sensitive, hard, once-only approval;
automatic local-write settings and task approval cannot waive it. Agent calls
also pass the engine's normal sensitive dispatch gate, so currently require
two reviews: the tool request, then the concrete window/action. This preserves
the engine's plan, read-only specialist, parent and shared-execution gates.
UI actions derive plan/execute mode from the chat, rather than renderer input.
Shared and messenger execution is rejected. The model cannot select a window.

A process-wide lease covers approval and input across all chats. A second
action fails busy rather than queueing input. The fixed, hidden Windows
PowerShell MTA worker starts on demand and exits after one request. No shell
source, commands or arbitrary arguments can be supplied by a model. It
revalidates HWND, PID, process start time, runtime ID and title immediately
before interacting, and resolves only one enabled, non-password classic editor.
Cancellation or the 12-second deadline kills and waits for the worker before
releasing the lease. An action already delivered to Windows cannot be undone.

Selection and worker responses are not persisted by the module. Explicitly
submitted replacement text is visible in the existing chat/tool and approval
history; it is not a private text vault. There is no background observation,
input subscription, elevation or `uiAccess` bypass.

## Verification and limitations

Focused acceptance command, from the repository root with project dependencies:

```powershell
$env:PYTHONPATH='collie-core'
python -m unittest discover -s collie-core/tests/collie -p test_native_control_acceptance.py -v
```

One test uses the real SQLite approval broker and a controlled adapter to check
an approved interaction, denial, stale identity after review, shared context,
read-only/plan dispatch policy, global concurrency rejection, cancellation and
lease recovery. This is interface acceptance, not real Windows input acceptance.
The actual Windows worker's discovery smoke check returned successfully with
zero supported windows on the development machine. Focus, type and scroll have
not been verified against a real supported editor; packaged-app acceptance is
still required. Many multiline editors expose read-only TextPattern and will
refuse typing by design. Renderer TypeScript checking passed; no full build or
broad test suite was run.

## Adapter API references

The Windows adapter uses Microsoft's [UI Automation](https://learn.microsoft.com/en-us/dotnet/framework/ui-automation/ui-automation-overview),
[FromHandle](https://learn.microsoft.com/en-us/dotnet/api/system.windows.automation.automationelement.fromhandle)
and [GetRuntimeId](https://learn.microsoft.com/en-us/dotnet/api/system.windows.automation.automationelement.getruntimeid).
Its compound identity check is a Collie safeguard; runtime IDs alone may be reused.
[SetFocus](https://learn.microsoft.com/en-us/dotnet/api/system.windows.automation.automationelement.setfocus)
does not guarantee foreground visibility.
[ValuePattern.SetValue](https://learn.microsoft.com/en-us/dotnet/api/system.windows.automation.valuepattern.setvalue)
replaces a value; TextPattern has no setter.
[ScrollPattern](https://learn.microsoft.com/en-us/dotnet/api/system.windows.automation.scrollpattern)
requires provider support. A separate [MTA worker](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-threading)
keeps accessibility calls off the UI thread.
