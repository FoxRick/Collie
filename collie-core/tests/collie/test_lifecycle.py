"""Tests for collie_core.lifecycle (parent watchdog + stale-core port reclaim)."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from collie_core.lifecycle import reclaim_stale_core_port

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="subprocess lifecycle tests target the POSIX CI/AppImage path",
)


def _python() -> str:
    return sys.executable


def _run_until_marker(cmd: list[str], marker: str, log: Path, timeout_s: float) -> subprocess.Popen:
    """Start a process and wait until its stdout log contains the marker."""
    with log.open("w", encoding="utf-8") as handle:
        proc = subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT, env=os.environ.copy())
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(
                f"process exited early ({proc.returncode}): {log.read_text()[-500:]}"
            )
        if marker in log.read_text(encoding="utf-8", errors="replace"):
            return proc
        time.sleep(0.1)
    proc.kill()
    raise AssertionError(f"marker {marker!r} not seen: {log.read_text()[-500:]}")


def test_watchdog_exits_when_parent_is_killed(tmp_path: Path) -> None:
    """A hard-killed parent (the app crash case) takes the core down with it."""
    core_script = textwrap.dedent(
        """
        import time
        from collie_core.lifecycle import arm_parent_watchdog

        arm_parent_watchdog(interval=0.2)
        print("CORE_ARMED", flush=True)
        time.sleep(300)
        """
    )
    shell_script = textwrap.dedent(
        f"""
        import subprocess, sys, time
        subprocess.Popen([sys.executable, "-c", {core_script!r}])
        print("shell up", flush=True)
        time.sleep(300)
        """
    )
    shell_log = tmp_path / "shell.log"
    shell = _run_until_marker([_python(), "-c", shell_script], "shell up", shell_log, timeout_s=15)
    # Find the grandchild core: the shell's child.
    core_pid = _child_pid(shell.pid)
    assert core_pid is not None, "core process not found under the shell"

    # The core arms its watchdog during startup. Killing the shell before
    # that arms it tests the startup race, not the watchdog: a still-booting
    # core sees the dead shell as "already daemonized" (ppid == init) and
    # skips arming entirely. Coverage-instrumented runs widen the window
    # (slower subprocess imports) and flake deterministically. Wait for the
    # arm marker — the core's stdout lands in the same inherited log.
    armed_deadline = time.monotonic() + 30
    while time.monotonic() < armed_deadline:
        if "CORE_ARMED" in shell_log.read_text(encoding="utf-8", errors="replace"):
            break
        time.sleep(0.1)
    else:
        pytest.fail("core never armed its watchdog (CORE_ARMED not seen)")

    # Simulate a crash: SIGKILL, no cleanup, no signal to the child.
    os.kill(shell.pid, signal.SIGKILL)

    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        try:
            os.kill(core_pid, 0)
        except OSError:
            return  # core exited — watchdog worked
        time.sleep(0.2)
    pytest.fail("core survived its dead parent (watchdog did not fire)")


def _child_pid(parent: int) -> int | None:
    """First child of `parent` via /proc (POSIX)."""
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # stat format: pid (comm) state ppid ...
        try:
            right = stat.rindex(")")
            fields = stat[right + 2 :].split()
            if int(fields[1]) == parent:
                return int(entry.name)
        except (ValueError, IndexError):
            continue
    return None


def test_reclaim_kills_stale_core_holding_the_port(tmp_path: Path) -> None:
    """A leftover runtime on the IPC port is terminated so the next boot binds."""
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    home = tmp_path / "home"
    log = tmp_path / "orphan.log"
    env = os.environ.copy()
    env["COLLIE_HOME"] = str(home)
    orphan = _run_until_marker(
        [_python(), "-m", "collie_core.runtime", "--port", str(port)],
        "COLLIE_READY",
        log,
        timeout_s=30,
    )
    try:
        assert reclaim_stale_core_port(port) is True
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and orphan.poll() is None:
            time.sleep(0.1)
        assert orphan.poll() is not None, "stale core was not terminated"
    finally:
        orphan.kill()
        orphan.wait(timeout=5)


def test_reclaim_is_noop_when_port_is_free(tmp_path: Path) -> None:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    started = time.monotonic()
    assert reclaim_stale_core_port(port) is True
    assert time.monotonic() - started < 2  # free port returns immediately


def test_reclaim_leaves_other_processes_alone(tmp_path: Path) -> None:
    """A process that does not look like a collie core is never touched."""
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    # A dummy server on the port whose cmdline is NOT a collie runtime.
    script = (
        "import socket,time,sys\n"
        "s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        f"s.bind(('127.0.0.1', {port})); s.listen(); print('DUMMY_UP', flush=True)\n"
        "time.sleep(120)\n"
    )
    log = tmp_path / "dummy.log"
    proc = _run_until_marker([_python(), "-c", script], "DUMMY_UP", log, timeout_s=10)
    try:
        assert reclaim_stale_core_port(port) is False  # can't free it, must not kill it
        assert proc.poll() is None, "reclaim killed an unrelated process"
    finally:
        proc.kill()
        proc.wait(timeout=5)
