# Provider access and onboarding strategy

**Status:** product decision record
**Date:** 2026-08-01

## Product position

Collie is a free, local-first Windows desktop application. Users download the
installer, connect an intelligence provider they already use, and optionally
connect their own services. Collie does not require a separate Collie account
to use the application.

Collie must make three different kinds of account clear:

- **Collie account:** not required.
- **Model-provider account:** required only for the provider the user connects.
- **Connected-service account:** required only for a service such as Gmail,
  Outlook, or Telegram.

The marketing site may maintain a mailing list for early access, release
announcements, feedback, and support. This is a contact and consent record,
not an application identity system. Collect the minimum useful information:
email, optional use case, clear consent, and a one-click unsubscribe route.

## Distribution

- The public website is the friendly entry point and should lead with a
  **Download for Windows** call to action once the release is public.
- GitHub Releases are the canonical location for versioned installers,
  checksums, release notes, and source code.
- During the invited alpha, the website mailing list is the tester queue. Send
  approved testers an onboarding email and download link; do not make them
  create a Collie login.

## Model-provider decisions

| Provider | Access experience | Product decision |
| --- | --- | --- |
| OpenAI / ChatGPT | Browser sign-in | Keep as a primary card, subject to the provider-supported production flow. |
| Anthropic / Claude | Browser sign-in | Keep as a primary card, subject to the provider-supported production flow. |
| GitHub Copilot | Browser sign-in with GitHub OAuth | Add as a primary provider after a registered GitHub OAuth app and subscription-entitlement testing. GitHub documents this model-access pattern through its Copilot SDK. |
| Google Gemini | API key / Google AI Studio project | Add under API providers, not as a consumer browser-login card. A Google/Gemini consumer subscription is not equivalent to Gemini API access and billing. |
| Perplexity | API key / Perplexity API portal | Add under API providers. Current official developer documentation is API-group and API-key based; no equivalent third-party consumer sign-in flow was identified. Collie can support Sonar as a web-grounded model and later its Agent API when the runtime supports the necessary transport. |
| OpenRouter | API key | Add as a high-value API provider: one key gives users access to a broad model catalogue. |
| xAI, DeepSeek, Groq, Mistral, Together, Fireworks, etc. | API key | Offer through the provider catalogue rather than prominent first-run cards. |
| Ollama / local models | Local connection | Treat as a separate local option; detect a local installation and list its models. |
| Custom/self-hosted endpoint | Base URL + optional key | Keep as an advanced escape hatch for OpenAI- and Anthropic-compatible endpoints. |

Do not label a provider as a browser sign-in merely because the company offers
a consumer chat subscription. The integration must have an explicit
third-party authorization path that gives Collie valid model access.

## Onboarding experience

The first run should not expose base URLs, endpoint paths, protocols, headers,
or raw model identifiers.

1. Present three simple choices:
   - **Sign in to a provider**: ChatGPT, Claude, GitHub Copilot.
   - **Use an API key**: Gemini, Perplexity, OpenRouter, and other providers.
   - **Use a local model**: Ollama or another local compatible server.
2. For an API provider, the normal form has only:
   - provider picker (searchable),
   - API key field,
   - **Connect** button.
3. On Connect, Collie fills the protocol, base URL, required headers, and a
   sensible default model; saves the secret to OS-protected storage; then
   validates the connection.
4. Where supported, query the provider's model-list endpoint using the user's
   key. Otherwise, make a small read-only test request to the recommended
   model.
5. Show a human result, for example: **Connected - using Gemini Flash**. Let
   the user change models later rather than forcing a model choice before their
   first chat.

For custom/self-hosted providers, start with just:

```text
Base URL: [ https://... ]
API key:  [ optional for local servers ]
          [ Detect models ]
```

Collie should normalize common URLs, try the standard model-discovery route,
and present detected models. Reveal protocol, model ID, and custom-header
fields only under **Advanced** or when automatic detection fails.

## Provider catalogue

Use [models.dev](https://models.dev) as the open-source, periodically updated
catalogue. Its MIT-licensed API data covers providers, model identifiers,
capabilities, pricing and limits, and records default API URLs for
OpenAI-compatible providers.

Implementation rules:

- Bundle a reviewed snapshot with every Collie release so onboarding works
  offline and is stable.
- Offer an optional, HTTPS-fetched, schema-validated catalogue refresh on a
  sensible cadence (for example weekly), retaining the version and hash of the
  accepted snapshot rather than making a live feed a runtime dependency.
- Maintain a small Collie-owned curated layer over the catalogue. It defines
  the display name, authentication type, correct adapter, default model, help
  link, and whether Collie has tested the provider.
- Use live provider discovery and a real test request as the final authority on
  whether a user's key can access a model. A generic catalogue cannot know an
  account's subscription, billing state, region, or allowed models.
- Do not claim that every catalogue provider is fully supported. The catalogue
  is discovery metadata; a provider adapter and verification determine support.

LiteLLM is a possible future internal adapter layer and model/cost metadata
source. Do not introduce its self-hosted gateway merely to solve first-run UX;
the catalogue, provider adapters, and validation flow are the immediate need.

## Implementation priority

1. Replace the normal custom-provider form in `WelcomeScreen` and
   `ProviderManager` with the provider picker + API-key flow. Keep all current
   endpoint/protocol/model/header fields under Advanced.
2. Add a bundled provider preset layer for the existing primary providers plus
   Gemini, Perplexity, OpenRouter, and Ollama.
3. Implement model discovery and a safe connection test; return readable
   failures and never log secrets.
4. Add the models.dev snapshot importer and optional refresh with a version,
   hash, rollback, and last-updated display.
5. Add GitHub Copilot browser OAuth after implementing and testing the GitHub
   OAuth application flow.
6. Add provider-specific capability checks so Collie only enables tool use,
   vision, structured output, or attachments when the selected model supports
   them.

## Sources checked

- [models.dev repository and API](https://github.com/anomalyco/models.dev)
- [GitHub Copilot OAuth setup](https://docs.github.com/en/copilot/how-tos/copilot-sdk/setup/github-oauth)
- [Gemini API OAuth guidance](https://ai.google.dev/gemini-api/docs/oauth)
- [Gemini API billing](https://ai.google.dev/gemini-api/docs/billing)
- [Perplexity API quickstart](https://docs.perplexity.ai/docs/getting-started/quickstart)
- [Perplexity API key management](https://docs.perplexity.ai/docs/admin/api-key-management)
- [LiteLLM](https://github.com/BerriAI/litellm)
