import asyncio

import pytest

import syros.remote
from syros import (
    AgentOptions,
    AssistantMessage,
    PermissionResultAllow,
    ResultMessage,
    TextBlock,
)
from syros.errors import SessionTerminated
from syros.remote import run_remote
from syros.types import message_to_doc

from .fakes import FakeStore


@pytest.fixture(autouse=True)
def no_job_trigger(monkeypatch):
    triggered = []

    async def fake_trigger(project, region, job, session_id):
        triggered.append((project, region, job, session_id))

    monkeypatch.setattr(syros.remote, "_trigger_job", fake_trigger)
    return triggered


def options(**kwargs):
    return AgentOptions(sandbox="gcp", project="proj-1", **kwargs)


async def drive_runner(store, session_id, messages, *, start_seq=0):
    """Simulate the runner: wait for the inbox, then mirror messages."""
    while not await store.pop_messages(session_id):
        await asyncio.sleep(0.01)
    seq = start_seq
    for message in messages:
        seq += 1
        await store.append_event(session_id, seq, message_to_doc(message))
    await store.release_session(session_id, status="idle", stop_reason="success", seq_head=seq)


RESULT = ResultMessage(
    subtype="success",
    duration_ms=1,
    duration_api_ms=1,
    is_error=False,
    num_turns=1,
    session_id="claude-uuid",
    total_cost_usd=0.01,
)


async def test_query_streams_until_result(no_job_trigger):
    store = FakeStore()
    opts = options()

    async def run():
        collected = []
        async for message in run_remote("do it", opts, store=store):
            collected.append(message)
        return collected

    query_task = asyncio.create_task(run())
    while not store.sessions:
        await asyncio.sleep(0.01)
    (session_id,) = store.sessions
    runner_task = asyncio.create_task(
        drive_runner(
            store,
            session_id,
            [AssistantMessage(content=[TextBlock(text="hi")], model="m"), RESULT],
        )
    )
    collected = await asyncio.wait_for(query_task, timeout=5)
    await runner_task

    assert isinstance(collected[0], AssistantMessage)
    assert collected[0].content == [TextBlock(text="hi")]
    assert isinstance(collected[-1], ResultMessage)
    assert no_job_trigger == [("proj-1", "asia-northeast1", "syros-runner", session_id)]


async def test_resume_starts_after_seq_head(no_job_trigger):
    store = FakeStore()
    session_id = "sess_existing"
    await store.create_session(session_id, {})
    await store.append_event(session_id, 1, message_to_doc(RESULT))  # old turn
    await store.update_session(session_id, status="idle", seq_head=1)

    async def run():
        return [m async for m in run_remote("again", options(resume=session_id), store=store)]

    query_task = asyncio.create_task(run())
    runner_task = asyncio.create_task(
        drive_runner(
            store,
            session_id,
            [AssistantMessage(content=[TextBlock(text="turn2")], model="m"), RESULT],
            start_seq=1,
        )
    )
    collected = await asyncio.wait_for(query_task, timeout=5)
    await runner_task
    assert [type(m).__name__ for m in collected] == ["AssistantMessage", "ResultMessage"]
    assert collected[0].content == [TextBlock(text="turn2")]


async def test_resume_terminated_session_raises():
    store = FakeStore()
    await store.create_session("sess_dead", {})
    await store.update_session("sess_dead", status="terminated")
    with pytest.raises(SessionTerminated):
        async for _ in run_remote("x", options(resume="sess_dead"), store=store):
            pass


async def test_approval_relay(no_job_trigger):
    store = FakeStore()
    seen = []

    async def approve(tool_name, tool_input, ctx):
        seen.append((tool_name, tool_input))
        return PermissionResultAllow()

    opts = options(can_use_tool=approve)

    async def run():
        return [m async for m in run_remote("do it", opts, store=store)]

    query_task = asyncio.create_task(run())
    while not store.sessions:
        await asyncio.sleep(0.01)
    (session_id,) = store.sessions

    async def runner():
        while not await store.pop_messages(session_id):
            await asyncio.sleep(0.01)
        await store.request_approval(session_id, "hash1", "Bash", {"command": "rm"})
        while (await store.get_approval(session_id, "hash1"))["status"] == "pending":
            await asyncio.sleep(0.01)
        await store.append_event(session_id, 1, message_to_doc(RESULT))

    runner_task = asyncio.create_task(runner())
    collected = await asyncio.wait_for(query_task, timeout=5)
    await runner_task

    assert seen == [("Bash", {"command": "rm"})]
    assert (await store.get_approval(session_id, "hash1"))["status"] == "allow"
    assert isinstance(collected[-1], ResultMessage)
