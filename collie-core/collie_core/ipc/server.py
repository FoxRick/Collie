"""Collie IPC WebSocket server.

Localhost-only JSON protocol between the Electron shell and the Python core.

Client -> server commands (JSON objects; ``type`` + optional ``id``):
- ``ping``                     -> ``pong``
- ``get_status``               -> core status (provider, model, counts)
- ``transcribe``               -> local English microphone dictation
- ``chat``                     -> start an agent turn; streams events back
- ``stop``                     -> note a stop request for a conversation
- ``new_conversation`` / ``list_conversations`` / ``get_messages`` /
  ``rename_conversation`` / ``delete_conversation`` / ``search_messages``
- ``get_settings`` / ``set_setting``
- ``set_api_key``              -> inject provider secret (memory only)
- ``configure``                -> rebuild the agent with current settings
- ``get_profile`` / ``get_people`` / ``get_dates`` (Settings -> Memory tab)
- ``get_memory_journal`` (recent memory mutations, newest first)
- ``list_connector_catalog`` / ``list_connector_connections`` /
  ``begin_connector_auth`` / ``test_connector`` / ``update_connector`` /
  ``remove_connector`` (consumer connector directory and lifecycle)
- ``list_services`` / ``connect_service`` / ``disconnect_service``
  (temporary compatibility aliases)
- ``list_subagents`` / ``create_subagent`` / ``update_subagent`` /
  ``delete_subagent`` (Settings -> Subagents tab; Collie writes the prompt
  from a plain-English description when none is given)
- ``get_messengers`` / ``set_messenger`` / ``set_messenger_secret`` /
  ``approve_pairing`` / ``deny_pairing`` / ``revoke_messenger_sender``
  (Settings -> Phone tab; Telegram/WhatsApp/Slack/Discord access)

Server -> client events:
- ``ready``    on connect
- ``ok`` / ``error`` command replies (echo ``id``)
- ``thinking`` dog-themed state for the ThinkingBar + pet
- ``delta``    streamed assistant text
- ``message``  persisted message (user echo + final assistant)
- ``messenger_qr`` / ``messenger_status`` / ``messenger_pairing``
  (Phone tab live updates)
- ``automation`` a briefing/reminder fired (name + content for OS notify)
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import inspect
import json
import os
import re
import urllib.parse
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger
from websockets.asyncio.server import ServerConnection, serve

from collie_core.db import CollieDB, collie_home
from collie_core.ipc.thinking import phrase_for_state, thinking_state_for_tool
from collie_core.permissions.classifier import classify_tool
from collie_core.permissions.models import Risk
from collie_core.providers.storage import legacy_oauth_data_root
from collie_core.session_identity import desktop_session_key
from collie_core.voice import LocalVoiceService, VoiceInputError
from nanobot.security.workspace_access import (
    WorkspaceScopeError,
    set_live_local_file_scope,
    validate_local_file_access_scope_payload,
)
from nanobot.webui.attachment_ingress import store_inbound_attachments
from nanobot.webui.ingress_policy import DEFAULT_WEBUI_INGRESS_POLICY
from nanobot.webui.skills_api import webui_skill_detail_payload, webui_skills_payload

__all__ = ["CollieIPCServer"]

_MAX_FRAME_BYTES = DEFAULT_WEBUI_INGRESS_POLICY.minimum_full_policy_frame_bytes()

_MAX_PREVIEW_BYTES = 256 * 1024
_SAFE_PREVIEW_MIMES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
_PREVIEW_DATA_URL = re.compile(
    r"data:(image/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/]*={0,2})",
    re.IGNORECASE,
)

_ATTACHMENT_ERRORS = {
    "too_many_images": "Up to four files at a time, please.",
    "too_many_videos": "One video at a time, please.",
    "too_many_attachments": "Up to four files at a time, please.",
    "total_size": "Those files are too large together. Keep the total under 24 MB.",
    "mime": "I cannot read that file type yet.",
    "size": "That file is too large. Keep each file under 6 MB.",
    "malformed": "That attachment did not arrive correctly. Try choosing it again.",
    "decode": "I could not open that attachment. Try choosing it again.",
}

_TITLE_PREFIX = re.compile(
    r"^(?:please\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+|"
    r"i\s+(?:want|need)\s+(?:you\s+)?to\s+)",
    re.IGNORECASE,
)

# Keys the shell may set over IPC. Everything else is written only by the
# core itself (defense-in-depth: an attacker who reaches the socket must not
# be able to flip permission or messenger settings).
_IPC_SETTABLE_SETTINGS = {"provider.auth", "provider.name", "provider.model", "provider.api_base"}

_ALLOWED_ORIGIN_HOSTS = {"localhost", "127.0.0.1", "::1"}

_PERSON_FIELDS = frozenset(
    {"relationship", "birthday", "allergies", "preferences", "gift_ideas", "notes"}
)


@dataclass
class _OAuthAttemptState:
    generation: int
    attempt_id: str
    login: Any
    task: asyncio.Task | None = None


def _fallback_chat_title(content: str) -> str:
    clean = " ".join(content.strip().split())
    clean = _TITLE_PREFIX.sub("", clean).strip(" \t\r\n.,:;!?-")
    if not clean:
        return "New conversation"
    words = clean.split()
    title = " ".join(words[:8])
    if len(words) > 8 or len(title) > 48:
        title = title[:47].rstrip() + "…"
    return title[0].upper() + title[1:]


def _safe_preview_data_url(attachment: dict[str, Any]) -> str | None:
    """Return a bounded raster preview supplied by the shell, or omit it safely."""
    attachment_mime = str(attachment.get("mime") or "").strip().lower()
    if attachment_mime not in _SAFE_PREVIEW_MIMES:
        return None

    preview = attachment.get("preview_data_url")
    if not isinstance(preview, str):
        return None
    match = _PREVIEW_DATA_URL.fullmatch(preview)
    if match is None:
        return None

    encoded = match.group(2)
    max_encoded_length = ((_MAX_PREVIEW_BYTES + 2) // 3) * 4
    if not encoded or len(encoded) > max_encoded_length:
        return None
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None
    if not decoded or len(decoded) > _MAX_PREVIEW_BYTES:
        return None
    return preview


class CollieIPCServer:
    """Serve the Collie IPC protocol on 127.0.0.1."""

    def __init__(
        self,
        db: CollieDB,
        *,
        host: str = "127.0.0.1",
        port: int = 3818,
        chat_runner: Callable[..., Awaitable[Any]] | None = None,
        on_set_api_key: Callable[[str, str], None] | None = None,
        on_delete_api_key: Callable[[str], None] | None = None,
        on_configure: Callable[[], Awaitable[dict[str, Any]]] | None = None,
        on_configure_provider_candidate: (
            Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None
        ) = None,
        on_finalize_provider_candidate: (
            Callable[[str], Awaitable[dict[str, Any]]] | None
        ) = None,
        on_rollback_provider_candidate: (
            Callable[[str], Awaitable[dict[str, Any]]] | None
        ) = None,
        status_provider: Callable[[], dict[str, Any]] | None = None,
        service_manager: Any = None,
        subagent_loader: Any = None,
        prompt_writer: Callable[[str, str], Awaitable[str]] | None = None,
        title_generator: Callable[[str], Awaitable[str]] | None = None,
        subagents_running: Callable[[str], int] | None = None,
        subagent_canceler: Callable[[str], Awaitable[int]] | None = None,
        conversation_canceler: Callable[[str], Awaitable[int]] | None = None,
        chat_steerer: Callable[[str, str], Awaitable[bool]] | None = None,
        messenger_manager: Any = None,
        skills_workspace: Path | None = None,
        profile_store: Any = None,
        command_runner: Callable[..., Awaitable[dict[str, Any] | None]] | None = None,
        command_catalog: Callable[[], dict[str, Any]] | None = None,
        command_requires_approval: Callable[[str], bool] | None = None,
        session_target: Callable[[str], tuple[str, str]] | None = None,
        conversation_deleter: Callable[[str], None] | None = None,
        on_set_approval_preset: Callable[[str], None] | None = None,
        legacy_oauth_root: Path | None = None,
        token: str | None = None,
        dream_runner: Callable[[], Awaitable[dict[str, Any]]] | None = None,
        gardener_runner: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self.db = db
        self.host = host
        self.port = port
        self._chat_runner = chat_runner
        self._on_set_api_key = on_set_api_key
        self._on_delete_api_key = on_delete_api_key
        self._on_configure = on_configure
        self._on_configure_provider_candidate = on_configure_provider_candidate
        self._on_finalize_provider_candidate = on_finalize_provider_candidate
        self._on_rollback_provider_candidate = on_rollback_provider_candidate
        self._status_provider = status_provider
        self._service_manager = service_manager
        self._subagent_loader = subagent_loader
        self._prompt_writer = prompt_writer
        self._title_generator = title_generator
        self._subagents_running = subagents_running
        self._subagent_canceler = subagent_canceler
        self._conversation_canceler = conversation_canceler
        self._chat_steerer = chat_steerer
        self._messenger_manager = messenger_manager
        self._skills_workspace = skills_workspace
        self._profile_store = profile_store
        self._command_runner = command_runner
        self._command_catalog = command_catalog
        self._command_requires_approval = command_requires_approval
        self._session_target = session_target
        self._conversation_deleter = conversation_deleter
        self._on_set_approval_preset = on_set_approval_preset
        self._legacy_oauth_root = Path(legacy_oauth_root or legacy_oauth_data_root())
        self._token = token
        self._dream_runner = dream_runner
        self._gardener_runner = gardener_runner
        self._clients: set[ServerConnection] = set()
        self._server: Any = None
        self._chat_tasks: dict[str, asyncio.Task] = {}
        self._command_tasks: set[asyncio.Task] = set()
        self._active_material_runs: dict[str, int] = {}
        self._background_tasks: set[asyncio.Task] = set()
        self._oauth_attempts: dict[str, _OAuthAttemptState] = {}
        self._oauth_generations: dict[str, int] = {}
        self._oauth_worker_tasks: set[asyncio.Task] = set()
        self.approval_broker: Any = None
        self._voice = LocalVoiceService()

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        subprotocols = [f"collie-{self._token}"] if self._token else None
        self._server = await serve(
            self._handle_connection,
            self.host,
            self.port,
            max_size=_MAX_FRAME_BYTES,
            process_request=self._authorize_handshake,
            subprotocols=subprotocols,
        )
        logger.info("Collie IPC listening on ws://{}:{}", self.host, self.port)

    async def stop(self) -> None:
        oauth_attempts = list(self._oauth_attempts.values())
        tasks = list(self._chat_tasks.values()) + list(self._background_tasks)
        oauth_tasks = list(self._oauth_worker_tasks)
        self._chat_tasks.clear()
        self._background_tasks.clear()
        self._oauth_attempts.clear()
        self._oauth_worker_tasks.clear()
        for attempt in oauth_attempts:
            attempt.login.cancel()
        for task in tasks:
            task.cancel()
        # Drain cancelled tasks before the caller closes the DB: a task's
        # CancelledError handler may still be mid-write.
        if tasks or oauth_tasks:
            await asyncio.gather(*tasks, *oauth_tasks, return_exceptions=True)
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # -- broadcast helpers ------------------------------------------------------

    async def _send(self, connection: ServerConnection, payload: dict[str, Any]) -> None:
        try:
            await connection.send(json.dumps(payload, ensure_ascii=False))
        except Exception:
            logger.debug("IPC send failed; dropping client")

    async def broadcast(self, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False)
        for connection in list(self._clients):
            try:
                await connection.send(raw)
            except Exception:
                self._clients.discard(connection)

    async def send_thinking(self, conversation_id: str, state: str) -> None:
        await self.broadcast(
            {"type": "thinking", "conversation_id": conversation_id,
             **phrase_for_state(state)}
        )

    @staticmethod
    def _renderer_task(task: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in task.items() if key != "conversation_id"}

    async def _broadcast_task_state(self, task: dict[str, Any] | None) -> None:
        if task is None:
            return
        conversation_id = str(task.get("conversation_id") or "")
        if not conversation_id:
            return
        await self.broadcast(
            {
                "type": "task_state",
                "conversation_id": conversation_id,
                "task": self._renderer_task(task),
            }
        )

    async def _publish_superseded_checklist(
        self, task: dict[str, Any] | None
    ) -> None:
        if task is None:
            return
        conversation_id = str(task.get("conversation_id") or "")
        message = self.db.add_message(
            conversation_id,
            "assistant",
            "I closed the earlier checklist because the approved plan is starting.",
            task_state=self._renderer_task(task),
        )
        await self.broadcast(
            {
                "type": "message",
                "conversation_id": conversation_id,
                "message": message,
            }
        )
        await self._broadcast_task_state(task)

    async def _finalize_requested_plan_change(
        self, run_id: str
    ) -> dict[str, Any]:
        result = self.db.finalize_plan_change(run_id)
        if result["changed"]:
            await self.broadcast({"type": "run_failed", "run": result["run"]})
        await self._broadcast_task_state(result["task"])
        conversation_id = str(result["request"]["conversation_id"])
        message = self.db.claim_plan_change_terminal_message(run_id)
        if message is not None:
            await self.broadcast(
                {
                    "type": "message",
                    "conversation_id": conversation_id,
                    "message": message,
                }
            )
        return result

    async def _broadcast_run_step(
        self, run_id: str, step: dict[str, Any] | None
    ) -> None:
        run = self.db.get_run(run_id)
        conversation_id = str((run or {}).get("conversation_id") or "")
        if step is not None:
            await self.broadcast(
                {
                    "type": "run_step_updated",
                    "conversation_id": conversation_id,
                    "step": step,
                }
            )
        await self._broadcast_task_state(self.db.get_run_task(run_id))

    # -- connection handling --------------------------------------------------------

    async def _authorize_handshake(self, connection: ServerConnection, request: Any) -> Any:
        """Reject handshakes that lack the per-boot token or send a foreign Origin.

        The Electron shell generates the token and passes it out-of-band via
        ``COLLIE_IPC_TOKEN``; the renderer presents it as the WebSocket
        ``Sec-WebSocket-Protocol`` value ``collie-<token>``. Browsers cannot set
        arbitrary headers, so the subprotocol is the standard channel. Returns
        an HTTP response to reject, or None to proceed.
        """
        if self._token is not None:
            offered = {
                item.strip()
                for item in request.headers.get("Sec-WebSocket-Protocol", "").split(",")
                if item.strip()
            }
            if f"collie-{self._token}" not in offered:
                return connection.respond(401, "unauthorized")
        origin = request.headers.get("Origin", "")
        if origin:
            try:
                parsed = urllib.parse.urlparse(origin)
            except ValueError:
                return connection.respond(403, "forbidden")
            if parsed.scheme in ("http", "https"):
                if str(parsed.hostname or "").lower() not in _ALLOWED_ORIGIN_HOSTS:
                    return connection.respond(403, "forbidden")
        return None

    async def _handle_connection(self, connection: ServerConnection) -> None:
        self._clients.add(connection)
        await self._send(connection, {
            "type": "ready",
            "protocol": 1,
            **phrase_for_state("startup"),
        })
        try:
            async for raw in connection:
                await self._handle_frame(connection, raw)
        except Exception:
            logger.debug("IPC connection closed")
        finally:
            self._clients.discard(connection)

    async def _handle_frame(self, connection: ServerConnection, raw: str | bytes) -> None:
        try:
            frame = json.loads(raw)
        except (TypeError, ValueError):
            await self._send(connection, {"type": "error", "message": "invalid JSON"})
            return
        if not isinstance(frame, dict) or not isinstance(frame.get("type"), str):
            await self._send(connection, {"type": "error", "message": "missing type"})
            return

        kind = frame["type"]
        req_id = frame.get("id")
        handler = getattr(self, f"_cmd_{kind}", None)
        if handler is None:
            await self._send(connection, {
                "type": "error", "id": req_id, "message": f"unknown command: {kind}",
            })
            return
        try:
            result = await handler(connection, frame)
            if result is not None:
                await self._send(connection, {"type": "ok", "id": req_id, "data": result})
        except Exception as e:
            # Never include frame locals in diagnostics: secret-bearing commands
            # carry API keys, OAuth credentials, or Telegram bot tokens.
            logger.error("IPC command failed: {} ({})", kind, type(e).__name__)
            safe_message = (
                str(e)
                if isinstance(e, (ValueError, VoiceInputError))
                else "Uh oh. That didn't go as planned. Try again?"
            )
            await self._send(connection, {
                "type": "error", "id": req_id,
                "message": safe_message,
                "detail": safe_message,
            })

    # -- commands ----------------------------------------------------------------------

    async def _cmd_ping(self, connection: ServerConnection, frame: dict) -> dict:
        return {"pong": True}

    async def _cmd_get_status(self, connection: ServerConnection, frame: dict) -> dict:
        status: dict[str, Any] = {
            "conversations": len(self.db.list_conversations(include_archived=True)),
            "providers": self.db.list_providers(),
            "usage_this_month": self.db.usage_this_month(),
        }
        if self._status_provider is not None:
            status.update(self._status_provider())
        return status

    async def _cmd_list_commands(self, connection: ServerConnection, frame: dict) -> dict:
        if self._command_catalog is None:
            return {"commands": [], "agents": [], "skills": []}
        return self._command_catalog()

    async def _cmd_transcribe(self, connection: ServerConnection, frame: dict) -> dict:
        """Turn a renderer-captured WAV into English text without a cloud service."""
        text = await self._voice.transcribe_data_url(str(frame.get("audio") or ""))
        return {"text": text}

    async def _cmd_list_skills(self, connection: ServerConnection, frame: dict) -> dict:
        if self._skills_workspace is None:
            return {"skills": []}
        disabled = set(self.db.get_setting("agent.disabled_skills", []) or [])
        return webui_skills_payload(self._skills_workspace, disabled_skills=disabled)

    async def _cmd_get_skill(self, connection: ServerConnection, frame: dict) -> dict:
        if self._skills_workspace is None:
            raise ValueError("Skills are not available yet.")
        name = str(frame.get("name") or "").strip()
        if not name:
            raise ValueError("Pick a skill to inspect.")
        disabled = set(self.db.get_setting("agent.disabled_skills", []) or [])
        skill = webui_skill_detail_payload(
            self._skills_workspace,
            name,
            disabled_skills=disabled,
        )
        if skill is None:
            raise ValueError(f"Skill not found: {name}")
        # Collie's UI needs a useful overview, not the full local instruction file.
        skill.pop("raw_markdown", None)
        return {"skill": skill}

    async def _cmd_new_conversation(self, connection: ServerConnection, frame: dict) -> dict:
        conversation = self.db.create_conversation(str(frame.get("title") or "New chat"))
        self.db.set_conversation_mode(str(conversation["id"]), "execute")
        conversation["execution_mode"] = "execute"
        return conversation

    async def _cmd_list_conversations(self, connection: ServerConnection, frame: dict) -> dict:
        return {"conversations": await asyncio.to_thread(
            self.db.list_conversations,
            bool(frame.get("include_archived")),
        )}

    async def _cmd_get_messages(self, connection: ServerConnection, frame: dict) -> dict:
        conv_id = str(frame.get("conversation_id") or "")
        limit = frame.get("limit")
        # Off the event loop: a long conversation loads asynchronously.
        messages = await asyncio.to_thread(
            self.db.get_messages,
            conv_id,
            int(limit) if limit is not None else None,
        )
        return {"messages": messages}

    async def _cmd_get_run_records(self, connection: ServerConnection, frame: dict) -> dict:
        """List turn events (most recent first) — read-only telemetry."""
        conv_id = str(frame.get("conversation_id") or "") or None
        session_key = str(frame.get("session_key") or "") or None
        since = str(frame.get("since") or "") or None
        limit = frame.get("limit")
        turns = await asyncio.to_thread(
            self.db.list_turn_events,
            conversation_id=conv_id,
            session_key=session_key,
            since=since,
            limit=int(limit) if limit is not None else 200,
        )
        return {"turns": turns}

    async def _cmd_get_tool_events(self, connection: ServerConnection, frame: dict) -> dict:
        """List tool events (most recent first) — read-only telemetry."""
        turn_id = str(frame.get("turn_id") or "") or None
        tool_name = str(frame.get("tool_name") or "") or None
        limit = frame.get("limit")
        events = await asyncio.to_thread(
            self.db.list_tool_events,
            turn_id=turn_id,
            tool_name=tool_name,
            limit=int(limit) if limit is not None else 500,
        )
        return {"tool_events": events}

    async def _cmd_get_active_task(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        conv_id = str(frame.get("conversation_id") or "")
        task = await asyncio.to_thread(self.db.get_active_task, conv_id)
        return {"task": self._renderer_task(task) if task is not None else None}

    async def _cmd_set_execution_mode(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        conv_id = str(frame.get("conversation_id") or "")
        mode = str(frame.get("execution_mode") or "")
        self.db.set_conversation_mode(conv_id, mode)
        return {"conversation_id": conv_id, "execution_mode": mode}

    async def _cmd_set_file_access_scope(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        """Apply a file-access scope immediately, including to a running turn.

        The renderer fires this when the user changes the Files scope mid-chat;
        local file tools consult the live override so the new folders apply to
        the in-flight turn instead of only the next message.
        """
        conv_id = str(frame.get("conversation_id") or "").strip()
        if not conv_id:
            raise ValueError("A conversation is required to update file access.")
        conversation = await asyncio.to_thread(self.db.get_conversation, conv_id)
        if conversation is None:
            raise ValueError("That conversation no longer exists.")
        selected_folder = str(conversation.get("project_path") or "") or None
        raw = frame.get("file_access_scope")
        if not isinstance(raw, dict):
            raise ValueError("file_access_scope must be an object")
        roots, unrestricted = validate_local_file_access_scope_payload(
            raw, selected_folder=selected_folder
        )
        set_live_local_file_scope(conv_id, roots, unrestricted)
        scope: dict[str, Any] = {
            "mode": "full_file_access" if unrestricted else str(raw["mode"])
        }
        if not unrestricted and raw["mode"] == "chosen_folders":
            scope["roots"] = [str(root) for root in roots]
        return {"applied": True, "file_access_scope": scope}

    async def _cmd_rename_conversation(self, connection: ServerConnection, frame: dict) -> dict:
        self.db.rename_conversation(str(frame["conversation_id"]), str(frame["title"]))
        return {"renamed": True}

    async def _cmd_delete_conversation(self, connection: ServerConnection, frame: dict) -> dict:
        conv_id = str(frame["conversation_id"])
        # Cancel any in-flight turn first: a running task keeps inserting into
        # a deleted conversation (FK violation + ghost replies).
        task = self._chat_tasks.pop(conv_id, None)
        if task is not None and not task.done():
            task.cancel()
        if self._conversation_canceler is not None:
            try:
                await self._conversation_canceler(conv_id)
            except Exception:
                logger.exception("Failed to cancel work for deleted conversation")
        elif self._subagent_canceler is not None:
            try:
                await self._subagent_canceler(conv_id)
            except Exception:
                logger.exception("Failed to cancel subagents for deleted conversation")
        if self.approval_broker is not None:
            try:
                await self.approval_broker.cancel_conversation(conv_id)
            except Exception:
                logger.exception("Failed to cancel approvals for deleted conversation")
        if self._conversation_deleter is not None:
            try:
                self._conversation_deleter(conv_id)
            except Exception:
                logger.exception("Failed to delete session files for {}", conv_id)
        self._prune_conversation_media(conv_id)
        self.db.delete_conversation(conv_id)
        return {"deleted": True}

    def _prune_conversation_media(self, conv_id: str) -> None:
        """Unlink uploads referenced only by the deleted conversation."""
        uploads_root = (collie_home() / "media" / "uploads").resolve()
        for message in self.db.get_messages(conv_id):
            for attachment in message.get("attachments") or []:
                if not isinstance(attachment, dict):
                    continue
                stored = str(attachment.get("path") or "")
                if not stored:
                    continue
                try:
                    path = Path(stored).resolve()
                except (OSError, ValueError):
                    continue
                if path.is_relative_to(uploads_root) and path.exists():
                    with suppress(OSError):
                        path.unlink()

    async def _cmd_search_messages(self, connection: ServerConnection, frame: dict) -> dict:
        query = str(frame.get("query") or "")
        return {"results": await asyncio.to_thread(self.db.search_messages, query)}

    async def _cmd_export_data(self, connection: ServerConnection, frame: dict) -> dict:
        """Write everything to a zip in ~/.collie/exports and return its path."""
        import zipfile
        from datetime import datetime, timezone

        from collie_core.db import collie_home

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        exports = collie_home() / "exports"
        exports.mkdir(parents=True, exist_ok=True)
        zip_path = exports / f"collie-export-{stamp}.zip"

        data = json.dumps(self.db.export_all(), ensure_ascii=False, indent=2)
        workspace = collie_home() / "workspace"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("collie-data.json", data)
            if workspace.exists():
                for path in sorted(workspace.rglob("*.md")):
                    zf.write(path, f"workspace/{path.relative_to(workspace)}")
        # Rotation: keep the newest ten exports so the folder cannot grow
        # without bound.
        try:
            exports_list = sorted(
                (
                    path
                    for path in exports.iterdir()
                    if path.is_file() and path.name.startswith("collie-export-")
                ),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for old in exports_list[10:]:
                with suppress(OSError):
                    old.unlink()
        except OSError:
            logger.warning("Export rotation failed for {}", exports)
        return {"path": str(zip_path)}

    async def _cmd_clear_all_data(self, connection: ServerConnection, frame: dict) -> dict:
        """Delete user data and report any best-effort filesystem gaps.

        The database is cleared first in one SQLite transaction. Files are
        removed only after that succeeds, so a database failure leaves session
        and credential files untouched. Once the database is empty, deletion
        failures are returned as warnings instead of becoming a misleading
        generic IPC error.
        """
        if not frame.get("confirm"):
            raise ValueError("Add confirm=true — this deletes everything I remember!")
        try:
            self.db.clear_all()
        except Exception as error:
            logger.exception("Clear-all stopped before filesystem deletion")
            return {
                "cleared": False,
                "partial": False,
                "database_cleared": False,
                "filesystem_cleared": False,
                "warnings": [
                    {
                        "scope": "database",
                        "target": str(self.db.path),
                        "error": f"{type(error).__name__}: {error}"[:500],
                    }
                ],
            }

        warnings = self._wipe_stored_credentials()
        filesystem_cleared = not warnings
        return {
            "cleared": filesystem_cleared,
            "partial": not filesystem_cleared,
            "database_cleared": True,
            "filesystem_cleared": filesystem_cleared,
            "warnings": warnings,
        }

    def _wipe_stored_credentials(self) -> list[dict[str, str]]:
        """Best-effort deletion after the transactional database clear."""
        import shutil

        warnings: list[dict[str, str]] = []

        def record(path: Path, error: OSError) -> None:
            warning = {
                "scope": "filesystem",
                "target": str(path),
                "error": f"{type(error).__name__}: {error}"[:500],
            }
            warnings.append(warning)
            logger.warning("Clear-all could not delete {}: {}", path, error)

        home = collie_home()
        for relative in ("credentials", "media", "exports"):
            path = home / relative
            try:
                shutil.rmtree(path)
            except FileNotFoundError:
                pass
            except OSError as error:
                record(path, error)

        pairing_path = home / "pairing.json"
        try:
            pairing_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            record(pairing_path, error)

        # Engine session history (the agent's working memory of chats).
        sessions_dir = home / "workspace" / "sessions"
        try:
            session_paths = list(sessions_dir.glob("*.jsonl*"))
        except OSError as error:
            record(sessions_dir, error)
        else:
            for path in session_paths:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as error:
                    record(path, error)

        # Legacy plaintext oauth_cli_kit tokens (pre-DPAPI); leave other tools'
        # files inside the shared app-data dir alone.
        legacy_auth = self._legacy_oauth_root / "auth"
        for name in ("claude.json", "codex.json", "claude.json.lock", "codex.json.lock"):
            path = legacy_auth / name
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as error:
                record(path, error)
        return warnings

    async def _cmd_get_settings(self, connection: ServerConnection, frame: dict) -> dict:
        return {"settings": self.db.all_settings()}

    async def _cmd_set_setting(self, connection: ServerConnection, frame: dict) -> dict:
        key = str(frame.get("key") or "")
        if not key:
            raise ValueError("set_setting requires 'key'")
        if key not in _IPC_SETTABLE_SETTINGS:
            raise ValueError(f"Setting '{key}' is managed by Collie itself.")
        if key == "provider.model":
            # The active model is two sources in one transaction (setting +
            # default provider row); a bare settings write would let a later
            # provider rebuild revert the choice.
            value = frame.get("value")
            if not isinstance(value, str) or not value.strip():
                raise ValueError("provider.model must be a non-empty model id")
            self.db.set_active_model(value)
        else:
            self.db.set_setting(key, frame.get("value"))
        return {"saved": True}

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
                item for item in self.db.list_providers()
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
                    str(self.db.get_setting("provider.model") or "") or None
                    if is_default
                    else None
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

    async def _cmd_activate_provider(self, connection: ServerConnection, frame: dict) -> dict:
        provider_id = str(frame.get("provider_id") or "").strip()
        provider = self.db.get_provider(provider_id)
        if provider is None:
            raise ValueError("That provider is no longer available.")
        if (
            provider.get("auth_type") == "api-key"
            and self._on_configure_provider_candidate is not None
        ):
            result = await self._on_configure_provider_candidate({
                "provider_id": provider["id"],
                "name": provider["name"],
                "auth_type": provider["auth_type"],
                "model": provider.get("model"),
                "runtime_name": provider.get("runtime_name"),
                "protocol": provider.get("protocol"),
                "api_base": provider.get("api_base"),
                "secret_name": provider.get("secret_name"),
            })
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
                    result.update({
                        "configured": False,
                        "error": "Provider activation could not be finalized.",
                        **rollback,
                    })
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

    def _apply_provider_settings(self, provider: dict[str, Any] | None) -> None:
        if provider is None:
            self.db.set_setting("provider.auth", "")
            self.db.set_setting("provider.name", "")
            self.db.set_setting("provider.model", None)
            self.db.set_setting("provider.api_base", None)
            self.db.set_setting("provider.secret_name", None)
            return
        self.db.set_setting("provider.auth", provider["auth_type"])
        self.db.set_setting(
            "provider.name", provider.get("runtime_name") or provider["name"]
        )
        self.db.set_setting("provider.model", provider.get("model"))
        self.db.set_setting("provider.api_base", provider.get("api_base"))
        self.db.set_setting(
            "provider.secret_name", provider.get("secret_name") or provider["name"]
        )

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
            return (
                self._oauth_attempts.get(provider) is attempt
                and not attempt.login.cancelled
            )

        async def _run_oauth() -> None:
            try:
                result = await asyncio.to_thread(attempt.login.run)
            except asyncio.CancelledError:
                attempt.login.cancel()
                await self._send(connection, {
                    "type": "error", "id": req_id,
                    "message": "Sign-in cancelled.",
                })
                return
            except Exception as e:
                attempt.login.discard()
                if attempt.login.cancelled or not is_current():
                    await self._send(connection, {
                        "type": "error", "id": req_id,
                        "message": "Sign-in cancelled.",
                    })
                    return
                logger.error("OAuth sign-in failed for {}: {}", provider, e)
                await self._send(connection, {
                    "type": "error", "id": req_id,
                    "message": str(e) if isinstance(e, ValueError) else
                               "Uh oh. That didn't go as planned. Try again?",
                    "detail": str(e),
                })
                return

            # Keep the final ownership check, token commit, and provider-state
            # writes await-free so another IPC cancel cannot interleave here.
            if not is_current() or not attempt.login.commit():
                attempt.login.discard()
                await self._send(connection, {
                    "type": "error", "id": req_id,
                    "message": "Sign-in cancelled.",
                })
                return
            if result.get("signed_in"):
                canonical = str(result.get("provider") or "")
                is_claude = canonical == "claude"
                provider_record = self.db.configure_provider_candidate_record(
                    f"oauth-{canonical}",
                    name="anthropic" if is_claude else "openai_codex",
                    auth_type="claude-oauth" if is_claude else "chatgpt-oauth",
                    model=(
                        "claude-sonnet-4-6"
                        if is_claude
                        else "openai-codex/gpt-5.4"
                    ),
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
                requested_generation is not None
                and int(requested_generation) != attempt.generation
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

    async def _cmd_auth_status(self, connection: ServerConnection, frame: dict) -> dict:
        from collie_core.providers import auth as collie_auth

        provider = str(frame.get("provider") or "")
        return await asyncio.to_thread(collie_auth.oauth_status, provider)

    async def _cmd_configure(self, connection: ServerConnection, frame: dict) -> dict:
        if self._on_configure is None:
            raise ValueError("configure is not available")
        return await self._on_configure()

    async def _cmd_get_profile(self, connection: ServerConnection, frame: dict) -> dict:
        return {"profile": self.db.all_profile()}

    async def _cmd_get_memory_journal(self, connection: ServerConnection, frame: dict) -> dict:
        """Recent memory mutations (Settings -> Memory -> Recent activity)."""
        limit = frame.get("limit")
        try:
            limit = int(limit) if limit is not None else 50
        except (TypeError, ValueError):
            limit = 50
        return {"entries": self.db.list_memory_journal(limit=max(1, min(limit, 500)))}

    async def _cmd_run_dream(self, connection: ServerConnection, frame: dict) -> dict:
        """Manual trigger: run one Dream consolidation pass now."""
        if self._dream_runner is None:
            raise ValueError("The memory review isn't available right now.")
        outcome = await self._dream_runner()
        return dict(outcome or {})

    async def _cmd_get_dream_history(self, connection: ServerConnection, frame: dict) -> dict:
        """Past Dream consolidations (memory_dream versions), newest first."""
        versions = await asyncio.to_thread(
            self.db.list_artifact_versions,
            artifact_type="memory_dream",
            limit=50,
        )
        return {"versions": versions}

    async def _cmd_run_gardener(self, connection: ServerConnection, frame: dict) -> dict:
        """Manual trigger: run one Gardener pass (evidence → suggestions)."""
        if self._gardener_runner is None:
            raise ValueError("The improvement suggestions aren't available right now.")
        outcome = await self._gardener_runner()
        return dict(outcome or {})

    async def _cmd_apply_gardener_suggestion(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        """Approve one suggestion: re-validate, apply, version (undoable)."""
        from collie_core.gardener.propose import ProposalValidationError
        from collie_core.gardener.runner import apply_suggestion
        from collie_core.versions import VersionStore

        suggestion = frame.get("suggestion")
        if not isinstance(suggestion, dict):
            raise ValueError("A suggestion is required to approve.")
        try:
            result = await asyncio.to_thread(
                apply_suggestion,
                workspace=collie_home() / "workspace",
                suggestion=suggestion,
                version_store=VersionStore(self.db),
                subagent_loader=self._subagent_loader,
            )
        except ProposalValidationError as exc:
            raise ValueError(str(exc)) from exc
        return result

    def _memory(self) -> Any:
        if self._profile_store is None:
            raise ValueError("memory is not available")
        return self._profile_store

    async def _cmd_set_profile_memory(self, connection: ServerConnection, frame: dict) -> dict:
        key = str(frame.get("key") or "").strip()
        value = str(frame.get("value") or "").strip()
        if not key:
            raise ValueError("A memory key is required")
        if value:
            self._memory().set(key, value)
        else:
            self._memory().delete(key)
        return {"profile": self._memory().all()}

    async def _cmd_delete_profile_memory(self, connection: ServerConnection, frame: dict) -> dict:
        key = str(frame.get("key") or "").strip()
        if not key:
            raise ValueError("A memory key is required")
        self._memory().delete(key)
        return {"profile": self._memory().all()}

    async def _cmd_add_person_memory(self, connection: ServerConnection, frame: dict) -> dict:
        fields = frame.get("fields") if isinstance(frame.get("fields"), dict) else {}
        name = str(fields.get("name") or "").strip()
        if not name:
            raise ValueError("A person's name is required")
        person = self._memory().add_person(name, **{
            key: value
            for key, value in fields.items()
            if key in _PERSON_FIELDS and value not in (None, "")
        })
        return {"person": person}

    async def _cmd_update_person_memory(self, connection: ServerConnection, frame: dict) -> dict:
        person_id = str(frame.get("person_id") or "").strip()
        fields = frame.get("fields") if isinstance(frame.get("fields"), dict) else {}
        if not person_id:
            raise ValueError("A person is required")
        self._memory().update_person(person_id, **fields)
        return {"person": self._memory().get_person(person_id)}

    async def _cmd_delete_person_memory(self, connection: ServerConnection, frame: dict) -> dict:
        person_id = str(frame.get("person_id") or "").strip()
        if not person_id:
            raise ValueError("A person is required")
        self._memory().delete_person(person_id)
        return {"deleted": True}

    async def _cmd_add_date_memory(self, connection: ServerConnection, frame: dict) -> dict:
        date = str(frame.get("date") or "").strip()
        label = str(frame.get("label") or "").strip()
        if not date or not label:
            raise ValueError("A date and label are required")
        entry = self._memory().add_date(
            date,
            label,
            recurring=bool(frame.get("recurring")),
        )
        return {"date": entry}

    async def _cmd_update_date_memory(self, connection: ServerConnection, frame: dict) -> dict:
        date_id = str(frame.get("date_id") or "").strip()
        fields = frame.get("fields") if isinstance(frame.get("fields"), dict) else {}
        if not date_id:
            raise ValueError("A date is required")
        self._memory().update_date(date_id, **fields)
        return {"dates": self._memory().list_dates()}

    async def _cmd_delete_date_memory(self, connection: ServerConnection, frame: dict) -> dict:
        date_id = str(frame.get("date_id") or "").strip()
        if not date_id:
            raise ValueError("A date is required")
        self._memory().delete_date(date_id)
        return {"deleted": True}

    # -- messengers (Settings -> Phone) -------------------------------------------

    def _messengers(self) -> Any:
        if self._messenger_manager is None:
            raise ValueError("messengers are not available")
        return self._messenger_manager

    async def _cmd_get_messengers(self, connection: ServerConnection, frame: dict) -> dict:
        return {"messengers": self._messengers().status()}

    async def _cmd_set_messenger(self, connection: ServerConnection, frame: dict) -> dict:
        from collie_core.messengers import MESSENGERS

        manager = self._messengers()
        name = str(frame.get("messenger") or "").lower()
        has_updates = "enabled" in frame or "deliver_automations" in frame
        if has_updates and name not in MESSENGERS:
            raise ValueError(f"unknown messenger: {name or '(none)'}")
        if "enabled" in frame:
            enabled = bool(frame.get("enabled"))
            manager.set_enabled(name, enabled)
            if not enabled:
                manager.clear_local_connection(name)
        if "deliver_automations" in frame:
            manager.set_deliver_automations(name, bool(frame.get("deliver_automations")))
        await manager.restart()
        return {"messengers": manager.status()}

    async def _cmd_set_messenger_secret(self, connection: ServerConnection, frame: dict) -> dict:
        manager = self._messengers()
        name = str(frame.get("messenger") or "").lower()
        key = str(frame.get("key") or "")
        value = str(frame.get("value") or "")
        if not name or not key:
            raise ValueError("set_messenger_secret requires 'messenger' and 'key'")
        if name == "telegram" and key == "token":
            if not value or ":" not in value:
                raise ValueError("That Telegram token doesn't look right. Copy it from @BotFather.")
            try:
                from telegram import Bot

                async with Bot(value) as bot:
                    await bot.get_me()
            except Exception as error:
                raise ValueError(
                    "Telegram didn't accept that token. Copy the latest token from @BotFather."
                ) from error
        manager.set_secret(name, key, value)
        return {"saved": True}

    async def _cmd_approve_pairing(self, connection: ServerConnection, frame: dict) -> dict:
        from nanobot.pairing import approve_code

        code = str(frame.get("code") or "").strip().upper()
        result = approve_code(code)
        if result is None:
            raise ValueError("That code didn't match — it may have expired. Try again?")
        channel, sender_id = result
        confirmed = await self._messengers().confirm_pairing(channel, sender_id)
        await self.broadcast({"type": "messenger_pairing", "messenger": channel})
        return {
            "approved": True,
            "messenger": channel,
            "sender_id": sender_id,
            "confirmed": confirmed,
        }

    async def _cmd_deny_pairing(self, connection: ServerConnection, frame: dict) -> dict:
        from nanobot.pairing import deny_code

        code = str(frame.get("code") or "").strip().upper()
        return {"denied": deny_code(code)}

    async def _cmd_revoke_messenger_sender(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        from nanobot.pairing import revoke

        name = str(frame.get("messenger") or "").lower()
        sender_id = str(frame.get("sender_id") or "")
        return {"revoked": revoke(name, sender_id)}

    async def _cmd_get_people(self, connection: ServerConnection, frame: dict) -> dict:
        return {"people": self.db.list_people()}

    async def _cmd_get_dates(self, connection: ServerConnection, frame: dict) -> dict:
        return {"dates": self.db.list_dates()}

    async def _cmd_stop(self, connection: ServerConnection, frame: dict) -> dict:
        conv_id = str(frame.get("conversation_id") or "")
        task = self._chat_tasks.get(conv_id)
        stopped = False
        turn_will_handle_stop = task is not None and not task.done()
        if turn_will_handle_stop:
            task.cancel()
            stopped = True
        cancelled_subagents = 0
        if self._conversation_canceler is not None and conv_id:
            cancelled_subagents = await self._conversation_canceler(conv_id)
        elif self._subagent_canceler is not None and conv_id:
            cancelled_subagents = await self._subagent_canceler(conv_id)
        cancelled_approvals = 0
        if self.approval_broker is not None and conv_id:
            cancelled_approvals = await self.approval_broker.cancel_conversation(conv_id)
        active = self.db.get_active_task(conv_id) if conv_id and not turn_will_handle_stop else None
        if active is not None and active.get("source") == "checklist":
            try:
                cancelled = self.db.cancel_task_checklist(
                    str(active["id"]),
                    expected_revision=int(active["revision"]),
                    reason="Stopped by the user.",
                )
                await self._broadcast_task_state(cancelled)
                stopped = True
            except ValueError:
                # The model may have finished the checklist between the stop
                # request and this compare-and-set read.
                pass
        return {
            "stopped": stopped or cancelled_subagents > 0 or cancelled_approvals > 0,
            "cancelled_subagents": cancelled_subagents,
            "cancelled_approvals": cancelled_approvals,
        }

    async def _cmd_steer(self, connection: ServerConnection, frame: dict) -> dict:
        conv_id = str(frame.get("conversation_id") or "")
        content = str(frame.get("content") or "").strip()
        if not conv_id or not content:
            raise ValueError("conversation_id and content are required")
        if self._chat_steerer is None:
            raise ValueError("mid-turn steering is not available")
        accepted = await self._chat_steerer(conv_id, content)
        if not accepted:
            raise ValueError("That task just finished. Send again to start a new turn.")
        user_message = self.db.add_message(conv_id, "user", content)
        await self.broadcast({
            "type": "message",
            "conversation_id": conv_id,
            "message": user_message,
        })
        return {"accepted": True}

    def _resolve_workspace_path(self, path: str) -> Path:
        """Resolve a client-supplied path strictly inside ~/.collie/workspace.

        On Windows ``Path("/Windows")`` is not absolute, ``Path("C:secret")``
        is drive-relative, and ``Path.resolve()`` on an unreachable UNC share
        blocks on a network lookup — so no ``is_absolute()``/``".."``/``resolve``
        shortcut is safe. Drive/rooted/UNC inputs are rejected outright, the
        rest is normalized as pure strings, and an existing candidate is
        re-checked against its resolved (symlink-following) location.
        """
        if not path:
            raise ValueError("Invalid path")
        raw = Path(path)
        if raw.drive or raw.is_absolute() or path.startswith(("\\\\", "//")):
            raise ValueError("Invalid path")
        workspace = Path(os.path.normpath(collie_home() / "workspace"))
        candidate = Path(os.path.normpath(workspace / path))
        if not candidate.is_relative_to(workspace):
            raise ValueError("Invalid path")
        if candidate.exists():
            final = candidate.resolve()
            if final.drive != workspace.drive or not final.is_relative_to(workspace):
                raise ValueError("Invalid path")
            return final
        return candidate

    async def _cmd_read_file(self, connection: ServerConnection, frame: dict) -> dict:
        file_path = self._resolve_workspace_path(str(frame.get("path") or ""))
        if not file_path.exists():
            return {"content": ""}
        return {"content": file_path.read_text(encoding="utf-8")}

    async def _cmd_write_file(self, connection: ServerConnection, frame: dict) -> dict:
        file_path = self._resolve_workspace_path(str(frame.get("path") or ""))
        content = str(frame.get("content") or "")
        file_path.parent.mkdir(parents=True, exist_ok=True)
        before = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
        artifact = self._classify_workspace_artifact(file_path)
        version_id: str | None = None
        diff_text: str | None = None
        if artifact is not None and before != content:
            from collie_core.versions import VersionStore, make_diff

            version_id = VersionStore(self.db).snapshot(
                artifact[0], artifact[1], before, content, source="user"
            )
            if version_id is not None:
                diff_text = make_diff(before, content, artifact[1])
        file_path.write_text(content, encoding="utf-8")
        return {
            "saved": True,
            "version_id": version_id,
            "diff_text": diff_text,
        }

    def _classify_workspace_artifact(self, file_path: Path) -> tuple[str, str] | None:
        """Map a workspace file to a versioned artifact type, if it is one."""
        workspace = Path(os.path.normpath(collie_home() / "workspace"))
        try:
            rel = file_path.relative_to(workspace)
        except ValueError:
            return None
        parts = rel.parts
        if parts == ("VISION.md",):
            return ("vision", "VISION.md")
        if parts == ("AGENTS.md",):
            return ("agents", "AGENTS.md")
        if parts == ("MEMORY.md",):
            return ("memory_profile", "MEMORY.md")
        if parts == ("memory", "MEMORY.md"):
            return ("memory_dream", "MEMORY.md")
        if len(parts) == 2 and parts[0] == "subagents" and parts[1].endswith(".md"):
            return ("subagent", parts[1])
        return None

    def _artifact_target(self, artifact_type: str, key: str) -> Path:
        """Resolve an artifact type+key to its workspace path (strict)."""
        if not key or "/" in key or "\\" in key or key in (".", ".."):
            raise ValueError("Invalid artifact key")
        workspace = Path(os.path.normpath(collie_home() / "workspace"))
        if artifact_type == "subagent":
            target = workspace / "subagents" / key
        elif artifact_type == "memory_dream":
            target = workspace / "memory" / key
        elif artifact_type in ("vision", "agents", "memory_profile", "skill"):
            target = workspace / key
        else:
            raise ValueError(f"Unknown artifact type: {artifact_type}")
        if not target.is_relative_to(workspace):
            raise ValueError("Invalid artifact path")
        return target

    async def _cmd_list_versions(self, connection: ServerConnection, frame: dict) -> dict:
        """List artifact versions (most recent first) — read-only rollback rail."""
        artifact_type = str(frame.get("artifact_type") or "") or None
        artifact_key = str(frame.get("artifact_key") or "") or None
        limit = frame.get("limit")
        try:
            limit = int(limit) if limit is not None else 100
        except (TypeError, ValueError):
            limit = 100
        versions = await asyncio.to_thread(
            self.db.list_artifact_versions,
            artifact_type=artifact_type,
            artifact_key=artifact_key,
            limit=max(1, min(limit, 500)),
        )
        return {"versions": versions}

    async def _cmd_rollback_artifact(self, connection: ServerConnection, frame: dict) -> dict:
        """Undo one artifact version (no-clobber guarded) and re-sync state."""
        from collie_core.versions import VersionConflictError, VersionStore

        version_id = str(frame.get("version_id") or "")
        row = self.db.get_artifact_version(version_id)
        if row is None:
            raise ValueError("I can't find that change — it may have been cleaned up.")
        artifact_type = str(row["artifact_type"])
        key = str(row["artifact_key"])
        target = self._artifact_target(artifact_type, key)
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        try:
            result = VersionStore(self.db).rollback(
                artifact_type,
                key,
                to_version=int(row["version"]),
                current_text=current,
            )
        except VersionConflictError as exc:
            raise ValueError(str(exc)) from exc
        restored = result["restored_text"]
        if restored:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(restored, encoding="utf-8")
        elif target.exists():
            target.unlink()
        # A subagent rollback also restores the database row (or renames it
        # back): the loader reconciles disk -> DB.
        if artifact_type == "subagent" and self._subagent_loader is not None:
            await asyncio.to_thread(self._subagent_loader.sync)
        return {
            "rolled_back": True,
            "version_id": result["version_id"],
            "artifact_type": artifact_type,
            "artifact_key": key,
            "version": result["version"],
        }

    async def _cmd_list_automations(self, connection: ServerConnection, frame: dict) -> dict:
        return {"automations": self.db.list_automations()}

    async def _cmd_toggle_automation(self, connection: ServerConnection, frame: dict) -> dict:
        auto_id = str(frame.get("automation_id") or "")
        enabled = bool(frame.get("enabled"))
        self.db.toggle_automation(auto_id, enabled)
        return {"toggled": True}

    async def _cmd_create_automation(self, connection: ServerConnection, frame: dict) -> dict:
        from collie_core.automations.custom import create_custom_automation

        row = create_custom_automation(
            self.db,
            str(frame.get("description") or ""),
            name=(str(frame["name"]) if frame.get("name") else None),
            timezone_name=str(frame.get("timezone") or "UTC"),
        )
        return {"automation": row}

    async def _cmd_delete_automation(self, connection: ServerConnection, frame: dict) -> dict:
        auto_id = str(frame.get("automation_id") or "")
        if auto_id.startswith("collie-"):
            raise ValueError(
                "That one's built in — flip it off instead of deleting it!"
            )
        self.db.delete_automation(auto_id)
        return {"deleted": True}

    # -- routines, plans, runs, and approvals ----------------------------------------

    async def _cmd_list_routines(self, connection: ServerConnection, frame: dict) -> dict:
        return {"routines": self.db.list_automations()}

    async def _cmd_create_routine(self, connection: ServerConnection, frame: dict) -> dict:
        from datetime import datetime, timezone

        from collie_core.routines.schedule import next_occurrence, parse_schedule

        plan_id = str(frame.get("plan_id") or "")
        version = int(frame.get("version") or 0)
        plan = self.db.approve_plan(
            plan_id, version, str(frame.get("plan_hash") or "")
        )
        zone = str(frame.get("timezone") or "UTC")
        schedule = parse_schedule(str(frame.get("schedule_description") or ""), zone)
        upcoming = next_occurrence(schedule, datetime.now(timezone.utc))
        routine = self.db.add_automation(
            str(frame.get("name") or plan["title"]),
            description=str(plan["goal"]),
            schedule=schedule.time.strftime("%H:%M"),
            action_type="approved_plan",
            action_config={"plan_id": plan_id, "plan_version": version},
            enabled=True,
            timezone_name=zone,
            schedule_json=schedule.to_dict(),
            next_run_at=upcoming.isoformat(timespec="seconds") if upcoming else None,
            plan_id=plan_id,
            plan_version=version,
        )
        self.db.attach_plan_to_routine(plan_id, version, str(routine["id"]))
        await self.broadcast({"type": "routine_updated", "routine": routine})
        return {"routine": routine}

    async def _cmd_get_routine(self, connection: ServerConnection, frame: dict) -> dict:
        row = self.db.get_automation(str(frame.get("routine_id") or ""))
        if row is None:
            raise ValueError("routine not found")
        return {"routine": row}

    async def _cmd_update_routine(self, connection: ServerConnection, frame: dict) -> dict:
        from datetime import datetime, timezone

        from collie_core.routines.schedule import next_occurrence, parse_schedule

        routine_id = str(frame.get("routine_id") or "")
        updates = dict(frame.get("updates") or {})
        description = updates.pop("schedule_description", None)
        if description is not None:
            zone = str(updates.get("timezone") or frame.get("timezone") or "UTC")
            schedule = parse_schedule(str(description), zone)
            upcoming = next_occurrence(schedule, datetime.now(timezone.utc))
            updates.update(
                {
                    "schedule_json": schedule.to_dict(),
                    "timezone": zone,
                    "next_run_at": (
                        upcoming.isoformat(timespec="seconds") if upcoming else None
                    ),
                }
            )
        return {"routine": self.db.update_automation(routine_id, **updates)}

    async def _cmd_pause_routine(self, connection: ServerConnection, frame: dict) -> dict:
        routine_id = str(frame.get("routine_id") or "")
        self.db.toggle_automation(routine_id, False)
        return {"routine": self.db.get_automation(routine_id)}

    async def _cmd_resume_routine(self, connection: ServerConnection, frame: dict) -> dict:
        routine_id = str(frame.get("routine_id") or "")
        row = self.db.get_automation(routine_id)
        if row is None:
            raise ValueError("routine not found")
        if (
            row.get("action_type") == "approved_plan"
            and (not row.get("plan_id") or not row.get("plan_version"))
        ):
            raise ValueError("Review and approve this routine's plan before enabling it.")
        self.db.toggle_automation(routine_id, True)
        return {"routine": self.db.get_automation(routine_id)}

    async def _cmd_delete_routine(self, connection: ServerConnection, frame: dict) -> dict:
        return await self._cmd_delete_automation(
            connection, {"automation_id": frame.get("routine_id")}
        )

    async def _cmd_run_routine_now(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        routine_id = str(frame.get("routine_id") or "")
        row = self.db.get_automation(routine_id)
        if row is None:
            raise ValueError("routine not found")
        plan = None
        if row.get("action_type") == "approved_plan":
            if not row.get("plan_id") or not row.get("plan_version"):
                raise ValueError("This routine needs an approved plan first.")
            plan = self.db.get_plan(str(row["plan_id"]), int(row["plan_version"]))
            if plan is None or plan.get("status") != "approved":
                raise ValueError("This routine's plan changed and needs review.")
        conv_key = f"automations.{routine_id}.conversation_id"
        conv_id = str(self.db.get_setting(conv_key, "") or "")
        if not conv_id or self.db.get_conversation(conv_id) is None:
            conv_id = str(self.db.create_conversation(f"Routine: {row['name']}")["id"])
            self.db.set_setting(conv_key, conv_id)
        if conv_id in self._chat_tasks and not self._chat_tasks[conv_id].done():
            raise ValueError("This routine is already running.")
        run = self.db.create_run(
            trigger_type="manual",
            idempotency_key=f"manual:{routine_id}:{uuid.uuid4().hex}",
            plan_id=row.get("plan_id"),
            plan_version=row.get("plan_version"),
            routine_id=routine_id,
            conversation_id=conv_id,
        )
        if row.get("action_type") != "approved_plan":
            action_config = row.get("action_config")
            if isinstance(action_config, str):
                try:
                    action_config = json.loads(action_config)
                except (TypeError, json.JSONDecodeError):
                    action_config = {}
            instruction = str(
                action_config.get("prompt") if isinstance(action_config, dict) else ""
            ).strip()
            if not instruction:
                raise ValueError("This routine has no instruction to run.")
        else:
            instruction = (
                "Execute this approved routine plan sequentially. Do not take material "
                f"actions outside it. Verify the result.\n\n{plan['plan_json']}"
            )
        task = asyncio.create_task(
            self._run_chat_turn(
                conv_id,
                instruction,
                execution_mode="execute",
                run_id=str(run["id"]),
                plan_id=str(row["plan_id"]) if row.get("plan_id") else None,
                plan_version=int(row["plan_version"]) if row.get("plan_version") else None,
            )
        )
        self._chat_tasks[conv_id] = task
        task.add_done_callback(lambda _task, cid=conv_id: self._chat_tasks.pop(cid, None))
        return {"run": run}

    async def _cmd_test_routine(self, connection: ServerConnection, frame: dict) -> dict:
        row = self.db.get_automation(str(frame.get("routine_id") or ""))
        if row is None:
            raise ValueError("routine not found")
        return {
            "safe": bool(row.get("plan_id") and row.get("plan_version")),
            "services_available": True,
            "side_effects_performed": False,
        }

    async def _cmd_list_routine_runs(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        return {
            "runs": self.db.list_runs(
                routine_id=str(frame.get("routine_id") or ""),
                limit=int(frame.get("limit") or 100),
            )
        }

    async def _cmd_retry_routine_run(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        previous = self.db.get_run(str(frame.get("run_id") or ""))
        if previous is None or previous.get("status") != "failed":
            raise ValueError("Only a failed run can be retried.")
        plan = self.db.get_plan(
            str(previous.get("plan_id") or ""),
            int(previous.get("plan_version") or 0),
        )
        if plan is None or plan.get("status") != "approved":
            raise ValueError("The plan changed and needs review before retrying.")
        conv_id = str(previous.get("conversation_id") or "")
        if not conv_id or self.db.get_conversation(conv_id) is None:
            conv_id = str(self.db.create_conversation("Retried routine")["id"])
        if conv_id in self._chat_tasks and not self._chat_tasks[conv_id].done():
            raise ValueError("This routine is already running.")
        run = self.db.create_run(
            trigger_type="retry",
            idempotency_key=f"retry:{previous['id']}:{uuid.uuid4().hex}",
            plan_id=previous.get("plan_id"),
            plan_version=previous.get("plan_version"),
            routine_id=previous.get("routine_id"),
            conversation_id=conv_id,
        )
        instruction = (
            "Retry this approved routine plan sequentially. Do not take material "
            f"actions outside it. Verify the result.\n\n{plan['plan_json']}"
        )
        task = asyncio.create_task(
            self._run_chat_turn(
                conv_id,
                instruction,
                execution_mode="execute",
                run_id=str(run["id"]),
                plan_id=str(previous["plan_id"]),
                plan_version=int(previous["plan_version"]),
            )
        )
        self._chat_tasks[conv_id] = task
        task.add_done_callback(lambda _task, cid=conv_id: self._chat_tasks.pop(cid, None))
        return {"run": run}

    async def _cmd_create_plan(self, connection: ServerConnection, frame: dict) -> dict:
        from collie_core.plans.models import validate_plan

        plan = validate_plan(frame.get("plan"))
        row = self.db.create_plan(
            title=plan["title"],
            goal=plan["goal"],
            plan=plan,
            conversation_id=str(frame.get("conversation_id") or "") or None,
            routine_id=str(frame.get("routine_id") or "") or None,
            plan_id=str(frame.get("plan_id") or "") or None,
        )
        await self.broadcast({"type": "plan_updated", "plan": row})
        return {"plan": row}

    async def _cmd_get_plan(self, connection: ServerConnection, frame: dict) -> dict:
        version = frame.get("version")
        row = self.db.get_plan(
            str(frame.get("plan_id") or ""),
            int(version) if version is not None else None,
        )
        if row is None:
            raise ValueError("plan not found")
        return {"plan": row}

    def _require_plan_execution_ready(self) -> None:
        if self._chat_runner is None:
            raise ValueError("I'm not hooked up to a model yet. Add a provider in Settings.")
        if self._status_provider is None:
            return
        try:
            status = self._status_provider()
        except Exception as exc:
            raise ValueError("I couldn't verify that the model is ready yet.") from exc
        if status.get("configured") is False:
            raise ValueError("I'm not hooked up to a model yet. Add a provider in Settings.")

    def _validate_plan_execution_request(
        self, plan_id: str, version: int, plan_hash: str
    ) -> dict[str, Any]:
        plan = self.db.get_plan(plan_id, version)
        if plan is None:
            raise ValueError("plan not found")
        if plan.get("plan_hash") != plan_hash:
            raise ValueError("plan changed; review the new version before approving")
        if plan.get("status") == "superseded":
            raise ValueError("this plan version has been superseded")
        if plan.get("status") not in {"draft", "approved"}:
            raise ValueError("this plan version is not reviewable")
        conversation_id = str(plan.get("conversation_id") or "")
        if not conversation_id:
            raise ValueError("This plan has no conversation to run in.")
        if self.db.get_conversation(conversation_id) is None:
            raise ValueError("This plan's conversation no longer exists.")
        return plan

    def _start_plan_execution_task(
        self, conversation_id: str, plan: dict[str, Any], run: dict[str, Any]
    ) -> asyncio.Task:
        coroutine = self._run_chat_turn(
            conversation_id,
            "Execute the following approved, immutable plan sequentially. "
            "Do not take material actions absent from it. Verify each step before "
            "declaring success.\n\n"
            f"{plan['plan_json']}",
            execution_mode="execute",
            run_id=str(run["id"]),
            plan_id=str(plan["id"]),
            plan_version=int(plan["version"]),
        )
        try:
            return asyncio.create_task(coroutine)
        except BaseException:
            coroutine.close()
            raise

    async def _launch_claimed_plan_execution(
        self, plan: dict[str, Any], run: dict[str, Any]
    ) -> None:
        conversation_id = str(run["conversation_id"])
        try:
            task = self._start_plan_execution_task(conversation_id, plan, run)
        except Exception as exc:
            failed = self.db.transition_run(
                str(run["id"]),
                "failed",
                error_code="task_start_failed",
                error_message=str(exc)[:1000],
            )
            await self.broadcast({"type": "run_failed", "run": failed})
            await self._broadcast_task_state(self.db.get_run_task(str(run["id"])))
            raise ValueError("The plan was approved, but its execution couldn't start.") from exc
        self._chat_tasks[conversation_id] = task
        task.add_done_callback(
            lambda _task, cid=conversation_id: self._chat_tasks.pop(cid, None)
        )

    async def _cmd_approve_plan(self, connection: ServerConnection, frame: dict) -> dict:
        plan_id = str(frame.get("plan_id") or "")
        version = int(frame.get("version") or 0)
        plan_hash = str(frame.get("plan_hash") or "")
        plan = self._validate_plan_execution_request(plan_id, version, plan_hash)
        self._require_plan_execution_ready()
        conv_id = str(plan["conversation_id"])

        # A duplicate is an idempotent read. Check it before the active-turn
        # guard so the losing half of a rapid double approval receives the
        # original run instead of a misleading busy error.
        key = f"plan:{plan_id}:v{version}"
        existing_run = self.db.get_run_by_idempotency_key(key)
        if existing_run is not None:
            claim = self.db.claim_plan_execution(plan_id, version, plan_hash)
            await self._publish_superseded_checklist(claim.get("superseded_checklist"))
            return {
                "plan": claim["plan"],
                "run": claim["run"],
                "created": False,
            }

        existing_task = self._chat_tasks.get(conv_id)
        if existing_task is not None and not existing_task.done():
            raise ValueError("Finish the current turn before executing this plan.")
        claim = self.db.claim_plan_execution(plan_id, version, plan_hash)
        plan = claim["plan"]
        run = claim["run"]
        await self._publish_superseded_checklist(claim.get("superseded_checklist"))
        if claim["created"]:
            await self._launch_claimed_plan_execution(plan, run)
            await self.broadcast({"type": "plan_updated", "plan": plan})
        return {"plan": plan, "run": run, "created": bool(claim["created"])}

    async def _cmd_change_plan(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        conversation_id = str(frame.get("conversation_id") or "")
        run_id = str(frame.get("run_id") or "")
        if not conversation_id or not run_id:
            raise ValueError("Choose an active plan run to change.")
        request = self.db.request_plan_change(
            run_id,
            conversation_id=conversation_id,
            reason=str(frame.get("reason") or "Plan change requested by the user."),
        )
        self.db.set_conversation_mode(conversation_id, "plan")
        if self._active_material_runs.get(run_id, 0) > 0:
            status = "pending_safe_boundary"
            await self.broadcast(
                {
                    "type": "plan_change_requested",
                    "conversation_id": conversation_id,
                    "run_id": run_id,
                    "status": status,
                }
            )
        else:
            await self._finalize_requested_plan_change(run_id)
            status = "cancelled"
        return {
            "requested": True,
            "conversation_id": conversation_id,
            "run_id": run_id,
            "plan_id": request["plan_id"],
            "version": request["plan_version"],
            "plan_version": request["plan_version"],
            "execution_mode": "plan",
            "status": status,
        }

    async def _cmd_retry_plan_execution(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        run_id = str(frame.get("run_id") or "")
        previous = self.db.get_run(run_id)
        if previous is None:
            raise ValueError("run not found")
        plan = self.db.get_plan(
            str(previous.get("plan_id") or ""),
            int(previous.get("plan_version") or 0),
        )
        if plan is None or plan.get("status") != "approved":
            raise ValueError("The plan changed and needs review before retrying.")
        conversation_id = str(previous.get("conversation_id") or "")
        if not conversation_id or self.db.get_conversation(conversation_id) is None:
            raise ValueError("This plan's conversation no longer exists.")
        self._require_plan_execution_ready()
        existing_task = self._chat_tasks.get(conversation_id)
        if existing_task is not None and not existing_task.done():
            raise ValueError("Finish the current turn before retrying this plan.")

        retry = self.db.requeue_failed_plan_execution(run_id)
        await self._launch_claimed_plan_execution(retry["plan"], retry["run"])
        return {"plan": retry["plan"], "run": retry["run"]}

    async def _cmd_list_pending_approvals(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        return {"approvals": self.db.list_pending_approvals()}

    async def _cmd_resolve_approval(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self.approval_broker is None:
            raise ValueError("approvals are not available")
        approval = await self.approval_broker.resolve(
            str(frame.get("approval_id") or ""),
            str(frame.get("resolution") or ""),
            scope_type=str(frame.get("scope_type") or "") or None,
            scope_value=str(frame.get("scope_value") or "") or None,
        )
        return {"approval": approval}

    async def _cmd_list_approval_rules(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        return {"rules": self.db.list_approval_rules()}

    async def _cmd_delete_approval_rule(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        self.db.delete_approval_rule(str(frame.get("rule_id") or ""))
        return {"deleted": True}

    async def _cmd_set_approval_preset(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        preset = str(frame.get("preset") or "")
        if preset not in {"ask", "allow"}:
            raise ValueError("preset must be 'ask' or 'allow'")
        self.db.set_setting("permissions.local_write_preset", preset)
        if self._on_set_approval_preset is not None:
            self._on_set_approval_preset(preset)
        return {"preset": preset}

    async def _cmd_approve_all_for_run(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        run_id = str(frame.get("run_id") or "")
        if not run_id or self.db.get_run(run_id) is None:
            raise ValueError("run not found")
        rule = self.db.add_approval_rule(
            action="*",
            resource_pattern="*",
            effect="allow",
            scope_type="run",
            scope_value=run_id,
        )
        return {"rule": rule}

    async def _cmd_list_subagents(self, connection: ServerConnection, frame: dict) -> dict:
        from collie_core.subagents.loader import STARTERS

        if self._subagent_loader is not None:
            subagents = self._subagent_loader.sync()
        else:
            subagents = self.db.list_subagents()
        return {"subagents": subagents, "starters": list(STARTERS)}

    async def _cmd_create_subagent(self, connection: ServerConnection, frame: dict) -> dict:
        from collie_core.subagents.loader import draft_system_prompt

        if self._subagent_loader is None:
            raise ValueError("subagents aren't available yet")
        name = str(frame.get("name") or "").strip()
        description = str(frame.get("description") or "").strip()
        system_prompt = str(frame.get("system_prompt") or "").strip()
        execution_posture = str(
            frame.get("execution_posture") or "read_only"
        ).strip()
        if execution_posture not in {"read_only", "inherit"}:
            execution_posture = "read_only"
        if not name:
            raise ValueError("Every helper needs a name!")
        generated = False
        if not system_prompt:
            if self._prompt_writer is not None:
                try:
                    system_prompt = (await self._prompt_writer(name, description)).strip()
                    generated = bool(system_prompt)
                except Exception:
                    logger.exception("LLM prompt writing failed; using template")
            if not system_prompt:
                system_prompt = draft_system_prompt(name, description)
        row = self._subagent_loader.create(
            name,
            description=description,
            system_prompt=system_prompt,
            execution_posture=execution_posture,
        )
        return {"subagent": row, "prompt_written_by_collie": generated}

    async def _cmd_update_subagent(self, connection: ServerConnection, frame: dict) -> dict:
        if self._subagent_loader is None:
            raise ValueError("subagents aren't available yet")
        row = self._subagent_loader.update(
            str(frame.get("subagent_id") or ""),
            name=frame.get("name"),
            description=frame.get("description"),
            system_prompt=frame.get("system_prompt"),
            execution_posture=frame.get("execution_posture"),
        )
        return {"subagent": row}

    async def _cmd_delete_subagent(self, connection: ServerConnection, frame: dict) -> dict:
        if self._subagent_loader is None:
            raise ValueError("subagents aren't available yet")
        self._subagent_loader.delete(str(frame.get("subagent_id") or ""))
        return {"deleted": True}

    async def _cmd_cancel_subagent(self, connection: ServerConnection, frame: dict) -> dict:
        conversation_id = str(frame.get("conversation_id") or "")
        if not conversation_id:
            raise ValueError("conversation_id is required")
        if self._subagent_canceler is None:
            raise ValueError("subagent cancellation is not available")
        count = await self._subagent_canceler(conversation_id)
        return {"cancelled": count}

    # -- connectors ---------------------------------------------------------------------

    async def _cmd_list_connector_catalog(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self._service_manager is None:
            return {"connectors": []}
        return {"connectors": self._service_manager.catalog_view()}

    async def _cmd_list_connector_connections(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self._service_manager is None:
            return {"connections": []}
        return {"connections": self._service_manager.list_connections()}

    async def _cmd_get_connector(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self._service_manager is None:
            raise ValueError("connectors aren't available yet")
        connection_id = str(frame.get("connection_id") or "")
        item = self._service_manager.get_connection(connection_id)
        if item is None:
            raise ValueError("I couldn't find that connection.")
        return {"connection": item}

    async def _cmd_begin_connector_auth(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self._service_manager is None:
            raise ValueError("connectors aren't available yet")
        provider_id = str(frame.get("provider_id") or "")
        origin = str(frame.get("origin") or "connectors_ui")
        connection_id = f"con_{uuid.uuid4().hex}"
        flow_id = f"caf_{uuid.uuid4().hex}"
        replace_connection_id = (
            str(frame.get("replace_connection_id") or "") or None
        )
        await self.broadcast(
            {
                "type": "connector_auth_started",
                "provider_id": provider_id,
                "connection_id": connection_id,
                "flow_id": flow_id,
                "origin": origin,
                "status": "authorizing",
            }
        )
        try:
            result = await asyncio.to_thread(
                self._service_manager.connect,
                provider_id,
                None,
                origin=origin,
                replace_connection_id=replace_connection_id,
                connection_id=connection_id,
            )
        except Exception as error:
            await self.broadcast(
                {
                    "type": "connector_failed",
                    "provider_id": provider_id,
                    "origin": origin,
                    "message": str(error),
                }
            )
            raise
        result["reconfigured"] = await self._reconfigure_quietly()
        await self.broadcast({"type": "connector_connected", **result})
        result["flow_id"] = flow_id
        return result

    async def _cmd_cancel_connector_auth(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self._service_manager is None:
            raise ValueError("connectors aren't available yet")
        result = self._service_manager.cancel_auth(
            str(frame.get("connection_id") or "")
        )
        if result["cancelled"]:
            await self.broadcast(
                {
                    "type": "connector_failed",
                    "connection_id": result["connection_id"],
                    "status": "failed",
                    "message": "Sign-in was cancelled. Nothing was connected.",
                }
            )
        return result

    async def _cmd_test_connector(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self._service_manager is None:
            raise ValueError("connectors aren't available yet")
        connection_id = str(frame.get("connection_id") or "")
        await self.broadcast(
            {
                "type": "connector_status_changed",
                "connection_id": connection_id,
                "status": "testing",
            }
        )
        item = await asyncio.to_thread(self._service_manager.test, connection_id)
        await self.broadcast(
            {
                "type": "connector_status_changed",
                "connection_id": connection_id,
                "status": item["status"],
            }
        )
        return {"connection": item}

    async def _cmd_update_connector(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self._service_manager is None:
            raise ValueError("connectors aren't available yet")
        capabilities = frame.get("enabled_capabilities")
        if capabilities is not None and not isinstance(capabilities, list):
            raise ValueError("enabled_capabilities must be a list")
        item = self._service_manager.update(
            str(frame.get("connection_id") or ""),
            display_name=(
                str(frame["display_name"]) if "display_name" in frame else None
            ),
            enabled_capabilities=capabilities,
            approval_preference=(
                str(frame["approval_preference"])
                if "approval_preference" in frame
                else None
            ),
        )
        await self.broadcast(
            {
                "type": "connector_status_changed",
                "connection_id": item["id"],
                "status": item["status"],
            }
        )
        return {"connection": item}

    async def _cmd_remove_connector(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        if self._service_manager is None:
            raise ValueError("connectors aren't available yet")
        result = self._service_manager.remove(
            str(frame.get("connection_id") or ""),
            origin=str(frame.get("origin") or "connectors_ui"),
        )
        result["reconfigured"] = await self._reconfigure_quietly()
        await self.broadcast({"type": "connector_removed", **result})
        return result

    async def _cmd_list_connector_tools(
        self, connection: ServerConnection, frame: dict
    ) -> dict:
        connection_id = str(frame.get("connection_id") or "")
        return {"tools": self.db.list_connector_tools(connection_id)}

    # -- services compatibility aliases ------------------------------------

    async def _cmd_list_services(self, connection: ServerConnection, frame: dict) -> dict:
        if self._service_manager is None:
            return {"services": []}
        view = getattr(self._service_manager, "legacy_catalog_view", None)
        return {
            "services": (
                view() if callable(view) else self._service_manager.catalog_view()
            )
        }

    async def _cmd_connect_service(self, connection: ServerConnection, frame: dict) -> dict:
        if self._service_manager is None:
            raise ValueError("services aren't available yet")
        service_id = str(frame.get("service_id") or "")
        credentials = frame.get("credentials")
        if credentials is not None and not isinstance(credentials, dict):
            raise ValueError("credentials must be an object")
        result = await asyncio.to_thread(
            self._service_manager.connect, service_id, credentials
        )
        result["reconfigured"] = await self._reconfigure_quietly()
        return result

    async def _cmd_disconnect_service(self, connection: ServerConnection, frame: dict) -> dict:
        if self._service_manager is None:
            raise ValueError("services aren't available yet")
        service_id = str(frame.get("service_id") or "")
        result = self._service_manager.disconnect(service_id)
        result["reconfigured"] = await self._reconfigure_quietly()
        return result

    async def _reconfigure_quietly(self) -> bool:
        """Rebuild the agent after a service change; never fail the command."""
        if self._on_configure is None:
            return False
        try:
            outcome = await self._on_configure()
            return bool(outcome.get("configured"))
        except Exception:
            logger.exception("Reconfigure after service change failed")
            return False

    # -- chat -------------------------------------------------------------------------------

    async def _cmd_chat(self, connection: ServerConnection, frame: dict) -> None:
        content = str(frame.get("content") or "").strip()
        raw_attachments = frame.get("attachments") or []
        if not isinstance(raw_attachments, list):
            raw_attachments = []
        if not content and not raw_attachments:
            await self._send(connection, {
                "type": "error", "id": frame.get("id"),
                "message": "Say something and I'm on it!",
            })
            return
        media_paths: list[str] = []
        attachment_meta: list[dict[str, Any]] = []
        if raw_attachments:
            media_dir = collie_home() / "media" / "uploads"
            media_dir.mkdir(parents=True, exist_ok=True)
            media_paths, rejection = store_inbound_attachments(
                raw_attachments,
                media_dir=media_dir,
                logger=logger,
            )
            if rejection:
                await self._send(connection, {
                    "type": "error",
                    "id": frame.get("id"),
                    "message": _ATTACHMENT_ERRORS.get(rejection, "I could not attach that file."),
                })
                return
            for item, saved_path in zip(raw_attachments, media_paths, strict=False):
                metadata = {
                    "name": str(item.get("name") or Path(saved_path).name),
                    "mime": str(item.get("mime") or "application/octet-stream"),
                    "size": int(item.get("size") or 0),
                    "path": saved_path,
                }
                preview_data_url = _safe_preview_data_url(item)
                if preview_data_url is not None:
                    metadata["preview_data_url"] = preview_data_url
                attachment_meta.append(metadata)

        conv_id = str(frame.get("conversation_id") or "")
        project_path = str(frame.get("project_path") or "").strip() or None
        if project_path is not None:
            project = Path(project_path).expanduser()
            if (
                not project.is_absolute()
                or not project.is_dir()
                or project_path.startswith(("\\\\", "//"))
            ):
                await self._send(connection, {
                    "type": "error",
                    "id": frame.get("id"),
                    "message": "That project folder is no longer available.",
                })
                return
            project_path = str(project.resolve())
        conversation = self.db.get_conversation(conv_id) if conv_id else None
        selected_project_path = project_path or (
            conversation.get("project_path") if conversation is not None else None
        )
        file_access_scope: dict[str, Any] | None = None
        if "file_access_scope" in frame:
            raw_file_access_scope = frame.get("file_access_scope")
            try:
                roots, unrestricted = validate_local_file_access_scope_payload(
                    raw_file_access_scope,
                    selected_folder=selected_project_path,
                )
            except WorkspaceScopeError as exc:
                await self._send(connection, {
                    "type": "error",
                    "id": frame.get("id"),
                    "message": f"That file access choice is not available: {exc.message}",
                })
                return
            if unrestricted:
                file_access_scope = {"mode": "full_file_access"}
            else:
                raw_mode = str(raw_file_access_scope["mode"])
                file_access_scope = {"mode": raw_mode}
                if raw_mode == "chosen_folders":
                    file_access_scope["roots"] = [str(root) for root in roots]
        created_conversation = conversation is None
        if conversation is None:
            conversation = self.db.create_conversation(project_path=project_path)
            conv_id = conversation["id"]
            self.db.set_conversation_mode(conv_id, "execute")
            conversation["execution_mode"] = "execute"
        elif project_path and conversation.get("project_path") != project_path:
            self.db.set_conversation_project(conv_id, project_path)
            conversation["project_path"] = project_path
        else:
            project_path = conversation.get("project_path")

        command_result: dict[str, Any] | None = None
        agent_content = content
        message_metadata: dict[str, Any] | None = None
        session_key, command_origin = (
            self._session_target(conv_id)
            if self._session_target is not None
            else (desktop_session_key(conv_id), "desktop")
        )
        execution_mode = str(conversation.get("execution_mode") or "execute")
        if (
            self._command_runner is not None
            and not raw_attachments
            and self._command_requires_approval is not None
            and self._command_requires_approval(content)
        ):
            # Approval-gated commands (/model switch) await a user approval
            # that resolves over THIS socket. Run them in a background task
            # so the receive loop stays free to process resolve_approval —
            # awaiting inline would deadlock until the approval times out.
            user_msg = self.db.add_message(
                conv_id, "user", content, attachments=attachment_meta or None
            )
            await self._send(connection, {"type": "ok", "id": frame.get("id"), "data": {
                "conversation_id": conv_id,
                "message": user_msg,
                "command_handled": False,
            }})
            await self.broadcast({"type": "message", "conversation_id": conv_id,
                                  "message": user_msg})
            task = asyncio.create_task(
                self._run_approval_command_task(
                    conv_id,
                    content=content,
                    session_key=session_key,
                    origin=command_origin,
                    execution_mode=execution_mode,
                )
            )
            self._command_tasks.add(task)
            task.add_done_callback(self._command_tasks.discard)
            return
        if (
            self._command_runner is not None
            and not raw_attachments
        ):
            command_result = await self._command_runner(
                content,
                session_key=session_key,
                origin=command_origin,
                conversation_id=conv_id,
                execution_mode=execution_mode,
            )
            if command_result and command_result.get("new_conversation"):
                conversation = self.db.create_conversation(
                    title="New chat",
                    project_path=project_path,
                )
                conv_id = str(conversation["id"])
                # Keep the user's message in the fresh conversation (it was
                # sent to a conversation that no longer exists).
                user_echo = self.db.add_message(
                    conv_id, "user", content, attachments=attachment_meta or None
                )
                assistant_msg = self.db.add_message(
                    conv_id,
                    "assistant",
                    str(command_result.get("content") or ""),
                    card_type=command_result.get("card_type"),
                    card_data=command_result.get("card_data"),
                )
                await self._send(connection, {
                    "type": "ok",
                    "id": frame.get("id"),
                    "data": {
                        "conversation_id": conv_id,
                        "command_handled": True,
                    },
                })
                await self.broadcast({
                    "type": "message",
                    "conversation_id": conv_id,
                    "message": user_echo,
                })
                await self.broadcast({
                    "type": "message",
                    "conversation_id": conv_id,
                    "message": assistant_msg,
                })
                return
            if command_result and not command_result.get("handled"):
                agent_content = str(command_result.get("forward_prompt") or content)
                raw_message_metadata = command_result.get("message_metadata")
                if isinstance(raw_message_metadata, dict):
                    message_metadata = dict(raw_message_metadata)

        user_msg = self.db.add_message(
            conv_id,
            "user",
            content,
            attachments=attachment_meta or None,
        )
        if conversation.get("title") in (None, "", "New chat"):
            title_source = content or attachment_meta[0]["name"]
            title = _fallback_chat_title(title_source)
            self.db.rename_conversation(conv_id, title)
            if content and self._title_generator is not None:
                title_task = asyncio.create_task(
                    self._generate_conversation_title(conv_id, content, title)
                )
                self._background_tasks.add(title_task)
                title_task.add_done_callback(self._background_tasks.discard)

        await self._send(connection, {"type": "ok", "id": frame.get("id"), "data": {
            "conversation_id": conv_id,
            "message": user_msg,
            "command_handled": bool(command_result and command_result.get("handled")),
        }})
        await self.broadcast({"type": "message", "conversation_id": conv_id,
                              "message": user_msg})

        if command_result and command_result.get("handled"):
            assistant_msg = self.db.add_message(
                conv_id,
                "assistant",
                str(command_result.get("content") or ""),
                card_type=command_result.get("card_type"),
                card_data=command_result.get("card_data"),
            )
            await self.broadcast({
                "type": "message",
                "conversation_id": conv_id,
                "message": assistant_msg,
            })
            return

        if self._chat_runner is None:
            await self.broadcast({
                "type": "error",
                "conversation_id": conv_id,
                "message": "I'm not hooked up to a model yet. Add a provider in Settings.",
            })
            return

        if conv_id in self._chat_tasks and not self._chat_tasks[conv_id].done():
            await self._send(connection, {
                "type": "error", "id": frame.get("id"),
                "message": "One sec — still chewing on the last one!",
            })
            return

        mode = str(
            frame.get("execution_mode")
            or ("execute" if created_conversation else conversation.get("execution_mode"))
            or "execute"
        )
        self.db.set_conversation_mode(conv_id, mode)
        task = asyncio.create_task(
            self._run_chat_turn(
                conv_id,
                agent_content,
                media_paths,
                execution_mode=mode,
                project_path=project_path,
                file_access_scope=file_access_scope,
                message_metadata=message_metadata,
            )
        )
        self._chat_tasks[conv_id] = task
        task.add_done_callback(lambda t, c=conv_id: self._chat_tasks.pop(c, None))

    async def _run_approval_command_task(
        self,
        conv_id: str,
        *,
        content: str,
        session_key: str,
        origin: str,
        execution_mode: str,
    ) -> None:
        """Execute an approval-gated command outside the frame handler.

        The command's authorization awaits a user resolution that arrives as
        a frame on the same socket; running it inline would block the
        receive loop until the approval times out.
        """
        if self._command_runner is None:
            return
        try:
            result = await self._command_runner(
                content,
                session_key=session_key,
                origin=origin,
                conversation_id=conv_id,
                execution_mode=execution_mode,
            )
        except Exception:
            logger.exception("Approval-gated command failed")
            return
        if not result or not result.get("handled"):
            return
        assistant_msg = self.db.add_message(
            conv_id,
            "assistant",
            str(result.get("content") or ""),
            card_type=result.get("card_type"),
            card_data=result.get("card_data"),
        )
        await self.broadcast({
            "type": "message",
            "conversation_id": conv_id,
            "message": assistant_msg,
        })

    async def _generate_conversation_title(
        self,
        conversation_id: str,
        first_request: str,
        provisional_title: str,
    ) -> None:
        try:
            generated = await self._title_generator(first_request)
            title = " ".join(str(generated or "").strip().strip("\"'`").split())
            title = title[:48].rstrip(" .,:;!?-")
            current = self.db.get_conversation(conversation_id)
            if not title or current is None or current.get("title") != provisional_title:
                return
            self.db.rename_conversation(conversation_id, title)
            updated = self.db.get_conversation(conversation_id)
            await self.broadcast({
                "type": "conversation_updated",
                "conversation": updated,
            })
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("Automatic chat title generation failed")

    async def _finalize_plan_run_task(self, run_id: str) -> dict[str, Any] | None:
        """Finalize a reviewed run from persisted step truth and publish it."""
        task = self.db.get_run_task(run_id)
        if task is None:
            self.db.transition_run(run_id, "completed")
            await self.broadcast(
                {"type": "run_completed", "run": self.db.get_run(run_id)}
            )
            return None

        change = self.db.get_plan_change_request(run_id)
        if change is not None and str(change.get("status") or "") == "requested":
            await self._finalize_requested_plan_change(run_id)
            return None
        if change is not None and str(change.get("status") or "") in {
            "finalized",
            "replanned",
        }:
            await self._finalize_requested_plan_change(run_id)
            return None

        steps = self.db.list_run_steps(run_id)
        statuses = {str(step.get("status") or "") for step in steps}
        if "blocked" in statuses:
            self.db.transition_run(
                run_id,
                "failed",
                error_code="step_blocked",
                error_message="A planned outcome is blocked.",
            )
            await self.broadcast({"type": "run_failed", "run": self.db.get_run(run_id)})
        elif "failed" in statuses:
            self.db.transition_run(
                run_id,
                "failed",
                error_code="step_failed",
                error_message="A planned outcome failed.",
            )
            await self.broadcast({"type": "run_failed", "run": self.db.get_run(run_id)})
        elif steps and all(
            str(step.get("status") or "") in {"completed", "skipped"} for step in steps
        ):
            self.db.transition_run(run_id, "completed")
            await self.broadcast(
                {"type": "run_completed", "run": self.db.get_run(run_id)}
            )
        else:
            current = self.db.get_current_run_step(run_id)
            if current is not None:
                self.db.update_run_task_step(
                    run_id,
                    str(current["step_key"]),
                    status="failed",
                    error_message="The run ended before this outcome was finished.",
                )
                failed_step = next(
                    (
                        step
                        for step in self.db.list_run_steps(run_id)
                        if step["step_key"] == current["step_key"]
                    ),
                    None,
                )
                await self._broadcast_run_step(run_id, failed_step)
            self.db.transition_run(
                run_id,
                "failed",
                error_code="incomplete_plan",
                error_message="The run ended before every planned outcome was finished.",
            )
            await self.broadcast({"type": "run_failed", "run": self.db.get_run(run_id)})
        task = self.db.get_run_task(run_id)
        await self._broadcast_task_state(task)
        return task

    async def _run_chat_turn(
        self,
        conv_id: str,
        content: str,
        media: list[str] | None = None,
        *,
        execution_mode: str = "plan",
        run_id: str | None = None,
        plan_id: str | None = None,
        plan_version: int | None = None,
        project_path: str | None = None,
        file_access_scope: dict[str, Any] | None = None,
        message_metadata: dict[str, Any] | None = None,
    ) -> None:
        streamed = False
        parts: list[str] = []
        tool_results: list[str] = []
        turn_task_snapshot: dict[str, Any] | None = None

        async def on_stream(delta: str) -> None:
            nonlocal streamed
            if not streamed:
                streamed = True
                await self.send_thinking(conv_id, "generating")
            parts.append(delta)
            await self.broadcast({"type": "delta", "conversation_id": conv_id,
                                  "text": delta})

        async def on_superseded_response(content: str) -> None:
            """Deliver a complete answer that a mid-turn steer superseded.

            The runner keeps the superseded answer in model history but only
            emits the follow-up response as the turn's outbound; without this,
            the streamed text the user already watched never lands in the
            transcript (it only reappears if the agent re-quotes it later).
            Persist it as its own assistant message right away so it stays
            chronologically before the follow-up answer.
            """
            if not content:
                return
            superseded_msg = self.db.add_message(conv_id, "assistant", content)
            await self.broadcast({
                "type": "message",
                "conversation_id": conv_id,
                "message": superseded_msg,
            })

        async def finish_material_boundary() -> None:
            if not run_id:
                return
            remaining = max(0, self._active_material_runs.get(run_id, 0) - 1)
            if remaining:
                self._active_material_runs[run_id] = remaining
                return
            self._active_material_runs.pop(run_id, None)
            change = self.db.get_plan_change_request(run_id)
            if change is not None and str(change.get("status") or "") == "requested":
                await self._finalize_requested_plan_change(run_id)

        async def on_progress(text: str = "", **kwargs: Any) -> None:
            nonlocal turn_task_snapshot
            for event in kwargs.get("tool_events") or []:
                if not isinstance(event, dict):
                    continue
                phase = str(event.get("phase") or "")
                tool_name = str(event.get("name") or "")
                is_task_tool = tool_name == "manage_task_checklist"
                arguments = event.get("arguments")
                arguments = arguments if isinstance(arguments, dict) else {}
                permission = classify_tool(None, tool_name, arguments)
                is_material_tool = (
                    run_id is not None
                    and permission.risk != Risk.READ
                    and tool_name not in {"manage_task_checklist", "present_plan"}
                )
                if phase == "start":
                    await self.send_thinking(conv_id, thinking_state_for_tool(tool_name))
                    if is_material_tool and run_id:
                        self._active_material_runs[run_id] = (
                            self._active_material_runs.get(run_id, 0) + 1
                        )
                    if run_id and not is_task_tool:
                        current = self.db.get_current_run_step(run_id)
                        if current is not None:
                            updated = self.db.upsert_run_step(
                                run_id,
                                str(current["step_key"]),
                                ordinal=int(current["ordinal"]),
                                title=str(current["title"]),
                                status=str(current["status"]),
                                tool_name=tool_name,
                            )
                            await self._broadcast_run_step(run_id, updated)
                    continue

                result = event.get("result")
                data: dict[str, Any] | None = None
                if phase == "end" and isinstance(result, str):
                    tool_results.append(result)
                    try:
                        parsed = json.loads(result)
                    except (TypeError, json.JSONDecodeError):
                        parsed = None
                    data = parsed if isinstance(parsed, dict) else None
                    terminal_message = (
                        data.get("plan_change_terminal_message")
                        if data is not None
                        else None
                    )
                    if (
                        isinstance(terminal_message, dict)
                        and terminal_message.get("conversation_id") == conv_id
                    ):
                        terminal_state = terminal_message.get("task_state")
                        if isinstance(terminal_state, dict):
                            await self.broadcast(
                                {
                                    "type": "task_state",
                                    "conversation_id": conv_id,
                                    "task": terminal_state,
                                }
                            )
                        await self.broadcast(
                            {
                                "type": "message",
                                "conversation_id": conv_id,
                                "message": terminal_message,
                            }
                        )
                    if data is not None and data.get("card_type"):
                        preview = dict(data)
                        preview.pop("card_type", None)
                        preview.pop("plan_change_terminal_message", None)
                        await self.broadcast(
                            {
                                "type": "card",
                                "conversation_id": conv_id,
                                "card_type": data["card_type"],
                                "card_data": preview,
                            }
                        )
                    if (
                        is_task_tool
                        and data is not None
                        and data.get("type") == "task_state"
                        and data.get("conversation_id") == conv_id
                        and isinstance(data.get("task"), dict)
                    ):
                        turn_task_snapshot = dict(data["task"])
                        await self.broadcast(data)
                        if run_id and data["task"].get("source") == "plan_run":
                            arguments = event.get("arguments")
                            step_key = str(
                                arguments.get("step_key") or ""
                                if isinstance(arguments, dict)
                                else ""
                            )
                            updated = next(
                                (
                                    step
                                    for step in self.db.list_run_steps(run_id)
                                    if str(step.get("step_key") or "") == step_key
                                ),
                                None,
                            )
                            if updated is not None:
                                run = self.db.get_run(run_id)
                                await self.broadcast(
                                    {
                                        "type": "run_step_updated",
                                        "conversation_id": str(
                                            (run or {}).get("conversation_id") or ""
                                        ),
                                        "step": updated,
                                    }
                                )

                if not run_id or is_task_tool:
                    continue
                current = self.db.get_current_run_step(run_id)
                if current is None:
                    if is_material_tool and phase in {"end", "error"}:
                        await finish_material_boundary()
                    continue
                if phase == "end":
                    safe_tool_name = tool_name.strip()[:100] or "Tool"
                    updated = self.db.upsert_run_step(
                        run_id,
                        str(current["step_key"]),
                        ordinal=int(current["ordinal"]),
                        title=str(current["title"]),
                        status=str(current["status"]),
                        tool_name=tool_name,
                        output_summary=f"{safe_tool_name} finished successfully.",
                    )
                    await self._broadcast_run_step(run_id, updated)
                elif phase == "error":
                    self.db.update_run_task_step(
                        run_id,
                        str(current["step_key"]),
                        status="failed",
                        error_message=str(
                            event.get("error") or "Tool execution failed"
                        )[:500],
                    )
                    failed_step = next(
                        (
                            step
                            for step in self.db.list_run_steps(run_id)
                            if step["step_key"] == current["step_key"]
                        ),
                        None,
                    )
                    await self._broadcast_run_step(run_id, failed_step)
                if is_material_tool and phase in {"end", "error"}:
                    await finish_material_boundary()

        if run_id:
            self.db.transition_run(run_id, "running")
            await self.broadcast({"type": "run_started", "run": self.db.get_run(run_id)})
            await self._broadcast_task_state(self.db.get_run_task(run_id))
        await self.send_thinking(conv_id, "processing")
        try:
            chat_kwargs = {
                "conversation_id": conv_id,
                "on_stream": on_stream,
                "on_progress": on_progress,
                "on_superseded_response": on_superseded_response,
                "execution_mode": execution_mode,
                "run_id": run_id,
                "plan_id": plan_id,
                "plan_version": plan_version,
                "project_path": project_path,
                "file_access_scope": file_access_scope,
            }
            if media:
                chat_kwargs["media"] = media
            if message_metadata is not None:
                chat_kwargs["message_metadata"] = dict(message_metadata)
            parameters = inspect.signature(self._chat_runner).parameters
            accepts_extra = any(
                item.kind == inspect.Parameter.VAR_KEYWORD
                for item in parameters.values()
            )
            if not accepts_extra:
                chat_kwargs = {
                    key: value for key, value in chat_kwargs.items() if key in parameters
                }
            outbound = await self._chat_runner(content, **chat_kwargs)
        except asyncio.CancelledError:
            if run_id:
                self._active_material_runs.pop(run_id, None)
                run = self.db.get_run(run_id)
                if str((run or {}).get("error_code") or "") == "plan_superseded":
                    terminal_task = self.db.get_run_task(run_id)
                    await self._broadcast_task_state(terminal_task)
                    message = self.db.claim_plan_change_terminal_message(run_id)
                    if message is not None:
                        await self.broadcast(
                            {
                                "type": "message",
                                "conversation_id": conv_id,
                                "message": message,
                            }
                        )
                    await self.send_thinking(conv_id, "idle")
                    return
            terminal_task: dict[str, Any] | None = None
            if run_id:
                current = self.db.get_current_run_step(run_id)
                if current is not None:
                    self.db.update_run_task_step(
                        run_id,
                        str(current["step_key"]),
                        status="skipped",
                        summary="Stopped by the user.",
                    )
                    skipped_step = next(
                        (
                            step
                            for step in self.db.list_run_steps(run_id)
                            if step["step_key"] == current["step_key"]
                        ),
                        None,
                    )
                    await self._broadcast_run_step(run_id, skipped_step)
                self.db.transition_run(run_id, "cancelled")
                terminal_task = self.db.get_run_task(run_id)
                await self._broadcast_task_state(terminal_task)
            else:
                active = self.db.get_active_task(conv_id)
                if active is not None and active.get("source") == "checklist":
                    terminal_task = self.db.cancel_task_checklist(
                        str(active["id"]),
                        expected_revision=int(active["revision"]),
                        reason="Stopped by the user.",
                    )
                    await self._broadcast_task_state(terminal_task)
                elif (
                    turn_task_snapshot is not None
                    and turn_task_snapshot.get("source") == "checklist"
                    and turn_task_snapshot.get("status") != "active"
                ):
                    terminal_task = turn_task_snapshot
            await self.send_thinking(conv_id, "idle")
            stopped_message = self.db.add_message(
                conv_id,
                "assistant",
                "Stopped.",
                task_state=(
                    self._renderer_task(terminal_task) if terminal_task is not None else None
                ),
            )
            await self.broadcast({
                "type": "message",
                "conversation_id": conv_id,
                "message": stopped_message,
            })
            return
        except Exception as e:
            if run_id:
                self._active_material_runs.pop(run_id, None)
            terminal_task: dict[str, Any] | None = None
            plan_superseded = False
            if run_id:
                run = self.db.get_run(run_id)
                plan_superseded = str((run or {}).get("error_code") or "") == "plan_superseded"
                if plan_superseded:
                    terminal_task = self.db.get_run_task(run_id)
                else:
                    current = self.db.get_current_run_step(run_id)
                    if current is not None:
                        self.db.update_run_task_step(
                            run_id,
                            str(current["step_key"]),
                            status="failed",
                            error_message=str(e)[:500] or "Task execution failed.",
                        )
                        failed_step = next(
                            (
                                step
                                for step in self.db.list_run_steps(run_id)
                                if step["step_key"] == current["step_key"]
                            ),
                            None,
                        )
                        await self._broadcast_run_step(run_id, failed_step)
                    self.db.transition_run(
                        run_id,
                        "failed",
                        error_code=type(e).__name__,
                        error_message=str(e)[:1000],
                    )
                    await self.broadcast(
                        {"type": "run_failed", "run": self.db.get_run(run_id)}
                    )
                    terminal_task = self.db.get_run_task(run_id)
                    await self._broadcast_task_state(terminal_task)
            else:
                active = self.db.get_active_task(conv_id)
                if active is not None and active.get("source") == "checklist":
                    failing = active.get("current_step_key") or next(
                        (
                            step["key"]
                            for step in active.get("steps", [])
                            if step.get("status") == "pending"
                        ),
                        None,
                    )
                    if failing:
                        terminal_task = self.db.update_task_checklist(
                            str(active["id"]),
                            expected_revision=int(active["revision"]),
                            step_key=str(failing),
                            status="failed",
                            error_message=str(e)[:500] or "Task execution failed.",
                        )
                        await self._broadcast_task_state(terminal_task)
                    else:
                        terminal_task = self.db.complete_task_checklist(
                            str(active["id"]),
                            expected_revision=int(active["revision"]),
                        )
                        await self._broadcast_task_state(terminal_task)
                elif (
                    turn_task_snapshot is not None
                    and turn_task_snapshot.get("source") == "checklist"
                    and turn_task_snapshot.get("status") != "active"
                ):
                    terminal_task = turn_task_snapshot
            if plan_superseded and run_id:
                await self._broadcast_task_state(terminal_task)
                message = self.db.claim_plan_change_terminal_message(run_id)
                if message is not None:
                    await self.broadcast(
                        {
                            "type": "message",
                            "conversation_id": conv_id,
                            "message": message,
                        }
                    )
                logger.info("Chat turn stopped for a requested plan change: {}", conv_id)
                await self.send_thinking(conv_id, "idle")
                return
            card_type, card_data = self._extract_card(tool_results)
            failure_message = self.db.add_message(
                conv_id,
                "assistant",
                "I couldn't finish that task.",
                card_type=card_type,
                card_data=card_data,
                task_state=(
                    self._renderer_task(terminal_task)
                    if terminal_task is not None
                    else None
                ),
            )
            await self.broadcast(
                {
                    "type": "message",
                    "conversation_id": conv_id,
                    "message": failure_message,
                }
            )
            logger.exception("Chat turn failed for {}", conv_id)
            await self.send_thinking(conv_id, "error")
            await self.broadcast({
                "type": "error", "conversation_id": conv_id,
                "message": "Uh oh. That didn't go as planned. Try again?",
                "detail": str(e),
            })
            return

        if run_id:
            self._active_material_runs.pop(run_id, None)

        still_working = 0
        if self._subagents_running is not None:
            try:
                still_working = self._subagents_running(conv_id)
            except Exception:
                still_working = 0

        terminal_task: dict[str, Any] | None = None
        if run_id:
            terminal_task = await self._finalize_plan_run_task(run_id)
        else:
            active = self.db.get_active_task(conv_id)
            if (
                active is not None
                and active.get("source") == "checklist"
                and not still_working
            ):
                failing = active.get("current_step_key") or next(
                    (
                        step["key"]
                        for step in active.get("steps", [])
                        if step.get("status") == "pending"
                    ),
                    None,
                )
                if failing:
                    terminal_task = self.db.update_task_checklist(
                        str(active["id"]),
                        expected_revision=int(active["revision"]),
                        step_key=str(failing),
                        status="failed",
                        error_message="The turn ended before this outcome was finished.",
                    )
                else:
                    terminal_task = self.db.complete_task_checklist(
                        str(active["id"]), expected_revision=int(active["revision"])
                    )
                await self._broadcast_task_state(terminal_task)
            elif (
                turn_task_snapshot is not None
                and turn_task_snapshot.get("source") == "checklist"
                and turn_task_snapshot.get("status") != "active"
            ):
                terminal_task = turn_task_snapshot

        card_type, card_data = self._extract_card(tool_results)
        final = getattr(outbound, "content", None) or "".join(parts)
        message_task = terminal_task
        if run_id:
            change = self.db.get_plan_change_request(run_id)
            if change is not None and change.get("terminal_message_id"):
                message_task = None
        assistant_msg = self.db.add_message(
            conv_id, "assistant", final or "",
            card_type=card_type, card_data=card_data,
            task_state=(
                self._renderer_task(message_task) if message_task is not None else None
            ),
        )
        # If the turn handed work to a subagent, keep the bar on "buddy" —
        # the runtime's outbound consumer sends "done" when the result lands.
        await self.send_thinking(conv_id, "buddy" if still_working else "done")
        await self.broadcast({
            "type": "message", "conversation_id": conv_id,
            "message": assistant_msg,
        })

    @staticmethod
    def _extract_card(tool_results: list[str]) -> tuple[str | None, Any]:
        import json as _json

        for raw in reversed(tool_results):
            try:
                data = _json.loads(raw)
            except (TypeError, _json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get("card_type"):
                card_type = data.pop("card_type", None)
                data.pop("plan_change_terminal_message", None)
                return card_type, data
        return None, None
