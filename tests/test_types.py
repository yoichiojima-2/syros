import json

from syros.types import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    doc_to_message,
    message_to_doc,
)

MESSAGES = [
    UserMessage(content="hello"),
    UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="ok", is_error=False)]),
    AssistantMessage(
        content=[
            TextBlock(text="hi"),
            ThinkingBlock(thinking="", signature=""),
            ToolUseBlock(id="t1", name="Bash", input={"command": "ls"}),
        ],
        model="claude-sonnet-5",
        session_id="abc",
    ),
    SystemMessage(subtype="init", data={"tools": ["Bash"]}),
    ResultMessage(
        subtype="success",
        duration_ms=1200,
        duration_api_ms=900,
        is_error=False,
        num_turns=3,
        session_id="abc",
        total_cost_usd=0.05,
        usage={"input_tokens": 10, "output_tokens": 20},
        result="done",
    ),
]


def test_round_trip():
    for message in MESSAGES:
        doc = message_to_doc(message)
        assert doc is not None
        restored = doc_to_message(doc)
        assert restored == message


def test_docs_are_json_safe():
    for message in MESSAGES:
        json.dumps(message_to_doc(message))


def test_unknown_message_returns_none():
    assert message_to_doc(object()) is None


def test_forward_compatible_extra_fields():
    doc = message_to_doc(SystemMessage(subtype="status", data={}))
    doc["future_field"] = 42
    restored = doc_to_message(doc)
    assert isinstance(restored, SystemMessage)
