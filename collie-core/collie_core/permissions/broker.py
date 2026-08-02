"""Suspend tool coroutines while the user reviews an approval request."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from collie_core.db import CollieDB
from collie_core.permissions.classifier import classify_tool
from collie_core.permissions.evaluator import PermissionEvaluator
from collie_core.permissions.models import Effect, ExecutionContext, Risk, Scope


class PermissionDeniedError(RuntimeError):
    """A structured policy rejection that must never fall through to execution."""


class ApprovalBroker:
    def __init__(
        self,
        db: CollieDB,
        evaluator: PermissionEvaluator,
        broadcaster: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        *,
        timeout_seconds: float = 300,
    ) -> None:
        self.db = db
        self.evaluator = evaluator
        self.broadcaster = broadcaster
        self.timeout_seconds = timeout_seconds
        self._pending: dict[str, asyncio.Future[str]] = {}

    async def _enforce_plan_change(self, context: ExecutionContext, request: Any) -> None:
        if (
            not context.run_id
            or request.risk == Risk.READ
            or request.action in {"plan.present", "task.progress"}
        ):
            return
        change = self.db.get_plan_change_request(context.run_id)
        if change is None or str(change.get("status") or "") not in {
            "requested",
            "finalized",
        }:
            return
        result = self.db.finalize_plan_change(context.run_id)
        if result["changed"]:
            task = dict(result["task"])
            conversation_id = str(task.pop("conversation_id", ""))
            message = self.db.claim_plan_change_terminal_message(context.run_id)
            if self.broadcaster:
                await self.broadcaster({"type": "run_failed", "run": result["run"]})
                await self.broadcaster(
                    {
                        "type": "task_state",
                        "conversation_id": conversation_id,
                        "task": task,
                    }
                )
                if message is not None:
                    await self.broadcaster(
                        {
                            "type": "message",
                            "conversation_id": conversation_id,
                            "message": message,
                        }
                    )
        raise PermissionDeniedError(
            "The user requested a plan change, so this action was not run."
        )

    async def authorize(
        self,
        context: ExecutionContext,
        tool_call: Any,
        tool: Any,
        params: dict[str, Any],
    ) -> None:
        request = classify_tool(tool, str(tool_call.name), params)
        await self._enforce_plan_change(context, request)
        decision = self.evaluator.evaluate(context, request)
        if decision.effect == Effect.ALLOW:
            return
        if decision.effect == Effect.DENY:
            raise PermissionDeniedError(decision.reason)

        row = self.db.create_approval_request(
            action=request.action,
            resource=request.resource,
            risk=request.risk,
            display={
                "summary": request.summary,
                "resource": request.resource,
                "reversible": request.reversible,
                "data_leaving_device": list(request.data_leaving_device),
                "parameters": request.redacted_parameters,
                "suggested_scope": request.suggested_scope,
                "approve_for_me_eligible": self.evaluator._approve_for_me_eligible(request),
                "reason": decision.reason,
            },
            run_id=context.run_id,
            conversation_id=context.conversation_id,
            tool_call_id=str(getattr(tool_call, "id", "") or ""),
        )
        request_id = str(row["id"])
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending[request_id] = future
        if self.broadcaster:
            await self.broadcaster({"type": "approval_requested", "approval": row})
        try:
            resolution = await asyncio.wait_for(future, self.timeout_seconds)
        except TimeoutError as exc:
            self.db.resolve_approval_request(request_id, "timeout")
            raise PermissionDeniedError("Approval timed out; the action was not run.") from exc
        finally:
            self._pending.pop(request_id, None)
        if resolution not in {"allow_once", "allow_run"}:
            raise PermissionDeniedError("You rejected this action.")
        await self._enforce_plan_change(context, request)

    async def resolve(
        self,
        request_id: str,
        resolution: str,
        *,
        scope_type: str | None = None,
        scope_value: str | None = None,
    ) -> dict[str, Any]:
        if resolution not in {"allow_once", "allow_run", "reject"}:
            raise ValueError("unsupported approval resolution")
        pending = {row["id"]: row for row in self.db.list_pending_approvals()}
        row = pending.get(request_id)
        if row is None:
            raise ValueError("approval request is no longer pending")
        rule_id = None
        if resolution == "allow_run":
            run_id = str(row.get("run_id") or "").strip()
            if not run_id:
                raise ValueError("approval request is not tied to a run")
            display = row.get("display") if isinstance(row.get("display"), dict) else {}
            if not bool(display.get("approve_for_me_eligible")):
                raise ValueError("this action is not eligible for approval for this task")
            rule = self.db.add_approval_rule(
                action=str(row["action"]),
                resource_pattern=str(row["resource"]),
                effect="allow",
                scope_type=Scope.RUN,
                scope_value=run_id,
            )
            rule_id = str(rule["id"])
        resolved = self.db.resolve_approval_request(request_id, resolution, rule_id)
        future = self._pending.get(request_id)
        if future and not future.done():
            future.set_result(resolution)
        if self.broadcaster:
            await self.broadcaster({"type": "approval_resolved", "approval": resolved})
        return resolved

    async def cancel_conversation(self, conversation_id: str) -> int:
        """Cancel and close every pending approval for one conversation."""
        rows = [
            row
            for row in self.db.list_pending_approvals()
            if str(row.get("conversation_id") or "") == conversation_id
        ]
        for row in rows:
            request_id = str(row["id"])
            future = self._pending.get(request_id)
            if future is not None and not future.done():
                future.cancel()
            resolved = self.db.resolve_approval_request(request_id, "cancelled")
            if self.broadcaster:
                await self.broadcaster({
                    "type": "approval_resolved",
                    "approval": resolved,
                })
        return len(rows)

    def cancel_all(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.cancel()
