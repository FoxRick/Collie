# Collie vision

**Status:** canonical
**Audience:** product, engineering, design, and go-to-market

## The product

Collie is a friendly, local-first personal AI for nontechnical Windows users:
powerful enough to complete real work, clear enough to understand, and safe
enough to trust. It should feel like a smart, loyal companion rather than a
developer console or a collection of disconnected AI features.

The primary interface is conversation. Menus remain useful for discovery and
control, but a user should be able to ask Collie to configure safe settings,
connect supported services, create routines, manage specialist agents, and
complete everyday tasks without learning technical vocabulary.

## Product promise

Collie should make advanced AI feel:

- **Approachable:** plain language, guided setup, and honest recovery.
- **Useful:** durable memory, specialist skills, connected services, routines,
  and visible progress on multi-step work.
- **Controllable:** clear plans when scope is broad, explicit approval for
  consequential actions, and meaningful stop/retry controls.
- **Private by default:** local state, minimal data collection, OS-protected
  secrets, and no mandatory Collie account.
- **Truthful:** availability, connection state, model support, and release
  claims come from verification rather than catalogue flags or marketing hope.

## Who it is for

The first user is a Windows user who wants the leverage of agentic AI without
having to understand terminals, APIs, MCP servers, prompt engineering, or
automation syntax. Experts may use Collie, but expert-only flexibility must not
make the normal path intimidating.

## Experience principles

1. **Chat first, controls nearby.** Safe UI actions should normally have a
   corresponding approved chat tool backed by the same implementation.
2. **Show useful progress, not hidden reasoning.** Multi-step work gets a
   compact, durable checklist; broad or materially risky work gets a
   reviewable plan.
3. **Permissions are behavioral, not tonal.** A warm personality never grants
   authority. Central policy and per-action approvals remain authoritative.
4. **Local-first is an architecture choice.** User state lives locally unless
   a connected feature explicitly needs an external service.
5. **Recovery is part of the product.** Diagnostics should explain failures in
   human terms and safely repair what can be repaired.
6. **Improvement follows evidence.** Collie may maintain indexes and suggest
   better instructions, but meaningful behavior, memory, permission, or agent
   changes require review and rollback.

## Alpha boundaries

- Windows x64 is the initial supported platform.
- Users connect a supported model provider; Collie does not require its own
  application account.
- Connected services are enabled only after authentication, discovery, a live
  health check, and packaged-app verification.
- High-consequence actions remain disabled or explicitly approved until their
  safety and recovery behavior is designed and tested.
- The public website is the entry point and mailing-list surface; GitHub
  Releases are the canonical home for versioned installers and checksums.

## Success

Collie succeeds when a new user can install it, connect intelligence, ask for
real work in ordinary language, understand what is happening, safely correct
or stop it, and return later without having to reconstruct their context.

## Non-goals

- A developer shell disguised as a consumer application.
- A universal connector claim unsupported by real provider verification.
- Autonomous changes to permissions, identity, or personal memory without
  review.
- Showing chain-of-thought or raw tool traffic as the default progress model.
- Trading user control or truthful behavior for a more magical demo.
