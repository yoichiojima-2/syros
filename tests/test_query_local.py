import pytest

import syros.local
from syros import AgentOptions, AssistantMessage, ResultMessage, TextBlock, query


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


async def test_query_validates_options():
    from syros.errors import OptionsError

    with pytest.raises(OptionsError):
        async for _ in query(prompt="x", options=AgentOptions(sandbox="gcp")):
            pass
