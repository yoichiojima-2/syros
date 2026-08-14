"""GCP sandbox backend: create/reuse a session, trigger the Cloud Run Job,
stream messages back from the Firestore event feed, and relay approvals to the
caller's can_use_tool callback.

The client is also the reconciler: if the session is idle when input or an
approval decision arrives, it simply re-triggers the job.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from . import journal
from .errors import SessionTerminated, SyrosError
from .store import Store, StoreProtocol, lease_active, new_session_id, runtime
from .options import AgentOptions
from .types import Message, PermissionResultAllow, ResultMessage, doc_to_message

EVENT_POLL_SECONDS = 0.5


async def _trigger_job(project: str, region: str, job: str, session_id: str) -> None:
    from google.cloud import run_v2

    client = run_v2.JobsAsyncClient()
    overrides = run_v2.RunJobRequest.Overrides(
        container_overrides=[run_v2.RunJobRequest.Overrides.ContainerOverride(args=[session_id])]
    )
    request = run_v2.RunJobRequest(
        name=f"projects/{project}/locations/{region}/jobs/{job}", overrides=overrides
    )
    await client.run_job(request=request)  # fire; do not await completion


async def _prompt_texts(prompt: str | AsyncIterable[dict[str, Any]]) -> list[str]:
    """Flatten a prompt (plain string or the SDK's streamed-input dicts) into
    the plain texts that travel through the Firestore inbox."""
    if isinstance(prompt, str):
        return [prompt]
    texts = []
    async for item in prompt:
        content = item.get("message", item).get("content", item.get("content"))
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.append("\n".join(b.get("text", "") for b in content if isinstance(b, dict)))
        else:
            raise SyrosError(f"cannot serialize streamed prompt item: {item!r}")
    return texts


async def _relay_approvals(store: StoreProtocol, session_id: str, options: AgentOptions) -> None:
    """Answer pending approvals with the caller's can_use_tool callback.

    The sandbox's gate only ever sees the approval document; this is the piece
    that lets an SDK callback drive it, exactly as it drives claude_agent_sdk.
    """
    if options.can_use_tool is None:
        return
    from .types import ToolPermissionContext

    for approval in await store.list_pending_approvals(session_id):
        result = await options.can_use_tool(
            approval["tool_name"],
            approval.get("input") or {},
            ToolPermissionContext(tool_use_id=approval.get("tool_use_id")),
        )
        allow = isinstance(result, PermissionResultAllow)
        await store.decide_approval(
            session_id,
            approval["call_hash"],
            allow=allow,
            decided_by="sdk",
            deny_message=None if allow else (result.message or "denied"),
        )


async def _turn_base(
    store: StoreProtocol, session_id: str, event: dict[str, Any]
) -> dict[str, Any] | None:
    """The turn boundary at or before `event`: the nearest result message,
    walking parent_uuid back. None means the event precedes the first turn.

    Rewind granularity is turns because that is where the SDK can fork its own
    transcript — the result message's context records which SDK session the
    turn belonged to.
    """
    current: dict[str, Any] | None = event
    while current is not None:
        if (
            current.get("type") == "message"
            and (current.get("payload") or {}).get("kind") == "result"
        ):
            return current
        parent = current.get("parent_uuid")
        current = await store.get_event(session_id, parent) if parent else None
    return current


async def branch_from_event(
    store: StoreProtocol, session_id: str, session: dict[str, Any], from_event: str
) -> tuple[str, int]:
    """Rewind: branch the transcript from a past event, returning the new
    (branch, cursor). Conversation-only — workspace state keeps its latest
    checkpoint.

    Model-memory caveat: the SDK forks at the *end* of the base turn's SDK
    session. Turns from the same runner execution share one SDK session, so a
    rewind into the middle of the latest run removes the later turns from the
    journal while the forked model context may still contain them. Rewinds
    across runs (the common case: each re-trigger starts a new SDK session)
    fork cleanly.
    """
    if lease_active(session):
        raise SyrosError(f"session {session_id} is running — interrupt it before rewinding")
    event = await store.get_event(session_id, from_event)
    if event is None:
        raise SyrosError(f"event {from_event} not found in session {session_id}")
    base = await _turn_base(store, session_id, event)
    branch_id = journal.new_branch_id()
    base_uuid = base["uuid"] if base else None
    base_seq = int(base["seq"]) if base else 0
    # The result payload's session_id is the SDK session that produced *this*
    # turn; the context snapshot can lag one run behind (it is refreshed only
    # after a turn completes), so the payload wins.
    claude_session_id = ((base or {}).get("payload") or {}).get("session_id") or (
        (base or {}).get("context") or {}
    ).get("claude_session_id")
    await store.create_branch(
        session_id,
        branch_id,
        base_uuid=base_uuid,
        base_seq=base_seq,
        claude_session_id=claude_session_id,
    )
    # The branch's first record marks where it came from; the runner's
    # recover_head then continues from here.
    await store.append_event(
        session_id,
        journal.make_event(
            "lifecycle",
            {"event": "branch_created", "from_event": from_event, "base_uuid": base_uuid},
            parent_uuid=base_uuid,
            branch=branch_id,
            seq=base_seq + 1,
        ),
    )
    return branch_id, base_seq + 1


async def attach_session(store: StoreProtocol, options: AgentOptions) -> tuple[str, str, int]:
    """Resolve (session_id, branch, cursor) — reusing options.resume (optionally
    rewound to options.from_event) or creating anew."""
    if options.from_event and not options.resume:
        raise SyrosError("from_event requires resume=<session_id>")
    if options.resume:
        session_id = options.resume
        session = await store.get_session(session_id)
        if session is None:
            raise SyrosError(f"session {session_id} not found")
        if runtime(session).get("status") == "terminated":
            raise SessionTerminated(session_id)
        if options.from_event:
            branch, cursor = await branch_from_event(store, session_id, session, options.from_event)
            return session_id, branch, cursor
        branch = session.get("active_branch") or "main"
        # Cursor from the journal itself, not the advisory seq_head: after a
        # mid-turn crash seq_head lags, and a stale cursor would replay the
        # crashed turn's partial messages as if they answered the new prompt.
        head, _tip = await store.recover_head(session_id, branch)
        return session_id, branch, head
    # An agent reference resolves here, once: the session stores the merged
    # options, so a later edit to the agent never changes this session.
    from . import agents

    options = await agents.resolve(store, options)
    session_id = new_session_id()
    await store.create_session(session_id, options.serialize(), agent=options.agent)
    return session_id, "main", 0


async def send_prompt(
    store: StoreProtocol,
    session_id: str,
    options: AgentOptions,
    prompt: str | AsyncIterable[dict[str, Any]],
) -> None:
    """Queue the prompt and make sure a sandbox execution is (or will be) running."""
    for text in await _prompt_texts(prompt):
        await store.push_inbox(session_id, "message", text)
    session = await store.get_session(session_id)
    if not lease_active(session):
        await _trigger_job(
            options.resolved_project(),
            options.resolved_region(),
            options.resolved_job(),
            session_id,
        )
        # Only once the trigger landed: a session that failed to be triggered
        # is not starting, and the caller sees the error either way.
        await store.mark_starting(session_id)


async def stream_response(
    store: StoreProtocol, session_id: str, options: AgentOptions, branch: str, after: int
) -> AsyncIterator[tuple[int, Message]]:
    """Yield (seq, message) from one branch of the journal until a
    ResultMessage, relaying pending approvals to options.can_use_tool while
    waiting. Journal-only records (tool_call, approval, lifecycle) advance the
    cursor without yielding — the SDK stream never carried them."""
    cursor = after
    while True:
        events = await store.list_events(session_id, branch, after=cursor)
        for event in events:
            cursor = int(event["seq"])
            doc = journal.event_message(event)
            if doc is None:
                continue
            message = doc_to_message(doc)
            yield cursor, message
            if isinstance(message, ResultMessage):
                return
        await _relay_approvals(store, session_id, options)
        if not events:
            session = await store.get_session(session_id)
            if session and runtime(session).get("status") == "terminated":
                raise SessionTerminated(session_id)
            await asyncio.sleep(EVENT_POLL_SECONDS)


async def run_remote(
    prompt: str | AsyncIterable[dict[str, Any]],
    options: AgentOptions,
    *,
    store: StoreProtocol | None = None,
) -> AsyncIterator[Message]:
    store = store or Store(options.resolved_project())
    session_id, branch, cursor = await attach_session(store, options)
    await send_prompt(store, session_id, options, prompt)
    async for _, message in stream_response(store, session_id, options, branch, cursor):
        yield message
