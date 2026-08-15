"""A multi-turn conversation with SyrosClient, mirroring ClaudeSDKClient.

    export SYROS_PROJECT=YOUR_PROJECT
    uv run python examples/multi-turn.py

query() is one prompt in, one ResultMessage out. SyrosClient keeps the session
open across turns: connect once, then query/receive_response as often as you
like. The conversation is durable — the session, its workspace and its
transcript live in your project, and the sandbox scales to zero between turns —
so "connected" here means "this process is attached to it", not "a VM is
burning money somewhere".
"""

import asyncio

from syros import AgentOptions, AssistantMessage, ResultMessage, SyrosClient, TextBlock


async def drain(client: SyrosClient) -> None:
    """Consume one response, up to and including its ResultMessage.

    receive_response() reads the journal from the client's cursor and stops at
    the next ResultMessage, so this returns when the turn is done. Drain each
    turn before sending the next prompt: prompts queue in order, and draining is
    also what relays approvals to a can_use_tool callback.
    """
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text)
        elif isinstance(message, ResultMessage):
            print(f"[{message.subtype}] turns={message.num_turns}\n")


async def main() -> None:
    options = AgentOptions(
        system_prompt="You are a terse assistant.",
        allowed_tools=["Read", "Glob", "Grep", "Write"],
        permission_mode="default",
        max_turns=20,
    )

    # The context manager connects on entry and disconnects on exit. connect()
    # creates the session (or attaches to options.resume) — the id exists only
    # after that, which is why session_id is None until you are inside.
    async with SyrosClient(options) as client:
        await client.query("Write a haiku about durable sessions to haiku.txt.")
        await drain(client)

        print(f"session: {client.session_id}")

        # Same session, same workspace: the file written above is still there.
        await client.query("Now read haiku.txt back to me and count its syllables.")
        await drain(client)

        # Two more things you can do to a live session:
        #   await client.interrupt()   stop the current turn (queued as an
        #                              interrupt the runner picks up)
        #   await client.terminate()   end it for good — flips the kill switch,
        #                              so every further tool call is denied

    # Leaving the block only detaches. The session is still there: resume it
    # from any process with AgentOptions(resume=...), or watch it with
    # `syros tail <session_id>`. See resume-and-rewind.py.


if __name__ == "__main__":
    asyncio.run(main())
