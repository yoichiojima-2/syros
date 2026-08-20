import asyncio

import pytest

from syros import AgentOptions, AssistantMessage, ResultMessage, SyrosClient, TextBlock
from syros.errors import SyrosError
from syros.types import message_to_doc

from .fakes import FakeStore, append_message


def make_client(store, **kwargs):
    client = SyrosClient(AgentOptions(project="proj-1", **kwargs))
    client._store = store
    return client


def result(n):
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=n,
        session_id="uuid",
    )


async def answer_turn(store, session_id, text, start_seq):
    while not await store.pop_messages(session_id):
        await asyncio.sleep(0.01)
    await append_message(
        store,
        session_id,
        start_seq + 1,
        message_to_doc(AssistantMessage(content=[TextBlock(text=text)], model="m")),
    )
    await append_message(store, session_id, start_seq + 2, message_to_doc(result(1)))
    await store.update_session(session_id, seq_head=start_seq + 2)


async def test_multi_turn_conversation():
    store = FakeStore()
    async with make_client(store) as client:
        assert client.session_id is not None

        await client.query("first")
        driver = asyncio.create_task(answer_turn(store, client.session_id, "one", 0))
        turn1 = [m async for m in client.receive_response()]
        await driver

        await client.query("second")
        driver = asyncio.create_task(answer_turn(store, client.session_id, "two", 2))
        turn2 = [m async for m in client.receive_response()]
        await driver

    assert turn1[0].content == [TextBlock(text="one")]
    assert turn2[0].content == [TextBlock(text="two")]
    assert isinstance(turn1[-1], ResultMessage)
    assert isinstance(turn2[-1], ResultMessage)


async def test_interrupt_and_terminate():
    store = FakeStore()
    async with make_client(store) as client:
        await client.interrupt()
        assert store.inbox[client.session_id][0]["kind"] == "interrupt"
        await client.terminate()
        session = await store.get_session(client.session_id)
        assert session["runtime"]["status"] == "terminated"
        assert session["disabled"] is True


async def test_query_before_connect_raises():
    client = SyrosClient(AgentOptions(project="proj-1"))
    with pytest.raises(SyrosError, match="not connected"):
        await client.query("x")
