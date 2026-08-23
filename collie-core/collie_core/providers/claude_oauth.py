"""Claude subscription OAuth provider.

Signs the user in with their existing Claude Pro/Max subscription using the
public Claude Code OAuth client (PKCE + localhost callback), then talks to
the Anthropic API with the resulting Bearer token.

Token storage/refresh is handled by ``oauth_cli_kit`` in the same way as the
ChatGPT (Codex) provider, keeping both OAuth paths symmetrical.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
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
# Matches Claude Code's current flow: console.anthropic.com authorize
# endpoint and the registered localhost:54545/callback redirect. The legacy
# claude.ai/oauth/authorize surface rejects this request shape ("Invalid
# request format") for signed-in users, so do not regress these values.
ANTHROPIC_OAUTH_PROVIDER = OAuthProviderConfig(
    client_id="9d1c250a-e61b-44d9-88ed-5944d1962f5e",
    authorize_url="https://console.anthropic.com/oauth/authorize",
    token_url="https://console.anthropic.com/v1/oauth/token",
    # oauth_cli_kit's callback server is hardcoded to the Codex endpoint, so
    # collie_core.providers.callback_server derives port/path from this value.
    redirect_uri="http://localhost:54545/callback",
    scope="org:create_api_key user:profile",
    token_filename="claude.json",
)

_OAUTH_BETA_HEADER = "oauth-2025-04-20"
_DEFAULT_MODEL = "claude-sonnet-4-6"
_EXPIRY_SKEW_SECONDS = 60


@dataclass(frozen=True, slots=True)
class _AccessToken:
    access: str
    expires_at: float


def claude_storage() -> Any:
    from collie_core.providers.storage import DpapiTokenStorage

    return DpapiTokenStorage(token_filename=ANTHROPIC_OAUTH_PROVIDER.token_filename)


def _current_access_token() -> _AccessToken | None:
    from oauth_cli_kit import get_token

    try:
        token = get_token(provider=ANTHROPIC_OAUTH_PROVIDER, storage=claude_storage())
    except Exception:
        logger.debug("No Claude OAuth token available")
        return None
    if not token or not token.access:
        return None
    return _AccessToken(access=str(token.access), expires_at=float(token.expires) / 1000)


class ClaudeOAuthProvider(AnthropicProvider):
    """AnthropicProvider variant authenticated with a subscription Bearer token."""

    def __init__(self, default_model: str = _DEFAULT_MODEL):
        token = _current_access_token()
        if token is None:
            raise RuntimeError("Not signed in with Claude. Complete the OAuth flow first.")
        # Skip AnthropicProvider.__init__ client construction; build our own
        # Bearer-authenticated client instead.
        from nanobot.providers.base import LLMProvider

        LLMProvider.__init__(self, api_key=None, api_base=None)
        self.default_model = default_model
        self.extra_headers: dict[str, str] = {"anthropic-beta": _OAUTH_BETA_HEADER}
        self._access_token = token.access
        self._access_token_expires_at = token.expires_at
        self._token_lock = asyncio.Lock()
        self._client = self._build_client(token.access)

    def _build_client(self, access: str) -> Any:
        from anthropic import AsyncAnthropic

        return AsyncAnthropic(
            auth_token=access,
            default_headers=dict(self.extra_headers),
            max_retries=0,
        )

    def _token_is_fresh(self, now: float) -> bool:
        return now < self._access_token_expires_at - _EXPIRY_SKEW_SECONDS

    async def refresh_auth(self) -> bool:
        """Refresh near-expiry auth once without blocking the event loop."""
        if self._token_is_fresh(time.time()):
            return True

        async with self._token_lock:
            if self._token_is_fresh(time.time()):
                return True
            try:
                token = await asyncio.to_thread(_current_access_token)
            except Exception:
                logger.debug("Claude OAuth token refresh failed")
                return False
            if token is None:
                return False

            token_changed = token.access != self._access_token
            self._access_token = token.access
            self._access_token_expires_at = token.expires_at
            if token_changed:
                self._client = self._build_client(token.access)
            return True

    def get_default_model(self) -> str:
        return self.default_model

    async def chat(self, *args: Any, **kwargs: Any) -> Any:
        await self.refresh_auth()
        return await super().chat(*args, **kwargs)

    async def chat_stream(self, *args: Any, **kwargs: Any) -> Any:
        await self.refresh_auth()
        return await super().chat_stream(*args, **kwargs)
