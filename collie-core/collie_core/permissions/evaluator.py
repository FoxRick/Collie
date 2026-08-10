"""Deterministic allow/ask/deny precedence."""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from collie_core.permissions.defaults import is_automatic_local_action_ineligible
from collie_core.permissions.models import (
    Effect,
    ExecutionContext,
    PermissionDecision,
    PermissionRequest,
    Risk,
)


def canonical_folder_contains(folder: str, resource: str) -> bool:
    """Match canonical paths without traversal or prefix confusion."""
    try:
        base = Path(folder).expanduser().resolve(strict=True)
        target = Path(resource).expanduser().resolve(strict=False)
        target.relative_to(base)
        return True
    except (OSError, ValueError):
        return False


def _is_path_resource(resource: str) -> bool:
    """Heuristic: does *resource* look like a local filesystem path?"""
    if not resource:
        return False
    # URLs contain forward slashes, but those are protocol separators rather
    # than filesystem path components.  In particular, a selected project
    # must not turn harmless read-only web tools into external-directory
    # approvals.
    try:
        parsed = urlsplit(resource)
    except ValueError:
        parsed = None
    if parsed and parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
        return False
    expanded = os.path.expanduser(resource)
    return os.path.isabs(expanded) or "/" in resource or "\\" in resource


class PermissionEvaluator:
    def __init__(
        self,
        rule_store: Any = None,
        *,
        local_write_preset: str = "ask",
        review_gate_provider: Callable[[str], Any] | None = None,
    ) -> None:
        self.rule_store = rule_store
        self.review_gate_provider = review_gate_provider
        self.set_local_write_preset(local_write_preset)

    def set_local_write_preset(self, preset: str) -> None:
        """Apply a validated local-write preset to future evaluations.

        ``deny`` is the engineering/bench posture: local writes are
        refused outright (no auto-allow, no ask) unless an explicit rule
        or run-wide approval already granted them. It rides the same
        setter as the product's ask/allow toggles — never a bypass.
        """
        if preset not in {"ask", "allow", "deny"}:
            raise ValueError("local-write preset must be 'ask', 'allow', or 'deny'")
        self.local_write_preset = preset

    def evaluate(
        self, context: ExecutionContext, request: PermissionRequest
    ) -> PermissionDecision:
        if context.parent_effect == Effect.DENY:
            return PermissionDecision(Effect.DENY, "The parent run denied this capability.")

        if context.execution_posture == "read_only" and request.risk != Risk.READ:
            return PermissionDecision(
                Effect.DENY, "This specialist has a read-only execution posture."
            )

        rules = self._matching_rules(context, request)
        denied = next((rule for rule in rules if rule.get("effect") == Effect.DENY), None)
        if denied:
            return PermissionDecision(
                Effect.DENY, "An explicit deny rule blocks this action.", str(denied["id"])
            )

        ordinary_safe = self._ordinary_safe(request)
        review_safe = ordinary_safe or self._approve_for_me_eligible(request)

        if (
            request.risk != Risk.READ
            and context.conversation_id
            and self.review_gate_provider is not None
        ):
            try:
                review_gate = self.review_gate_provider(context.conversation_id)
            except Exception:
                return PermissionDecision(
                    Effect.DENY,
                    "Review status is unavailable, so this action cannot proceed safely.",
                )
            if review_gate and request.action not in {"plan.present", "task.progress"} and not review_safe:
                return PermissionDecision(
                    Effect.DENY,
                    "This work needs an approved plan before changes can continue.",
                )

        if request.action == "task.progress":
            return PermissionDecision(
                Effect.ALLOW,
                "Updating internal task progress is allowed.",
            )

        if context.execution_mode == "plan" and request.risk != Risk.READ:
            if request.action in {
                "capability.agent.create",
                "capability.skill.create",
            }:
                return PermissionDecision(
                    Effect.ASK,
                    "Creating a reusable capability always requires reviewing its definition.",
                )
            if request.action != "plan.present":
                return PermissionDecision(
                    Effect.DENY, "Plan mode is read-only. Switch to Execute to make changes."
                )
            return PermissionDecision(
                Effect.ALLOW, "Saving a plan for review is allowed in Plan mode."
            )

        if request.hard_approval:
            return PermissionDecision(
                Effect.ASK, "This action always needs a fresh explicit approval."
            )

        allowed = next((rule for rule in rules if rule.get("effect") == Effect.ALLOW), None)
        if allowed:
            if (
                str(allowed.get("scope_type") or "") == "run"
                and not self._approve_for_me_eligible(request)
            ):
                return PermissionDecision(
                    Effect.ASK,
                    "This action is not eligible for approval for this task.",
                )
            return PermissionDecision(
                Effect.ALLOW, "A matching approval rule allows this action.", str(allowed["id"])
            )

        if context.approve_all_for_run and context.run_id and self._approve_for_me_eligible(request):
            return PermissionDecision(Effect.ALLOW, "Approved for this run.")
        if request.risk == Risk.READ:
            redacted = (
                request.redacted_parameters
                if isinstance(request.redacted_parameters, dict)
                else {}
            )
            if redacted.get("unrestricted_local_files") is True:
                # Full local-file access is selected: no project-boundary ask
                # for local file tools. In-scope content reads are covered by
                # the folder/file-access consent itself (local_files declares
                # them READ rather than hard-gated); any other operation still
                # hits its own gates above and below.
                return PermissionDecision(
                    Effect.ALLOW, "Full local file access is selected."
                )
            allowed_roots = redacted.get("allowed_local_roots")
            if isinstance(allowed_roots, list):
                for root in allowed_roots:
                    if isinstance(root, str) and canonical_folder_contains(
                        root, request.resource
                    ):
                        return PermissionDecision(
                            Effect.ALLOW,
                            "The resource is inside a granted local folder.",
                        )
            if context.project_path and _is_path_resource(request.resource):
                if not canonical_folder_contains(context.project_path, request.resource):
                    return PermissionDecision(
                        Effect.ASK,
                        f"'{request.resource}' is outside the active project. "
                        "I need your approval before I can take a look in there.",
                    )
            return PermissionDecision(Effect.ALLOW, "Read-only actions are allowed.")
        if (
            request.risk == Risk.LOCAL_WRITE
            and self.local_write_preset == "allow"
            and self._approve_for_me_eligible(request)
        ):
            return PermissionDecision(Effect.ALLOW, "The local-write preset allows this action.")
        # deny is the strict engineering/bench posture: local writes are
        # refused outright (unless an explicit allow rule or run-wide
        # approval already granted them above). Reads stay allowed.
        if request.risk == Risk.LOCAL_WRITE and self.local_write_preset == "deny":
            return PermissionDecision(
                Effect.DENY, "The local-write preset denies local changes."
            )
        if ordinary_safe:
            return PermissionDecision(
                Effect.ALLOW,
                "This ordinary personal action is safe to do without another approval.",
            )
        return PermissionDecision(Effect.ASK, "Your approval is needed before this action.")

    @staticmethod
    def _ordinary_safe(request: PermissionRequest) -> bool:
        """Limit approval-free work to explicitly tagged reversible local operations."""
        return bool(
            request.approval_free
            and request.risk == Risk.LOCAL_WRITE
            and request.reversible
            and not request.hard_approval
            and not is_automatic_local_action_ineligible(request.action)
        )

    @staticmethod
    def _approve_for_me_eligible(request: PermissionRequest) -> bool:
        """Keep run-wide approval away from external and consequential actions."""
        return bool(
            request.approve_for_me
            and request.risk == Risk.LOCAL_WRITE
            and not request.hard_approval
            and not is_automatic_local_action_ineligible(request.action)
        )

    def _matching_rules(
        self, context: ExecutionContext, request: PermissionRequest
    ) -> list[dict[str, Any]]:
        if self.rule_store is None:
            return []
        now = datetime.now(timezone.utc)
        matches: list[dict[str, Any]] = []
        for rule in self.rule_store.list_rules():
            expiry = rule.get("expires_at")
            if expiry:
                try:
                    parsed_expiry = datetime.fromisoformat(str(expiry))
                    if parsed_expiry.tzinfo is None:
                        parsed_expiry = parsed_expiry.replace(tzinfo=timezone.utc)
                    if parsed_expiry <= now:
                        continue
                except (TypeError, ValueError):
                    # A malformed allow must never grant access indefinitely.
                    # Keep malformed denies active so damaged metadata fails closed.
                    if rule.get("effect") != Effect.DENY:
                        continue
            if not fnmatch.fnmatchcase(request.action, str(rule.get("action") or "")):
                continue
            if not self._scope_matches(rule, context, request):
                continue
            matches.append(rule)
        return matches

    @staticmethod
    def _scope_matches(
        rule: dict[str, Any], context: ExecutionContext, request: PermissionRequest
    ) -> bool:
        kind = str(rule.get("scope_type") or "")
        value = str(rule.get("scope_value") or "")
        pattern = str(rule.get("resource_pattern") or "*")
        resource_matches = fnmatch.fnmatchcase(request.resource, pattern)
        if kind == "global":
            return resource_matches
        if kind == "run":
            return bool(value and context.run_id and value == context.run_id and resource_matches)
        if kind == "routine":
            return bool(
                value
                and context.routine_id
                and value == context.routine_id
                and resource_matches
            )
        if kind == "folder":
            return bool(value) and canonical_folder_contains(value, request.resource)
        if kind in {"service", "once"}:
            return False
        return False
