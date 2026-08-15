"""Smoke test: run one prompt in the GCP sandbox and print what comes back.

    export SYROS_PROJECT=YOUR_PROJECT
    uv run python examples/hello.py

The smallest thing that exercises the whole path: options -> a Firestore
session -> the Cloud Run Job -> the journal -> typed messages back here.
"""

import asyncio

from syros import (
    AgentOptions,
    AssistantMessage,
    PermissionResultAllow,
    ResultMessage,
    TextBlock,
    query,
)


async def approve(tool_name, tool_input, context):
    """Rubber-stamp whatever the allowlist did not cover — approval-policy.py
    has a callback that actually decides."""
    print(f"\n[approval] {tool_name}({tool_input}) -> allow")
    return PermissionResultAllow()


async def main() -> None:
    options = AgentOptions(
        system_prompt="You are terse.",
        # Tools named here run immediately. Anything else stops at the gate,
        # which records the call and waits for a decision — relayed to
        # can_use_tool while this client is streaming.
        allowed_tools=["Read", "Glob", "Grep"],
        permission_mode="default",
        can_use_tool=approve,
        max_turns=10,
    )
    async for message in query(prompt="List the files here, then say hi.", options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text)
        elif isinstance(message, ResultMessage):
            # Terminal message: the run is over and the sandbox scales to zero.
            cost = message.total_cost_usd
            print(f"\n[{message.subtype}] turns={message.num_turns} cost=${cost}")


if __name__ == "__main__":
    asyncio.run(main())
