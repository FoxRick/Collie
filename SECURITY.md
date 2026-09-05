# Security Policy

## System and scope

Collie is a local-first Windows desktop application. This repository contains
the Electron desktop shell (`collie-ui/`), the managed Python runtime
(`collie-core/`), and release tooling. The landing site is a separate
repository and is not covered by this policy.

Important assets include local user data, provider and messenger credentials,
approval decisions, and the integrity of packaged Windows releases. The
desktop shell owns OS-protected secret storage; the Python runtime owns local
state, agent behavior, tool execution, and permission evaluation.

## Threat model and security invariants

Reviewers should treat prompts, attachments, local files, network responses,
connected-service data, and renderer input as potentially attacker-controlled.
Meaningful security properties include:

- Long-lived secrets must not be exposed to the renderer, source control,
  logs, fixtures, screenshots, or documentation.
- Consequential, destructive, financial, external-write, send, and publish
  actions must remain centrally gated by the approval policy.
- The Electron renderer must use the narrow preload bridge rather than gaining
  unrestricted OS or secret access.
- Localhost IPC must authenticate peers and reject untrusted callers.
- Connector or messenger credentials and data must not be sent to unrelated
  parties.
- A release must be traceable to reviewed source and its published integrity
  data.

## Reporting a vulnerability

Do not report security vulnerabilities in public issues, discussions, or
social-media posts. Once GitHub private vulnerability reporting is enabled,
use the private reporting channel in this repository's Security tab. If that
channel is unavailable, email [hello@heycollie.com](mailto:hello@heycollie.com)
with `Security report` in the subject line.

Include the affected version or commit, reproducible steps, impact, and a
minimal proof of concept where safe. Do not access other people's data,
disrupt services, or include credentials in a report.

**Publication prerequisite:** enable GitHub private vulnerability reporting
and verify the repository's reporting settings before relying on this policy.
The email address above is a manual fallback, not a claim of a dedicated
security mailbox or response-time commitment.

## Reportable findings and severity context

Report realistic, reachable failures of the invariants above, including secret
exposure, approval bypass, unauthorized IPC or privileged-bridge access,
unsafe handling of untrusted content, credential leakage, and release-integrity
failures. Impact and reachability matter: a hypothetical issue without a
credible path to the desktop application, packaged release, or user data may
not warrant the same priority.

## Limits and known alpha risks

Collie is pre-release software. Its connectors and the optional Collie account
sign-in (hosted by Supabase, identity only) are live in alpha but provided on a
best-effort basis, and there is no public installer yet. The application sends
a request's relevant content to the model provider a user chooses; provider
data handling is an external trust boundary. Windows release signing, a
clean-account installation rehearsal, release artifact validation, and
support/security routing are tracked release gates, not completed assurances.

## Security development expectations

Keep changes narrow, avoid logging sensitive data, preserve authorization and
approval tests, and report suspected committed secrets through the private
route. Do not reproduce a suspected secret in a public issue or commit.

## Attribution and notices

Collie includes adapted code from
[HKUDS/nanobot](https://github.com/HKUDS/nanobot), licensed under MIT. See
[LICENSE](LICENSE) and
[collie-core/THIRD_PARTY_NOTICES.md](collie-core/THIRD_PARTY_NOTICES.md).
