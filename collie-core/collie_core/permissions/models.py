"""Permission model shared by chat, subagents, MCP tools, and routines."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Effect(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class Risk(StrEnum):
    READ = "read"
    LOCAL_WRITE = "local_write"
    EXTERNAL_WRITE = "external_write"
    SENSITIVE = "sensitive"
    DESTRUCTIVE = "destructive"


class Scope(StrEnum):
    ONCE = "once"
    RUN = "run"
    ROUTINE = "routine"
    SERVICE = "service"
    FOLDER = "folder"
    GLOBAL = "global"


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    execution_mode: str = "execute"
    run_id: str | None = None
    plan_id: str | None = None
    plan_version: int | None = None
    conversation_id: str | None = None
    routine_id: str | None = None
    origin: str = "chat"
    execution_posture: str = "inherit"
    parent_effect: Effect | None = None
    approve_all_for_run: bool = False
    project_path: str | None = None


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    action: str
    resource: str
    risk: Risk
    summary: str
    reversible: bool
    data_leaving_device: tuple[str, ...] = ()
    suggested_scope: Scope = Scope.ONCE
    redacted_parameters: dict[str, Any] = field(default_factory=dict)
    hard_approval: bool = False


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    effect: Effect
    reason: str
    rule_id: str | None = None
