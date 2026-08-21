"""Name-sanity helpers shared by the starter conversation and the remember tool.

A name worth remembering is a single short line. Sentences, instructions, or
preference statements are not names — they must never land in the profile's
Name field (wrong memory is worse than none: it resurfaced as the user's
name in QA). Both writers (``capture_starter_name`` and ``RememberTool``)
route through these helpers so the rule cannot diverge.
"""

from __future__ import annotations

import re

_MAX_NAME_LENGTH = 64
_MAX_NAME_WORDS = 6

# Reply patterns that clearly are not a bare name. Matched anywhere in the
# candidate so "Remember that I prefer short answers." is caught by
# "remember", "prefer", and "that i" alike.
_SENTENCE_MARKERS = re.compile(
    r"\b(remember|prefer|prefers|preferred|like|likes|dislike|dislikes|"
    r"want|wants|need|needs|hate|hates|that i|i prefer|i like|i am|i'm|"
    r"my name is|call me|can you|please|whats|what's)\b",
    re.IGNORECASE,
)

# Leading phrasings people use when answering "What's your name?" — strip
# them, then judge the remainder. ("My name is Rick" → "Rick".)
_NAME_PREFIX = re.compile(r"^(my name is|i am|i'm|call me|it's|its)\s+", re.IGNORECASE)


def strip_name_prefix(text: str) -> str:
    """Drop a leading 'my name is / i'm / call me' phrasing, if present."""
    return _NAME_PREFIX.sub("", (text or "").strip(), count=1).strip()


def is_reasonable_name(text: str) -> bool:
    """True when ``text`` plausibly names a person.

    Conservative on purpose: when in doubt we refuse and keep the Name field
    empty rather than store a sentence that later resurfaces as a name.
    """
    candidate = strip_name_prefix(text)
    if not candidate:
        return False
    if len(candidate) > _MAX_NAME_LENGTH:
        return False
    if "\n" in candidate or "\r" in candidate:
        return False
    if candidate[-1] in ".?!":
        return False
    if _SENTENCE_MARKERS.search(candidate):
        return False
    return len(candidate.split()) <= _MAX_NAME_WORDS


def name_candidate(text: str) -> str | None:
    """Return the cleaned name when ``text`` looks like one, else ``None``."""
    cleaned = strip_name_prefix(text)
    if is_reasonable_name(cleaned):
        return cleaned
    return None
