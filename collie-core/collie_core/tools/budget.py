"""Budget tool: in-app expense tracking (F031, Step 35).

Log expenses, set category budgets, and get a monthly breakdown rendered as
a BudgetCard.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any

from collie_core.permissions.models import PermissionRequest, Risk
from collie_core.tools.life_db import life_db
from nanobot.agent.tools.base import Tool, tool_parameters

__all__ = ["BudgetTool"]


def _this_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def _card(db: Any, month: str) -> str:
    spent_rows = {r["category"]: float(r["spent"] or 0) for r in db.expenses_by_category(month)}
    budgets = {r["category"]: float(r["monthly_limit"] or 0) for r in db.list_budgets()}
    names = sorted(set(spent_rows) | set(budgets))
    categories = [
        {
            "name": name,
            "spent": round(spent_rows.get(name, 0.0), 2),
            "budget": round(budgets.get(name, 0.0), 2),
        }
        for name in names
    ]
    return json.dumps(
        {
            "card_type": "budget",
            "month": month,
            "categories": categories,
            "total_spent": round(sum(spent_rows.values()), 2),
            "total_budget": round(sum(budgets.values()), 2),
        }
    )


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["log_expense", "summary", "set_budget"],
                "description": "log an expense, show this month's breakdown, or set "
                "a monthly budget for a category.",
            },
            "amount": {
                "type": "number",
                "description": "For log_expense/set_budget: the amount.",
            },
            "category": {
                "type": "string",
                "description": "e.g. Groceries, Rent, Fun, Transport, Eating out.",
            },
            "description": {
                "type": "string",
                "description": "For log_expense: what it was.",
            },
            "date": {
                "type": "string",
                "description": "For log_expense: date YYYY-MM-DD (default today).",
            },
            "month": {
                "type": "string",
                "description": "For summary: month YYYY-MM (default this month).",
            },
        },
        "required": ["action"],
    }
)
class BudgetTool(Tool):
    """Track spending — log expenses, budgets, monthly breakdowns."""

    @property
    def name(self) -> str:
        return "budget"

    def permission_request(self, params: dict[str, Any]) -> PermissionRequest:
        action = str(params.get("action") or "").strip().lower()
        return PermissionRequest(
            action=f"budget.{action or 'manage'}",
            resource=str(params.get("month") or "budget"),
            risk=Risk.READ if action == "summary" else Risk.LOCAL_WRITE,
            summary="Review your budget" if action == "summary" else "Update your budget",
            reversible=True,
        )

    @property
    def description(self) -> str:
        return (
            "Track the user's spending: log expenses with a category, set "
            "monthly category budgets, and show a month's breakdown of "
            "spending vs. budget."
        )

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return life_db() is not None

    @classmethod
    def create(cls, ctx: Any) -> BudgetTool:
        return cls()

    async def execute(self, **kwargs: Any) -> Any:
        db = life_db()
        if db is None:
            return self.error("The budget tracker isn't available right now.")

        action = str(kwargs.get("action") or "").strip().lower()

        if action == "log_expense":
            try:
                amount = float(kwargs.get("amount"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return self.error("How much was it? I need an amount.")
            if not math.isfinite(amount) or amount < 0 or amount > 1_000_000_000:
                return self.error("That amount doesn't look right — try a sensible number.")
            category = str(kwargs.get("category") or "Other").strip() or "Other"
            db.add_expense(
                amount,
                category=category,
                description=str(kwargs.get("description") or "") or None,
                spent_at=str(kwargs.get("date") or "") or None,
            )
            return _card(db, _this_month())

        if action == "summary":
            month = str(kwargs.get("month") or "").strip() or _this_month()
            return _card(db, month)

        if action == "set_budget":
            category = str(kwargs.get("category") or "").strip()
            if not category:
                return self.error("Which category should this budget cover?")
            try:
                amount = float(kwargs.get("amount"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return self.error("What's the monthly limit? I need an amount.")
            if not math.isfinite(amount) or amount < 0 or amount > 1_000_000_000:
                return self.error("That limit doesn't look right — try a sensible number.")
            db.set_budget(category, amount)
            return _card(db, _this_month())

        return self.error(
            f"Not sure what to do with action '{action}'. Try log_expense, summary, or set_budget."
        )
