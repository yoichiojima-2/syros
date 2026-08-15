"""The journal envelope: identity, chaining, typed records, compat reads."""

from __future__ import annotations

import pytest

from syros.journal import JournalWriter, build_context, event_message, make_event, new_branch_id

from .fakes import FakeStore

SID = "sess_j"


def test_make_event_mints_identity_and_shape():
    event = make_event(
        "message", {"kind": "user", "content": "hi"}, parent_uuid=None, branch="main", seq=1
    )
    assert set(event) == {"uuid", "parent_uuid", "branch", "seq", "type", "context", "payload"}
    assert event["uuid"] and event["parent_uuid"] is None
    assert event["branch"] == "main" and event["seq"] == 1
    other = make_event("message", {}, parent_uuid=None, branch="main", seq=1)
    assert other["uuid"] != event["uuid"]


def test_make_event_rejects_unknown_type():
    with pytest.raises(ValueError, match="unknown event type"):
        make_event("nonsense", {}, parent_uuid=None, branch="main", seq=1)


def test_event_message_renders_messages_and_prompts_only():
    message = make_event(
        "message", {"kind": "assistant", "content": []}, parent_uuid=None, branch="main", seq=1
    )
    assert event_message(message) == {"kind": "assistant", "content": []}
    prompt = make_event(
        "prompt", {"text": "do it", "source": "inbox"}, parent_uuid=None, branch="main", seq=2
    )
    assert event_message(prompt) == {"kind": "user", "content": "do it"}
    for type_ in ("tool_call", "approval", "lifecycle"):
        record = make_event(type_, {}, parent_uuid=None, branch="main", seq=3)
        assert event_message(record) is None
    # pre-journal docs ({"seq", "message"}) are not rendered: branch-filtered
    # queries can never return them (documented no-migration assumption)
    assert event_message({"seq": 1, "message": {"kind": "user", "content": "old"}}) is None


def test_build_context_snapshot():
    context = build_context(
        cwd="/work/ws",
        model="m",
        permission_mode="default",
        team="shared",
        lease_id="l1",
        claude_session_id="c1",
    )
    assert context["cwd"] == "/work/ws"
    assert context["model"] == "m"
    assert context["team"] == "shared"
    assert context["lease_id"] == "l1"
    assert context["claude_session_id"] == "c1"
    assert "version" in context and "git" in context


def test_new_branch_ids_are_distinct():
    assert new_branch_id() != new_branch_id()
    assert new_branch_id().startswith("br_")


async def test_writer_chains_parent_uuids_and_seq():
    store = FakeStore()
    await store.create_session(SID, {})
    writer = JournalWriter(store, SID, branch="main", seq=0, tip_uuid=None, context={"model": "m"})
    first = await writer.append("prompt", {"text": "a"})
    second = await writer.append("message", {"kind": "assistant", "content": []})
    assert (first["seq"], second["seq"]) == (1, 2)
    assert first["parent_uuid"] is None
    assert second["parent_uuid"] == first["uuid"]
    assert writer.seq == 2 and writer.tip_uuid == second["uuid"]
    assert first["context"] == {"model": "m"}
    events = await store.list_events(SID, "main", after=0)
    assert [e["uuid"] for e in events] == [first["uuid"], second["uuid"]]


async def test_writer_context_override_per_record():
    store = FakeStore()
    await store.create_session(SID, {})
    writer = JournalWriter(store, SID, branch="main", seq=0, tip_uuid=None, context={"model": "m"})
    event = await writer.append("lifecycle", {"event": "claimed"}, context={})
    assert event["context"] == {}
