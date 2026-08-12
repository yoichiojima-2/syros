"""Message and permission types, plus their Firestore-document serialization.

The types are claude_agent_sdk's own — re-exported, not redefined — so
`isinstance` checks and pattern matching work identically whether a message
came from a local run or was deserialized from a remote session's event feed.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import (
    CanUseTool,
    ContentBlock,
    Message,
    PermissionResult,
    PermissionResultAllow,
    PermissionResultDeny,
    ServerToolResultBlock,
    ServerToolUseBlock,
    ToolPermissionContext,
)

__all__ = [
    "AssistantMessage",
    "CanUseTool",
    "ContentBlock",
    "Message",
    "PermissionResult",
    "PermissionResultAllow",
    "PermissionResultDeny",
    "ResultMessage",
    "ServerToolResultBlock",
    "ServerToolUseBlock",
    "SystemMessage",
    "TextBlock",
    "ThinkingBlock",
    "ToolPermissionContext",
    "ToolResultBlock",
    "ToolUseBlock",
    "UserMessage",
    "doc_to_message",
    "message_to_doc",
]

_BLOCK_TYPES: dict[str, type] = {
    "text": TextBlock,
    "thinking": ThinkingBlock,
    "tool_use": ToolUseBlock,
    "tool_result": ToolResultBlock,
    "server_tool_use": ServerToolUseBlock,
    "server_tool_result": ServerToolResultBlock,
}
_BLOCK_NAMES = {cls: name for name, cls in _BLOCK_TYPES.items()}

_MESSAGE_TYPES: dict[str, type] = {
    "user": UserMessage,
    "assistant": AssistantMessage,
    "system": SystemMessage,
    "result": ResultMessage,
}


def _block_to_doc(block: ContentBlock) -> dict[str, Any]:
    doc = dataclasses.asdict(block)
    doc["type"] = _BLOCK_NAMES[type(block)]
    return doc


def _doc_to_block(doc: dict[str, Any]) -> ContentBlock:
    cls = _BLOCK_TYPES[doc["type"]]
    fields = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in doc.items() if k in fields})


def message_to_doc(message: Message) -> dict[str, Any] | None:
    """Serialize a message to a JSON/Firestore-safe dict, or None if not serializable.

    Non-dataclass extras (StreamEvent and friends) and unknown subclasses fall
    back to their base message kind; anything else returns None and is simply
    not mirrored to the event feed.
    """
    for kind, cls in _MESSAGE_TYPES.items():
        if isinstance(message, cls):
            doc: dict[str, Any] = {"kind": kind}
            for f in dataclasses.fields(cls):
                value = getattr(message, f.name)
                if f.name == "content" and isinstance(value, list):
                    value = [_block_to_doc(b) for b in value]
                doc[f.name] = value
            return doc
    return None


def doc_to_message(doc: dict[str, Any]) -> Message:
    """Inverse of message_to_doc."""
    cls = _MESSAGE_TYPES[doc["kind"]]
    fields = {f.name for f in dataclasses.fields(cls)}
    kwargs = {k: v for k, v in doc.items() if k in fields}
    content = kwargs.get("content")
    if isinstance(content, list):
        kwargs["content"] = [_doc_to_block(b) for b in content]
    return cls(**kwargs)
