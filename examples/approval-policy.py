"""A can_use_tool policy: what to allow, what to refuse, and what it costs.

    export SYROS_PROJECT=YOUR_PROJECT
    uv run python examples/approval-policy.py

Tools named in allowed_tools run immediately. Everything else stops at the
sandbox gate, which writes the call to the audit trail and waits for a decision
— so nothing runs unrecorded. While a client is streaming a response it answers
those requests with this callback, exactly as claude_agent_sdk would call it on
your machine.

Worth knowing before you rely on it:

  * The callback only runs while a client is attached and streaming. Nothing is
    home between runs, and a scheduled workflow has no callback at all — that
    is what `syros approvals <session_id> allow <call_hash>` and the console's
    approve/deny buttons are for.
  * Only the decision crosses the queue. The message on PermissionResultDeny is
    relayed back to the model, but updated_input and updated_permissions on
    PermissionResultAllow are dropped (see remote._relay_approvals), so steer a
    bad call by denying it with an explanation rather than by rewriting it.
  * The gate fails closed. An approval nobody answers within
    $SYROS_APPROVAL_TIMEOUT (default 300s) is recorded as a deny by "timeout",
    so a client that dies mid-run does not leave a tool call hanging.
  * `context` carries the tool_use_id. The local-only fields of
    ToolPermissionContext (suggestions, blocked_path, ...) are not populated —
    the sandbox gate only ever sees the approval document.
"""

import asyncio
from pathlib import PurePosixPath
from typing import Any

from syros import (
    AgentOptions,
    AssistantMessage,
    PermissionResult,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolPermissionContext,
    query,
)

READ_ONLY = {"Read", "Glob", "Grep"}
WRITE_ROOT = "out"  # the only directory this policy lets the agent write into
BANNED_COMMANDS = ("rm -rf", "sudo", "curl", "chmod 777")


def _under_write_root(path: str) -> bool:
    """Paths arrive as the model wrote them — relative or absolute — so match on
    a path segment rather than a prefix string."""
    return WRITE_ROOT in PurePosixPath(path).parts


async def policy(
    tool_name: str,
    tool_input: dict[str, Any],
    context: ToolPermissionContext,
) -> PermissionResult:
    if tool_name in READ_ONLY:
        return PermissionResultAllow()

    if tool_name in ("Write", "Edit"):
        path = tool_input.get("file_path", "")
        if not _under_write_root(path):
            # The model sees this text and can retry with a better path.
            return PermissionResultDeny(
                message=f"writes are confined to ./{WRITE_ROOT}/ — {path} is outside it"
            )
        return PermissionResultAllow()

    if tool_name == "Bash":
        command = tool_input.get("command", "")
        for banned in BANNED_COMMANDS:
            if banned in command:
                return PermissionResultDeny(message=f"{banned!r} is not allowed in this session")
        return PermissionResultAllow()

    return PermissionResultDeny(message=f"{tool_name} is not part of this task")


async def main() -> None:
    options = AgentOptions(
        system_prompt="You are a careful assistant.",
        # Deliberately empty: with nothing pre-allowed every call goes through
        # the policy above, which is the point of this example. In real use,
        # allowlist the boring read-only tools — the sandbox blocks on each
        # approval, polling Firestore for the decision while the clock runs.
        allowed_tools=[],
        permission_mode="default",
        can_use_tool=policy,
        max_turns=15,
    )
    prompt = (
        "Summarize the files in this directory into out/summary.md, "
        "then try to write the same summary to /etc/summary.md."
    )
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text)
        elif isinstance(message, ResultMessage):
            # permission_denials lists what the policy refused, if anything.
            print(f"\n[{message.subtype}] denials={message.permission_denials}")


if __name__ == "__main__":
    asyncio.run(main())
