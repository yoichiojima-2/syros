import asyncio
import time

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
from syros.remote import run_remote, send_prompt
from syros.types import message_to_doc

from .fakes import FakeStore, append_message


@pytest.fixture(autouse=True)
def no_job_trigger(monkeypatch):
    triggered = []

    async def fake_trigger(project, region, job, session_id):
        triggered.append((project, region, job, session_id))

    monkeypatch.setattr(syros.remote, "_trigger_job", fake_trigger)
    return triggered


def options(**kwargs):
    return AgentOptions(project="proj-1", **kwargs)


async def drive_runner(store, session_id, messages, *, start_seq=0):
    """Simulate the runner: wait for the inbox, then mirror messages."""
    while not await store.pop_messages(session_id):
        await asyncio.sleep(0.01)
    seq = start_seq
    for message in messages:
        seq += 1
        await append_message(store, session_id, seq, message_to_doc(message))
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
    await append_message(store, session_id, 1, message_to_doc(RESULT))  # old turn
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


async def test_send_prompt_marks_session_starting(no_job_trigger):
    store = FakeStore()
    await store.create_session("sess_1", {})

    await send_prompt(store, "sess_1", options(), "hello")

    session = await store.get_session("sess_1")
    assert session["runtime"]["status"] == "starting"
    assert session["runtime"]["triggered_at"] == pytest.approx(time.time(), abs=5)


async def test_send_prompt_leaves_a_live_run_alone(no_job_trigger):
    """A session an execution already holds is past "starting" — no trigger,
    and nothing that would make a running session look like it never started."""
    store = FakeStore()
    await store.create_session("sess_1", {})
    await store.claim_session("sess_1", "lease-1", 60)

    await send_prompt(store, "sess_1", options(), "hello")

    assert no_job_trigger == []
    assert (await store.get_session("sess_1"))["runtime"]["status"] == "running"


async def test_resume_terminated_session_raises():
    store = FakeStore()
    await store.create_session("sess_dead", {})
    await store.update_session("sess_dead", status="terminated")
    with pytest.raises(SessionTerminated):
        async for _ in run_remote("x", options(resume="sess_dead"), store=store):
            pass


async def test_from_event_rewinds_onto_a_new_branch(no_job_trigger):
    from syros.journal import make_event
    from syros.remote import attach_session

    store = FakeStore()
    sid = "sess_tree"
    await store.create_session(sid, {})
    # two finished turns on main; contexts record the SDK session per turn
    prompt1 = make_event("prompt", {"text": "one"}, parent_uuid=None, branch="main", seq=1)
    await store.append_event(sid, prompt1)
    result1 = make_event(
        "message",
        message_to_doc(RESULT),
        parent_uuid=prompt1["uuid"],
        branch="main",
        seq=2,
        context={"claude_session_id": "c-turn1"},
    )
    await store.append_event(sid, result1)
    prompt2 = make_event(
        "prompt", {"text": "two"}, parent_uuid=result1["uuid"], branch="main", seq=3
    )
    await store.append_event(sid, prompt2)
    await store.update_session(sid, seq_head=3, status="idle")

    # rewinding from mid-turn-2 snaps to the turn boundary: turn 1's result
    session_id, branch, cursor = await attach_session(
        store, options(resume=sid, from_event=prompt2["uuid"])
    )
    assert session_id == sid
    assert branch.startswith("br_") and cursor == 3

    session = await store.get_session(sid)
    assert session["active_branch"] == branch
    assert session["branches"][branch]["base_uuid"] == result1["uuid"]
    assert session["branches"][branch]["base_seq"] == 2
    # the SDK session to fork from came from the base turn's context
    assert session["branches"][branch]["claude_session_id"] == "c-turn1"
    # the branch opens with its provenance record, chained to the base
    (created,) = await store.list_events(sid, branch, after=0)
    assert created["type"] == "lifecycle"
    assert created["payload"]["event"] == "branch_created"
    assert created["parent_uuid"] == result1["uuid"]
    # main is untouched
    assert [e["seq"] for e in await store.list_events(sid, "main", after=0)] == [1, 2, 3]


async def test_from_event_refuses_running_session(no_job_trigger):
    import pytest as _pytest

    from syros.errors import SyrosError
    from syros.remote import attach_session

    store = FakeStore()
    await store.create_session("sess_1", {})
    await store.claim_session("sess_1", "lease-1", 60)
    with _pytest.raises(SyrosError, match="interrupt"):
        await attach_session(store, options(resume="sess_1", from_event="e1"))


async def test_from_event_requires_resume(no_job_trigger):
    import pytest as _pytest

    from syros.errors import SyrosError
    from syros.remote import attach_session

    with _pytest.raises(SyrosError, match="requires resume"):
        await attach_session(FakeStore(), options(from_event="e1"))


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
        await append_message(store, session_id, 1, message_to_doc(RESULT))

    runner_task = asyncio.create_task(runner())
    collected = await asyncio.wait_for(query_task, timeout=5)
    await runner_task

    assert seen == [("Bash", {"command": "rm"})]
    assert (await store.get_approval(session_id, "hash1"))["status"] == "allow"
    assert isinstance(collected[-1], ResultMessage)
