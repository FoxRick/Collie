"""Validation for model-authored plans before persistence."""

from __future__ import annotations

from typing import Any

_STEP_RISKS = {"read", "local_write", "external_write", "sensitive", "destructive"}


def validate_plan(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("A plan must be an object.")
    title = str(value.get("title") or "").strip()
    goal = str(value.get("goal") or "").strip()
    steps = value.get("steps")
    if not title or not goal:
        raise ValueError("A plan needs both a title and a goal.")
    if not isinstance(steps, list) or not steps:
        raise ValueError("A plan needs at least one step.")
    if len(steps) > 50:
        raise ValueError("Keep plans to 50 steps or fewer.")

    normalized_steps: list[dict[str, Any]] = []
    keys: set[str] = set()
    for ordinal, raw in enumerate(steps):
        if not isinstance(raw, dict):
            raise ValueError(f"Step {ordinal + 1} must be an object.")
        key = str(raw.get("key") or "").strip()
        step_title = str(raw.get("title") or "").strip()
        if not key or not step_title:
            raise ValueError(f"Step {ordinal + 1} needs a stable key and title.")
        if key in keys:
            raise ValueError(f"Step key '{key}' is duplicated.")
        keys.add(key)
        risk = str(raw.get("risk") or "read")
        if risk not in _STEP_RISKS:
            raise ValueError(f"Step '{key}' has an unknown risk.")
        expected = raw.get("expectedTools", [])
        if not isinstance(expected, list) or not all(isinstance(item, str) for item in expected):
            raise ValueError(f"Step '{key}' expectedTools must be a list of tool names.")
        normalized_steps.append(
            {
                "key": key,
                "title": step_title,
                "description": str(raw.get("description") or "").strip(),
                "expectedTools": expected,
                "risk": risk,
                "verification": str(raw.get("verification") or "").strip(),
            }
        )

    def string_list(name: str) -> list[str]:
        raw = value.get(name, [])
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            raise ValueError(f"{name} must be a list of strings.")
        return raw

    return {
        "title": title,
        "goal": goal,
        "assumptions": string_list("assumptions"),
        "steps": normalized_steps,
        "services": string_list("services"),
        "dataLeavingDevice": string_list("dataLeavingDevice"),
        "approvalsExpected": string_list("approvalsExpected"),
        "successCriteria": string_list("successCriteria"),
    }
