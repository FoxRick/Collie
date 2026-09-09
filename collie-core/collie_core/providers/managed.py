"""Collie account inference through the authenticated Electron transport.

The bridge resolves a fresh account session on each call. The core never
receives a Supabase refresh token or a supplier credential.
"""
from __future__ import annotations

import os

from nanobot.providers.openai_compat_provider import OpenAICompatProvider

MODEL = "collie-auto"


def managed_transport() -> tuple[str, str]:
    try:
        port = int(os.environ.get("COLLIE_KEYCHAIN_PORT", "0"))
    except ValueError:
        port = 0
    token = os.environ.get("COLLIE_KEYCHAIN_TOKEN", "")
    if not 1 <= port <= 65535 or not token:
        raise ValueError("Collie AI needs the desktop app. Please restart Collie.")
    return f"http://127.0.0.1:{port}/inference/v1", token


class ManagedProvider(OpenAICompatProvider):
    """Fixed hosted model; never retry uncertain attempts automatically."""

    def _build_kwargs(self, messages, tools, model, max_tokens, temperature,
                      reasoning_effort, tool_choice):
        return super()._build_kwargs(messages, tools, MODEL, min(max_tokens, 1024),
                                     temperature, None, tool_choice)

    def _should_use_responses_api(self, model, reasoning_effort):
        return False

    @staticmethod
    def _handle_error(e, *, spec=None, api_base=None):
        response = OpenAICompatProvider._handle_error(e, spec=spec, api_base=api_base)
        response.error_should_retry = False
        return response


def managed_provider() -> ManagedProvider:
    base, token = managed_transport()
    return ManagedProvider(api_key=token, api_base=base, default_model=MODEL)
