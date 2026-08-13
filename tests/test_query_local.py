import asyncio

import pytest

import syros.local
from syros import (
    AgentOptions,
    AssistantMessage,
    PermissionResultAllow,
    ResultMessage,
    TextBlock,
    query,
)


def fake_sdk_query(captured):
    async def _fake(*, prompt, options):
        captured["prompt"] = prompt
        captured["options"] = options
        yield AssistantMessage(content=[TextBlock(text="hi")], model="m")
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s",
        )

    return _fake


async def test_local_query_passes_through(monkeypatch):
    captured = {}
    monkeypatch.setattr(syros.local, "_sdk_query", fake_sdk_query(captured))

    messages = [
        m
        async for m in query(
            prompt="hello",
            options=AgentOptions(system_prompt="sp", project="proj-1", workspace="/tmp"),
        )
    ]

    assert isinstance(messages[0], AssistantMessage)
    assert isinstance(messages[-1], ResultMessage)
    assert captured["prompt"] == "hello"
    assert captured["options"].system_prompt == "sp"
    assert captured["options"].cwd == "/tmp"
    assert captured["options"].env["CLAUDE_CODE_USE_VERTEX"] == "1"
    assert captured["options"].env["ANTHROPIC_VERTEX_PROJECT_ID"] == "proj-1"


async def test_local_wraps_string_prompt_and_holds_stream_open(monkeypatch):
    """can_use_tool needs streaming input, and the stream must stay open for
    the whole run: the SDK closes stdin when input is exhausted, and stdin
    also carries the permission responses."""

    async def approve(tool_name, tool_input, context):
        return PermissionResultAllow()

    async def fake_sdk_query(*, prompt, options):
        stream = aiter(prompt)
        first = await anext(stream)
        assert first["message"]["content"] == "hello"
        # Mid-run the stream must not be exhausted — anext() should hang.
        pending = asyncio.ensure_future(anext(stream))
        await asyncio.sleep(0)
        assert not pending.done()
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s",
        )
        # The result releases the stream; it ends instead of yielding again.
        with pytest.raises(StopAsyncIteration):
            await pending

    monkeypatch.setattr(syros.local, "_sdk_query", fake_sdk_query)

    messages = [
        m
        async for m in query(
            prompt="hello",
            options=AgentOptions(can_use_tool=approve),
        )
    ]
    assert isinstance(messages[-1], ResultMessage)


async def test_query_validates_options():
    from syros.errors import OptionsError

    with pytest.raises(OptionsError):
        async for _ in query(prompt="x", options=AgentOptions(sandbox="gcp")):
            pass
