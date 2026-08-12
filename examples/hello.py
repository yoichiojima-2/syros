"""Smoke test: run the same agent locally, then in the GCP sandbox.

uv run python examples/hello.py           # local (Vertex if SYROS_PROJECT is set)
uv run python examples/hello.py gcp       # sandboxed on Cloud Run
"""

import asyncio
import sys

from syros import AgentOptions, AssistantMessage, PermissionResultAllow, ResultMessage, query


async def approve(tool_name, tool_input, context):
    print(f"\n[approval] {tool_name}({tool_input}) -> allow")
    return PermissionResultAllow()


async def main() -> None:
    sandbox = sys.argv[1] if len(sys.argv) > 1 else "local"
    options = AgentOptions(
        system_prompt="You are terse.",
        allowed_tools=["Read", "Glob", "Grep"],
        permission_mode="default",
        can_use_tool=approve,
        max_turns=10,
        sandbox=sandbox,
    )
    async for message in query(prompt="List the files here, then say hi.", options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "text"):
                    print(block.text)
        elif isinstance(message, ResultMessage):
            print(f"\n[{message.subtype}] turns={message.num_turns} cost=${message.total_cost_usd}")


asyncio.run(main())
