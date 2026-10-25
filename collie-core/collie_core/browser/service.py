"""Task-scoped, centrally authorized supervisor for disposable visible browsers."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from collie_core.permissions.models import ExecutionContext, PermissionRequest, Risk, Scope

OPERATIONS = frozenset(
    {"open", "navigate", "observe", "fill", "click", "screenshot", "handoff", "close", "list"}
)


class BrowserCommand(BaseModel):
    """Bounded data contract accepted from either UI or tools."""

    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal[
        "open", "navigate", "observe", "fill", "click", "screenshot", "handoff", "close", "list"
    ]
    session_id: str | None = Field(default=None, max_length=64)
    url: str | None = Field(default=None, max_length=2048)
    selector: str | None = Field(default=None, max_length=512)
    text: str | None = Field(default=None, max_length=4000)


def origin(url: str) -> str:
    """Require explicit HTTP origins without credentials, queries or fragments."""
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Use an HTTP or HTTPS address without credentials.")
    if parsed.query or parsed.fragment or len(url) > 2048:
        raise ValueError(
            "Open a page without query strings or fragments; enter searches on the page."
        )
    return f"{parsed.scheme}://{parsed.netloc.lower()}"


@dataclass
class BrowserSession:
    id: str
    conversation_id: str
    task_id: str | None
    origin: str
    process: asyncio.subprocess.Process | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    handed_off: bool = False


class _Permission:
    def __init__(self, request: PermissionRequest):
        self.request = request

    def permission_request(self, _params: dict) -> PermissionRequest:
        return self.request


class BrowserService:
    """Single implementation for IPC and agent tools. Every effect is approved here.

    A session is owned by one conversation and task; IPC may control only sessions
    with no agent task, except that it may always close/handoff its own browser.
    Shared execution is refused until separate browser audience authority exists.
    """

    def __init__(self, authorizer: Any, *, channel: str = "msedge"):
        self.authorizer = authorizer
        self.channel = channel
        self.sessions: dict[str, BrowserSession] = {}
        self._opening = asyncio.Lock()

    async def execute(self, context: ExecutionContext, command: dict[str, Any]) -> dict:
        try:
            command = BrowserCommand.model_validate(command).model_dump(exclude_none=True)
        except ValidationError:
            raise ValueError(
                "Choose a valid, bounded browser action; extra fields are refused."
            ) from None
        operation = command.get("operation")
        if operation not in OPERATIONS:
            raise ValueError("Unknown browser operation.")
        if not context.conversation_id or context.shared_session_id:
            raise ValueError("Browser work needs a private local conversation.")
        if operation == "list":
            return {
                "sessions": [
                    self._info(s)
                    for s in self.sessions.values()
                    if s.conversation_id == context.conversation_id
                ]
            }
        if operation == "open":
            async with self._opening:
                if len(self.sessions) >= 3:
                    raise ValueError("Close a browser first (up to three sessions).")
                if importlib.util.find_spec("playwright") is None:
                    raise ValueError(
                        "Collie's browser runtime is missing. Repair or reinstall Collie, and check that Microsoft Edge is installed."
                    )
                site = origin(str(command.get("url") or ""))
                session = BrowserSession(uuid4().hex, context.conversation_id, context.run_id, site)
                await self._authorize(context, session, command)
                self.sessions[session.id] = session
                try:
                    return await self._send(
                        session, {**command, "origin": site, "channel": self.channel}
                    )
                except BaseException:
                    await self._close(session)
                    raise
        session = self.sessions.get(str(command.get("session_id") or ""))
        if session is None or session.conversation_id != context.conversation_id:
            raise ValueError("That browser is not owned by this conversation.")
        if operation == "close":
            await self._close(session)
            return {"closed": True}
        if (
            session.task_id != context.run_id
            and operation != "handoff"
            and not (context.origin == "browser_ui" and operation in {"observe", "screenshot"})
        ):
            raise ValueError("That browser belongs to another task. Close it or take over visibly.")
        if session.handed_off:
            raise ValueError(
                "You have control of that browser. Close it when finished; automation cannot resume."
            )
        if operation == "navigate" and origin(str(command.get("url") or "")) != session.origin:
            raise ValueError("Open a separate approved browser for a different origin.")
        if (
            len(str(command.get("selector") or "")) > 512
            or len(str(command.get("text") or "")) > 4000
        ):
            raise ValueError("That browser input is too long.")
        async with session.lock:
            await self._authorize(context, session, command)
            if self.sessions.get(session.id) is not session:
                raise ValueError("That browser was stopped while waiting for approval.")
            result = await self._send(session, command)
            if operation == "handoff":
                session.handed_off = True
                result["handed_off"] = True
            return result

    async def _authorize(
        self, context: ExecutionContext, session: BrowserSession, command: dict
    ) -> None:
        op = str(command["operation"])
        # Even fill can trigger autosave; naming an operation never makes it safe.
        request = PermissionRequest(
            action=f"browser.{op}",
            resource=f"browser:{session.conversation_id}:{session.task_id or 'manual'}:{session.id}:{session.origin}",
            risk=Risk.EXTERNAL_WRITE
            if op in {"open", "navigate", "click", "fill", "handoff"}
            else Risk.SENSITIVE,
            summary=(
                f"Browser: {op} on {session.origin}. Page scripts may cause external effects. "
                "Final submissions and sign-in need your visible handoff."
            ),
            reversible=False,
            data_leaving_device=(session.origin,),
            suggested_scope=Scope.ONCE,
            redacted_parameters={
                "operation": op,
                "origin": session.origin,
                "session_id": session.id,
                "task_id": session.task_id,
                "selector": str(command.get("selector") or "")[:512],
                "input": "[not recorded]" if op == "fill" else None,
            },
            hard_approval=True,
        )
        await self.authorizer.authorize(
            context, SimpleNamespace(name="browser", id=uuid4().hex), _Permission(request), {}
        )

    @staticmethod
    def _info(session: BrowserSession) -> dict:
        return {
            "session_id": session.id,
            "origin": session.origin,
            "task_id": session.task_id,
            "handed_off": session.handed_off,
        }

    async def _send(self, session: BrowserSession, command: dict) -> dict:
        try:
            if session.process is None:
                session.process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "collie_core.browser.worker",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    limit=256 * 1024,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            process = session.process
            process.stdin.write((json.dumps(command) + "\n").encode())
            await process.stdin.drain()
            line = await asyncio.wait_for(process.stdout.readline(), 35)
            if not line:
                raise RuntimeError("worker exited")
            reply = json.loads(line)
            if "error" in reply:
                raise ValueError(reply["error"])
            return {**reply, **self._info(session)}
        except asyncio.CancelledError:
            await self._close(session)
            raise
        except (TimeoutError, OSError, RuntimeError, ValueError):
            await self._close(session)
            raise ValueError(
                "Browser stopped. No action was retried; an effect may already have happened. Open a fresh browser after checking the visible result."
            ) from None

    async def _close(self, session: BrowserSession) -> None:
        self.sessions.pop(session.id, None)
        process = session.process
        if process is None or process.returncode is not None:
            return
        # Normal closure lets Playwright dispose its temporary profile. A wedged
        # action gets only a short grace period before the entire process tree dies.
        process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), 2)
            return
        except TimeoutError:
            pass
        # Stop the driver and its browser children, including a wedged worker.
        if os.name == "nt":
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            await asyncio.wait_for(killer.wait(), 5)
        else:
            process.terminate()
        await asyncio.wait_for(process.wait(), 5)

    async def close_conversation(self, conversation_id: str) -> None:
        for session in list(self.sessions.values()):
            if session.conversation_id == conversation_id:
                await self._close(session)

    async def close(self) -> None:
        for session in list(self.sessions.values()):
            await self._close(session)
