# Product documentation

Active product truth is organized by purpose:

- `strategy/provider-onboarding.md` - provider access, account model, and
  first-run direction.
- `features/agent-system.md` - chat-first configuration, Guide, Doctor, and
  evidence-driven Gardener direction.
- `features/connectors.md` - target connector experience and initial provider
  stack.
- `features/planning-ui-and-more/` - accepted checklist and reviewed-plan
  behavior.
- `features/shared-sessions-implementation-plan.md` - proposed account-based shared
  sessions, personal execution, bounded cloud sync, and verified local auto-archive;
  step-by-step backend/core/frontend plan, not implemented.
- `features/share-collie.md` - parked earlier Telegram multiplayer proposal;
  its identity and execution defaults are superseded by the shared-session plan.
- `design/assets/` - product and character design references used by the app.

When implementation changes a product decision, update the single relevant
file. When a feature ships, keep its durable behavior here and move temporary
execution notes to `../archive/`.
