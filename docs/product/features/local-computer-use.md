# Local Windows computer control

**Status:** implemented alpha; installer/release acceptance remains open.

Collie's Computer panel and `computer` chat tool share one local controller and
the existing central approval broker. There is no automation subscription,
Collie account, cloud desktop, relay, OCR service or model download. The pinned
MIT Cua Driver 0.37.0 Windows wheel supplies the local implementation. The
framework works offline after explicit installation; online apps still need
their own network. AI inference follows the existing provider configuration.
Fully offline inference needs a compatible configured local model and suitable
hardware; hosted model inference is not promised free.

## Supported flow

Open a private chat, expand **Computer · local preview**, find an open window,
and choose its exact app/process/window. Read its controls, select an element,
and click or enter ordinary text. Every read and action receives central
approval. The agent tool follows the same discover → observe → act → verify
flow using observation IDs and fresh element tokens. It has no shell, arbitrary
code, clipboard, registry, download, application-launch or permission-grant tool.
Background UIA delivery is the default. Explicit foreground delivery requires
its own action approval; Collie never silently escalates or replays a failed
write. A new observation after the action reports current state, not a claim
that the intended business outcome succeeded.

Use **Open selected menu** (`click` with `expand=true`) on a freshly observed
menu control, then observe and click the exposed command. Modern Notepad's
closed-menu Save hotkey is refused by the driver; the observed File expansion
and Save control route is verified, including file read-back. Native keyboard,
scroll and drag delivery varies by app; a refused or unverifiable action needs
fresh observation and sometimes user takeover. Human approval UI focus effects
on transient menus still need installed-app acceptance.

The manual panel can request a chosen-window screenshot or an explicit whole
desktop capture. Capture-grounded coordinates support window click/type,
scroll and drag. Whole-desktop mode currently supports capture and clicks;
choose an exact window for keyboard input and other operations. Images are
capped at a 1280-pixel long edge and expire with the observation. The driver
performs DPI/capture-coordinate mapping. Changed target pixels or geometry
cause refusal before input. Coordinates are never inferred for a model that
has not seen the screen.

The current **agent route is text-only**, including for configured vision
models. Screenshots stay in the explicit manual panel; they are never sent as
image messages to an incompatible provider. Autonomous visual inference and
capability discovery for model image input are acceptance gaps, not advertised
features. Accessible workflows do not need images. Poorly exposed canvas/game
controls can require manual capture and user assistance.

## Authority and privacy

One service-wide input lease belongs to one chat and task. Other tasks are
refused until the owner stops or its 120-second idle lease expires. **Stop /
take over** in the owning chat cancels pending approval/input and kills the
private driver. The chat's regular Stop also releases it. A stop cannot undo
input already delivered; inspect the app before continuing. Switching chats
does not grant access to another task's controls. Shared sessions are refused.

Every action, including a potentially consequential click/hotkey/type, uses a
hard central approval. Approval summaries identify operation, exact process,
window, scope and background/foreground mode. The user must assess the intended
effect in the visible app. App text, instructions on screen and control labels
cannot grant authority. Passwords, payment details and other secrets must be
entered by the user after takeover. Broad desktop capture can contain private
information, so it always needs explicit permission.

Observations are bounded to 120 nodes/depth 8 by default, at most 250/depth 16,
with a 1500 ms native traversal budget and 24,000 returned text characters.
Query filtering narrows the returned projection; it does not promise a cheap
indexed lookup. Tokens bind to a fresh snapshot, process creation time and
HWND. A read after approval detects changed control state before dispatch.
The worker deadline is 12 seconds. One current observation and 32 content-free
timing/size records are retained in memory; target changes clear native caches.
There is no idle capture/polling, and a one-shot expiry closes idle workers.

Typed input, trees and captures are omitted from tool telemetry, progress
events, debug logs, result offload files and durable engine history/checkpoints.
They are available in the active inference context to perform the authorized
task. User/assistant prose can naturally describe the task and follows normal
chat retention. No secret storage or provider behavior changes here. Worker
environment excludes provider credentials, proxies and remote configuration;
upstream telemetry and background update checks are explicitly disabled.
The allowlist retains required Windows `SystemDrive`/`ProgramData` values, and
the worker uses a private temporary working directory so OS caches cannot land
in the application installation or source directory.

## Installation, packaging and licenses

Install core dependencies explicitly with the normal project installation.
`cua-driver==0.37.0` is Windows-only and requires no Python dependencies. Its
32.8 MB compressed wheel contains approximately 94.5 MB of native payload:
the main executable, a UIA helper executable and the SDK DLL. Only the main
executable is launched; the other two are inventoried because the wheel bundles
them. All three sizes and SHA-256 digests are checked at staging and launch by
`computer/package_check.py` against `runtime-artifacts.json`. An unexpected
payload fails closed. Nothing downloads automatically at runtime.

The upstream MIT license and derivative-project notices ship beside the
controller. No Cua Spaces, Driver Bench, ffmpeg, perception extension, AGPL
detector or weights are included. Upstream derivative notices cover MIT
components including trope-cua, Interface-Agent, QuickJS-ng and rquickjs.
A complete compiled Rust dependency/SBOM license audit remains a release gate;
the file inventory is not a claim that every compiled dependency was audited.

## Verification

Run focused service checks normally. Run actual OS acceptance only in an
interactive Windows desktop with disposable data:
Keep that desktop idle during the focus-preservation assertion; unrelated
user/app focus changes invalidate that measurement.

```powershell
$env:COLLIE_COMPUTER_ACCEPTANCE = '1'
.\collie-core\.venv\Scripts\python.exe -m pytest collie-core/tests/collie/test_computer_acceptance.py -q -s
```

The harness uses the real approval broker and native driver, opens a uniquely
named temporary Notepad file, Calculator and a disposable WPF fixture, and
prints content-free counts/timings. It never clicks or edits another Notepad
document. Test windows/files may need owner cleanup if acceptance is interrupted.
Unit checks cover denial, stale observations/instances, pending-action stop,
two-chat/task arbitration, text-only refusal of images, and bounded schemas.

In a Windows x64 development run on 12 logical AMD64 processors and 34.28 GB
RAM, Calculator arithmetic and read-back edits in modern Notepad/WPF passed.
The same acceptance test also passed from staged Python/native resources,
asserting that imports resolved to the staged package. It verified a saved
Notepad scratch file, window capture/DPI-grounded click, desktop capture and
owning-chat Stop while a real approval was pending. This is staged-runtime
acceptance, not acceptance of an installed release.
Settled two-second driver idle CPU was 0.0 CPU seconds after disabling the
native animated cursor. Representative native background actions took 7–13 ms;
bounded reads took 31–70 ms, with roughly 0.9–8.5 KB native text responses.
Extended staged runs had warm reads up to 131 ms and cold target-switch reads
around 316–363 ms, including native integrity checks/restart. A window pixel
click took 23 ms; 1280-pixel desktop captures took 412–425 ms and 357–495 KB.
These are developer-machine samples, excluding approval wait and model
inference, not guarantees. No model tokens or inference benchmark was measured.
A connection sample showed zero driver-owned network connections; it does not
prove absence of all transient traffic. Full network-isolation capture,
installer acceptance, multi-monitor DPI variations and autonomous visual-model
support remain gates before broader release claims.

When testing staged resources, run the staged interpreter with `-B` (or set
`PYTHONDONTWRITEBYTECODE=1`) and assert `COLLIE_COMPUTER_EXPECT_ROOT` against the
staged package. Run the portability smoke after acceptance, not concurrently:
developer bytecode contains build paths and is deliberately rejected by the
packaging guard. Staging clears every payload entry and refuses linked roots,
while preserving the directory itself for Windows test-host cwd handles.
