"""Durable sessions: resume one later, and branch it from an earlier turn.

    export SYROS_PROJECT=YOUR_PROJECT
    uv run python examples/resume-and-rewind.py                   # start a session
    uv run python examples/resume-and-rewind.py sess_...          # resume it, print its journal
    uv run python examples/resume-and-rewind.py sess_... <uuid>   # rewind there, then continue

A run ends at its ResultMessage and the sandbox scales to zero, but the session
survives: its options, workspace and transcript are in your project. resume=
re-triggers the job and continues the same conversation — the client is the
reconciler, nothing is running in between.

A transcript is a journal of typed records — messages, your prompts, the
tool-call audit trail, approvals, lifecycle transitions — each with its own
uuid and a parent pointer, so it is a tree rather than a log. from_event= forks
a new branch at any past record; the old branch stays intact. Caveats worth
keeping in mind:

  * Rewind is conversation-only. The workspace keeps its latest checkpoint —
    files the trimmed turns wrote are still on disk.
  * The branch point snaps to the nearest turn boundary at or before the event.
  * Turns from a single runner execution share one underlying SDK session, so
    rewinding into the middle of the newest run trims the journal while the
    forked model context may still remember the trimmed turns. Rewinds across
    runs fork cleanly.

`syros tail <session_id>` and `syros rewind <session_id> <uuid>` do the same two
things from the CLI.
"""

import asyncio
import sys

from syros import AgentOptions, AssistantMessage, ResultMessage, SyrosClient, TextBlock, query
from syros.journal import active_branch, event_message
from syros.store import Store
from syros.types import doc_to_message


def show(message) -> None:
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                print(block.text)
    elif isinstance(message, ResultMessage):
        print(f"[{message.subtype}] turns={message.num_turns}")


async def run(options: AgentOptions, prompt: str) -> None:
    async for message in query(prompt=prompt, options=options):
        show(message)


async def start() -> None:
    """First run. SyrosClient is used here only because it exposes the session
    id — query() runs the same session, it just does not hand you the handle."""
    options = AgentOptions(
        system_prompt="You are terse.",
        allowed_tools=["Read", "Glob", "Grep", "Write"],
        permission_mode="default",
        max_turns=10,
    )
    async with SyrosClient(options) as client:
        await client.query("Pick a colour and write it to colour.txt. Say which one.")
        async for message in client.receive_response():
            show(message)
        print(f"\nresume with: uv run python examples/resume-and-rewind.py {client.session_id}")


async def show_journal(session_id: str) -> None:
    """Print the active branch so you can pick an event uuid to rewind to.

    This is the same pair of calls `syros tail` makes: the session doc names its
    active branch, and events are read from that branch past a cursor.
    """
    store = Store(AgentOptions().resolved_project())
    session = await store.get_session(session_id)
    if session is None:
        raise SystemExit(f"no such session: {session_id}")

    branch = active_branch(session)
    cursor = 0
    while True:
        # list_events returns at most `limit` (200) records per call; the last
        # seq you saw is the cursor for the next page.
        events = await store.list_events(session_id, branch, after=cursor, limit=200)
        if not events:
            break
        for event in events:
            cursor = int(event["seq"])
            # Messages and prompts render as SDK message objects; tool_call,
            # approval and lifecycle records are journal-only.
            doc = event_message(event)
            if doc is None:
                body = f"<{event['type']}> {event['payload']}"
            else:
                body = str(doc_to_message(doc))
            print(f"[{cursor}] {event['uuid']}  {body[:120]}")


async def resume(session_id: str) -> None:
    """Continue the conversation. Options the session already stored win, so
    there is no need to repeat the persona here."""
    await run(AgentOptions(resume=session_id), "What colour did you pick? Same file, same session.")
    print()
    await show_journal(session_id)


async def rewind(session_id: str, event_uuid: str) -> None:
    """Fork the transcript at `event_uuid` and carry on from there.

    The next prompt lands on a new branch; the old one is still readable, and
    the console follows whichever branch is active.
    """
    options = AgentOptions(resume=session_id, from_event=event_uuid)
    await run(options, "Ignore any later choices — pick a different colour and explain why.")


async def main() -> None:
    match sys.argv[1:]:
        case []:
            await start()
        case [session_id]:
            await resume(session_id)
        case [session_id, event_uuid]:
            await rewind(session_id, event_uuid)
        case _:
            raise SystemExit(__doc__)


if __name__ == "__main__":
    asyncio.run(main())
