"""Guard the storage-domain split of :class:`collie_core.db.CollieDB`.

``collie_core.db`` used to be one 4k line module holding every storage method.
The storage domains now live in ``collie_core.db_domains``, one module per
domain, and are mixed back into ``CollieDB``, which stays the single public
interface, so call sites keep using ``db.<method>``.

These tests pin the properties that keep the split honest:

- every domain method is still reachable as ``CollieDB.<method>`` and is owned
  by the domain module named below;
- no two domains own the same name, because a collision would silently shadow
  one of them through the MRO;
- a domain module reaches only for the shared plumbing, never for another
  domain, which is what lets the next split happen without touching callers.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import collie_core.db_domains as db_domains
from collie_core.db import CollieDB

# Frozen ownership table. Add a method to a domain module and it belongs here;
# delete one and this test tells you which call sites you just broke.
DOMAIN_METHODS: dict[str, tuple[str, ...]] = {
    "approvals": (
        "add_approval_rule",
        "list_approval_rules",
        "delete_approval_rule",
        "create_approval_request",
        "list_pending_approvals",
        "resolve_approval_request",
    ),
    "artifacts": (
        "snapshot_artifact",
        "latest_artifact_version",
        "list_artifact_versions",
        "get_artifact_version",
        "mark_artifact_rolled_back",
    ),
    "automations": (
        "add_automation",
        "list_automations",
        "toggle_automation",
        "mark_routine_result",
        "_mark_routine_result_with",
        "get_automation",
        "update_automation",
        "set_routine_shared_delivery",
        "record_routine_shared_delivery",
        "delete_automation",
    ),
    "checklists": (
        "require_conversation_review",
        "get_conversation_review_gate",
        "_checklist_task_with",
        "create_task_checklist",
        "get_task_checklist",
        "_require_checklist_revision",
        "update_task_checklist",
        "complete_task_checklist",
        "cancel_task_checklist",
    ),
    "connectors": (
        "upsert_service",
        "get_service",
        "list_services",
        "upsert_connector_connection",
        "get_connector_connection",
        "list_connector_connections",
        "delete_connector_connection",
        "save_connector_definition",
        "get_connector_definition",
        "list_connector_definitions",
        "advance_connector_operation",
        "replace_connector_tools",
        "list_connector_tools",
        "upsert_subagent",
        "list_subagents",
        "delete_subagent",
    ),
    "conversations": (
        "create_conversation",
        "get_conversation",
        "set_conversation_mode",
        "set_conversation_project",
        "list_conversations",
        "rename_conversation",
        "delete_conversation",
        "add_message",
        "get_messages",
        "all_messages_with_attachments",
        "search_messages",
    ),
    "life": (
        "add_person",
        "get_person",
        "find_person",
        "list_people",
        "update_person",
        "delete_person",
        "add_date",
        "list_dates",
        "get_date",
        "update_date",
        "delete_date",
        "log_memory_journal",
        "list_memory_journal",
        "add_reminder",
        "list_reminders",
        "due_reminders",
        "_resolve_reminder_id",
        "complete_reminder",
        "snooze_reminder",
        "delete_reminder",
        "add_shopping_item",
        "list_shopping_items",
        "find_shopping_item",
        "check_shopping_item_by_name",
        "delete_shopping_item_by_name",
        "clear_checked_shopping_items",
        "add_expense",
        "expenses_by_category",
        "set_budget",
        "list_budgets",
        "log_health",
        "health_logs_since",
        "health_latest",
    ),
    "providers": (
        "snapshot_provider_configuration",
        "configure_provider_candidate_record",
        "restore_provider_configuration",
        "upsert_provider",
        "get_provider",
        "list_providers",
        "default_provider",
        "set_default_provider",
        "delete_provider",
        "record_usage",
        "usage_this_month",
        "_increment_product_metrics",
        "product_metrics",
    ),
    "settings": (
        "get_setting",
        "set_setting",
        "set_active_model",
        "all_settings",
        "delete_setting",
        "get_profile",
        "set_profile",
        "all_profile",
        "delete_profile",
    ),
}

# What the host class lends to every domain: the read/write helpers, the
# connection state held on the instance, and the constants they read. Nothing
# listed here belongs to another domain.
PLUMBING = {
    "_CHECKLIST_STEP_STATUSES",
    "_canonical_json",
    "_conn",
    "_lock",
    "_local_today",
    "_product_metrics_enabled",
    "_row",
    "_rows",
    "_write",
    "_write_immediate",
}

DOMAIN_DIR = Path(db_domains.__file__).parent


@pytest.mark.parametrize(("domain", "methods"), DOMAIN_METHODS.items())
def test_domain_owns_its_methods(domain: str, methods: tuple[str, ...]) -> None:
    """Each listed method is reachable on CollieDB and owned by its module."""
    for name in methods:
        member = getattr(CollieDB, name)
        assert callable(member), f"CollieDB.{name} is not callable"
        owner = member.__module__
        assert owner == f"collie_core.db_domains.{domain}", (
            f"CollieDB.{name} is owned by {owner}, expected the {domain} domain"
        )


def test_no_domain_shadows_another() -> None:
    """Two domains owning one name would shadow silently through the MRO."""
    owners: dict[str, list[str]] = {}
    for domain, methods in DOMAIN_METHODS.items():
        for name in methods:
            owners.setdefault(name, []).append(domain)
    collisions = {name: domains for name, domains in owners.items() if len(domains) > 1}
    assert collisions == {}


@pytest.mark.parametrize("domain", sorted(DOMAIN_METHODS))
def test_domain_depends_only_on_the_host_plumbing(domain: str) -> None:
    """A domain module may not reach into another domain."""
    module_path = DOMAIN_DIR / f"{domain}.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    klass = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    local = {
        node.name
        for node in klass.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    # Class-level constants the domain owns count as local too.
    for node in klass.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            local |= {t.id for t in targets if isinstance(t, ast.Name)}

    reached = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            reached.add(node.attr)

    foreign = sorted(reached - local - PLUMBING)
    assert foreign == [], (
        f"the {domain} domain reaches for {foreign}; move the shared piece into "
        f"the host plumbing in collie_core.db or keep it inside the domain"
    )
