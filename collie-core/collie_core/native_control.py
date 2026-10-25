"""Supervised, session-scoped accessibility control of unsaved classic Notepad."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from collie_core.permissions.models import ExecutionContext, PermissionRequest, Risk

# Fixed program, never interpolated shell input. Each request has a fresh killable MTA worker.
_WORKER = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$q = [Console]::In.ReadToEnd() | ConvertFrom-Json
function Identity($w) {
  $p = Get-Process -Id $w.Current.ProcessId
  @{ hwnd=$w.Current.NativeWindowHandle; pid=$p.Id; started=$p.StartTime.ToUniversalTime().Ticks.ToString(); runtime=($w.GetRuntimeId() -join '.'); title=$w.Current.Name }
}
function Supported($w) {
  $p = Get-Process -Id $w.Current.ProcessId
  ($p.ProcessName -eq 'notepad' -and $w.Current.ClassName -eq 'Notepad' -and $w.Current.Name -eq 'Untitled - Notepad')
}
$root = [System.Windows.Automation.AutomationElement]::RootElement
if ($q.operation -eq 'discover') {
  $items = @()
  foreach ($w in $root.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)) {
    if (Supported $w) { $items += (Identity $w) }
  }
  ConvertTo-Json -InputObject @{windows=$items} -Compress -Depth 5
  exit
}
$w = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]([int]$q.target.hwnd))
if (!$w -or !(Supported $w)) { throw 'The selected unsaved Notepad window is no longer available.' }
$now = Identity $w
foreach ($key in @('hwnd','pid','started','runtime','title')) {
  if ([string]$now[$key] -cne [string]$q.target.$key) { throw 'The selected window changed. Select it again.' }
}
# Classic Edit only: no menus, dialogs, buttons, password fields, Save or Send.
$condition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ClassNameProperty, 'Edit')
$edits = $w.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition)
if ($edits.Count -ne 1) { throw 'This Notepad version has no supported classic editor. Use a classic unsaved Notepad window.' }
$e = $edits.Item(0)
if ($e.Current.IsPassword -or !$e.Current.IsEnabled) { throw 'This editor is unavailable.' }
switch ($q.operation) {
 'observe' { }
 'focus' { $e.SetFocus() }
 'click' { $e.SetFocus() } # semantic editor focus; no coordinate click
 'type' {
   $v = $e.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
   if ($v.Current.IsReadOnly) { throw 'This editor is read-only.' }
   $v.SetValue([string]$q.text)
 }
 'scroll' {
   $s = $e.GetCurrentPattern([System.Windows.Automation.ScrollPattern]::Pattern)
   $amount = [System.Windows.Automation.ScrollAmount]::SmallIncrement
   if ($q.direction -eq 'up') { $amount = [System.Windows.Automation.ScrollAmount]::SmallDecrement }
   $s.Scroll([System.Windows.Automation.ScrollAmount]::NoAmount,$amount)
 }
 default { throw 'Unsupported native operation.' }
}
# Do not read document text, names of descendants, clipboard, or screenshots.
@{ok=$true; editor=@{class='Edit'; password=$false}; operation=$q.operation} | ConvertTo-Json -Compress -Depth 4
"""


class WindowsAdapter:
    async def call(self, payload: dict[str, Any]) -> dict[str, Any]:
        if sys.platform != "win32":
            raise ValueError(
                "Native control needs Windows and Windows PowerShell with UI Automation."
            )
        import base64
        import os
        from pathlib import Path

        executable = (
            Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
            / "System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        if not executable.is_file():
            raise ValueError(
                "Windows PowerShell is unavailable. Restore the Windows component to use native control."
            )
        process = await asyncio.create_subprocess_exec(
            str(executable),
            "-NoProfile",
            "-NonInteractive",
            "-Mta",
            "-EncodedCommand",
            base64.b64encode(_WORKER.encode("utf-16-le")).decode(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=0x08000000,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(json.dumps(payload).encode()),
                12,
            )
            if process.returncode:
                raise ValueError(
                    "The selected editor changed or does not support this action. Select an unsaved classic Notepad window again."
                )
            return json.loads(stdout.decode("utf-8-sig"))
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()


@dataclass(frozen=True)
class Selection:
    target: dict[str, Any]
    scope: str


# One lease across every runtime/chat in this process, including time spent approving.
_INPUT_LEASE = asyncio.Lock()


class NativeControl:
    def __init__(self, broker: Any, adapter: Any = None) -> None:
        self.broker = broker
        self.adapter = adapter or WindowsAdapter()
        self.selections: dict[str, Selection] = {}
        self.active: dict[str, asyncio.Task] = {}

    async def discover(self) -> dict[str, Any]:
        return await self.adapter.call({"operation": "discover"})

    async def select(self, conversation: str, target: dict[str, Any], scope: str) -> None:
        if scope != "unsaved_editor" or not conversation:
            raise ValueError("Choose a chat and the unsaved-editor scope.")
        windows = (await self.discover())["windows"]
        if target not in windows:
            raise ValueError("The window changed. Refresh and select it again.")
        self.stop(conversation)
        self.selections[conversation] = Selection(dict(target), scope)

    def stop(self, conversation: str) -> None:
        self.selections.pop(conversation, None)
        task = self.active.get(conversation)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def stop_all(self) -> None:
        for conversation in tuple(self.selections):
            self.stop(conversation)

    async def execute(
        self, context: ExecutionContext, operation: str, text: str = "", direction: str = "down"
    ) -> dict[str, Any]:
        conversation = context.conversation_id or ""
        if context.shared_session_id or context.origin != "chat":
            raise ValueError("Native control is available only in a private desktop chat.")
        selection = self.selections.get(conversation)
        if selection is None:
            raise ValueError("Select an unsaved Notepad window in this chat first.")
        if (
            operation not in {"observe", "focus", "click", "type", "scroll"}
            or direction not in {"up", "down"}
            or len(text) > 16000
        ):
            raise ValueError("Unsupported native action or text longer than 16,000 characters.")
        if _INPUT_LEASE.locked():
            raise ValueError("Another native action is active. Wait for it to finish or stop it.")
        async with _INPUT_LEASE:
            task = asyncio.current_task()
            self.active[conversation] = task
            try:
                return await self._perform(context, operation, text, direction, selection)
            except asyncio.CancelledError:
                self.stop(conversation)
                raise
            finally:
                self.active.pop(conversation, None)

    async def _perform(
        self,
        context: ExecutionContext,
        operation: str,
        text: str,
        direction: str,
        selection: Selection,
    ) -> dict[str, Any]:
        conversation = context.conversation_id or ""
        params = {
            "operation": operation,
            "target": selection.target,
            "text": text,
            "direction": direction,
        }
        request = PermissionRequest(
            action="native.interact",
            resource=json.dumps(selection.target, sort_keys=True),
            risk=Risk.SENSITIVE,
            summary=f"{operation} in the selected unsaved Notepad editor",
            reversible=False,
            hard_approval=True,
            redacted_parameters={
                "operation": operation,
                "text": text,
                "direction": direction,
                "scope": selection.scope,
            },
        )
        policy = SimpleNamespace(permission_request=lambda _: request)
        await self.broker.authorize(
            context, SimpleNamespace(name="native_control", id=""), policy, params
        )
        if self.selections.get(conversation) is not selection:
            raise ValueError("Native control was stopped or the target changed.")
        # Worker revalidates identity immediately before performing the operation.
        try:
            return await self.adapter.call(params)
        except BaseException:
            self.stop(conversation)
            raise
