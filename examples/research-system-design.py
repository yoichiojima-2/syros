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
        # Same task as syros-document.py, but run as the stored "researcher"
        # agent instead of inline options — the persona supplies the defaults.
        # Create it with `syros presets install researcher`.
        options=AgentOptions(agent="researcher", can_use_tool=approve),
    ):
        print(message)


if __name__ == "__main__":
    asyncio.run(main())
