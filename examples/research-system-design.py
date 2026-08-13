import asyncio
from syros import query, AgentOptions, PermissionResultAllow


async def approve(tool_name, tool_input, context):
    print(f"allow {tool_name}({tool_input})?")
    return PermissionResultAllow()


async def main():
    async for message in query(
        prompt=(
            "create a syros system documentation html artifact. in detail. "
            "repo: https://github.com/yoichiojima-2/syros"
        ),
        options=AgentOptions(agent="researcher"),
    ):
        print(message)


if __name__ == "__main__":
    asyncio.run(main())
