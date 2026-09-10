# Storage domains — splitting the CollieDB god object

> **Status (2026-09-10):** landed as a behavior-preserving refactor. The public
> interface did not change: every storage call site still uses `db.<method>`.

## Goal

`collie_core/db.py` had grown to 4,072 lines and one class with 164 methods
spanning conversations, reminders, shopping lists, connectors, providers,
checklists, and approvals. A file that size is hard for a human to hold and
hard for an agent to navigate: finding the reminders code meant reading past
everything else, and every feature PR touched the same file, so branches
collided in the same hunks.

The refactor does not add behaviour. It gives each storage domain one module
of its own, while `CollieDB` stays the single public interface.

## Shape

```
collie_core/
  db.py                  connection, PRAGMAs, schema, migrations, shared
                         row helpers, run/plan/telemetry storage, export/clear
  db_primitives.py       collie_home(), utc_now(), new_id() (leaf module)
  db_domains/
    __init__.py          re-exports the domain classes
    settings.py          SettingsDomain
    conversations.py     ConversationsDomain
    life.py              LifeDomain       people, dates, journal, reminders,
                                          shopping, budgets, health
    automations.py       AutomationsDomain
    checklists.py        ChecklistsDomain
    connectors.py        ConnectorsDomain services, connections, tools, subagents
    approvals.py         ApprovalsDomain
    providers.py         ProvidersDomain  providers, usage, product metrics
    artifacts.py         ArtifactsDomain
```

`CollieDB` composes those mixins:

```python
class CollieDB(
    SettingsDomain,
    ConversationsDomain,
    LifeDomain,
    ...
):
```

Callers are untouched. `from collie_core.db import CollieDB, collie_home,
utc_now, new_id` keeps working, because `db.py` re-imports the primitives and
lists them in `__all__`.

## The seam

A domain module may reach for exactly one thing outside itself: the plumbing
that `CollieDB` provides.

| Kind | Names |
| --- | --- |
| Write helpers | `_write`, `_write_immediate` |
| Read helpers | `_row`, `_rows`, `_canonical_json` |
| Instance state | `_conn`, `_lock`, `_product_metrics_enabled` |
| Shared vocabulary | `_local_today`, `_CHECKLIST_STEP_STATUSES` |

Everything else a domain needs lives in that domain. `collie_core/db_primitives.py`
is the only leaf the domains import, which is why the primitives moved out:
`db.py` imports the domains, so a domain importing `db.py` back for `new_id`
would be a cycle.

### Known cross-domain table reach

One domain owning one group of tables is the goal, not yet the whole truth.
Four domains touch tables they do not own, and each reach is deliberate:

| Domain | Foreign tables | Why |
| --- | --- | --- |
| conversations | plans, runs, run_steps, plan_change_requests, task_checklists, task_checklist_steps, conversation_review_gates, approval_requests | `delete_conversation` cascades into everything hanging off the conversation |
| checklists | conversations | `create_task_checklist` checks the conversation exists before writing |
| providers | settings | provider snapshot / restore reads and writes the settings rows |
| settings | providers | `set_active_model` updates the matching provider row |

`tests/collie/test_db_domain_split.py` enforces this list in both directions: an
undeclared reach fails, and a declaration no longer matched by the SQL fails too.
A new reach is therefore a conscious act with a reason, not something a later
reader has to re-derive from `DELETE` statements. The `delete_conversation`
cascade is the reason the run/plan split will have to touch `conversations.py`
alongside `db.py`.

## Why this is safe

Every moved method is verbatim the version it replaced: 160 of the 164
`CollieDB` members compared with `inspect.getsource` before and after the move,
the other 4 (three class constants and the `schema_version` property) compared
by value, since they carry no source. Result: 0 missing, 0 changed. The full
backend suite and the `ruff` gates are the behavioural proof.

## Adding or changing a domain

1. Put the method on the domain class, not on `CollieDB`.
2. If it needs a helper, ask whether the helper is domain vocabulary (keep it
   in the domain module) or host plumbing (add it to the `CollieDB` table
   above).
3. Add the method name to `DOMAIN_METHODS` in
   `tests/collie/test_db_domain_split.py`. That test fails if a method is
   unowned, silently shadowed by another domain, or reaching across domains,
   so the split cannot rot.
4. Never create a domain to domain call. If two domains need the same
   behaviour, it belongs to the host.

## Left behind on purpose

The run, plan, checklist-revision, and telemetry methods stay in `db.py`. They
share `_write_immediate` transaction semantics and the run-task revision
helpers, so they are one transactional cluster, not several domains. Splitting
them is a separate, riskier job: it changes how run state is written, which is
exactly the code that must not regress.
