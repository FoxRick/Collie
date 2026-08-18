"""Process-hygiene helpers for the desktop core.

Two failure modes they cover, both observed on the QA rig:

1. **Orphaned core after the app dies.** When the Electron main process dies
   hard (OOM kill, unhandled exception — no ``will-quit``, no Crashpad dump),
   the Python core it spawned is reparented and keeps running, holding the
   fixed IPC port. The next app launch then fails to bind: the boot probe
   times out and the splash stays up forever.

2. **Port collision with a leftover core.** Even with the watchdog in place,
   a core from an OLD build (pre-watchdog) can still be squatting on the
   port when the user relaunches.

``arm_parent_watchdog`` makes the core exit when its spawning shell dies
(immediate on Linux via ``PR_SET_PDEATHSIG``, polled elsewhere), and
``reclaim_stale_core_port`` clears any leftover core before the new one
binds.
"""

from __future__ import annotations

import ctypes
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterable

WATCHDOG_INTERVAL_S = 5.0
_RECLAIM_GRACE_S = 1.5
_RECLAIM_TIMEOUT_S = 5.0


# -- parent-death watchdog -------------------------------------------------------


def _arm_pdeathsig_linux() -> None:
    """Ask the kernel to SIGTERM us the moment our parent dies (Linux only)."""
    if sys.platform != "linux":
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except Exception:
        return  # polling fallback below still covers it
    # Race: the parent can die between getppid() and prctl(). If we were
    # already reparented to init, the kernel signal may have been delivered
    # to the OLD process slot semantics — check and exit now.
    if os.getppid() <= 1:
        os._exit(0)


def _parent_still_alive(original: int) -> bool:
    """True when the spawning shell process appears to still be running."""
    if sys.platform == "win32":
        # getppid() keeps returning the stale pid on Windows, so probe the
        # process table directly. Any uncertainty means "assume alive" —
        # under-killing beats killing a live shell's core.
        try:
            import ctypes
            from ctypes import wintypes

            process_query_limited = 0x1000
            still_active = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(process_query_limited, False, wintypes.DWORD(original))
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return True
                return code.value == still_active
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return True
    return os.getppid() == original


def arm_parent_watchdog(interval: float = WATCHDOG_INTERVAL_S) -> None:
    """Exit this process if the shell that spawned it dies.

    No-op when already daemonized (parent is init, ppid <= 1) or when the
    platform can neither signal nor poll liveness — under those conditions
    the core is expected to outlive its launcher.
    """
    parent = os.getppid()
    if parent <= 1:
        return
    _arm_pdeathsig_linux()
    if not _parent_still_alive(parent):  # died between getppid and arming
        os._exit(0)

    def _watch() -> None:
        while True:
            time.sleep(interval)
            try:
                if not _parent_still_alive(parent):
                    os._exit(0)
            except Exception:
                os._exit(0)

    threading.Thread(target=_watch, name="collie-parent-watchdog", daemon=True).start()


# -- stale-core port reclaim -----------------------------------------------------


def _port_in_use(port: int) -> bool:
    """True when anything is accepting TCP connections on 127.0.0.1:port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def _linux_core_holders(port: int) -> list[int]:
    """PIDs of collie_core.runtime processes bound to the given --port."""
    holders: list[int] = []
    for entry in _proc_entries():
        pid = entry.pid
        if pid == os.getpid():
            continue
        cmdline = entry.cmdline
        if "collie_core.runtime" not in cmdline:
            continue
        if "--port" not in cmdline:
            continue
        if str(port) not in cmdline:
            continue
        holders.append(pid)
    return holders


def _windows_core_holders(port: int) -> list[int]:
    """Same via PowerShell (present on all supported Windows versions)."""
    try:
        script = (
            "Get-CimInstance Win32_Process | Where-Object {"
            " $_.CommandLine -match 'collie_core[.]runtime'"
            f" -and $_.CommandLine -match '--port {port}'"
            " -and $_.ProcessId -ne $PID"
            "} | ForEach-Object { $_.ProcessId }"
        )
        output = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        pids: list[int] = []
        for line in output.splitlines():
            line = line.strip()
            if line.isdigit():
                pids.append(int(line))
        return pids
    except Exception:
        return []


def _core_holders(port: int) -> list[int]:
    if sys.platform == "win32":
        return _windows_core_holders(port)
    return _linux_core_holders(port)


def _terminate(pids: Iterable[int]) -> None:
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            continue
    deadline = time.monotonic() + _RECLAIM_GRACE_S
    remaining = [pid for pid in pids if _pid_alive(pid)]
    while remaining and time.monotonic() < deadline:
        time.sleep(0.15)
        remaining = [pid for pid in remaining if _pid_alive(pid)]
    for pid in remaining:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            continue


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def reclaim_stale_core_port(port: int, *, timeout_s: float = _RECLAIM_TIMEOUT_S) -> bool:
    """Free the IPC port by terminating leftover cores, if any.

    Only ever kills processes whose command line runs ``collie_core.runtime``
    with the same ``--port``. A live app instance is protected by Electron's
    single-instance lock, so a same-port holder can only be the leftover of a
    crashed app. Returns True when the port is free afterwards.
    """
    if not _port_in_use(port):
        return True
    _terminate(_core_holders(port))
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and _port_in_use(port):
        time.sleep(0.25)
    return not _port_in_use(port)


class _ProcEntry:
    __slots__ = ("pid", "cmdline")

    def __init__(self, pid: int, cmdline: list[str]) -> None:
        self.pid = pid
        self.cmdline = cmdline


def _proc_entries() -> list[_ProcEntry]:
    """Best-effort (pid, cmdline) scan. Linux /proc only; empty elsewhere."""
    entries: list[_ProcEntry] = []
    try:
        proc = os.listdir("/proc")
    except OSError:
        return entries
    for name in proc:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/cmdline", "rb") as handle:
                raw = handle.read()
        except OSError:
            continue
        if not raw:
            continue
        entries.append(
            _ProcEntry(int(name), raw.replace(b"\x00", b" ").decode("utf-8", "replace").split())
        )
    return entries
