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

from .errors import SessionTerminated, SyrosError
from .options import AgentOptions
from .store import Store, StoreProtocol, lease_active, new_session_id
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


async def attach_session(store: StoreProtocol, options: AgentOptions) -> tuple[str, int]:
    """Resolve (session_id, cursor) — reusing options.resume or creating anew."""
    if options.resume:
        session_id = options.resume
        session = await store.get_session(session_id)
        if session is None:
            raise SyrosError(f"session {session_id} not found")
        if session.get("status") == "terminated":
            raise SessionTerminated(session_id)
        return session_id, int(session.get("seq_head") or 0)
    session_id = new_session_id()
    await store.create_session(session_id, options.serialize())
    return session_id, 0


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


async def stream_response(
    store: StoreProtocol, session_id: str, options: AgentOptions, after: int
) -> AsyncIterator[tuple[int, Message]]:
    """Yield (seq, message) from the event feed until a ResultMessage, relaying
    pending approvals to options.can_use_tool while waiting."""
    cursor = after
    while True:
        events = await store.list_events(session_id, after=cursor)
        for event in events:
            cursor = int(event["seq"])
            message = doc_to_message(event["message"])
            yield cursor, message
            if isinstance(message, ResultMessage):
                return
        await _relay_approvals(store, session_id, options)
        if not events:
            session = await store.get_session(session_id)
            if session and session.get("status") == "terminated":
                raise SessionTerminated(session_id)
            await asyncio.sleep(EVENT_POLL_SECONDS)


async def run_remote(
    prompt: str | AsyncIterable[dict[str, Any]],
    options: AgentOptions,
    *,
    store: StoreProtocol | None = None,
) -> AsyncIterator[Message]:
    store = store or Store(options.resolved_project())
    session_id, cursor = await attach_session(store, options)
    await send_prompt(store, session_id, options, prompt)
    async for _, message in stream_response(store, session_id, options, cursor):
        yield message
