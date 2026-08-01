"""HTTP route adapter for WebUI Settings APIs.

Keep WebUI Settings route handlers here, not in ``channels/websocket.py``.
The websocket channel owns transport concerns; this module owns WebUI Settings
request mapping and response shaping.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from nanobot.agent.tools.mcp import request_mcp_reload
from nanobot.api.runtime import ApiRuntime, ApiStartOptions, api_runtime_paths
from nanobot.bus.queue import MessageBus
from nanobot.config.loader import get_config_path, load_config
from nanobot.pairing import approve_code, deny_code, list_pending
from nanobot.webui.http_utils import query_first as _query_first
from nanobot.webui.mcp_presets_api import mcp_presets_settings_action
from nanobot.webui.settings_api import (
    WebUISettingsError,
    create_model_configuration,
    decorate_settings_payload,
    login_oauth_provider,
    logout_oauth_provider,
    provider_models_payload,
    settings_payload,
    settings_usage_payload,
    update_agent_settings,
    update_api_settings,
    update_image_generation_settings,
    update_model_configuration,
    update_network_safety_settings,
    update_provider_settings,
    update_transcription_settings,
    update_web_search_settings,
)
from nanobot.webui.version_check import check_for_update

QueryParams = dict[str, list[str]]

_MCP_VALUES_HEADER = "X-Nanobot-MCP-Values"
_MCP_VALUES_HEADER_MAX_BYTES = 64 * 1024
_API_SERVICE_VALUES_HEADER = "X-Nanobot-API-Service-Values"
_API_SERVICE_VALUES_HEADER_MAX_BYTES = 8 * 1024

_MCP_PRESET_ACTIONS_BY_PATH = {
    "/api/settings/mcp-presets/enable": "enable",
    "/api/settings/mcp-presets/remove": "remove",
    "/api/settings/mcp-presets/test": "test",
    "/api/settings/mcp-presets/custom": "custom",
    "/api/settings/mcp-presets/import": "import",
    "/api/settings/mcp-presets/import-cursor": "import-cursor",
    "/api/settings/mcp-presets/tools": "tools",
}


class WebUISettingsRouter:
    """Route WebUI Settings HTTP requests behind a transport-neutral boundary."""

    def __init__(
        self,
        *,
        bus: MessageBus,
        logger: Any,
        check_api_token: Callable[[WsRequest], bool],
        parse_query: Callable[[str], QueryParams],
        json_response: Callable[[dict[str, Any]], Response],
        error_response: Callable[[int, str | None], Response],
        runtime_surface: str,
        runtime_capabilities: dict[str, Any],
    ) -> None:
        self.bus = bus
        self.logger = logger
        self._check_api_token = check_api_token
        self._parse_query = parse_query
        self._json_response = json_response
        self._error_response = error_response
        self._runtime_surface = runtime_surface
        self._runtime_capabilities = runtime_capabilities
        self._restart_sections: set[str] = set()

    async def dispatch(self, connection: Any, request: WsRequest, path: str) -> Response | None:
        if path == "/api/settings":
            return self._handle_settings(request)
        if path == "/api/settings/usage":
            return self._handle_settings_usage(request)
        if path == "/api/settings/update":
            return self._handle_settings_update(request)
        if path == "/api/settings/model-configurations/create":
            return self._handle_settings_model_configuration_create(request)
        if path == "/api/settings/model-configurations/update":
            return self._handle_settings_model_configuration_update(request)
        if path == "/api/settings/provider/update":
            return self._handle_settings_provider_update(request)
        if path == "/api/settings/provider-models":
            return await self._handle_settings_provider_models(request)
        if path == "/api/settings/provider/oauth-login":
            return await self._handle_settings_provider_oauth(request, "login")
        if path == "/api/settings/provider/oauth-logout":
            return await self._handle_settings_provider_oauth(request, "logout")
        if path == "/api/settings/web-search/update":
            return self._handle_settings_web_search_update(request)
        if path == "/api/settings/api-service":
            return self._handle_settings_api_service(request)
        if path == "/api/settings/api-service/start":
            return await self._handle_settings_api_service_start(connection, request)
        if path == "/api/settings/api-service/stop":
            return await self._handle_settings_api_service_stop(request)
        if path == "/api/settings/image-generation/update":
            return self._handle_settings_image_generation_update(request)
        if path == "/api/settings/transcription/update":
            return self._handle_settings_transcription_update(request)
        if path == "/api/settings/network-safety/update":
            return self._handle_settings_network_safety_update(request)
        if path == "/api/settings/pairing":
            return self._handle_settings_pairing(request)
        if path == "/api/settings/pairing/approve":
            return self._handle_settings_pairing_action(request, "approve")
        if path == "/api/settings/pairing/deny":
            return self._handle_settings_pairing_action(request, "deny")
        if path == "/api/settings/mcp-presets":
            return await self._handle_settings_mcp_presets(request)
        if path == "/api/settings/version-check":
            return await self._handle_settings_version_check(request)
        mcp_action = _MCP_PRESET_ACTIONS_BY_PATH.get(path)
        if mcp_action is not None:
            return await self._handle_settings_mcp_presets(request, mcp_action)
        return None

    def _query(self, request: WsRequest) -> QueryParams:
        return self._parse_query(request.path)

    def _authorized(self, request: WsRequest) -> bool:
        return self._check_api_token(request)

    def _unauthorized(self) -> Response:
        return self._error_response(401, "Unauthorized")

    def _with_restart_state(
        self,
        payload: dict[str, Any],
        *,
        section: str | None = None,
    ) -> dict[str, Any]:
        """Keep restart-required state alive for this gateway process."""
        if section and payload.get("requires_restart"):
            self._restart_sections.add(section)
        sections = sorted(self._restart_sections)
        payload = dict(payload)
        if sections:
            payload["requires_restart"] = True
        return decorate_settings_payload(
            payload,
            surface=self._runtime_surface,
            runtime_capability_overrides=self._runtime_capabilities,
            restart_required_sections=sections,
        )

    def _parse_mcp_settings_query(self, request: WsRequest) -> QueryParams:
        query = self._query(request)
        raw = request.headers.get(_MCP_VALUES_HEADER)
        if not raw:
            return query
        if len(raw.encode("utf-8")) > _MCP_VALUES_HEADER_MAX_BYTES:
            raise WebUISettingsError("MCP settings payload is too large")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WebUISettingsError("invalid MCP settings payload") from exc
        if not isinstance(payload, dict):
            raise WebUISettingsError("MCP settings payload must be a JSON object")
        merged = {key: list(values) for key, values in query.items()}
        for key, value in payload.items():
            if not isinstance(key, str) or not key:
                raise WebUISettingsError("MCP settings payload contains an invalid key")
            if value is None:
                continue
            if isinstance(value, str):
                text = value.strip()
            else:
                text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            if text:
                merged[key] = [text]
        return merged

    def _handle_settings(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        return self._json_response(
            self._with_restart_state(
                settings_payload(
                    surface=self._runtime_surface,
                    runtime_capability_overrides=self._runtime_capabilities,
                )
            )
        )

    def _handle_settings_usage(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        return self._json_response(settings_usage_payload())

    def _handle_settings_pairing(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        return self._json_response(_pairing_payload())

    def _handle_settings_pairing_action(self, request: WsRequest, action: str) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        query = self._query(request)
        code = (_query_first(query, "code") or "").strip()
        if not code:
            return self._error_response(400, "Missing pairing code")

        if action == "approve":
            result = approve_code(code)
            if result is None:
                return self._error_response(404, "Pairing code not found or expired")
            channel, sender_id = result
            return self._json_response(
                _pairing_payload({
                    "ok": True,
                    "action": "approve",
                    "message": f"Approved {sender_id} for {channel}",
                    "channel": channel,
                    "sender_id": sender_id,
                    "code": code,
                })
            )

        if not deny_code(code):
            return self._error_response(404, "Pairing code not found or expired")
        return self._json_response(
            _pairing_payload({
                "ok": True,
                "action": "deny",
                "message": f"Denied pairing code {code}",
                "code": code,
            })
        )

    def _handle_settings_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_agent_settings(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload, section="runtime"))

    def _handle_settings_model_configuration_create(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = create_model_configuration(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload))

    def _handle_settings_model_configuration_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_model_configuration(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload))

    def _handle_settings_provider_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_provider_settings(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload, section="image"))

    async def _handle_settings_provider_models(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = await asyncio.to_thread(provider_models_payload, self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        except Exception:
            self.logger.exception("failed to load provider model list")
            return self._error_response(500, "failed to load provider model list")
        return self._json_response(payload)

    async def _handle_settings_provider_oauth(
        self,
        request: WsRequest,
        action: str,
    ) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        query = self._query(request)
        try:
            if action == "login":
                payload = await asyncio.to_thread(login_oauth_provider, query)
            else:
                payload = await asyncio.to_thread(logout_oauth_provider, query)
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload))

    def _handle_settings_web_search_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_web_search_settings(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload, section="browser"))

    def _handle_settings_api_service(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        return self._json_response(self._api_service_payload())

    async def _handle_settings_api_service_start(
        self,
        connection: Any,
        request: WsRequest,
    ) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            update_api_settings(self._parse_api_service_settings_query(request))
            config = load_config()
            runtime = self._api_runtime()
            options = ApiStartOptions(
                host=config.api.host,
                port=config.api.port,
                workspace=str(config.workspace_path),
                config_path=str(get_config_path().expanduser().resolve(strict=False)),
            )
            current = runtime.status()
            result = await asyncio.to_thread(
                runtime.restart if current.running else runtime.start_background,
                options,
            )
            if not result.ok:
                return self._error_response(500, self._api_runtime_message(result.message))
        except WebUISettingsError as e:
            return self._error_response(getattr(e, "status", 400), getattr(e, "message", str(e)))
        except Exception as e:
            self.logger.exception("failed to start managed API service")
            return self._error_response(500, str(e))
        return self._json_response(self._api_service_payload(last_action="started"))

    def _parse_api_service_settings_query(self, request: WsRequest) -> QueryParams:
        query = self._query(request)
        if "api_key" in query or "apiKey" in query:
            raise WebUISettingsError("API service API key must be provided in the private header")
        raw = request.headers.get(_API_SERVICE_VALUES_HEADER)
        if not raw:
            return query
        if len(raw.encode("utf-8")) > _API_SERVICE_VALUES_HEADER_MAX_BYTES:
            raise WebUISettingsError("API service settings payload is too large")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WebUISettingsError("invalid API service settings payload") from exc
        if not isinstance(payload, dict):
            raise WebUISettingsError("API service settings payload must be a JSON object")

        unknown = set(payload) - {"api_key"}
        if unknown:
            raise WebUISettingsError("API service settings payload contains an invalid key")
        api_key = payload.get("api_key")
        if api_key is not None and not isinstance(api_key, str):
            raise WebUISettingsError("API service API key must be a string")

        merged = {key: list(values) for key, values in query.items() if key != "api_key"}
        if api_key is not None:
            merged["api_key"] = [api_key]
        return merged

    async def _handle_settings_api_service_stop(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            result = await asyncio.to_thread(self._api_runtime().stop)
        except Exception as e:
            self.logger.exception("failed to stop managed API service")
            return self._error_response(500, str(e))
        if not result.ok and result.message != "api_not_running":
            return self._error_response(500, self._api_runtime_message(result.message))
        return self._json_response(self._api_service_payload(last_action="stopped"))

    @staticmethod
    def _api_runtime() -> ApiRuntime:
        config_path = get_config_path().expanduser().resolve(strict=False)
        return ApiRuntime(paths=api_runtime_paths(config_path))

    def _api_service_payload(self, *, last_action: str | None = None) -> dict[str, Any]:
        config = load_config()
        status = self._api_runtime().status()
        try:
            import aiohttp  # noqa: F401
            api_installed = True
        except ImportError:
            api_installed = False
        connect_host = "127.0.0.1" if config.api.host in {"0.0.0.0", "::"} else config.api.host
        payload = {
            "installed": api_installed,
            "running": status.running,
            "managed": status.running,
            "host": config.api.host,
            "port": config.api.port,
            "timeout": config.api.timeout,
            "api_key_hint": self._masked_secret(config.api.api_key),
            "endpoint": f"http://{connect_host}:{config.api.port}/v1",
            "command": "nanobot serve",
            "log_path": str(status.log_path),
        }
        if last_action:
            payload["last_action"] = last_action
        return payload

    @staticmethod
    def _masked_secret(value: str) -> str | None:
        value = value.strip()
        if not value:
            return None
        return f"{value[:3]}...{value[-4:]}" if len(value) > 8 else "configured"

    @staticmethod
    def _api_runtime_message(message: str) -> str:
        known = {
            "api_exited_during_startup": "API server exited during startup. Check its log for details.",
            "api_stop_timeout": "API server did not stop in time.",
            "api_state_stale": "API server state was stale; try starting it again.",
        }
        if message in known:
            return known[message]
        if message.startswith("api_"):
            return f"API server {message.removeprefix('api_').replace('_', ' ')}"
        return message.replace("_", " ")

    def _handle_settings_image_generation_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_image_generation_settings(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload, section="image"))

    def _handle_settings_transcription_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_transcription_settings(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload))

    def _handle_settings_network_safety_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_network_safety_settings(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload, section="runtime"))


    async def _handle_settings_mcp_presets(
        self,
        request: WsRequest,
        action: str | None = None,
    ) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = await mcp_presets_settings_action(
                action,
                self._parse_mcp_settings_query(request),
                reload_mcp=lambda: request_mcp_reload(self.bus),
            )
        except Exception as e:
            status = getattr(e, "status", 500)
            message = getattr(e, "message", str(e))
            if status >= 500:
                self.logger.exception("MCP preset action '{}' failed", action or "list")
            return self._error_response(status, message)
        if action is None:
            return self._json_response(payload)
        return self._json_response(self._with_restart_state(payload, section="runtime"))

    async def _handle_settings_version_check(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            update_info = await asyncio.to_thread(check_for_update)
        except Exception:
            self.logger.exception("version check failed")
            return self._error_response(500, "version check failed")
        return self._json_response({
            "updateAvailable": update_info,
        })


def _pairing_payload(last_action: dict[str, Any] | None = None) -> dict[str, Any]:
    now = time.time()
    requests = []
    for item in list_pending():
        expires_at = float(item.get("expires_at", 0) or 0)
        created_at = float(item.get("created_at", 0) or 0)
        requests.append({
            "code": str(item.get("code", "")),
            "channel": str(item.get("channel", "")),
            "sender_id": str(item.get("sender_id", "")),
            "created_at_ms": int(created_at * 1000) if created_at else None,
            "expires_at_ms": int(expires_at * 1000) if expires_at else None,
            "expires_in_seconds": max(0, int(expires_at - now)) if expires_at else None,
        })
    payload: dict[str, Any] = {"requests": requests}
    if last_action is not None:
        payload["last_action"] = last_action
    return payload
