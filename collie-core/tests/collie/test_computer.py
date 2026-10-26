"""Authority, stale-state, ownership and no-replay checks at the real service seam."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from collie_core.computer.driver import ALLOWED, driver_environment
from collie_core.computer.privacy import compact_computer_context, redact_computer_history
from collie_core.computer.service import ComputerService
from collie_core.db import CollieDB
from collie_core.permissions.broker import ApprovalBroker, PermissionDeniedError
from collie_core.permissions.evaluator import PermissionEvaluator
from collie_core.permissions.models import ExecutionContext
from nanobot.agent.hook import AgentHookContext  # noqa: F401 - initialize engine before utils
from nanobot.utils.progress_events import build_tool_event_start_payload


class Driver:
    def __init__(self):
        self.calls = []
        self.version = 0
        self.value = "empty"
        self.closed = False
        self.hang = False

    async def call(self, name, args):
        self.calls.append((name, args))
        if self.hang:
            await asyncio.Event().wait()
        if name == "get_window_state":
            self.version += 1
            return {
                "structuredContent": {
                    "snapshot_id": f"s{self.version:08x}",
                    "elements": [
                        {
                            "element_token": f"s{self.version:08x}:0",
                            "element_index": 0,
                            "label": "Draft",
                            "value": self.value,
                            "role": "Edit",
                        }
                    ],
                }
            }
        if name == "type_text":
            self.value = args["text"]
        return {"structuredContent": {}}

    async def close(self):
        self.closed = True


class Authorizer:
    def __init__(self):
        self.requests = []
        self.deny = False
        self.hang = False

    async def authorize(self, context, call, tool, args):
        self.requests.append(tool.permission_request({}))
        if self.hang:
            await asyncio.Event().wait()
        if self.deny:
            raise PermissionDeniedError("Rejected")


@pytest.fixture
def setup():
    authorizer, driver = Authorizer(), Driver()
    service = ComputerService(authorizer, driver, identity=lambda p, w: (p, 123))
    return service, authorizer, driver, ExecutionContext(conversation_id="chat", run_id="task")


async def observe(service, context):
    return await service.execute(context, {"operation": "observe", "pid": 1, "window_id": 2})


def action(obs, **kwargs):
    return {
        "operation": "type",
        "pid": 1,
        "window_id": 2,
        "observation_id": obs["observation_id"],
        "element_token": obs["elements"][0]["element_token"],
        "text": "disposable",
        **kwargs,
    }


async def test_text_path_reindexes_after_approval_and_reads_postcondition(setup):
    service, auth, driver, ctx = setup
    obs = await observe(service, ctx)
    result = await service.execute(ctx, action(obs))
    assert result["postcondition"]["elements"][0]["value"] == "disposable"
    write = next(a for n, a in driver.calls if n == "type_text")
    assert write["element_token"] != obs["elements"][0]["element_token"]
    assert write["target"] == {"kind": "window", "pid": 1, "window_id": 2}
    assert all(r.hard_approval for r in auth.requests)
    assert "disposable" not in json.dumps([r.redacted_parameters for r in auth.requests])
    assert all("screenshot" not in a or not a["screenshot"] for _, a in driver.calls)


async def test_changed_ui_does_not_write(setup):
    service, _, driver, ctx = setup
    obs = await observe(service, ctx)
    driver.value = "user took over"
    with pytest.raises(ValueError, match="changed"):
        await service.execute(ctx, action(obs))
    assert not any(n == "type_text" for n, _ in driver.calls)


async def test_stale_process_instance_refused(setup):
    service, _, driver, ctx = setup
    obs = await observe(service, ctx)
    service.identity = lambda p, w: (p, 456)
    with pytest.raises(ValueError, match="Stale window"):
        await service.execute(ctx, action(obs))
    assert not any(n == "type_text" for n, _ in driver.calls)


@pytest.mark.parametrize(
    "change", [{"observation_id": "old"}, {"window_id": 3}, {"element_token": "old"}]
)
async def test_stale_observation_target_and_token(setup, change):
    service, _, driver, ctx = setup
    obs = await observe(service, ctx)
    with pytest.raises(ValueError):
        await service.execute(ctx, action(obs, **change))
    assert not any(n == "type_text" for n, _ in driver.calls)


async def test_other_chat_and_task_cannot_use_lease(setup):
    service, _, driver, ctx = setup
    obs = await observe(service, ctx)
    for other in [
        ExecutionContext(conversation_id="other"),
        ExecutionContext(conversation_id="chat", run_id="other"),
    ]:
        with pytest.raises(ValueError, match="Another task"):
            await service.execute(other, action(obs))
    with pytest.raises(ValueError, match="another chat"):
        await service.execute(ExecutionContext(conversation_id="other"), {"operation": "stop"})
    with pytest.raises(ValueError, match="another chat or task"):
        await service.execute(
            ExecutionContext(conversation_id="chat", run_id="other"), {"operation": "stop"}
        )
    await service.execute(ctx, {"operation": "stop"})
    assert driver.closed and service.owner is None


async def test_stop_cancels_pending_approval_and_input(setup):
    service, auth, driver, ctx = setup
    auth.hang = True
    pending = asyncio.create_task(observe(service, ctx))
    await asyncio.sleep(0)
    await service.execute(ctx, {"operation": "stop"})
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert driver.closed and not driver.calls
    auth.hang = False
    driver.hang = True
    pending = asyncio.create_task(observe(service, ctx))
    await asyncio.sleep(0)
    await service.execute(ctx, {"operation": "stop"})
    with pytest.raises(asyncio.CancelledError):
        await pending


async def test_denied_central_broker_never_starts_driver(tmp_path):
    db = CollieDB(tmp_path / "test.db")

    async def reject(event):
        if event["type"] != "approval_requested":
            return
        await broker.resolve(event["approval"]["id"], "reject")

    broker = ApprovalBroker(db, PermissionEvaluator(), reject)
    driver = Driver()
    service = ComputerService(broker, driver, identity=lambda p, w: (p, 1))
    with pytest.raises(PermissionDeniedError):
        await observe(service, ExecutionContext(conversation_id="private"))
    assert not driver.calls and service.owner is None
    db.close()


async def test_nonvisual_and_shared_refuse_images(setup):
    service, _, driver, ctx = setup
    for cmd in [
        {"operation": "desktop"},
        {"operation": "observe", "pid": 1, "window_id": 2, "screenshot": True},
    ]:
        with pytest.raises(ValueError, match="needs_vision"):
            await service.execute(ctx, cmd)
    with pytest.raises(ValueError, match="private"):
        await service.execute(
            ExecutionContext(conversation_id="chat", shared_session_id="shared"),
            {"operation": "windows"},
        )
    assert not driver.calls


async def test_no_arbitrary_code_or_unbounded_inputs(setup):
    service, _, driver, ctx = setup
    for cmd in [
        {"operation": "run_script"},
        {"operation": "observe", "max_elements": 99999},
        {"operation": "type", "text": "x" * 2001},
        {"operation": "windows", "shell": "no"},
    ]:
        with pytest.raises(ValueError):
            await service.execute(ctx, cmd)
    assert not driver.calls
    assert (
        not {"run_script", "run_steps", "clipboard_read", "launch_app", "install_extension"}
        & ALLOWED
    )


def test_content_free_environment_and_progress(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    env = driver_environment()
    assert "OPENAI_API_KEY" not in env
    assert env["CUA_DRIVER_RS_TELEMETRY_ENABLED"] == "false"
    assert env["CUA_DRIVER_RS_UPDATE_CHECK"] == "false"
    event = build_tool_event_start_payload(
        SimpleNamespace(name="computer", id="call", arguments={"text": "private"})
    )
    assert "private" not in json.dumps(event)


def test_desktop_history_and_checkpoint_are_redacted_without_changing_active_context(tmp_path):
    from nanobot.session.manager import Session, SessionManager

    call = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "native-call",
                "type": "function",
                "function": {"name": "computer", "arguments": '{"text":"PRIVATE_TYPED"}'},
            }
        ],
    }
    observation = {"role": "tool", "tool_call_id": "native-call", "content": "PRIVATE_SCREEN"}
    session = Session(
        key="test:computer",
        messages=[call, observation],
        metadata={
            "runtime_checkpoint": {
                "assistant_message": call,
                "completed_tool_results": [observation],
            }
        },
    )
    safe = redact_computer_history({"messages": session.messages, "metadata": session.metadata})
    assert "PRIVATE" not in json.dumps(safe)
    assert "PRIVATE_SCREEN" in json.dumps(session.messages)
    manager = SessionManager(tmp_path)
    manager.save(session)
    persisted = manager._get_session_path(session.key).read_text(encoding="utf-8")
    assert "PRIVATE" not in persisted
    newer = {"role": "tool", "tool_call_id": "native-call", "content": "FRESH_STATE"}
    compacted = compact_computer_context([call, observation, call, newer])
    assert "PRIVATE_SCREEN" not in json.dumps(compacted)
    assert compacted[-1]["content"] == "FRESH_STATE"


async def test_stopping_real_broker_cancels_pending_card(tmp_path):
    db = CollieDB(tmp_path / "pending.db")
    requested = asyncio.Event()

    async def review(event):
        if event["type"] == "approval_requested":
            requested.set()

    broker = ApprovalBroker(db, PermissionEvaluator(), review)
    service = ComputerService(broker, Driver(), identity=lambda p, w: (p, 1))
    ctx = ExecutionContext(conversation_id="pending-chat")
    task = asyncio.create_task(observe(service, ctx))
    await requested.wait()
    await service.execute(ctx, {"operation": "stop"})
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not db.list_pending_approvals()
    db.close()


def test_capture_coordinates_reject_outside_frame_and_changed_pixels():
    import base64
    import io

    from PIL import Image

    from collie_core.computer.capture import pixel_fingerprint

    def capture(color):
        buffer = io.BytesIO()
        Image.new("RGB", (32, 32), color).save(buffer, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()

    first, second = capture("white"), capture("black")
    assert pixel_fingerprint(first, 10, 10) != pixel_fingerprint(second, 10, 10)
    with pytest.raises(ValueError, match="outside"):
        pixel_fingerprint(first, 99, 10)
