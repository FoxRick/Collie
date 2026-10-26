"""Opt-in real Windows desktop acceptance; outputs counts/timings, never app content.

Requires disposable Calculator and a uniquely titled Notepad test window. Set
COLLIE_COMPUTER_ACCEPTANCE=1; setup creates only temporary files. No model API.
"""

import asyncio
import ctypes
import json
import os
import subprocess
from pathlib import Path

import pytest

from collie_core.computer.service import ComputerService
from collie_core.db import CollieDB
from collie_core.permissions.broker import ApprovalBroker, PermissionDeniedError
from collie_core.permissions.evaluator import PermissionEvaluator
from collie_core.permissions.models import ExecutionContext

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.getenv("COLLIE_COMPUTER_ACCEPTANCE") != "1",
    reason="Explicit interactive Windows acceptance only",
)


@pytest.mark.asyncio
async def test_real_windows_accessibility_and_broker(tmp_path):
    db = CollieDB(tmp_path / "acceptance.db")
    reject = False
    hold = False
    pending_approval = asyncio.Event()
    approvals = []

    async def review(event):
        if event["type"] != "approval_requested":
            return
        if hold:
            pending_approval.set()
            return
        approvals.append(event["approval"]["action"])
        await broker.resolve(event["approval"]["id"], "reject" if reject else "allow_once")

    broker = ApprovalBroker(db, PermissionEvaluator(), review, timeout_seconds=3)
    computer = ComputerService(broker)
    ctx = ExecutionContext(conversation_id="disposable-acceptance", run_id="local-test")
    wpf = None
    try:
        if expected_root := os.getenv("COLLIE_COMPUTER_EXPECT_ROOT"):
            import collie_core

            assert Path(collie_core.__file__).is_relative_to(Path(expected_root)), (
                "Acceptance imported source instead of staged runtime"
            )
        await computer.execute(ctx, {"operation": "windows"})
        driver = computer.driver
        # Harness-only startup is intentionally absent from the product tool allowlist.
        scratch = tmp_path / "Collie-computer-acceptance.txt"
        scratch.write_text("", encoding="utf-8")
        startup = subprocess.STARTUPINFO()
        startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 4  # SW_SHOWNOACTIVATE; the OS app broker may still activate.
        subprocess.Popen(
            [str(Path(os.environ["SystemRoot"]) / "System32" / "notepad.exe"), str(scratch)],
            startupinfo=startup,
        )
        await asyncio.sleep(1)
        await driver._rpc("tools/call", {"name": "launch_app", "arguments": {"name": "Calculator"}})
        await asyncio.sleep(1)
        windows = (await computer.execute(ctx, {"operation": "windows"}))["windows"]
        calc = next((w for w in windows if w.get("title") == "Calculator"), None)
        note = next(
            (w for w in windows if "Collie-computer-acceptance" in w.get("title", "")), None
        )
        assert calc is not None, "Disposable Calculator window unavailable"
        assert note is not None, (
            "Unique modern Notepad window unavailable; refusing other documents"
        )

        async def observe(window, **kwargs):
            return await computer.execute(
                ctx,
                {
                    "operation": "observe",
                    "pid": window["pid"],
                    "window_id": window["window_id"],
                    **kwargs,
                },
            )

        async def click_label(window, label):
            obs = await observe(window, max_elements=250)
            row = next((e for e in obs["elements"] if e.get("label") == label), None)
            assert row is not None, "Required accessible Calculator control unavailable"
            return await computer.execute(
                ctx,
                {
                    "operation": "click",
                    "pid": window["pid"],
                    "window_id": window["window_id"],
                    "observation_id": obs["observation_id"],
                    "element_token": row["element_token"],
                },
            )

        foreground = ctypes.windll.user32.GetForegroundWindow()
        for label in ["Clear", "One", "Plus", "Two", "Equals"]:
            result = await click_label(calc, label)
        rows = result["postcondition"]["elements"]
        assert any(
            "3" in str(e.get("label", "")) and "Display" in str(e.get("label", "")) for e in rows
        ), "Calculator result did not verify"
        assert ctypes.windll.user32.GetForegroundWindow() == foreground, (
            "Background action changed foreground"
        )

        obs = await observe(note, query="Text editor", max_elements=250)
        row = next((e for e in obs["elements"] if e.get("role") == "Document"), None)
        assert row is not None, "Modern Notepad document control unavailable"
        result = await computer.execute(
            ctx,
            {
                "operation": "type",
                "pid": note["pid"],
                "window_id": note["window_id"],
                "observation_id": obs["observation_id"],
                "element_token": row["element_token"],
                "text": "Collie disposable local edit",
            },
        )
        # UIA ValuePattern write must be independently visible in a fresh read.
        verified = any(
            "Collie disposable local edit" in str(e.get("value", ""))
            for e in result["postcondition"]["elements"]
        )
        assert verified, "Notepad edit could not be read back"
        result = await computer.execute(
            ctx,
            {
                "operation": "hotkey",
                "pid": note["pid"],
                "window_id": note["window_id"],
                "observation_id": result["postcondition"]["observation_id"],
                "keys": ["ctrl", "s"],
                "delivery_mode": "foreground",
            },
        )
        assert "Collie disposable local edit" in scratch.read_text(encoding="utf-8"), (
            "Approved Notepad save hotkey did not verify"
        )
        # Harness cleanup closes only the just-saved disposable tab, without reading another tab.
        await driver.call(
            "hotkey",
            {
                "target": {"kind": "window", "pid": note["pid"], "window_id": note["window_id"]},
                "keys": ["ctrl", "w"],
                "delivery_mode": "foreground",
            },
        )

        wpf = subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-STA",
                "-WindowStyle",
                "Hidden",
                "-File",
                str(Path(__file__).parents[1] / "fixtures" / "computer-wpf.ps1"),
            ],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        await asyncio.sleep(2)
        windows = (await computer.execute(ctx, {"operation": "windows"}))["windows"]
        fixture = next(
            (w for w in windows if w.get("title") == "Collie disposable WPF acceptance"), None
        )
        assert fixture is not None, "Disposable WPF fixture unavailable"
        obs = await observe(fixture)
        row = next((e for e in obs["elements"] if e.get("label") == "Disposable draft"), None)
        assert row is not None, "Accessible WPF draft control unavailable"
        result = await computer.execute(
            ctx,
            {
                "operation": "type",
                "pid": fixture["pid"],
                "window_id": fixture["window_id"],
                "observation_id": obs["observation_id"],
                "element_token": row["element_token"],
                "text": "Disposable WPF edit",
            },
        )
        assert any(
            e.get("value") == "Disposable WPF edit" for e in result["postcondition"]["elements"]
        ), "WPF edit did not verify"
        result = await click_label(fixture, "Apply locally")
        print(
            json.dumps(
                {
                    "wpf_action_route": result["action"].get("route"),
                    "wpf_action_effect": result["action"].get("effect"),
                }
            )
        )
        await asyncio.sleep(0.2)  # WPF Invoke is dispatched asynchronously; never replay the click.
        result["postcondition"] = await observe(fixture)
        assert any(
            e.get("label") == "Applied locally 1" for e in result["postcondition"]["elements"]
        ), "WPF click did not verify"

        capture = await computer.execute(
            ctx,
            {
                "operation": "observe",
                "pid": fixture["pid"],
                "window_id": fixture["window_id"],
                "screenshot": True,
            },
            images_allowed=True,
        )
        assert bool(capture.get("screenshot")), "Chosen-window capture unavailable"
        bounds = capture["window_bounds"]
        row = next(e for e in capture["elements"] if e.get("label") == "Applied locally 1")
        frame = row["frame"]
        x = (
            (frame["x"] + frame["w"] / 2 - bounds["x"])
            * capture["screenshot_width"]
            / bounds["width"]
        )
        y = (
            (frame["y"] + frame["h"] / 2 - bounds["y"])
            * capture["screenshot_height"]
            / bounds["height"]
        )
        result = await computer.execute(
            ctx,
            {
                "operation": "click",
                "pid": fixture["pid"],
                "window_id": fixture["window_id"],
                "observation_id": capture["observation_id"],
                "x": x,
                "y": y,
            },
            images_allowed=True,
        )
        assert any(
            e.get("label") == "Applied locally 2" for e in result["postcondition"]["elements"]
        ), "Capture/DPI-grounded click did not verify"
        desktop = await computer.execute(ctx, {"operation": "desktop"}, images_allowed=True)
        assert bool(desktop.get("screenshot")), "Explicit desktop capture unavailable"

        reject = True
        with pytest.raises(PermissionDeniedError):
            await observe(calc)
        reject = False
        await observe(calc)
        with pytest.raises(ValueError, match="Another task"):
            await computer.execute(
                ExecutionContext(conversation_id="second-chat"), {"operation": "windows"}
            )
        with pytest.raises(ValueError):
            await computer.execute(
                ctx, {"operation": "observe", "pid": calc["pid"], "window_id": 1}
            )
        await observe(calc)
        process = driver.process
        import psutil

        native = psutil.Process(process.pid)
        await asyncio.sleep(3)
        before = sum(native.cpu_times()[:2])
        await asyncio.sleep(2)
        idle_cpu_seconds = sum(native.cpu_times()[:2]) - before
        connections = native.net_connections()
        metrics = computer.metrics.copy()
        hold = True
        pending = asyncio.create_task(observe(calc))
        await pending_approval.wait()
        await computer.execute(ctx, {"operation": "stop"})
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not db.list_pending_approvals(), "Stop left an active approval card"
        assert process.returncode is not None
        assert not connections, "Driver unexpectedly owns network connections"
        print(
            json.dumps(
                {
                    "runtime": "cua-driver 0.37.0",
                    "metrics": metrics,
                    "idle_seconds": 2,
                    "idle_cpu_seconds": idle_cpu_seconds,
                    "driver_connections_at_sample": len(connections),
                    "approvals": len(approvals),
                    "cpu": os.getenv("PROCESSOR_IDENTIFIER"),
                    "logical_processors": os.cpu_count(),
                    "ram_bytes": psutil.virtual_memory().total,
                }
            )
        )
    finally:
        await computer.close()
        if wpf and wpf.poll() is None:
            wpf.terminate()
            wpf.wait(timeout=3)
        db.close()
