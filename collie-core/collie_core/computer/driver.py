"""Private, on-demand MCP transport; never connects to a shared daemon."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
from importlib.metadata import PackageNotFoundError, version
from typing import Any

ALLOWED = frozenset(
    {
        "list_apps",
        "list_windows",
        "get_window_state",
        "get_desktop_state",
        "click",
        "type_text",
        "hotkey",
        "scroll",
        "drag",
        "get_screen_size",
    }
)


def driver_environment() -> dict[str, str]:
    # Do not inherit provider credentials, remote MCP configuration or proxies.
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper()
        in {
            "SYSTEMROOT",
            "SYSTEMDRIVE",
            "PROGRAMDATA",
            "WINDIR",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "LOCALAPPDATA",
            "APPDATA",
            "PATH",
            "PROGRAMFILES",
            "PROGRAMFILES(X86)",
        }
    }
    env.update(
        CUA_DRIVER_RS_TELEMETRY_ENABLED="false",
        CUA_TELEMETRY_ENABLED="false",
        CUA_DRIVER_RS_UPDATE_CHECK="false",
    )
    return env


class LocalDriver:
    def __init__(self) -> None:
        self.process: asyncio.subprocess.Process | None = None
        self.serial = 0
        self.lock = asyncio.Lock()
        self.directory: tempfile.TemporaryDirectory | None = None

    async def _rpc(self, method: str, params: dict, *, notify: bool = False) -> dict:
        self.serial += 1
        frame = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notify:
            frame["id"] = self.serial
        assert self.process and self.process.stdin and self.process.stdout
        self.process.stdin.write((json.dumps(frame) + "\n").encode())
        await self.process.stdin.drain()
        if notify:
            return {}
        deadline = asyncio.get_running_loop().time() + 12
        for _ in range(32):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("Computer worker deadline exceeded.")
            line = await asyncio.wait_for(self.process.stdout.readline(), remaining)
            if not line:
                raise ValueError("Computer worker stopped; check the app before retrying.")
            reply = json.loads(line)
            if reply.get("id") == self.serial:
                if "error" in reply:
                    raise ValueError("Computer driver refused this request.")
                return reply["result"]
        raise ValueError("Unexpected computer worker response.")

    async def call(self, name: str, arguments: dict) -> dict[str, Any]:
        if name not in ALLOWED:
            raise ValueError("That native operation is not exposed by Collie.")
        async with self.lock:
            try:
                if self.process is None:
                    try:
                        compatible = os.name == "nt" and version("cua-driver") == "0.37.0"
                    except PackageNotFoundError:
                        compatible = False
                    if not compatible:
                        raise ValueError("Install Collie's pinned Windows computer runtime.")
                    from cua_driver.wrapper import get_binary_path

                    from collie_core.computer.package_check import validate_artifacts

                    await asyncio.to_thread(validate_artifacts)
                    self.directory = tempfile.TemporaryDirectory(
                        prefix="collie-computer-", ignore_cleanup_errors=True
                    )

                    self.process = await asyncio.create_subprocess_exec(
                        str(get_binary_path()),
                        "mcp",
                        "--direct",
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                        limit=4 * 1024 * 1024,
                        env=driver_environment(),
                        cwd=self.directory.name,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    await self._rpc(
                        "initialize",
                        {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "Collie", "version": "0.2.2"},
                        },
                    )
                    await self._rpc("notifications/initialized", {}, notify=True)
                    await self._rpc(
                        "tools/call",
                        {
                            "name": "set_agent_cursor_enabled",
                            "arguments": {"session": "collie", "enabled": False},
                        },
                    )
                async with asyncio.timeout(12):
                    return await self._rpc(
                        "tools/call",
                        {
                            "name": name,
                            "arguments": {**arguments, "session": "collie"}
                            if name != "get_screen_size"
                            else arguments,
                        },
                    )
            except BaseException:
                await self.close()
                raise

    async def close(self) -> None:
        process, self.process = self.process, None
        directory, self.directory = self.directory, None
        if process and process.returncode is None:
            process.kill()
            await asyncio.wait_for(process.wait(), 3)
        if directory:
            directory.cleanup()
