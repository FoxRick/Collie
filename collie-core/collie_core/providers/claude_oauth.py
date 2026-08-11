"""Claude subscription OAuth provider.

Signs the user in with their existing Claude Pro/Max subscription using the
public Claude Code OAuth client (PKCE + localhost callback), then talks to
the Anthropic API with the resulting Bearer token.

Token storage/refresh is handled by ``oauth_cli_kit`` in the same way as the
ChatGPT (Codex) provider, keeping both OAuth paths symmetrical.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from oauth_cli_kit import OAuthProviderConfig

from nanobot.providers.anthropic_provider import AnthropicProvider

__all__ = [
    "ANTHROPIC_OAUTH_PROVIDER",
    "ClaudeOAuthProvider",
    "claude_storage",
]

# Public OAuth client used by Claude Code and compatible harnesses.
ANTHROPIC_OAUTH_PROVIDER = OAuthProviderConfig(
    client_id="9d1c250a-e61b-44d9-88ed-5944d1962f5e",
    authorize_url="https://claude.ai/oauth/authorize",
    token_url="https://console.anthropic.com/v1/oauth/token",
    # oauth_cli_kit's callback server uses this fixed endpoint.
    redirect_uri="http://localhost:1455/auth/callback",
    scope="org:create_api_key user:profile user:inference",
    default_originator="collie",
    token_filename="claude.json",
)

_OAUTH_BETA_HEADER = "oauth-2025-04-20"
_DEFAULT_MODEL = "claude-sonnet-4-6"


def claude_storage() -> Any:
    from collie_core.providers.storage import DpapiTokenStorage

    return DpapiTokenStorage(token_filename=ANTHROPIC_OAUTH_PROVIDER.token_filename)


def _current_access_token() -> str | None:
    from oauth_cli_kit import get_token

    try:
        token = get_token(provider=ANTHROPIC_OAUTH_PROVIDER, storage=claude_storage())
    except Exception:
        logger.debug("No Claude OAuth token available")
        return None
    return token.access if token and token.access else None


class ClaudeOAuthProvider(AnthropicProvider):
    """AnthropicProvider variant authenticated with a subscription Bearer token."""

    def __init__(self, default_model: str = _DEFAULT_MODEL):
        access = _current_access_token()
        if not access:
            raise RuntimeError("Not signed in with Claude. Complete the OAuth flow first.")
        # Skip AnthropicProvider.__init__ client construction; build our own
        # Bearer-authenticated client instead.
        from anthropic import AsyncAnthropic

        from nanobot.providers.base import LLMProvider

        LLMProvider.__init__(self, api_key=None, api_base=None)
        self.default_model = default_model
        self.extra_headers: dict[str, str] = {"anthropic-beta": _OAUTH_BETA_HEADER}
        self._client = AsyncAnthropic(
            auth_token=access,
            default_headers=dict(self.extra_headers),
            max_retries=0,
        )

    def refresh_auth(self) -> bool:
        """Re-read the (possibly refreshed) token and rebuild the client."""
        access = _current_access_token()
        if not access:
            return False
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(
            auth_token=access,
            default_headers=dict(self.extra_headers),
            max_retries=0,
        )
        return True

    def get_default_model(self) -> str:
        return self.default_model

    async def chat(self, *args: Any, **kwargs: Any) -> Any:
        self.refresh_auth()
        return await super().chat(*args, **kwargs)

    async def chat_stream(self, *args: Any, **kwargs: Any) -> Any:
        self.refresh_auth()
        return await super().chat_stream(*args, **kwargs)
