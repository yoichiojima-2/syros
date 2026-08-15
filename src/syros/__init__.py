"""syros — minimal secure agent development environment on GCP.

Same API shape as claude_agent_sdk; every run is sandboxed in your GCP project.
"""

from .agents import AgentError
from .client import SyrosClient, query
from .errors import OptionsError, SessionTerminated, SyrosError
from .options import AgentOptions, claude_code_prompt
from .types import (
    AssistantMessage,
    ContentBlock,
    Message,
    PermissionResult,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

__all__ = [
    "AgentError",
    "AgentOptions",
    "AssistantMessage",
    "ContentBlock",
    "Message",
    "OptionsError",
    "PermissionResult",
    "PermissionResultAllow",
    "PermissionResultDeny",
    "ResultMessage",
    "SessionTerminated",
    "SyrosClient",
    "SyrosError",
    "SystemMessage",
    "TextBlock",
    "ThinkingBlock",
    "ToolPermissionContext",
    "ToolResultBlock",
    "ToolUseBlock",
    "UserMessage",
    "claude_code_prompt",
    "query",
]
