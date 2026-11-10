"""ChatGPT OAuth sign-in and legacy credential cleanup.

- ChatGPT OAuth  -> ``oauth_cli_kit`` Codex flow (browser + localhost callback)
- API key        -> held in memory via ``collie_core.settings.set_api_key``
                    (the Electron shell owns keychain persistence)

These helpers are synchronous (the OAuth flow blocks on the browser round
trip); the IPC server runs them in a thread.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from threading import Event, Lock
from typing import Any

from loguru import logger

__all__ = ["OAuthLoginAttempt", "login_provider", "logout_provider", "oauth_status"]

CODEX_DEFAULT_MODEL = "openai-codex/gpt-5.4"
CHATGPT = "chatgpt"
CLAUDE = "claude"


def _codex_provider_and_storage():
    from oauth_cli_kit.providers import OPENAI_CODEX_PROVIDER

    from collie_core.providers.storage import DpapiTokenStorage

    return OPENAI_CODEX_PROVIDER, DpapiTokenStorage(
        token_filename=OPENAI_CODEX_PROVIDER.token_filename
    )


def _spec(provider: str):
    name = (provider or "").strip().lower()
    if name in (CHATGPT, "openai", "openai_codex", "codex"):
        return CHATGPT, _codex_provider_and_storage()
    if _is_legacy_claude(name):
        raise ValueError("Connect Claude with an API key from Claude Console.")
    raise ValueError(f"unknown OAuth provider: {provider!r}")


def _is_legacy_claude(provider: str) -> bool:
    return provider.strip().lower() in (CLAUDE, "anthropic", "claude_oauth")


def get_chatgpt_token():
    """Read and refresh the same protected token used by desktop sign-in."""
    from oauth_cli_kit import get_token

    config, storage = _codex_provider_and_storage()
    return get_token(provider=config, storage=storage)


class OAuthFlowError(ValueError):
    """A safe sign-in error for the desktop."""


class OAuthLoginCancelledError(RuntimeError):
    """Raised when an OAuth attempt is cancelled before it can commit."""


class _StagedTokenStorage:
    """Hold OAuth writes in memory until the owning attempt is current."""

    def __init__(self, storage: Any, cancelled: Event) -> None:
        self._storage = storage
        self._cancelled = cancelled
        self._lock = Lock()
        self._token: Any = None

    def get_token_path(self):
        return self._storage.get_token_path()

    def load(self) -> Any:
        return self._storage.load()

    def save(self, token: Any) -> None:
        with self._lock:
            if not self._cancelled.is_set():
                self._token = token

    def commit(self) -> bool:
        with self._lock:
            if self._cancelled.is_set():
                self._token = None
                return False
            if self._token is not None:
                self._storage.save(self._token)
                self._token = None
            return True

    def discard(self) -> None:
        with self._lock:
            self._token = None


class OAuthLoginAttempt:
    """A cancellable OAuth run whose token writes require an explicit commit.

    The helper library has no callback-server cancellation hook. Cancelling an
    asyncio wrapper therefore cannot stop its worker immediately, but staged
    storage guarantees a late worker completion cannot persist credentials.
    The helper still closes its callback server in its own ``finally`` block.
    """

    def __init__(self, provider: str) -> None:
        self.provider, (self._config, storage) = _spec(provider)
        self._cancelled = Event()
        self._token: Any = None
        self._storage = _StagedTokenStorage(storage, self._cancelled)

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        self._cancelled.set()
        self.discard()

    def run(self) -> dict[str, Any]:
        return _login_with_storage(
            self.provider,
            self._config,
            self._storage,
            cancelled=self._cancelled,
            on_token=self._capture_token,
        )

    def _capture_token(self, token: Any) -> None:
        self._token = token

    async def validate(self, model: str) -> None:
        import asyncio

        from nanobot.providers.openai_codex_provider import OpenAICodexProvider

        provider = OpenAICodexProvider(default_model=model, token_loader=lambda: self._token)
        try:
            async with asyncio.timeout(20):
                response = await provider.chat(
                    [{"role": "user", "content": "Reply with OK."}],
                    reasoning_effort="low",
                )
        except TimeoutError:
            raise OAuthFlowError("ChatGPT took too long to respond. Try again.") from None
        if response.finish_reason == "stop" and response.content:
            return
        status = response.error_status_code
        if status in (401, 403):
            raise OAuthFlowError(
                "ChatGPT denied model access. Check your account or workspace permissions."
            )
        if status == 429:
            raise OAuthFlowError(
                "ChatGPT is rate-limited or out of plan usage. Check your plan and try again."
            )
        if status == 404:
            raise OAuthFlowError("Your ChatGPT account could not access Collie's default model.")
        raise OAuthFlowError("I couldn't verify ChatGPT model access. Try again in a moment.")

    def commit(self) -> bool:
        return self._storage.commit()

    def discard(self) -> None:
        self._storage.discard()
        self._token = None


def _login_oauth_cancellable(config: Any, storage: Any, cancelled: Event) -> Any:
    """Run oauth_cli_kit's browser flow with prompt listener cancellation.

    The public helper does not expose the callback server. Its low-level flow
    primitives are used here so an attempt cancellation can close that server
    instead of leaving its fixed localhost port occupied until timeout.
    """
    import asyncio
    import time
    import urllib.parse
    import webbrowser

    from oauth_cli_kit import flow as oauth_flow

    async def check_cancelled() -> None:
        if cancelled.is_set():
            raise OAuthLoginCancelledError("Sign-in cancelled.")

    async def run() -> Any:
        from collie_core.providers.callback_server import start_callback_server

        verifier, challenge = oauth_flow._generate_pkce()
        state = oauth_flow._create_state()
        params = {
            "response_type": "code",
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "scope": config.scope,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        params["id_token_add_organizations"] = "true"
        params["codex_cli_simplified_flow"] = "true"
        params["originator"] = config.default_originator
        url = f"{config.authorize_url}?{urllib.parse.urlencode(params)}"
        loop = asyncio.get_running_loop()
        code_future: asyncio.Future[str] = loop.create_future()

        def notify(code: str) -> None:
            if not code_future.done():
                loop.call_soon_threadsafe(code_future.set_result, code)

        server, _ = start_callback_server(config.redirect_uri, state, on_code=notify)
        try:
            await check_cancelled()
            should_open = oauth_flow._should_open_browser()
            if server is None:
                raise OAuthFlowError(
                    "Collie could not start its local sign-in listener. Close any other sign-in attempts and try again."
                )
            if should_open and not webbrowser.open(url):
                raise OAuthFlowError(
                    "Collie could not open your browser. Check your default browser and try again."
                )

            deadline = time.monotonic() + 120
            code: str | None = None
            while code is None and time.monotonic() < deadline:
                await check_cancelled()
                try:
                    code = await asyncio.wait_for(asyncio.shield(code_future), timeout=0.1)
                except TimeoutError:
                    continue
            if not code:
                raise OAuthFlowError(
                    "ChatGPT sign-in timed out. Try again and finish in your browser."
                )

            await check_cancelled()
            exchange = asyncio.create_task(
                oauth_flow._exchange_code_for_token_async(code, verifier, config)()
            )
            while not exchange.done():
                if cancelled.is_set():
                    exchange.cancel()
                    with suppress(asyncio.CancelledError):
                        await exchange
                    raise OAuthLoginCancelledError("Sign-in cancelled.")
                await asyncio.sleep(0.05)
            token = await exchange
            await check_cancelled()
            storage.save(token)
            return token
        finally:
            if server is not None:
                server.shutdown()
                server.server_close()

    return asyncio.run(run())


def _login_with_storage(
    canonical: str,
    config: Any,
    storage: Any,
    *,
    cancelled: Event | None = None,
    on_token: Callable[[Any], None] | None = None,
) -> dict[str, Any]:
    try:
        from oauth_cli_kit import get_token
    except ImportError as e:
        raise OAuthFlowError(
            "The OAuth sign-in helper library is not installed. "
            "Use an API key instead — it works the same way."
        ) from e

    def check_cancelled() -> None:
        if cancelled is not None and cancelled.is_set():
            raise OAuthLoginCancelledError("Sign-in cancelled.")

    check_cancelled()
    token = None
    with suppress(Exception):
        token = get_token(provider=config, storage=storage)
    check_cancelled()
    if not (token and token.access):
        try:
            # One flow for every caller: the cancellable path owns the
            # callback server derived from the provider's redirect_uri.
            token = _login_oauth_cancellable(
                config,
                storage,
                cancelled if cancelled is not None else Event(),
            )
        except (OAuthLoginCancelledError, OAuthFlowError):
            raise
        except Exception as e:
            logger.error("OAuth interactive flow failed: {}", type(e).__name__)
            raise OAuthFlowError("ChatGPT sign-in could not finish. Try signing in again.") from e
    check_cancelled()
    if not (token and token.access):
        raise OAuthFlowError("Sign-in didn't complete. Give it another go?")

    if on_token is not None:
        on_token(token)
    logger.info("OAuth sign-in complete for {}", canonical)
    return {
        "provider": canonical,
        "signed_in": True,
        "account_id": getattr(token, "account_id", None),
    }


def login_provider(provider: str) -> dict[str, Any]:
    """Run the interactive OAuth flow (opens the default browser)."""
    canonical, (config, storage) = _spec(provider)
    return _login_with_storage(canonical, config, storage)


def logout_provider(provider: str) -> dict[str, Any]:
    """Delete stored OAuth tokens for a provider."""
    from collie_core.providers.storage import DpapiTokenStorage

    if _is_legacy_claude(provider):
        canonical, storage = CLAUDE, DpapiTokenStorage(token_filename="claude.json")
    else:
        canonical, (_cfg, storage) = _spec(provider)
    storage.delete()
    return {"provider": canonical, "signed_in": False}


def oauth_status(provider: str) -> dict[str, Any]:
    """Report whether a working token exists (refreshing if needed)."""
    from oauth_cli_kit import get_token

    if _is_legacy_claude(provider):
        return {"provider": CLAUDE, "signed_in": False, "account_id": None}
    canonical, (cfg, storage) = _spec(provider)
    token = None
    with suppress(Exception):
        token = get_token(provider=cfg, storage=storage)
    signed_in = bool(token and token.access)
    return {
        "provider": canonical,
        "signed_in": signed_in,
        "account_id": getattr(token, "account_id", None) if signed_in else None,
    }
