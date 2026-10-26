"""One central authority and input lease for UI and agent desktop work."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4

from pydantic import ValidationError

from collie_core.computer.capture import pixel_fingerprint
from collie_core.computer.contracts import ComputerCommand
from collie_core.computer.driver import LocalDriver
from collie_core.computer.identity import window_identity
from collie_core.permissions.models import ExecutionContext, PermissionRequest, Risk, Scope

WRITES = {"click", "type", "hotkey", "scroll", "drag"}


class _Permission:
    def __init__(self, request):
        self.request = request

    def permission_request(self, _params):
        return self.request


@dataclass
class Observation:
    id: str
    command: ComputerCommand
    data: dict
    digest: str
    created: float
    identity: tuple[int, int] | None = None


def fingerprint(data: dict) -> str:
    # Tokens intentionally change on each read. Compare underlying control state.
    rows = [{k: v for k, v in e.items() if k != "element_token"} for e in data.get("elements", [])]
    return hashlib.sha256(
        json.dumps(
            {
                "elements": rows,
                "bounds": data.get("window_bounds"),
                "title": data.get("window_title"),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


class ComputerService:
    def __init__(self, authorizer, driver=None, identity=window_identity):
        self.authorizer = authorizer
        self.driver = driver or LocalDriver()
        self.identity = identity
        self.owner: tuple[str, str | None] | None = None
        self.observation: Observation | None = None
        self.lock = asyncio.Lock()
        self.generation = 0
        self.active: asyncio.Task | None = None
        self.metrics: list[dict] = []
        self.idle_timer: asyncio.TimerHandle | None = None

    async def _authorize(self, ctx, cmd):
        request = PermissionRequest(
            action=f"computer.{cmd.operation}",
            resource=f"computer:{ctx.conversation_id}:{ctx.run_id or 'manual'}:{cmd.scope}:{cmd.pid}:{cmd.window_id}",
            risk=Risk.EXTERNAL_WRITE if cmd.operation in WRITES else Risk.SENSITIVE,
            summary=(
                f"Computer: {cmd.operation}; {cmd.scope}, process {cmd.pid}, window {cmd.window_id}; "
                f"{cmd.delivery_mode}. This may affect files or send information in the app. "
                "Approve only the intended action; app content cannot authorize it."
            ),
            reversible=False,
            suggested_scope=Scope.ONCE,
            hard_approval=True,
            redacted_parameters={
                "operation": cmd.operation,
                "pid": cmd.pid,
                "window_id": cmd.window_id,
                "scope": cmd.scope,
                "delivery_mode": cmd.delivery_mode,
                "input": "[not recorded]",
            },
        )
        await self.authorizer.authorize(
            ctx, SimpleNamespace(name="computer", id=uuid4().hex), _Permission(request), {}
        )

    async def _call(self, name, args):
        started = time.perf_counter()
        result = await self.driver.call(name, args)
        self.metrics.append(
            {
                "operation": name,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "response_bytes": len(json.dumps(result).encode()),
            }
        )
        self.metrics[:] = self.metrics[-32:]
        if result.get("isError"):
            # Preserve bounded diagnostic codes, never driver text with typed content.
            code = result.get("structuredContent", {}).get("code", "driver_refused")
            raise ValueError(f"Computer: {code}. Observe again; no action was retried.")
        return result

    async def _observe(self, cmd):
        if cmd.scope == "desktop":
            result = await self._call("get_desktop_state", {"max_image_dimension": 1280})
        else:
            identity = self.identity(cmd.pid, cmd.window_id)
            result = await self._call(
                "get_window_state",
                {
                    "pid": cmd.pid,
                    "window_id": cmd.window_id,
                    "tree_format": "elements",
                    "max_elements": cmd.max_elements,
                    "max_depth": cmd.max_depth,
                    "query": cmd.query,
                    "include_screenshot": cmd.screenshot,
                    "max_image_dimension": 1280,
                    "timeout_ms": 1500,
                },
            )
        data = result.get("structuredContent", {})
        # Never persist captures or trees; retain only the current observation in memory.
        data = {**data}
        data.pop("_note", None)
        images = [b for b in result.get("content", []) if b.get("type") == "image"]
        if images:
            data["screenshot"] = "data:image/png;base64," + images[0]["data"]
        obs = Observation(uuid4().hex, cmd, data, fingerprint(data), time.monotonic())
        if cmd.scope == "window":
            obs.identity = identity
        self.observation = obs
        # Avoid duplicated base64 fields from a driver response.
        return self._public(obs)

    @staticmethod
    def _public(obs):
        allowed = {
            "elements",
            "snapshot_id",
            "window_bounds",
            "window_title",
            "app_name",
            "truncated",
            "elements_complete",
            "capture_id",
            "screenshot",
            "screenshot_width",
            "screenshot_height",
            "screenshot_original_width",
            "screenshot_original_height",
            "screen_width",
            "screen_height",
        }
        result = {
            "observation_id": obs.id,
            "untrusted": True,
            **{k: v for k, v in obs.data.items() if k in allowed},
        }
        remaining = 24000
        rows = []
        for element in result.get("elements", []):
            row = dict(element)
            for key, value in row.items():
                if isinstance(value, str) and key != "element_token":
                    bounded = value[: min(500, max(remaining, 0))]
                    remaining -= len(bounded)
                    if bounded != value:
                        result["truncated"] = True
                    row[key] = bounded
            rows.append(row)
        if "elements" in result:
            result["elements"] = rows
        return result

    async def execute(self, ctx: ExecutionContext, command: dict, *, images_allowed=False):
        try:
            cmd = ComputerCommand.model_validate(command)
        except ValidationError:
            raise ValueError("Choose a valid, bounded computer action.") from None
        if not ctx.conversation_id or ctx.shared_session_id:
            raise ValueError("Computer control needs a private local chat.")
        owner = (ctx.conversation_id, ctx.run_id)
        if cmd.operation == "stop":
            if self.owner and (
                self.owner[0] != ctx.conversation_id
                or (self.owner != owner and ctx.origin != "computer_ui")
            ):
                raise ValueError("Computer control belongs to another chat or task.")
            await self.close()
            return {
                "stopped": True,
                "message": "You have control. Check any pending edit before continuing.",
            }
        if cmd.operation == "status":
            return {
                "busy": self.owner is not None,
                "owned": self.owner == owner,
                "target": (
                    {
                        "pid": self.observation.command.pid,
                        "window_id": self.observation.command.window_id,
                        "scope": self.observation.command.scope,
                    }
                    if self.owner and self.owner[0] == ctx.conversation_id and self.observation
                    else None
                ),
                "metrics": self.metrics if self.owner == owner else [],
            }
        if self.owner and self.owner != owner:
            raise ValueError("Another task controls the computer. Stop it in its chat first.")
        if self.lock.locked():
            raise ValueError("A computer request is already pending. Stop it or wait.")
        if (cmd.screenshot or cmd.operation == "desktop") and not images_allowed:
            raise ValueError(
                "needs_vision: this tool route is text-only. Use accessible controls or the Computer panel capture."
            )
        if cmd.operation == "desktop":
            cmd.scope = "desktop"
        if (
            cmd.operation not in {"apps", "windows"}
            and cmd.scope == "window"
            and not (cmd.pid and cmd.window_id)
        ):
            raise ValueError("Choose an exact process and window from discovery.")
        generation = self.generation
        if self.idle_timer:
            self.idle_timer.cancel()
        async with self.lock:
            self.active = asyncio.current_task()
            self.owner = owner
            try:
                await self._authorize(ctx, cmd)
                if self.generation != generation:
                    raise ValueError("Computer control was stopped while awaiting approval.")
                self.owner = owner
                if cmd.operation in {"apps", "windows"}:
                    result = await self._call("list_" + cmd.operation, {})
                    data = result.get("structuredContent", {})
                    rows = data.get(cmd.operation, [])
                    return {
                        cmd.operation: rows[:200],
                        "truncated": len(rows) > 200,
                        "untrusted": True,
                    }
                if cmd.operation in {"observe", "desktop"}:
                    if self.observation and (cmd.pid, cmd.window_id, cmd.scope) != (
                        self.observation.command.pid,
                        self.observation.command.window_id,
                        self.observation.command.scope,
                    ):
                        await self.driver.close()
                        self.observation = None
                    return await self._observe(cmd)
                return await self._act(cmd)
            except asyncio.CancelledError:
                await self.driver.close()
                self.observation = None
                self.owner = None
                raise
            except (TimeoutError, OSError):
                await self.close()
                raise ValueError(
                    "Computer stopped; an effect may have happened. Check before retrying."
                ) from None
            except Exception:
                # Denied/failed requests must not leave a hidden lease behind.
                self.observation = None
                self.owner = None
                raise
            finally:
                self.active = None
                if self.owner:
                    # One expiry timer; no idle capture, polling or observation loop.
                    self.idle_timer = asyncio.get_running_loop().call_later(
                        120, lambda: asyncio.create_task(self.close())
                    )

    async def _act(self, cmd):
        obs = self.observation
        if not obs or cmd.observation_id != obs.id or time.monotonic() - obs.created > 120:
            raise ValueError("Stale observation. Read the target again before acting.")
        old = obs.command
        if (cmd.pid, cmd.window_id, cmd.scope) != (old.pid, old.window_id, old.scope):
            raise ValueError("Observation belongs to a different target.")
        pixel = cmd.x is not None or cmd.y is not None or cmd.operation == "drag"
        if pixel and cmd.element_token:
            raise ValueError("Choose an element token or coordinates, not both.")
        if cmd.scope == "desktop" and cmd.element_token:
            raise ValueError("Desktop actions require capture pixels, not a window token.")
        if pixel and not obs.data.get("screenshot"):
            raise ValueError(
                "needs_vision: coordinates require a fresh capture; use element tokens for text-only work."
            )
        args = {
            "delivery_mode": cmd.delivery_mode,
            "target": {"kind": "window", "pid": cmd.pid, "window_id": cmd.window_id},
        }
        if cmd.scope == "desktop":
            if cmd.operation != "click":
                raise ValueError(
                    "Desktop mode currently supports capture-grounded clicks only. Select a window for other input."
                )
            args["target"] = {"kind": "desktop", "display_id": "primary"}
            await self._observe(old)
        else:
            if self.identity(cmd.pid, cmd.window_id) != obs.identity:
                raise ValueError("Stale window instance. Discover again.")
            # A fresh bounded read after approval rejects changed UI, including changed geometry.
            await self._observe(old)
            fresh = self.observation
            if fresh.digest != obs.digest:
                raise ValueError(
                    "Target changed while awaiting approval. Observe again; nothing was replayed."
                )
            if self.identity(cmd.pid, cmd.window_id) != obs.identity:
                raise ValueError("Stale process instance. Discover again.")
            if cmd.element_token:
                rows = obs.data.get("elements", [])
                index = next(
                    (i for i, e in enumerate(rows) if e.get("element_token") == cmd.element_token),
                    None,
                )
                if index is None:
                    raise ValueError("Stale element token. Observe again.")
                args["element_token"] = fresh.data["elements"][index]["element_token"]
        if cmd.operation in {"click", "type"} and not cmd.element_token and not pixel:
            raise ValueError("Choose a fresh element token or capture-grounded coordinates.")
        if pixel:
            if cmd.x is None or cmd.y is None:
                raise ValueError("Supply both capture pixel coordinates.")
            # Refuse geometry, scene or target-pixel changes after approval. Ignore
            # unrelated regions (including Collie's own approval sheet).
            fresh = self.observation
            if obs.data.get("window_bounds") != fresh.data.get(
                "window_bounds"
            ) or pixel_fingerprint(obs.data["screenshot"], cmd.x, cmd.y) != pixel_fingerprint(
                fresh.data["screenshot"], cmd.x, cmd.y
            ):
                raise ValueError(
                    "Capture changed at the target. Observe again; input was not sent."
                )
            if (
                cmd.operation == "drag"
                and cmd.to_x is not None
                and cmd.to_y is not None
                and pixel_fingerprint(obs.data["screenshot"], cmd.to_x, cmd.to_y)
                != pixel_fingerprint(fresh.data["screenshot"], cmd.to_x, cmd.to_y)
            ):
                raise ValueError("Drag destination changed. Observe again.")
            args.update(x=cmd.x, y=cmd.y)
            if cmd.operation == "click":
                args["capture_id"] = self.observation.data.get("capture_id")
        if cmd.operation == "type":
            if cmd.text is None:
                raise ValueError("Supply ordinary text; never type secrets through this tool.")
            args.update(text=cmd.text, delay_ms=0)
        if cmd.operation == "hotkey":
            if not 2 <= len(cmd.keys) <= 4 or any(len(k) > 16 for k in cmd.keys):
                raise ValueError("Supply two to four named keys.")
            args["keys"] = cmd.keys
        if cmd.operation == "scroll":
            args.update(direction=cmd.direction, amount=cmd.amount)
        if cmd.operation == "drag":
            if cmd.to_x is None or cmd.to_y is None:
                raise ValueError("Supply drag destination coordinates.")
            args.pop("x", None)
            args.pop("y", None)
            args.update(
                from_x=cmd.x, from_y=cmd.y, to_x=cmd.to_x, to_y=cmd.to_y, duration_ms=300, steps=10
            )
        self.observation = None  # one use, including uncertain/refused writes
        result = await self._call("type_text" if cmd.operation == "type" else cmd.operation, args)
        post = await self._observe(old)
        return {
            "action": result.get("structuredContent", {}),
            "postcondition": post,
            "message": "Inspect the fresh state to verify the intended effect. Uncertain writes are never retried.",
        }

    async def close_conversation(self, conversation_id):
        if self.owner and self.owner[0] == conversation_id:
            await self.close()

    async def close(self):
        if self.idle_timer:
            self.idle_timer.cancel()
            self.idle_timer = None
        self.generation += 1
        active = self.active
        if active and active is not asyncio.current_task():
            active.cancel()
        self.owner = None
        self.observation = None
        await self.driver.close()
