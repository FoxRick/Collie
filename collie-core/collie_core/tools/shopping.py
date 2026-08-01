"""Shopping list tool: in-app SQLite engine.

Categorized items with checkboxes, rendered as a ShoppingListCard.
"""

from __future__ import annotations

import json
from typing import Any

from collie_core.tools.life_db import life_db
from nanobot.agent.tools.base import Tool, tool_parameters

__all__ = ["ShoppingTool"]


def _card(db: Any, list_name: str) -> str:
    items = db.list_shopping_items(list_name)
    categories: dict[str, list[dict[str, Any]]] = {}
    for row in items:
        categories.setdefault(str(row.get("category") or "Other"), []).append({
            "name": row["item"],
            "quantity": row.get("quantity"),
            "done": bool(row.get("checked")),
            "id": row["id"],
        })
    return json.dumps({
        "card_type": "shopping_list",
        "list_name": list_name,
        "categories": categories,
        "total": len(items),
        "remaining": sum(1 for r in items if not r.get("checked")),
    })


@tool_parameters({
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["add", "list", "check", "uncheck", "remove", "clear_checked"],
            "description": "add items, show the list, check/uncheck one off, "
                           "remove one, or clear everything already checked.",
        },
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "category": {
                        "type": "string",
                        "description": "e.g. Produce, Dairy, Pantry, Household.",
                    },
                    "quantity": {"type": "string", "description": "e.g. '2', '500g'."},
                },
                "required": ["name"],
            },
            "description": "For action=add: one or more items to add.",
        },
        "item": {
            "type": "string",
            "description": "For check/uncheck/remove: the item's name.",
        },
        "list_name": {
            "type": "string",
            "description": "Which list (default 'Groceries').",
        },
    },
    "required": ["action"],
})
class ShoppingTool(Tool):
    """Manage the shopping list — add, check off, clear."""

    @property
    def name(self) -> str:
        return "shopping_list"

    @property
    def description(self) -> str:
        return (
            "Manage the user's shopping list: add items (with category and "
            "quantity), show the list, check items off, or clear checked ones. "
            "Also useful to build a grocery list from a meal plan."
        )

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return life_db() is not None

    @classmethod
    def create(cls, ctx: Any) -> "ShoppingTool":
        return cls()

    async def execute(self, **kwargs: Any) -> Any:
        db = life_db()
        if db is None:
            return self.error("The shopping list isn't available right now.")

        action = str(kwargs.get("action") or "").strip().lower()
        list_name = str(kwargs.get("list_name") or "Groceries").strip() or "Groceries"

        if action == "add":
            items = kwargs.get("items") or []
            if not isinstance(items, list) or not items:
                return self.error("What should I add? Give me at least one item.")
            added = 0
            for entry in items:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name") or "").strip()
                if not name:
                    continue
                db.add_shopping_item(
                    name,
                    category=str(entry.get("category") or "Other"),
                    quantity=(str(entry["quantity"]) if entry.get("quantity") else None),
                    list_name=list_name,
                )
                added += 1
            if not added:
                return self.error("Those items didn't have names I could use.")
            return _card(db, list_name)

        if action == "list":
            return _card(db, list_name)

        if action in ("check", "uncheck", "remove"):
            name = str(kwargs.get("item") or "").strip()
            if not name:
                return self.error("Which item?")
            row = db.find_shopping_item(name, list_name)
            if row is None:
                return f"I don't see '{name}' on the {list_name} list."
            if action == "remove":
                db.delete_shopping_item_by_name(name, list_name)
            else:
                db.check_shopping_item_by_name(name, list_name, checked=(action == "check"))
            return _card(db, list_name)

        if action == "clear_checked":
            db.clear_checked_shopping_items(list_name)
            return _card(db, list_name)

        return self.error(
            f"Not sure what to do with action '{action}'. Try add, list, check, "
            "uncheck, remove, or clear_checked."
        )
