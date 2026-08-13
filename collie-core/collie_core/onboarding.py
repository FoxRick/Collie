"""Starter conversation: the scripted, local first message after connect.

After a provider is connected the app goes straight to chat — no empty
state. The conversation opens with a scripted greeting from Collie (instant,
local, can never fail — it is NOT model-generated). The greeting appears
only once per conversation; ``/get-started`` and the Settings button reopen
the same conversation idempotently.

The user's first reply ("Rick") still goes through the normal agent loop;
the name is captured into profile memory beforehand so the model greets
them by name (MEMORY.md is injected into the loop's bootstrap context).
"""

from __future__ import annotations

from typing import Any

STARTER_CONVERSATION_SETTING = "onboarding.starter_conversation_id"

STARTER_GREETING = (
    "Hey, welcome! I'm Collie — your personal AI. What's your name?\n\n"
    "(P.S. That's me — the little dog on your desktop. Click me to say hi anytime.)"
)

# A name worth remembering: a single short line. Longer answers, questions,
# or empty replies are not names — we never force it.
_MAX_NAME_LENGTH = 64


def _is_reasonable_name(reply: str) -> bool:
    text = (reply or "").strip()
    if not text:
        return False
    if len(text) > _MAX_NAME_LENGTH:
        return False
    return not ("\n" in text or "\r" in text)


def ensure_starter_conversation(
    db: Any,
    *,
    reuse_conversation_id: str | None = None,
) -> dict[str, Any]:
    """Return the starter conversation, seeding the greeting on first use.

    Idempotent: once the starter conversation exists (tracked in settings),
    later calls return it without re-greeting. If the recorded conversation
    was deleted, a fresh one is created. The seeded greeting is returned in
    ``greeting`` (``None`` when the thread already existed) so the async IPC
    caller can broadcast it — seeding itself never awaits or fails.
    """
    starter_id = str(db.get_setting(STARTER_CONVERSATION_SETTING, "") or "")
    if starter_id:
        existing = db.get_conversation(starter_id)
        if existing is not None:
            return {"conversation": existing, "greeted": False, "greeting": None}

    conversation = _candidate_starter_conversation(db, reuse_conversation_id)
    db.set_conversation_mode(str(conversation["id"]), "execute")
    conversation["execution_mode"] = "execute"

    greeting = db.add_message(str(conversation["id"]), "assistant", STARTER_GREETING)
    db.set_setting(STARTER_CONVERSATION_SETTING, str(conversation["id"]))
    return {"conversation": conversation, "greeted": True, "greeting": greeting}


def _candidate_starter_conversation(db: Any, reuse_conversation_id: str | None) -> dict[str, Any]:
    """Reuse a just-created empty conversation, or make a fresh starter one."""
    if reuse_conversation_id:
        candidate = db.get_conversation(reuse_conversation_id)
        if candidate is not None and not db.get_messages(reuse_conversation_id):
            return candidate
    return db.create_conversation(title="Getting started")


def is_starter_conversation(db: Any, conversation_id: str) -> bool:
    return str(db.get_setting(STARTER_CONVERSATION_SETTING, "") or "") == str(conversation_id)


def capture_starter_name(
    db: Any,
    profile_store: Any,
    conversation_id: str,
    reply: str,
) -> bool:
    """Save the first reply in the starter thread as the user's name.

    Only fires once (the profile has no name yet and the conversation holds
    only the greeting). Whatever they said is stored verbatim — or nothing;
    the name is never forced.
    """
    if profile_store is None:
        return False
    if not is_starter_conversation(db, conversation_id):
        return False
    if profile_store.get("name"):
        return False
    messages = db.get_messages(conversation_id)
    if len(messages) != 1:
        # The greeting is the only message before the first real reply.
        return False
    if not _is_reasonable_name(reply):
        return False
    profile_store.set("name", reply.strip())
    return True
