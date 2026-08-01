"""Non-overridable defaults for dangerous actions."""

from __future__ import annotations

HARD_APPROVAL_ACTIONS = frozenset(
    {
        "financial.purchase",
        "financial.transaction",
        "external.publish",
        "message.send",
        "email.send",
        "delete.destructive",
    }
)

SECRET_WORDS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "password",
        "secret",
        "token",
    }
)

PLAN_ALLOWED_ACTIONS = frozenset(
    {
        "conversation.read",
        "memory.read",
        "plan.present",
        "service.read",
        "weather.read",
        "web.read",
    }
)
