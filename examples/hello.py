"""Smoke test: run an agent in the GCP sandbox.

export SYROS_PROJECT=YOUR_PROJECT
uv run python examples/hello.py
"""

import asyncio

from syros import AgentOptions, AssistantMessage, PermissionResultAllow, ResultMessage, query


async def approve(tool_name, tool_input, context):
    print(f"\n[approval] {tool_name}({tool_input}) -> allow")
    return PermissionResultAllow()


async def main() -> None:
    options = AgentOptions(
        system_prompt="You are terse.",
        allowed_tools=["Read", "Glob", "Grep"],
        permission_mode="default",
        can_use_tool=approve,
        max_turns=10,
    )
    async for message in query(prompt="List the files here, then say hi.", options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "text"):
                    print(block.text)
        elif isinstance(message, ResultMessage):
            print(f"\n[{message.subtype}] turns={message.num_turns} cost=${message.total_cost_usd}")


asyncio.run(main())
