"""Read-only API-key validation probes used at Connect time.

Design rules (provider-onboarding.md):
- Prefer the provider's model-list endpoint with the user's key; fall back to
  a 1-token completion to the recommended model.
- The probe is the authority on whether a key works — prefixes are only hints.
- Never log the key. Never include it in errors, results, or telemetry.

Every probe is a plain HTTP request with a short timeout, run off the event
loop. A 401/403 is a definitive "key didn't work"; 429/5xx and network errors
are not treated as key failures (rate limits and outages are not bad keys).
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from typing import Any

from collie_core.catalog import CatalogueStore

_PROBE_TIMEOUT_SECONDS = 8
_MAX_RESPONSE_BYTES = 512 * 1024
_USER_AGENT = "Collie/alpha (connect validation)"


def _request(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
    timeout: float = _PROBE_TIMEOUT_SECONDS,
) -> tuple[int, Any]:
    """Return (status, parsed_json_or_none). Raises on network errors.

    HTTPError (4xx/5xx) is a subclass of URLError but is a *response*, not a
    network failure: it is caught here and returned as a status so callers can
    classify a 401/403 as a bad key rather than an outage.
    """
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if payload else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(body) > _MAX_RESPONSE_BYTES:
                raise ValueError("response exceeded the size limit")
            return int(response.status), _parse_json(body)
    except urllib.error.HTTPError as error:
        body = error.read(_MAX_RESPONSE_BYTES + 1)
        return int(error.code), _parse_json(body)


def _parse_json(body: bytes) -> Any:
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError:
        return None


def _openai_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
    }


def _anthropic_headers(api_key: str) -> dict[str, str]:
    return {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
    }


def _models_url(api_base: str, protocol: str) -> str:
    base = api_base.rstrip("/")
    if protocol == "anthropic":
        return f"{base}/v1/models" if not base.endswith("/v1") else f"{base}/models"
    return f"{base}/models"


def _chat_url(api_base: str, protocol: str) -> str:
    base = api_base.rstrip("/")
    if protocol == "anthropic":
        return f"{base}/v1/messages" if not base.endswith("/v1") else f"{base}/messages"
    return f"{base}/chat/completions"


def _one_token_payload(protocol: str, model: str) -> dict[str, Any]:
    if protocol == "anthropic":
        return {
            "model": model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        }
    return {
        "model": model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }


def _probe_models_endpoint(
    api_base: str,
    protocol: str,
    api_key: str,
) -> tuple[int, Any]:
    """Hit the model-list endpoint; returns (status, body)."""
    headers = (
        _anthropic_headers(api_key)
        if protocol == "anthropic"
        else _openai_headers(api_key)
    )
    return _request(_models_url(api_base, protocol), headers=headers)


def _probe_one_token(
    api_base: str,
    protocol: str,
    api_key: str,
    model: str,
) -> tuple[int, Any]:
    """Hit the chat endpoint with max_tokens=1; returns (status, body)."""
    headers = (
        _anthropic_headers(api_key)
        if protocol == "anthropic"
        else _openai_headers(api_key)
    )
    return _request(
        _chat_url(api_base, protocol),
        headers=headers,
        payload=_one_token_payload(protocol, model),
    )


def _is_auth_failure(status: int) -> bool:
    return status in (401, 403)


def _is_unsupported(status: int) -> bool:
    return status in (404, 405, 400)


def _parse_model_ids(protocol: str, body: Any) -> list[str]:
    if not isinstance(body, dict):
        return []
    raw = body.get("data")
    if isinstance(raw, list):
        ids: list[str] = []
        for item in raw:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                ids.append(item["id"])
        return ids
    return []


async def probe_api_key(
    *,
    provider_id: str,
    api_key: str,
    api_base: str | None = None,
    protocol: str = "openai",
    model: str | None = None,
    catalogue: CatalogueStore | None = None,
) -> dict[str, Any]:
    """Validate a key with a read-only request. Never logs the key.

    Returns::
        {"ok": True, "model": <id|None>, "model_label": <friendly|None>,
         "models": [...]}
        {"ok": False, "error": "auth"|"network"|"invalid", "detail": "..."}
    """
    catalogue = catalogue or CatalogueStore()
    if not api_base:
        api_base = catalogue.api_base(provider_id)
    if not api_base:
        return {"ok": False, "error": "invalid", "detail": "no api_base known for provider"}
    if not model:
        model = catalogue.default_model(provider_id)
    if not model:
        return {"ok": False, "error": "invalid", "detail": "no default model known for provider"}

    try:
        status, body = await asyncio.to_thread(
            _probe_models_endpoint, api_base, protocol, api_key
        )
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        return {"ok": False, "error": "network", "detail": str(error)}

    if _is_auth_failure(status):
        return {"ok": False, "error": "auth", "detail": f"models endpoint {status}"}
    if status == 200:
        ids = _parse_model_ids(protocol, body)
        chosen = model if model in ids else (ids[0] if ids else model)
        label = catalogue.model_label(provider_id, chosen) or chosen
        return {
            "ok": True,
            "model": chosen,
            "model_label": label,
            "models": ids,
        }

    # Model list unsupported (404/405/400) — fall back to a 1-token completion.
    try:
        status, _body = await asyncio.to_thread(
            _probe_one_token, api_base, protocol, api_key, model
        )
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        return {"ok": False, "error": "network", "detail": str(error)}
    if _is_auth_failure(status):
        return {"ok": False, "error": "auth", "detail": f"completion endpoint {status}"}
    if status in (200, 201):
        label = catalogue.model_label(provider_id, model) or model
        return {"ok": True, "model": model, "model_label": label, "models": []}
    return {
        "ok": False,
        "error": "invalid",
        "detail": f"completion endpoint {status}",
    }


async def detect_provider_for_key(
    api_key: str,
    catalogue: CatalogueStore | None = None,
) -> dict[str, Any]:
    """Resolve a pasted key to a provider, probing ambiguous prefixes.

    Unambiguous prefixes (``gsk_``, ``sk-ant-``, …) resolve instantly;
    ``sk-`` (OpenAI vs DeepSeek) is resolved by probing each candidate's
    model-list endpoint — the first one that accepts the key wins.
    """
    catalogue = catalogue or CatalogueStore()
    candidates = catalogue.detect_provider_for_key(api_key)
    if not candidates:
        return {"detected": False, "provider_id": None, "reason": "no_prefix_match"}
    if len(candidates) == 1:
        return {"detected": True, "provider_id": candidates[0], "reason": "prefix"}
    for provider_id in candidates:
        result = await probe_api_key(
            provider_id=provider_id, api_key=api_key, catalogue=catalogue
        )
        if result.get("ok"):
            return {"detected": True, "provider_id": provider_id, "reason": "probe"}
    return {
        "detected": False,
        "provider_id": None,
        "reason": "probe_failed",
        "candidates": candidates,
    }


async def detect_models_for_base_url(
    api_base: str,
    protocol: str = "openai",
    api_key: str | None = None,
) -> dict[str, Any]:
    """List models served by a custom base URL (the Advanced 'Detect models')."""
    base = (api_base or "").strip().rstrip("/")
    if not base:
        return {"detected": False, "error": "missing_base_url", "models": []}
    if api_key:
        headers = (
            _anthropic_headers(api_key)
            if protocol == "anthropic"
            else _openai_headers(api_key)
        )
    else:
        headers = {"Content-Type": "application/json", "User-Agent": _USER_AGENT}
    try:
        status, body = await asyncio.to_thread(
            _request, _models_url(base, protocol), headers=headers
        )
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        return {"detected": False, "error": "network", "detail": str(error), "models": []}
    if status != 200:
        return {"detected": False, "error": f"http_{status}", "models": []}
    ids = _parse_model_ids(protocol, body)
    return {"detected": bool(ids), "error": None, "models": ids}


async def detect_local_ollama() -> dict[str, Any]:
    """Check for a local Ollama install and list its models.

    The HTTP probe (default http://localhost:11434) is the authority — a
    bundled desktop app cannot rely on PATH. Honors OLLAMA_HOST like Ollama
    itself does.
    """
    host = os.environ.get("OLLAMA_HOST", "").strip()
    base = host.rstrip("/") if host else "http://localhost:11434"
    try:
        status, body = await asyncio.to_thread(
            _request, f"{base}/api/tags", headers={"User-Agent": _USER_AGENT}
        )
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return {"available": False, "models": []}
    if status != 200 or not isinstance(body, dict):
        return {"available": False, "models": []}
    models: list[str] = []
    for item in body.get("models") or []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            name = item["name"]
            if name.endswith(":latest"):
                name = name[: -len(":latest")]
            models.append(name)
    return {"available": bool(models), "models": sorted(models)}
