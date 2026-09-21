"""Patches the Agents SDK's Chat-Completions <-> Responses-API item
converter to round-trip Gemini's `extra_content.google.thought_signature`
on function-call tool calls.

Why this exists: Gemini 2.5+ requires every function-call part in a
multi-turn tool-calling exchange to carry back the thought_signature it
was issued with, or the *next* call 400s ("Function call is missing a
thought_signature..."). The Agents SDK normalizes every provider's
response into its own internal Responses-API item shape
(`ResponseFunctionToolCall`), which has no field for vendor extensions,
so the signature is silently dropped on the very first round trip —
breaking every Gemini conversation that involves more than one tool
call. Verified empirically (2026-09-21) that no public request param
avoids this: `reasoning_effort` at any value still requires the
signature once thinking has produced one.

Fix: cache `extra_content` by tool_call id when a response comes in
(`message_to_output_items`), and reattach it when that same call gets
serialized back into a follow-up request (`items_to_messages`). A no-op
for providers that never set extra_content (Groq, NVIDIA) — the cache
simply never has an entry for their tool_call ids.
"""
from __future__ import annotations

from agents.models.chatcmpl_converter import Converter

_thought_signatures: dict[str, object] = {}

_original_message_to_output_items = Converter.message_to_output_items.__func__
_original_items_to_messages = Converter.items_to_messages.__func__


def _patched_message_to_output_items(cls, message):
    for tool_call in getattr(message, "tool_calls", None) or []:
        extra = getattr(tool_call, "extra_content", None)
        if extra:
            _thought_signatures[tool_call.id] = extra
    return _original_message_to_output_items(cls, message)


def _patched_items_to_messages(cls, items):
    messages = _original_items_to_messages(cls, items)
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for tool_call in message.get("tool_calls") or []:
            extra = _thought_signatures.get(tool_call.get("id"))
            if extra:
                tool_call["extra_content"] = extra
    return messages


Converter.message_to_output_items = classmethod(_patched_message_to_output_items)
Converter.items_to_messages = classmethod(_patched_items_to_messages)
