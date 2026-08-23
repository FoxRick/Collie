"""Concurrency and caching tests for the Claude OAuth provider."""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from collie_core.providers import claude_oauth


def _token(access: str, expires_at: float) -> claude_oauth._AccessToken:
    return claude_oauth._AccessToken(access=access, expires_at=expires_at)


async def test_valid_cached_token_skips_storage_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def load_token() -> claude_oauth._AccessToken:
        nonlocal calls
        calls += 1
        return _token("cached", time.time() + 3600)

    async def fake_chat(*_args, **_kwargs):
        return "ok"

    monkeypatch.setattr(claude_oauth, "_current_access_token", load_token)
    monkeypatch.setattr(claude_oauth.AnthropicProvider, "chat", fake_chat)
    provider = claude_oauth.ClaudeOAuthProvider()

    assert calls == 0
    assert await provider.chat(messages=[]) == "ok"
    assert await provider.refresh_auth()
    assert calls == 1


async def test_concurrent_near_expiry_refreshes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = time.time()
    tokens = iter([_token("old", now + 30), _token("new", now + 3600)])
    calls = 0

    def load_token() -> claude_oauth._AccessToken:
        nonlocal calls
        calls += 1
        return next(tokens)

    monkeypatch.setattr(claude_oauth, "_current_access_token", load_token)
    provider = claude_oauth.ClaudeOAuthProvider()
    assert await provider.refresh_auth()

    assert await asyncio.gather(provider.refresh_auth(), provider.refresh_auth()) == [True, True]
    assert calls == 2
    assert provider._access_token == "new"


async def test_blocking_token_loader_does_not_block_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = time.time()
    calls = 0

    def load_token() -> claude_oauth._AccessToken:
        nonlocal calls
        calls += 1
        if calls > 1:
            time.sleep(0.2)
        return _token("cached", now + (30 if calls == 1 else 3600))

    monkeypatch.setattr(claude_oauth, "_current_access_token", load_token)
    provider = claude_oauth.ClaudeOAuthProvider()
    assert await provider.refresh_auth()

    started = asyncio.get_running_loop().time()
    refresh = asyncio.create_task(provider.refresh_auth())
    await asyncio.sleep(0.01)
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 0.1
    assert not refresh.done()
    assert await refresh


async def test_client_rebuilds_only_when_access_token_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = time.time()
    tokens = iter(
        [
            _token("same", now + 30),
            _token("same", now + 3600),
            _token("changed", now + 3600),
        ]
    )
    monkeypatch.setattr(claude_oauth, "_current_access_token", lambda: next(tokens))
    provider = claude_oauth.ClaudeOAuthProvider()
    assert await provider.refresh_auth()
    initial_client = provider._client

    assert await provider.refresh_auth()
    assert provider._client is initial_client

    provider._access_token_expires_at = 0
    assert await provider.refresh_auth()
    assert provider._client is not initial_client
    assert provider._client.auth_token == "changed"


@pytest.mark.parametrize("failure", ["missing", "error"])
async def test_refresh_failure_keeps_cached_client(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    now = time.time()
    calls = 0

    def load_token() -> claude_oauth._AccessToken | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _token("cached", now + 30)
        if failure == "error":
            raise RuntimeError("storage unavailable")
        return None

    monkeypatch.setattr(claude_oauth, "_current_access_token", load_token)
    provider = claude_oauth.ClaudeOAuthProvider()
    assert await provider.refresh_auth()
    initial_client = provider._client

    assert not await provider.refresh_auth()
    assert provider._access_token == "cached"
    assert provider._client is initial_client


async def test_cancelled_waiter_keeps_single_refresh_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def load_token() -> claude_oauth._AccessToken:
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(2)
        return _token("shared", time.time() + 3600)

    monkeypatch.setattr(claude_oauth, "_current_access_token", load_token)
    provider = claude_oauth.ClaudeOAuthProvider()
    first = asyncio.create_task(provider.refresh_auth())
    while not started.is_set():
        await asyncio.sleep(0)

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    second = asyncio.create_task(provider.refresh_auth())
    await asyncio.sleep(0.01)
    assert calls == 1
    release.set()

    assert await second
    assert calls == 1
    assert provider._access_token == "shared"


def test_oauth_expiry_milliseconds_and_skew_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    import oauth_cli_kit

    expires_ms = 2_000_000_000_000
    monkeypatch.setattr(
        oauth_cli_kit,
        "get_token",
        lambda **_kwargs: SimpleNamespace(access="tok", expires=expires_ms),
    )
    monkeypatch.setattr(claude_oauth, "claude_storage", lambda: object())

    token = claude_oauth._current_access_token()

    assert token == _token("tok", 2_000_000_000)
    provider = claude_oauth.ClaudeOAuthProvider()
    provider._access_token = token.access
    provider._access_token_expires_at = token.expires_at
    provider._client = object()
    assert provider._token_is_fresh(token.expires_at - 60.001)
    assert not provider._token_is_fresh(token.expires_at - 60)
