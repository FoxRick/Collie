"""Exclude raw desktop tool traffic from durable engine checkpoints/history."""

from copy import deepcopy


def compact_computer_context(messages):
    """Keep only the latest native result in the model copy, preserving call pairs."""
    ids = {
        call.get("id")
        for message in messages
        for call in message.get("tool_calls", [])
        if isinstance(call, dict)
        and isinstance(call.get("function"), dict)
        and call["function"].get("name") == "computer"
    }
    results = [
        i
        for i, message in enumerate(messages)
        if message.get("role") == "tool" and message.get("tool_call_id") in ids
    ]
    if len(results) < 2:
        return messages
    updated = list(messages)
    for index in results[:-1]:
        updated[index] = {
            **messages[index],
            "content": "[Stale computer observation omitted; use the latest observation.]",
        }
    return updated


def redact_computer_history(payload):
    result = deepcopy(payload)
    call_ids = set()

    def collect(node):
        if isinstance(node, list):
            for child in node:
                collect(child)
        elif isinstance(node, dict):
            for call in node.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function")
                if isinstance(function, dict) and function.get("name") == "computer":
                    call_ids.add(call.get("id"))
                    function["arguments"] = '{"operation":"status"}'
            for child in node.values():
                collect(child)

    def redact(node):
        if isinstance(node, list):
            for child in node:
                redact(child)
        elif isinstance(node, dict):
            if node.get("role") == "tool" and node.get("tool_call_id") in call_ids:
                node["content"] = (
                    "[Computer observation omitted; obtain fresh state before acting.]"
                )
            for child in node.values():
                redact(child)

    collect(result)
    redact(result)
    return result
