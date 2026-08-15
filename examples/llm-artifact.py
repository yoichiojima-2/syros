import asyncio
from syros import query, AgentOptions, PermissionResultAllow


async def approve(tool_name, tool_input, context):
    print(f"allow {tool_name}({tool_input})?")
    return PermissionResultAllow()


async def main():
    async for message in query(
        prompt="create a tailored html artifact about llm system architecture",
        options=AgentOptions(
            model="claude-sonnet-5",
            allowed_tools=["Write", "Read", "Edit", "WebSearch"],
            permission_mode="default",
            can_use_tool=approve,
            artifacts={"reports": "rw"},
        ),
    ):
        print(message)


if __name__ == "__main__":
    asyncio.run(main())
