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

# Automatic local approval must never turn durable authority or account
# changes into permission, even if a future wrapper accidentally labels one
# as a local write.
AUTOMATIC_LOCAL_INELIGIBLE_PREFIXES = (
    "automation.",
    "capability.",
    "connector.",
    "credential.",
    "cron.",
    "mcp.",
    "provider.",
    "routine.",
    "settings.",
)


def is_automatic_local_action_ineligible(action: str) -> bool:
    """Fail closed when an action name itself identifies durable or hard work."""
    normalized = str(action or "").strip().lower()
    return normalized in HARD_APPROVAL_ACTIONS or normalized.startswith(
        AUTOMATIC_LOCAL_INELIGIBLE_PREFIXES
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
