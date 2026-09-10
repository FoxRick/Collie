"""Provider catalogue, credential and OAuth commands.

Handlers for the ``providers`` commands, split out of :mod:`collie_core.ipc.server` so
that one command group lives in one module. They are mixed into
:class:`collie_core.ipc.server.CollieIPCServer`, which keeps the connection, the
frame dispatch and the shared server state; these handlers reach that state through
``self`` and never call another command module. The wire contract is unchanged:
``_cmd_<kind>`` resolves through the composed class.
"""

from __future__ import annotations

import asyncio
import urllib.parse
from dataclasses import dataclass
from typing import Any

from loguru import logger
from websockets.asyncio.server import ServerConnection


@dataclass
class _OAuthAttemptState:
    generation: int
    attempt_id: str
    login: Any
    task: asyncio.Task | None = None


class ProviderCommands:
    """Provider catalogue, credential and OAuth commands. Mixed into :class:`CollieIPCServer` by composition."""

    async def _cmd_get_provider_catalogue(self, connection: ServerConnection, frame: dict) -> dict:
        catalogue = self._catalogue()
        return {
            "providers": catalogue.providers(),
            "snapshot": catalogue.snapshot_metadata(),
            "refresh": catalogue.refresh_state(),
        }

    async def _cmd_refresh_provider_catalogue(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        catalogue = self._catalogue()
        return await catalogue.refresh(url=frame.get("url") or None)

    async def _cmd_rollback_provider_catalogue(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        catalogue = self._catalogue()
        return catalogue.rollback()

    async def _cmd_detect_provider_for_key(self, connection: ServerConnection, frame: dict) -> dict:
        from collie_core.providers.validation import detect_provider_for_key

        api_key = str(frame.get("api_key") or "")
        if not api_key:
            return {"detected": False, "provider_id": None, "reason": "empty_key"}
        return await detect_provider_for_key(api_key, catalogue=self._catalogue())

    async def _cmd_detect_models(self, connection: ServerConnection, frame: dict) -> dict:
        from collie_core.providers.validation import detect_models_for_base_url

        return await detect_models_for_base_url(
            str(frame.get("api_base") or ""),
            protocol=str(frame.get("protocol") or "openai"),
            api_key=str(frame.get("api_key") or "") or None,
        )

    async def _cmd_detect_local_models(self, connection: ServerConnection, frame: dict) -> dict:
        from collie_core.providers.validation import detect_local_ollama

        return await detect_local_ollama()

    async def _cmd_set_api_key(self, connection: ServerConnection, frame: dict) -> dict:
        provider = str(frame.get("provider") or "").strip()
        key = str(frame.get("key") or "")
        if not provider or not key:
            raise ValueError("set_api_key requires 'provider' and 'key'")
        if self._on_set_api_key is not None:
            self._on_set_api_key(provider, key)
        provider_key = provider.casefold()
        existing = next(
            (
                item
                for item in self.db.list_providers()
                if str(item.get("auth_type") or "").replace("_", "-") == "api-key"
                and any(
                    str(item.get(field) or "").strip().casefold() == provider_key
                    for field in ("secret_name", "name")
                )
            ),
            None,
        )
        if existing is None:
            current_name = str(self.db.get_setting("provider.name", "") or "")
            current_auth = str(self.db.get_setting("provider.auth", "") or "")
            is_default = current_name == provider and current_auth == "api-key"
            self.db.upsert_provider(
                f"api-{provider}",
                name=provider,
                auth_type="api-key",
                model=(
                    str(self.db.get_setting("provider.model") or "") or None if is_default else None
                ),
                is_default=is_default,
            )
        return {"saved": True}

    async def _cmd_upsert_provider(self, connection: ServerConnection, frame: dict) -> dict:
        provider_id = str(frame.get("provider_id") or "").strip()
        name = str(frame.get("name") or "").strip()
        auth_type = str(frame.get("auth_type") or "").strip()
        if not provider_id or not name or not auth_type:
            raise ValueError("provider_id, name, and auth_type are required")
        is_default = bool(frame.get("is_default"))
        model = str(frame.get("model") or "").strip() or None
        protocol = str(frame.get("protocol") or "openai").strip().lower()
        if protocol not in {"openai", "anthropic"}:
            raise ValueError("protocol must be openai or anthropic")
        api_base = str(frame.get("api_base") or "").strip() or None
        if api_base is not None:
            parsed = urllib.parse.urlparse(api_base)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError("api_base must be an http(s) URL")
        runtime_name = str(frame.get("runtime_name") or name).strip().lower()
        secret_name = str(frame.get("secret_name") or name).strip()
        if auth_type == "api-key" and api_base:
            runtime_name = "anthropic" if protocol == "anthropic" else "custom"
        self.db.upsert_provider(
            provider_id,
            name=name,
            auth_type=auth_type,
            model=model,
            runtime_name=runtime_name,
            protocol=protocol,
            api_base=api_base,
            secret_name=secret_name,
            is_default=is_default,
        )
        if is_default:
            self._apply_provider_settings(self.db.get_provider(provider_id))
        return {"provider": self.db.get_provider(provider_id)}

    async def _cmd_activate_managed_provider(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        from collie_core.providers.managed import MODEL, managed_transport

        managed_transport()
        if any(not task.done() for task in self._chat_tasks.values()):
            raise ValueError("Finish or stop the current task before switching providers.")
        if self._on_configure is None:
            raise ValueError("Provider configuration is not available.")
        previous = self.db.default_provider()
        created = self.db.get_provider("collie-managed") is None
        if created:
            self.db.upsert_provider(
                "collie-managed",
                name="Collie AI",
                auth_type="collie-managed",
                model=MODEL,
                runtime_name="custom",
                protocol="openai",
                api_base=None,
                secret_name="collie-managed",
                is_default=False,
            )
        result = await self._cmd_activate_provider(connection, {"provider_id": "collie-managed"})
        if not result.get("configured") and created:
            self.db.delete_provider("collie-managed")
            self._apply_provider_settings(previous)
            if previous:
                self.db.set_default_provider(str(previous["id"]))
            await self._on_configure()
        return result

    async def _cmd_activate_provider(self, connection: ServerConnection, frame: dict) -> dict:
        if any(not task.done() for task in self._chat_tasks.values()):
            raise ValueError("Finish or stop the current task before switching providers.")
        provider_id = str(frame.get("provider_id") or "").strip()
        provider = self.db.get_provider(provider_id)
        if provider is None:
            raise ValueError("That provider is no longer available.")
        if (
            provider.get("auth_type") == "api-key"
            and self._on_configure_provider_candidate is not None
        ):
            result = await self._on_configure_provider_candidate(
                {
                    "provider_id": provider["id"],
                    "name": provider["name"],
                    "auth_type": provider["auth_type"],
                    "model": provider.get("model"),
                    "runtime_name": provider.get("runtime_name"),
                    "protocol": provider.get("protocol"),
                    "api_base": provider.get("api_base"),
                    "secret_name": provider.get("secret_name"),
                }
            )
            transaction_id = str(result.get("transaction_id") or "")
            if (
                result.get("configured")
                and transaction_id
                and self._on_finalize_provider_candidate is not None
            ):
                finalized = await self._on_finalize_provider_candidate(transaction_id)
                result.pop("transaction_id", None)
                if not finalized.get("finalized"):
                    rollback = (
                        await self._on_rollback_provider_candidate(transaction_id)
                        if self._on_rollback_provider_candidate is not None
                        else {
                            "rolled_back": False,
                            "rollback_error": "provider rollback is not available",
                        }
                    )
                    result.update(
                        {
                            "configured": False,
                            "error": "Provider activation could not be finalized.",
                            **rollback,
                        }
                    )
            return result
        previous = self.db.default_provider()
        self.db.set_default_provider(provider_id)
        self._apply_provider_settings(provider)
        if self._on_configure is None:
            return {"provider": provider, "configured": False}
        configured = await self._on_configure()
        if not configured.get("configured") and previous is not None:
            self.db.set_default_provider(str(previous["id"]))
            self._apply_provider_settings(previous)
            await self._on_configure()
        return {"provider": self.db.get_provider(provider_id), **configured}

    async def _cmd_configure_provider_candidate(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self._on_configure_provider_candidate is None:
            raise ValueError("provider configuration is not available")
        candidate = {
            key: frame.get(key)
            for key in (
                "provider_id",
                "name",
                "auth_type",
                "model",
                "runtime_name",
                "protocol",
                "api_base",
                "secret_name",
                "api_key",
            )
        }
        return await self._on_configure_provider_candidate(candidate)

    async def _cmd_finalize_provider_candidate(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        transaction_id = str(frame.get("transaction_id") or "").strip()
        if not transaction_id:
            raise ValueError("transaction_id is required")
        if self._on_finalize_provider_candidate is None:
            raise ValueError("provider finalization is not available")
        return await self._on_finalize_provider_candidate(transaction_id)

    async def _cmd_rollback_provider_candidate(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        transaction_id = str(frame.get("transaction_id") or "").strip()
        if not transaction_id:
            raise ValueError("transaction_id is required")
        if self._on_rollback_provider_candidate is None:
            raise ValueError("provider rollback is not available")
        return await self._on_rollback_provider_candidate(transaction_id)

    async def _cmd_delete_provider(self, connection: ServerConnection, frame: dict) -> dict:
        provider_id = str(frame.get("provider_id") or "").strip()
        provider = self.db.get_provider(provider_id)
        if provider is None:
            return {"deleted": False}
        self.db.delete_provider(provider_id)
        if provider.get("auth_type") == "api-key" and self._on_delete_api_key is not None:
            self._on_delete_api_key(str(provider.get("name") or ""))
        replacement = self.db.default_provider()
        if replacement is not None and not replacement.get("is_default"):
            self.db.set_default_provider(str(replacement["id"]))
            replacement = self.db.get_provider(str(replacement["id"]))
        self._apply_provider_settings(replacement)
        if self._on_configure is not None:
            await self._on_configure()
        return {"deleted": True, "default_provider": replacement}

    async def _cmd_oauth_login(self, connection: ServerConnection, frame: dict) -> None:
        provider = str(frame.get("provider") or "").strip().lower()
        if provider not in ("chatgpt", "claude"):
            raise ValueError(f"unknown OAuth provider: {provider!r}")

        req_id = frame.get("id")

        # A new click owns a fresh generation. The old worker thread may keep
        # running because oauth_cli_kit has no callback-server cancellation
        # hook, but its staged token storage is invalidated before replacement.
        existing = self._oauth_attempts.pop(provider, None)
        if existing is not None:
            existing.login.cancel()

        from collie_core.providers.auth import OAuthLoginAttempt

        generation = self._oauth_generations.get(provider, 0) + 1
        self._oauth_generations[provider] = generation
        attempt = _OAuthAttemptState(
            generation=generation,
            attempt_id=f"{provider}:{generation}",
            login=OAuthLoginAttempt(provider),
        )
        self._oauth_attempts[provider] = attempt

        def is_current() -> bool:
            return self._oauth_attempts.get(provider) is attempt and not attempt.login.cancelled

        async def _run_oauth() -> None:
            try:
                result = await asyncio.to_thread(attempt.login.run)
            except asyncio.CancelledError:
                attempt.login.cancel()
                await self._send(
                    connection,
                    {
                        "type": "error",
                        "id": req_id,
                        "message": "Sign-in cancelled.",
                    },
                )
                return
            except Exception as e:
                attempt.login.discard()
                if attempt.login.cancelled or not is_current():
                    await self._send(
                        connection,
                        {
                            "type": "error",
                            "id": req_id,
                            "message": "Sign-in cancelled.",
                        },
                    )
                    return
                logger.error("OAuth sign-in failed for {}: {}", provider, e)
                await self._send(
                    connection,
                    {
                        "type": "error",
                        "id": req_id,
                        "message": str(e)
                        if isinstance(e, ValueError)
                        else "Uh oh. That didn't go as planned. Try again?",
                        "detail": str(e),
                    },
                )
                return

            # Keep the final ownership check, token commit, and provider-state
            # writes await-free so another IPC cancel cannot interleave here.
            if not is_current() or not attempt.login.commit():
                attempt.login.discard()
                await self._send(
                    connection,
                    {
                        "type": "error",
                        "id": req_id,
                        "message": "Sign-in cancelled.",
                    },
                )
                return
            if result.get("signed_in"):
                canonical = str(result.get("provider") or "")
                is_claude = canonical == "claude"
                provider_record = self.db.configure_provider_candidate_record(
                    f"oauth-{canonical}",
                    name="anthropic" if is_claude else "openai_codex",
                    auth_type="claude-oauth" if is_claude else "chatgpt-oauth",
                    model=("claude-sonnet-4-6" if is_claude else "openai-codex/gpt-5.4"),
                    runtime_name="anthropic" if is_claude else "openai_codex",
                    protocol="anthropic" if is_claude else "openai",
                    api_base=None,
                    secret_name="anthropic" if is_claude else "openai_codex",
                )
                result["provider_record"] = provider_record
            result["attempt_id"] = attempt.attempt_id
            result["generation"] = attempt.generation
            if self._oauth_attempts.get(provider) is attempt:
                self._oauth_attempts.pop(provider, None)
            attempt.login.discard()
            await self._send(connection, {"type": "ok", "id": req_id, "data": result})

        task = asyncio.create_task(_run_oauth())
        attempt.task = task
        self._oauth_worker_tasks.add(task)

        def remove_finished(_task: asyncio.Task) -> None:
            # A stale completion must not remove the active replacement.
            self._oauth_worker_tasks.discard(_task)
            if self._oauth_attempts.get(provider) is attempt:
                self._oauth_attempts.pop(provider, None)

        task.add_done_callback(remove_finished)
        # Return None: the background task sends the ok/error reply.

    async def _cmd_cancel_oauth(self, connection: ServerConnection, frame: dict) -> dict:
        provider = str(frame.get("provider") or "").strip().lower()
        if provider not in ("chatgpt", "claude"):
            raise ValueError(f"unknown OAuth provider: {provider!r}")
        attempt = self._oauth_attempts.get(provider)
        requested_id = str(frame.get("attempt_id") or "").strip()
        requested_generation = frame.get("generation")
        if (
            attempt is None
            or (requested_id and requested_id != attempt.attempt_id)
            or (
                requested_generation is not None and int(requested_generation) != attempt.generation
            )
        ):
            return {"cancelled": False}
        self._oauth_attempts.pop(provider, None)
        attempt.login.cancel()
        return {
            "cancelled": True,
            "attempt_id": attempt.attempt_id,
            "generation": attempt.generation,
        }

    async def _cmd_oauth_logout(self, connection: ServerConnection, frame: dict) -> dict:
        from collie_core.providers import auth as collie_auth

        provider = str(frame.get("provider") or "")
        result = await asyncio.to_thread(collie_auth.logout_provider, provider)
        # Only clear the current provider when it is the OAuth path being
        # signed out of — never clobber an API-key provider.
        canonical = str(result.get("provider") or "")
        expected = "claude-oauth" if canonical == "claude" else "chatgpt-oauth"
        if str(self.db.get_setting("provider.auth", "") or "") == expected:
            self.db.set_setting("provider.auth", "")
            self.db.set_setting("provider.name", "")
            self.db.set_setting("provider.model", None)
        return result
