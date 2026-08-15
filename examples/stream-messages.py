"""Every message type a run streams back, and one render() worth copying.

    export SYROS_PROJECT=YOUR_PROJECT
    uv run python examples/stream-messages.py

query() and SyrosClient.receive_response() yield claude_agent_sdk's own message
objects — syros re-exports the types rather than redefining them, so isinstance
checks and pattern matching work exactly as they do against the SDK even though
every message here was deserialized from the session's journal in Firestore.

One turn arrives roughly like this:

    SystemMessage(subtype="init")   the run's tools, model, working directory
    AssistantMessage                TextBlock / ThinkingBlock / ToolUseBlock
    UserMessage                     the ToolResultBlock coming back from a tool
    ...                             another assistant/user pair per tool call
    ResultMessage                   terminal: subtype, turns, cost, usage

Prompts you send are journalled too, and read back as UserMessage — so a
resumed session replays your side of the conversation as well as the model's.
"""

import asyncio

from syros import (
    AgentOptions,
    AssistantMessage,
    ContentBlock,
    Message,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)


def render_block(block: ContentBlock) -> str:
    if isinstance(block, TextBlock):
        return block.text
    if isinstance(block, ThinkingBlock):
        # Extended thinking. `signature` is the model's attestation of it.
        return f"(thinking) {block.thinking}"
    if isinstance(block, ToolUseBlock):
        # The call the gate audits and, unless pre-allowed, asks about.
        return f"(tool_use {block.id}) {block.name}({block.input})"
    if isinstance(block, ToolResultBlock):
        marker = "error" if block.is_error else "ok"
        return f"(tool_result {block.tool_use_id} {marker}) {block.content}"
    # Server-side tool blocks (web search and friends) land here as themselves.
    return repr(block)


def render(message: Message) -> str:
    if isinstance(message, SystemMessage):
        # subtype "init" carries the run's setup; data is the raw payload.
        return f"system/{message.subtype}: {message.data}"
    if isinstance(message, AssistantMessage):
        body = "\n".join(render_block(b) for b in message.content)
        return f"assistant ({message.model}):\n{body}"
    if isinstance(message, UserMessage):
        # content is a plain string for a prompt, or blocks for tool results.
        if isinstance(message.content, str):
            return f"user: {message.content}"
        body = "\n".join(render_block(b) for b in message.content)
        return f"user:\n{body}"
    if isinstance(message, ResultMessage):
        return (
            f"result/{message.subtype}: turns={message.num_turns} "
            f"cost=${message.total_cost_usd} stop={message.stop_reason} "
            f"duration={message.duration_ms}ms"
        )
    return repr(message)


async def main() -> None:
    options = AgentOptions(
        system_prompt="You are terse. Use a tool at least once.",
        allowed_tools=["Read", "Glob", "Grep"],
        permission_mode="default",
        max_turns=10,
    )
    async for message in query(prompt="What files are here? Then summarize.", options=options):
        print(render(message))
        print("-" * 60)


if __name__ == "__main__":
    asyncio.run(main())
