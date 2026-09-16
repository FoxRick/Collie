"""MCP OAuth 2.1 support backed by Collie's encrypted credential store."""

from __future__ import annotations

import asyncio
import contextlib
import threading
import urllib.parse
import webbrowser
from collections.abc import AsyncGenerator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

import httpx

from collie_core.services.credentials import CredentialStore

_SUCCESS = b"""<!doctype html><meta charset="utf-8"><title>Collie</title>
<body style="font-family:system-ui;text-align:center;padding:12vh 2rem">
<div style="font-size:64px">&#128021;</div><h1>You're connected!</h1>
<p>You can close this tab and head back to Collie.</p></body>"""

_STORAGE_LOCKS: dict[str, threading.RLock] = {}
_STORAGE_LOCKS_GUARD = threading.Lock()
_FLOW_LOCKS: dict[str, threading.Lock] = {}
_FLOW_LOCKS_GUARD = threading.Lock()
_ACTIVE_PROVIDERS: dict[str, set[Any]] = {}
_ACTIVE_PROVIDERS_GUARD = threading.Lock()


def _storage_lock(key: str) -> threading.RLock:
    with _STORAGE_LOCKS_GUARD:
        return _STORAGE_LOCKS.setdefault(key, threading.RLock())


def _flow_lock(key: str) -> threading.Lock:
    """Return one refresh/auth coordinator shared by every loop for an account."""
    with _FLOW_LOCKS_GUARD:
        return _FLOW_LOCKS.setdefault(key, threading.Lock())


class CredentialStoreTokenStorage:
    """Adapt the MCP SDK TokenStorage protocol to encrypted per-connection blobs."""

    def __init__(
        self,
        store: CredentialStore,
        connection_id: str,
        *,
        binding: dict[str, str],
        write_allowed: Any | None = None,
    ) -> None:
        self.store = store
        self.key = f"connector:{connection_id}"
        self.binding = binding
        self.write_allowed = write_allowed
        self._lock = _storage_lock(self.key)
        self._active = True
        _data, self._revision = self.store.load_with_revision(self.key)

    @property
    def coordinator_key(self) -> str:
        return f"{self.store.base_dir.resolve()}::{self.key}"

    def _load(self) -> dict[str, Any]:
        if not self._active or self.store.revision(self.key) != self._revision:
            return {}
        with self._lock:
            data = self.store.load(self.key) or {}
        stored_binding = data.get("oauth_binding")
        if stored_binding is not None and stored_binding != self.binding:
            return {}
        return data

    def _save_part(self, key: str, value: Any) -> None:
        with self._lock:
            if not self._active or (self.write_allowed is not None and not self.write_allowed()):
                return
            data = self._load()
            data["oauth_binding"] = self.binding
            data[key] = (
                value.model_dump(mode="json", exclude_none=True)
                if hasattr(value, "model_dump")
                else value
            )
            self.store.save_if_revision(self.key, data, self._revision)

    def _delete_parts(self, *keys: str) -> None:
        with self._lock:
            if not self._active or (self.write_allowed is not None and not self.write_allowed()):
                return
            data = self._load()
            for key in keys:
                data.pop(key, None)
            data["oauth_binding"] = self.binding
            self.store.save_if_revision(self.key, data, self._revision)

    def close(self) -> None:
        """Reject late writes after cancellation/removal closes this provider."""
        with self._lock:
            self._active = False

    def ensure_active(self) -> None:
        if not self._active or self.store.revision(self.key) != self._revision:
            raise RuntimeError("OAuth was cancelled or this connection was removed.")

    def clear_tokens(self) -> None:
        """Clear credentials while retaining the issuer/resource pin for reauthorization."""
        with self._lock:
            if not self._active or self.store.revision(self.key) != self._revision:
                return
            data = self._load()
            data.pop("tokens", None)
            state = data.get("oauth_state")
            if isinstance(state, dict):
                state["expires_at"] = None
            data["oauth_binding"] = self.binding
            self.store.save_if_revision(self.key, data, self._revision)

    async def get_tokens(self) -> Any:
        from mcp.shared.auth import OAuthToken

        data = self._load().get("tokens")
        return OAuthToken.model_validate(data) if isinstance(data, dict) else None

    async def set_tokens(self, tokens: Any) -> None:
        self._save_part("tokens", tokens)

    async def get_client_info(self) -> Any:
        from mcp.shared.auth import OAuthClientInformationFull

        data = self._load().get("client_info")
        return OAuthClientInformationFull.model_validate(data) if isinstance(data, dict) else None

    async def set_client_info(self, client_info: Any) -> None:
        self._save_part("client_info", client_info)


def _is_loopback_url(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1", "localhost"}


class CollieOAuthClientProvider:
    """Factory namespace; the SDK subclass is created lazily in ``build``."""

    @staticmethod
    def build(
        sdk_provider: type[Any],
        *args: Any,
        storage: CredentialStoreTokenStorage,
        configured_scopes: tuple[str, ...],
        configured_issuer: str | None,
        configured_resource: str,
        configured_resource_explicit: bool,
        allow_private_network: bool,
        interactive: bool,
        **kwargs: Any,
    ) -> Any:
        class _Provider(sdk_provider):
            def __init__(self) -> None:
                super().__init__(*args, storage=storage, **kwargs)
                self._collie_storage = storage
                self._collie_flow_lock = _flow_lock(storage.coordinator_key)
                self._collie_scopes = " ".join(configured_scopes) or None
                self._collie_issuer = configured_issuer
                self._collie_resource = configured_resource
                self._collie_resource_explicit = configured_resource_explicit
                self._collie_allow_private = allow_private_network
                self._collie_interactive = interactive

            def _validate_oauth_url(self, url: str, *, label: str) -> None:
                from collie_core.connectors.remote import validate_remote_endpoint

                parsed = urlsplit(url)
                if parsed.scheme != "https" and not _is_loopback_url(url):
                    raise ValueError(f"OAuth {label} must use HTTPS.")
                validate_remote_endpoint(
                    self.context.server_url,
                    url,
                    allow_private_network=self._collie_allow_private,
                )

            async def _initialize(self) -> None:
                from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata

                storage.ensure_active()
                self.context.current_tokens = None
                self.context.client_info = None
                self.context.oauth_metadata = None
                self.context.protected_resource_metadata = None
                self.context.auth_server_url = None
                self.context.token_expiry_time = None
                await super()._initialize()
                state = storage._load().get("oauth_state")
                if not isinstance(state, dict):
                    return
                if isinstance(state.get("oauth_metadata"), dict):
                    self.context.oauth_metadata = OAuthMetadata.model_validate(
                        state["oauth_metadata"]
                    )
                if isinstance(state.get("protected_resource_metadata"), dict):
                    self.context.protected_resource_metadata = (
                        ProtectedResourceMetadata.model_validate(
                            state["protected_resource_metadata"]
                        )
                    )
                self.context.auth_server_url = state.get("auth_server_url")
                expires_at = state.get("expires_at")
                self.context.token_expiry_time = (
                    float(expires_at) if isinstance(expires_at, (int, float)) else None
                )

            def _save_state(self) -> None:
                state = {
                    "expires_at": self.context.token_expiry_time,
                    "auth_server_url": self.context.auth_server_url,
                    "issuer": (
                        str(self.context.oauth_metadata.issuer)
                        if self.context.oauth_metadata
                        else self._collie_issuer
                    ),
                    "resource": self._collie_resource,
                    "oauth_metadata": (
                        self.context.oauth_metadata.model_dump(mode="json", exclude_none=True)
                        if self.context.oauth_metadata
                        else None
                    ),
                    "protected_resource_metadata": (
                        self.context.protected_resource_metadata.model_dump(
                            mode="json", exclude_none=True
                        )
                        if self.context.protected_resource_metadata
                        else None
                    ),
                }
                storage._save_part("oauth_state", state)

            def _validate_discovered_context(self) -> None:
                metadata = self.context.oauth_metadata
                if metadata is None:
                    return
                discovered_issuer = str(metadata.issuer).rstrip("/")
                stored_state = storage._load().get("oauth_state") or {}
                stored_issuer = stored_state.get("issuer")
                if stored_issuer and discovered_issuer != str(stored_issuer).rstrip("/"):
                    raise ValueError("OAuth issuer changed for this connection.")
                if self._collie_issuer and discovered_issuer != self._collie_issuer.rstrip("/"):
                    raise ValueError("OAuth issuer does not match this connection.")
                if (
                    self.context.auth_server_url
                    and discovered_issuer != self.context.auth_server_url.rstrip("/")
                ):
                    raise ValueError("OAuth metadata issuer does not match its authority.")
                for label, endpoint in (
                    ("issuer", discovered_issuer),
                    ("authorization endpoint", str(metadata.authorization_endpoint)),
                    ("token endpoint", str(metadata.token_endpoint)),
                    ("registration endpoint", str(metadata.registration_endpoint or "")),
                ):
                    if endpoint:
                        self._validate_oauth_url(endpoint, label=label)
                self._save_state()

            async def _handle_oauth_metadata_response(self, response: httpx.Response) -> None:
                await super()._handle_oauth_metadata_response(response)
                self._validate_discovered_context()

            async def _validate_resource_match(self, prm: Any) -> None:
                await super()._validate_resource_match(prm)
                if self._collie_resource_explicit and str(prm.resource).rstrip(
                    "/"
                ) != self._collie_resource.rstrip("/"):
                    raise ValueError("OAuth resource does not match this connection.")
                self._collie_resource = str(prm.resource)
                for auth_server in prm.authorization_servers:
                    self._validate_oauth_url(str(auth_server), label="authorization server")
                self._save_state()

            async def _perform_authorization_code_grant(self) -> tuple[str, str]:
                self._validate_discovered_context()
                if self._collie_scopes is not None:
                    self.context.client_metadata.scope = self._collie_scopes
                return await super()._perform_authorization_code_grant()

            async def _refresh_token(self) -> httpx.Request:
                self._validate_discovered_context()
                return await super()._refresh_token()

            def _get_token_endpoint(self) -> str:
                endpoint = super()._get_token_endpoint()
                self._validate_oauth_url(endpoint, label="token endpoint")
                return endpoint

            async def _handle_token_response(self, response: httpx.Response) -> None:
                from mcp.shared.auth import OAuthToken

                if response.status_code != 200:
                    await response.aread()
                    raise RuntimeError(f"OAuth token exchange failed ({response.status_code}).")
                try:
                    tokens = OAuthToken.model_validate_json(await response.aread())
                except Exception:
                    raise RuntimeError(
                        "OAuth provider returned an invalid token response."
                    ) from None
                previous = self.context.current_tokens
                if not tokens.refresh_token and previous and previous.refresh_token:
                    tokens = tokens.model_copy(update={"refresh_token": previous.refresh_token})
                self.context.current_tokens = tokens
                self.context.update_token_expiry(tokens)
                await storage.set_tokens(tokens)
                self._save_state()

            async def _handle_refresh_response(self, response: httpx.Response) -> bool:
                if response.status_code != 200:
                    await response.aread()
                    self.context.clear_tokens()
                    storage.clear_tokens()
                    return False
                try:
                    from mcp.shared.auth import OAuthToken

                    tokens = OAuthToken.model_validate_json(await response.aread())
                except Exception:
                    self.context.clear_tokens()
                    storage.clear_tokens()
                    return False
                previous_refresh = (
                    self.context.current_tokens.refresh_token
                    if self.context.current_tokens
                    else None
                )
                if not tokens.refresh_token and previous_refresh:
                    tokens = tokens.model_copy(update={"refresh_token": previous_refresh})
                self.context.current_tokens = tokens
                self.context.update_token_expiry(tokens)
                await storage.set_tokens(tokens)
                self._save_state()
                return True

            async def async_auth_flow(
                self, request: httpx.Request
            ) -> AsyncGenerator[httpx.Request, httpx.Response]:
                # ``anyio.Lock`` is provider-local and cannot coordinate the probe
                # thread with the runtime loop. Polling a process lock is cancellation-safe.
                while not self._collie_flow_lock.acquire(blocking=False):
                    await asyncio.sleep(0.01)
                flow = None
                worker: asyncio.Task[None] | None = None
                outgoing: asyncio.Queue[httpx.Request | None] = asyncio.Queue()
                incoming: asyncio.Queue[httpx.Response] = asyncio.Queue()
                try:
                    storage.ensure_active()
                    self._initialized = False
                    flow = super().async_auth_flow(request)

                    # The SDK flow holds a task-affine anyio lock for its whole
                    # lifetime (mcp oauth2.py ``async with self.context.lock``).
                    # Drive it on ONE dedicated worker task so that lock is
                    # always acquired AND released by the same task — even when
                    # this consumer advances the generator under
                    # ``asyncio.wait_for`` (which runs ``__anext__`` in a child
                    # task) and then closes it from a different one.
                    async def _drive_flow() -> None:
                        try:
                            next_request = await flow.__anext__()
                            while True:
                                await outgoing.put(next_request)
                                response = await incoming.get()
                                next_request = await flow.asend(response)
                        except StopAsyncIteration:
                            # Flow completed naturally; the SDK released its lock.
                            # Signal the consumer loop to stop.
                            await outgoing.put(None)
                            return
                        finally:
                            # Always close the SDK flow on the worker task so its
                            # task-affine lock is released, whether we completed,
                            # errored, or were cancelled. Shield so a cancellation
                            # cannot strand the lock.
                            with contextlib.suppress(Exception):
                                await asyncio.shield(flow.aclose())

                    worker = asyncio.create_task(_drive_flow())
                    while True:
                        outgoing_request = await outgoing.get()
                        if outgoing_request is None:
                            return
                        storage.ensure_active()
                        self._validate_oauth_url(str(outgoing_request.url), label="request")
                        incoming_response = yield outgoing_request
                        registration_endpoint = (
                            str(self.context.oauth_metadata.registration_endpoint)
                            if self.context.oauth_metadata
                            and self.context.oauth_metadata.registration_endpoint
                            else None
                        )
                        if registration_endpoint == str(outgoing_request.url):
                            if incoming_response.status_code not in {200, 201}:
                                await incoming_response.aread()
                                raise RuntimeError(
                                    f"OAuth client registration failed ({incoming_response.status_code})."
                                )
                            try:
                                from mcp.shared.auth import OAuthClientInformationFull

                                OAuthClientInformationFull.model_validate_json(
                                    await incoming_response.aread()
                                )
                            except Exception:
                                raise RuntimeError(
                                    "OAuth provider returned invalid client registration data."
                                ) from None
                        await incoming.put(incoming_response)
                        self._validate_discovered_context()
                except StopAsyncIteration:
                    return
                finally:
                    if worker is not None and not worker.done():
                        worker.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await worker
                    self._collie_flow_lock.release()

        return _Provider()


class _CallbackHandler(BaseHTTPRequestHandler):
    result: dict[str, str]
    done: threading.Event
    expected_path: str

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != type(self).expected_path:
            self.send_error(404)
            return
        type(self).result = {
            key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items()
        }
        body = _SUCCESS
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        type(self).done.set()

    def log_message(self, *args: Any) -> None:
        return


class LoopbackOAuthReceiver:
    """Single-use loopback receiver with an OS-assigned random port."""

    def __init__(
        self,
        server_url: str,
        *,
        allow_private_network: bool = False,
        redirect_uri: str | None = None,
    ) -> None:
        self.server_url = server_url
        self.allow_private_network = allow_private_network
        parsed_redirect = urlsplit(redirect_uri) if redirect_uri else None
        if redirect_uri and not _is_loopback_url(redirect_uri):
            raise ValueError("Interactive fixed OAuth callbacks must use a loopback URL.")
        if parsed_redirect and parsed_redirect.hostname == "::1":
            raise ValueError("IPv6 OAuth callbacks are not supported; use 127.0.0.1.")
        if parsed_redirect and parsed_redirect.port is None:
            raise ValueError("A fixed loopback OAuth callback must include its registered port.")
        callback_path = parsed_redirect.path or "/callback" if parsed_redirect else "/callback"
        handler = type(
            "_ConnectorCallback",
            (_CallbackHandler,),
            {"result": {}, "done": threading.Event(), "expected_path": callback_path},
        )
        self.handler = handler
        host = parsed_redirect.hostname if parsed_redirect else "127.0.0.1"
        port = parsed_redirect.port if parsed_redirect else 0
        self.server = ThreadingHTTPServer((host, port), handler)
        self.redirect_uri = redirect_uri or (
            f"http://127.0.0.1:{self.server.server_port}{callback_path}"
        )
        self._started = False

    async def redirect(self, authorization_url: str) -> None:
        from collie_core.connectors.remote import validate_remote_endpoint

        validate_remote_endpoint(
            self.server_url,
            authorization_url,
            allow_private_network=self.allow_private_network,
        )
        if not self._started:
            self._started = True
            threading.Thread(target=self.server.serve_forever, daemon=True).start()
        webbrowser.open(authorization_url)

    async def callback(self) -> tuple[str, str | None]:
        try:
            completed = await asyncio.to_thread(self.handler.done.wait, 300)
            if not completed:
                raise TimeoutError("Provider sign-in did not finish in time.")
            result = self.handler.result
            if result.get("error"):
                if result["error"] == "cancelled":
                    raise RuntimeError("Provider sign-in was cancelled.")
                raise RuntimeError("Provider declined sign-in.")
            code = result.get("code")
            if not code:
                raise RuntimeError("Provider returned no authorization code.")
            return code, result.get("state")
        finally:
            self.close()

    def close(self) -> None:
        if not self.handler.done.is_set():
            self.handler.result = {"error": "cancelled"}
            self.handler.done.set()
        if self._started:
            self.server.shutdown()
        self.server.server_close()


def build_oauth_provider(
    connection_id: str,
    server_url: str,
    store: CredentialStore,
    *,
    scopes: tuple[str, ...] = (),
    interactive: bool,
    allow_private_network: bool = False,
    registration: str = "automatic",
    client_id: str | None = None,
    redirect_uri: str | None = None,
    issuer: str | None = None,
    resource: str | None = None,
    client_metadata_url: str | None = None,
    write_allowed: Any | None = None,
) -> Any:
    """Build the Python MCP SDK OAuth provider.

    Interactive providers use a random loopback callback. Runtime providers
    reuse encrypted tokens and may refresh them, but never surprise-open a
    browser during an agent turn.
    """
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata

    if registration not in {"automatic", "preregistered"}:
        raise ValueError("Unsupported OAuth client registration strategy.")
    if not server_url.startswith("https://") and not _is_loopback_url(server_url):
        raise ValueError("Authenticated MCP endpoints must use HTTPS.")
    intended_resource = resource or server_url
    if not intended_resource.startswith("https://") and not _is_loopback_url(intended_resource):
        raise ValueError("OAuth resources must use HTTPS.")
    if issuer and not issuer.startswith("https://") and not _is_loopback_url(issuer):
        raise ValueError("OAuth issuers must use HTTPS.")

    binding = {
        "server_url": server_url,
        "issuer": issuer or "",
        "resource": resource or server_url,
        "registration": registration,
        "client_id": client_id or client_metadata_url or "",
    }
    storage = CredentialStoreTokenStorage(
        store, connection_id, binding=binding, write_allowed=write_allowed
    )
    if interactive:
        receiver = LoopbackOAuthReceiver(
            server_url,
            allow_private_network=allow_private_network,
            redirect_uri=redirect_uri,
        )
        redirect_uris = [receiver.redirect_uri]
        redirect_handler = receiver.redirect
        callback_handler = receiver.callback
    else:
        stored = storage._load().get("client_info") or {}
        redirect_uris = stored.get("redirect_uris") or [redirect_uri or "http://127.0.0.1/callback"]
        redirect_handler = None
        callback_handler = None
    metadata = OAuthClientMetadata(
        client_name="Collie",
        redirect_uris=redirect_uris,
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        scope=" ".join(scopes) or None,
    )
    if registration == "preregistered":
        if not client_id:
            raise ValueError("A pre-registered OAuth client id is required.")
        current = storage._load().get("client_info") or {}
        if current.get("client_id") != client_id or current.get("redirect_uris") != redirect_uris:
            info = OAuthClientInformationFull(
                client_id=client_id,
                redirect_uris=redirect_uris,
                token_endpoint_auth_method="none",
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                scope=" ".join(scopes) or None,
                client_name="Collie",
            )
            storage._save_part("client_info", info)
    elif interactive:
        current = storage._load().get("client_info") or {}
        if current and current.get("redirect_uris") != redirect_uris:
            storage._delete_parts("client_info", "tokens", "oauth_state")
    provider = CollieOAuthClientProvider.build(
        OAuthClientProvider,
        server_url,
        metadata,
        storage=storage,
        configured_scopes=scopes,
        configured_issuer=issuer,
        configured_resource=intended_resource,
        configured_resource_explicit=resource is not None,
        allow_private_network=allow_private_network,
        interactive=interactive,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
        timeout=300,
        client_metadata_url=client_metadata_url,
    )
    provider._collie_receiver = receiver if interactive else None
    provider._collie_connection_id = connection_id
    with _ACTIVE_PROVIDERS_GUARD:
        _ACTIVE_PROVIDERS.setdefault(connection_id, set()).add(provider)
    return provider


def close_oauth_provider(provider: Any) -> None:
    storage = getattr(provider, "_collie_storage", None)
    if storage is not None:
        storage.close()
    receiver = getattr(provider, "_collie_receiver", None)
    if receiver is not None:
        receiver.close()
    connection_id = getattr(provider, "_collie_connection_id", None)
    if connection_id is not None:
        with _ACTIVE_PROVIDERS_GUARD:
            providers = _ACTIVE_PROVIDERS.get(connection_id)
            if providers is not None:
                providers.discard(provider)
                if not providers:
                    _ACTIVE_PROVIDERS.pop(connection_id, None)


def cancel_oauth_connection(connection_id: str) -> None:
    """Cancel active callbacks and reject late credential writes for an account."""
    with _ACTIVE_PROVIDERS_GUARD:
        providers = tuple(_ACTIVE_PROVIDERS.get(connection_id, ()))
    for provider in providers:
        close_oauth_provider(provider)
