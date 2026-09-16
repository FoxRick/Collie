from __future__ import annotations

import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from collie_core.connectors.auth import (
    CredentialStoreTokenStorage,
    LoopbackOAuthReceiver,
    build_oauth_provider,
    close_oauth_provider,
)
from collie_core.services.credentials import CredentialStore


def _store(tmp_path):
    return CredentialStore(
        tmp_path / "credentials", protect=lambda value: value, unprotect=lambda value: value
    )


def _response(status: int, request: httpx.Request, **kwargs) -> httpx.Response:
    return httpx.Response(status, request=request, **kwargs)


@pytest.mark.asyncio
async def test_dynamic_registration_pkce_and_configured_scopes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "collie_core.connectors.remote.validate_remote_endpoint", lambda *_a, **_k: ("8.8.8.8",)
    )
    authorization_urls: list[str] = []

    async def redirect(url: str) -> None:
        authorization_urls.append(url)

    async def callback() -> tuple[str, str]:
        state = parse_qs(urlsplit(authorization_urls[-1]).query)["state"][0]
        return "auth-code", state

    provider = build_oauth_provider(
        "account-a",
        "https://resource.example/mcp",
        _store(tmp_path),
        scopes=("mail.read", "mail.send"),
        interactive=True,
        resource="https://resource.example/mcp",
    )
    provider.context.redirect_handler = redirect
    provider.context.callback_handler = callback
    request = httpx.Request("POST", "https://resource.example/mcp")
    flow = provider.async_auth_flow(request)

    outgoing = await flow.__anext__()
    assert outgoing is request
    outgoing = await flow.asend(
        _response(
            401,
            request,
            headers={
                "WWW-Authenticate": (
                    'Bearer resource_metadata="https://resource.example/.well-known/'
                    'oauth-protected-resource", scope="server.suggested"'
                )
            },
        )
    )
    assert "oauth-protected-resource" in str(outgoing.url)
    outgoing = await flow.asend(
        _response(
            200,
            outgoing,
            json={
                "resource": "https://resource.example/mcp",
                "authorization_servers": ["https://login.example"],
                "scopes_supported": ["server.suggested"],
            },
        )
    )
    assert ".well-known" in str(outgoing.url)
    outgoing = await flow.asend(
        _response(
            200,
            outgoing,
            json={
                "issuer": "https://login.example",
                "authorization_endpoint": "https://login.example/authorize",
                "token_endpoint": "https://login.example/token",
                "registration_endpoint": "https://login.example/register",
                "code_challenge_methods_supported": ["S256"],
            },
        )
    )
    assert str(outgoing.url) == "https://login.example/register"
    outgoing = await flow.asend(
        _response(
            201,
            outgoing,
            json={
                "client_id": "dynamic-client",
                "redirect_uris": [str(provider.context.client_metadata.redirect_uris[0])],
                "token_endpoint_auth_method": "none",
            },
        )
    )
    assert str(outgoing.url) == "https://login.example/token"
    auth_query = parse_qs(urlsplit(authorization_urls[-1]).query)
    assert auth_query["scope"] == ["mail.read mail.send"]
    assert auth_query["code_challenge_method"] == ["S256"]
    token_form = parse_qs(outgoing.content.decode())
    assert token_form["code_verifier"]
    assert token_form["resource"] == ["https://resource.example/mcp"]
    outgoing = await flow.asend(
        _response(
            200,
            outgoing,
            json={
                "access_token": "access-one",
                "refresh_token": "refresh-one",
                "token_type": "Bearer",
                "expires_in": 60,
            },
        )
    )
    assert outgoing.headers["Authorization"] == "Bearer access-one"
    with pytest.raises(StopAsyncIteration):
        await flow.asend(_response(200, outgoing))
    close_oauth_provider(provider)


@pytest.mark.asyncio
async def test_restart_restores_endpoint_expiry_and_keeps_rotating_refresh_token(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "collie_core.connectors.remote.validate_remote_endpoint", lambda *_a, **_k: ("8.8.8.8",)
    )
    store = _store(tmp_path)
    provider = build_oauth_provider(
        "account-b",
        "https://resource.example/mcp",
        store,
        interactive=False,
        registration="preregistered",
        client_id="desktop-client",
    )
    storage = provider._collie_storage
    from mcp.shared.auth import OAuthToken

    await storage.set_tokens(
        OAuthToken(
            access_token="expired", refresh_token="keep-me", token_type="Bearer", expires_in=1
        )
    )
    storage._save_part(
        "oauth_state",
        {
            "expires_at": time.time() - 30,
            "auth_server_url": "https://issuer.example",
            "resource": "https://resource.example/mcp",
            "oauth_metadata": {
                "issuer": "https://issuer.example",
                "authorization_endpoint": "https://issuer.example/authorize",
                "token_endpoint": "https://issuer.example/oauth/token",
            },
        },
    )
    close_oauth_provider(provider)

    restarted = build_oauth_provider(
        "account-b",
        "https://resource.example/mcp",
        store,
        interactive=False,
        registration="preregistered",
        client_id="desktop-client",
    )
    request = httpx.Request("GET", "https://resource.example/mcp")
    flow = restarted.async_auth_flow(request)
    refresh = await flow.__anext__()
    assert str(refresh.url) == "https://issuer.example/oauth/token"
    assert parse_qs(refresh.content.decode())["refresh_token"] == ["keep-me"]
    outgoing = await flow.asend(
        _response(
            200,
            refresh,
            json={"access_token": "fresh", "token_type": "Bearer", "expires_in": 120},
        )
    )
    assert outgoing.headers["Authorization"] == "Bearer fresh"
    with pytest.raises(StopAsyncIteration):
        await flow.asend(_response(200, outgoing))
    persisted = store.load("connector:account-b")
    assert persisted["tokens"]["refresh_token"] == "keep-me"
    assert persisted["oauth_state"]["expires_at"] > time.time()
    close_oauth_provider(restarted)


@pytest.mark.asyncio
async def test_removed_account_rejects_late_token_write(tmp_path) -> None:
    store = _store(tmp_path)
    storage = CredentialStoreTokenStorage(
        store,
        "removed",
        binding={"server_url": "https://example.test"},
    )
    from mcp.shared.auth import OAuthToken

    store.delete("connector:removed")
    await storage.set_tokens(OAuthToken(access_token="too-late", token_type="Bearer"))
    assert store.load("connector:removed") is None


def test_authenticated_endpoints_require_https_and_client_metadata_url_is_supported(
    tmp_path,
) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        build_oauth_provider(
            "unsafe", "http://public.example/mcp", _store(tmp_path), interactive=False
        )
    provider = build_oauth_provider(
        "metadata-client",
        "https://resource.example/mcp",
        _store(tmp_path),
        interactive=False,
        client_metadata_url="https://collie.example/oauth/client.json",
    )
    assert provider.context.client_metadata_url == "https://collie.example/oauth/client.json"
    close_oauth_provider(provider)


def test_persisted_oauth_blob_contains_no_response_body(tmp_path) -> None:
    store = _store(tmp_path)
    storage = CredentialStoreTokenStorage(
        store,
        "sanitized",
        binding={"server_url": "https://example.test"},
    )
    storage._save_part("oauth_state", {"expires_at": 1.0})
    assert "response" not in json.dumps(store.load("connector:sanitized"))


@pytest.mark.asyncio
async def test_denied_consent_is_reported_without_provider_details() -> None:
    receiver = LoopbackOAuthReceiver("https://resource.example/mcp")
    receiver.handler.result = {
        "error": "access_denied",
        "error_description": "sensitive provider diagnostics",
    }
    receiver.handler.done.set()
    with pytest.raises(RuntimeError, match="declined") as failure:
        await receiver.callback()
    assert "sensitive provider diagnostics" not in str(failure.value)


@pytest.mark.asyncio
async def test_cancelled_flow_releases_account_refresh_coordinator(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "collie_core.connectors.remote.validate_remote_endpoint", lambda *_a, **_k: ("8.8.8.8",)
    )
    store = _store(tmp_path)
    first = build_oauth_provider("shared", "https://resource.example/mcp", store, interactive=False)
    first_flow = first.async_auth_flow(httpx.Request("GET", "https://resource.example/mcp"))
    await first_flow.__anext__()
    await first_flow.aclose()

    second = build_oauth_provider(
        "shared", "https://resource.example/mcp", store, interactive=False
    )
    second_flow = second.async_auth_flow(httpx.Request("GET", "https://resource.example/mcp"))
    outgoing = await __import__("asyncio").wait_for(second_flow.__anext__(), timeout=1)
    assert str(outgoing.url) == "https://resource.example/mcp"
    await second_flow.aclose()
    close_oauth_provider(first)
    close_oauth_provider(second)


@pytest.mark.asyncio
async def test_closed_provider_before_flow_does_not_leak_account_coordinator(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "collie_core.connectors.remote.validate_remote_endpoint", lambda *_a, **_k: ("8.8.8.8",)
    )
    store = _store(tmp_path)
    closed = build_oauth_provider(
        "closed-first", "https://resource.example/mcp", store, interactive=False
    )
    close_oauth_provider(closed)
    with pytest.raises(RuntimeError, match="cancelled|removed"):
        await closed.async_auth_flow(
            httpx.Request("GET", "https://resource.example/mcp")
        ).__anext__()

    fresh = build_oauth_provider(
        "closed-first", "https://resource.example/mcp", store, interactive=False
    )
    flow = fresh.async_auth_flow(httpx.Request("GET", "https://resource.example/mcp"))
    outgoing = await __import__("asyncio").wait_for(flow.__anext__(), timeout=1)
    assert str(outgoing.url) == "https://resource.example/mcp"
    await flow.aclose()
    close_oauth_provider(fresh)


@pytest.mark.asyncio
async def test_failed_refresh_preserves_issuer_pin_for_reauthorization(tmp_path) -> None:
    store = _store(tmp_path)
    provider = build_oauth_provider(
        "issuer-pin",
        "https://resource.example/mcp",
        store,
        interactive=False,
        registration="preregistered",
        client_id="desktop-client",
    )
    storage = provider._collie_storage
    from mcp.shared.auth import OAuthMetadata, OAuthToken

    await storage.set_tokens(
        OAuthToken(access_token="expired", refresh_token="bad", token_type="Bearer")
    )
    storage._save_part(
        "oauth_state",
        {
            "expires_at": 0,
            "issuer": "https://issuer.example",
            "auth_server_url": "https://issuer.example",
            "resource": "https://resource.example/mcp",
            "oauth_metadata": {
                "issuer": "https://issuer.example",
                "authorization_endpoint": "https://issuer.example/authorize",
                "token_endpoint": "https://issuer.example/token",
            },
        },
    )
    await provider._initialize()
    assert not await provider._handle_refresh_response(
        _response(400, httpx.Request("POST", "https://issuer.example/token"), text="secret")
    )
    persisted = store.load("connector:issuer-pin")
    assert "tokens" not in persisted
    assert persisted["oauth_state"]["issuer"] == "https://issuer.example"

    provider.context.oauth_metadata = OAuthMetadata.model_validate(
        {
            "issuer": "https://evil.example",
            "authorization_endpoint": "https://evil.example/authorize",
            "token_endpoint": "https://evil.example/token",
        }
    )
    provider.context.auth_server_url = "https://evil.example"
    with pytest.raises(ValueError, match="issuer changed"):
        provider._validate_discovered_context()
    close_oauth_provider(provider)


@pytest.mark.asyncio
async def test_malformed_registration_body_is_not_logged(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setattr(
        "collie_core.connectors.remote.validate_remote_endpoint", lambda *_a, **_k: ("8.8.8.8",)
    )
    provider = build_oauth_provider(
        "bad-registration",
        "https://resource.example/mcp",
        _store(tmp_path),
        interactive=False,
    )
    request = httpx.Request("GET", "https://resource.example/mcp")
    flow = provider.async_auth_flow(request)
    try:
        outgoing = await flow.__anext__()
        outgoing = await flow.asend(
            _response(
                401,
                outgoing,
                headers={
                    "WWW-Authenticate": (
                        'Bearer resource_metadata="https://resource.example/.well-known/'
                        'oauth-protected-resource"'
                    )
                },
            )
        )
        outgoing = await flow.asend(
            _response(
                200,
                outgoing,
                json={
                    "resource": "https://resource.example/mcp",
                    "authorization_servers": ["https://login.example"],
                },
            )
        )
        outgoing = await flow.asend(
            _response(
                200,
                outgoing,
                json={
                    "issuer": "https://login.example",
                    "authorization_endpoint": "https://login.example/authorize",
                    "token_endpoint": "https://login.example/token",
                    "registration_endpoint": "https://login.example/register",
                },
            )
        )
        with pytest.raises(RuntimeError, match="invalid client registration"):
            await flow.asend(
                _response(
                    201,
                    outgoing,
                    json={"client_secret": "must-never-appear", "unexpected": True},
                )
            )
    finally:
        await flow.aclose()
        close_oauth_provider(provider)
    assert "must-never-appear" not in caplog.text
