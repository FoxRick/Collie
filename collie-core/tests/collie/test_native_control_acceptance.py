"""One narrow public-interface acceptance gate; no real desktop writes."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from collie_core.db import CollieDB
from collie_core.native_control import NativeControl
from collie_core.permissions.broker import ApprovalBroker, PermissionDeniedError
from collie_core.permissions.evaluator import PermissionEvaluator
from collie_core.permissions.models import ExecutionContext
from collie_core.tools.native_control import NativeControlTool


class ControlledEditor:
    """Adapter contract fake: identity can change while approval is pending."""

    def __init__(self):
        self.window = dict(hwnd=12, pid=9, started="101", runtime="1.2", title="Untitled - Notepad")
        self.actions = []
        self.entered = asyncio.Event()
        self.block = False
        self.cancelled = False

    async def call(self, payload):
        if payload["operation"] == "discover":
            return {"windows": [dict(self.window)]}
        if payload["target"] != self.window:
            raise ValueError("stale window")
        self.entered.set()
        if self.block:
            try:
                await asyncio.Future()
            finally:
                self.cancelled = True
        self.actions.append(payload)
        return {"ok": True}


class NativeControlAcceptance(unittest.IsolatedAsyncioTestCase):
    async def test_selected_editor_approval_fencing_and_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            db = CollieDB(Path(folder) / "acceptance.db")
            chat = db.create_conversation()["id"]
            other = db.create_conversation()["id"]
            requested = asyncio.Queue()

            async def broadcast(event):
                if event["type"] == "approval_requested":
                    await requested.put(event["approval"])

            broker = ApprovalBroker(db, PermissionEvaluator(local_write_preset="allow"), broadcast)
            dispatch = NativeControlTool().permission_request({"operation": "focus"})
            self.assertEqual(
                PermissionEvaluator()
                .evaluate(ExecutionContext(execution_posture="read_only"), dispatch)
                .effect,
                "deny",
            )
            self.assertEqual(
                PermissionEvaluator()
                .evaluate(ExecutionContext(execution_mode="plan"), dispatch)
                .effect,
                "deny",
            )
            editor = ControlledEditor()
            service = NativeControl(broker, editor)
            context = ExecutionContext(conversation_id=chat)
            await service.select(chat, editor.window, "unsaved_editor")
            await service.select(other, editor.window, "unsaved_editor")

            # Even automatic local permission cannot waive a native action's approval.
            action = asyncio.create_task(service.execute(context, "type", "local draft"))
            approval = await asyncio.wait_for(requested.get(), 2)
            self.assertEqual(editor.actions, [])
            self.assertEqual(approval["action"], "native.interact")
            with self.assertRaisesRegex(ValueError, "Another native action"):
                await service.execute(ExecutionContext(conversation_id=other), "focus")
            with self.assertRaisesRegex(ValueError, "private desktop"):
                await service.execute(
                    ExecutionContext(conversation_id=chat, shared_session_id="shared"), "focus"
                )
            await broker.resolve(approval["id"], "allow_once")
            self.assertTrue((await action)["ok"])
            self.assertEqual(editor.actions[-1]["text"], "local draft")

            denied = asyncio.create_task(service.execute(context, "focus"))
            approval = await asyncio.wait_for(requested.get(), 2)
            await broker.resolve(approval["id"], "reject")
            with self.assertRaises(PermissionDeniedError):
                await denied
            self.assertEqual(len(editor.actions), 1)

            stale = asyncio.create_task(service.execute(context, "focus"))
            approval = await asyncio.wait_for(requested.get(), 2)
            editor.window["started"] = "102"  # Simulate HWND reuse during review.
            await broker.resolve(approval["id"], "allow_once")
            with self.assertRaisesRegex(ValueError, "stale"):
                await stale
            self.assertEqual(len(editor.actions), 1)
            with self.assertRaisesRegex(ValueError, "Select"):
                await service.execute(context, "focus")

            await service.select(chat, editor.window, "unsaved_editor")
            editor.entered.clear()
            editor.block = True
            running = asyncio.create_task(service.execute(context, "focus"))
            approval = await asyncio.wait_for(requested.get(), 2)
            await broker.resolve(approval["id"], "allow_once")
            await asyncio.wait_for(editor.entered.wait(), 2)
            service.stop(chat)
            with self.assertRaises(asyncio.CancelledError):
                await running
            self.assertTrue(editor.cancelled)
            # Cancellation releases the process-wide lease for another chat.
            editor.block = False
            await service.select(other, editor.window, "unsaved_editor")
            recovered = asyncio.create_task(
                service.execute(ExecutionContext(conversation_id=other), "focus")
            )
            approval = await asyncio.wait_for(requested.get(), 2)
            await broker.resolve(approval["id"], "allow_once")
            self.assertTrue((await recovered)["ok"])
            db.close()


if __name__ == "__main__":
    unittest.main()
