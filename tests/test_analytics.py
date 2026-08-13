"""The export flattens the Firestore session tree into BigQuery-shaped rows."""

from __future__ import annotations

import datetime

from syros.analytics import (
    SCHEMAS,
    approval_row,
    collect,
    event_row,
    session_row,
    tool_call_row,
)
from .fakes import FakeStore


async def _populated_store() -> FakeStore:
    store = FakeStore()
    await store.create_session("sess_a", {"model": "claude-sonnet-5"}, created_by="alice")
    await store.update_session("sess_a", status="idle", cost_usd=0.42, seq_head=2)
    await store.append_event("sess_a", 1, {"kind": "assistant", "content": [{"type": "text"}]})
    await store.append_event("sess_a", 2, {"kind": "result", "total_cost_usd": 0.42})
    await store.record_tool_call(
        "sess_a",
        {"tool_name": "Bash", "input": {"command": "ls"}, "call_hash": "h1", "decision": "allowed"},
    )
    await store.request_approval("sess_a", "h1", "Bash", {"command": "ls"})
    await store.decide_approval("sess_a", "h1", allow=True, decided_by="alice")
    await store.create_session("sess_b", {"model": "claude-haiku-4-5"})
    return store


async def test_collect_flattens_every_collection():
    tables = await collect(await _populated_store())
    assert {r["session_id"] for r in tables["sessions"]} == {"sess_a", "sess_b"}
    assert [r["seq"] for r in tables["events"]] == [1, 2]
    assert tables["events"][1]["kind"] == "result"
    assert tables["tool_calls"][0]["tool_name"] == "Bash"
    assert tables["approvals"][0]["status"] == "allow"
    assert all(r["session_id"] == "sess_a" for r in tables["events"])


async def test_collect_pages_through_long_event_feeds():
    store = FakeStore()
    await store.create_session("sess_a", {})
    for seq in range(1, 8):
        await store.append_event("sess_a", seq, {"kind": "assistant"})
    tables = await collect(store, page_size=3)
    assert [r["seq"] for r in tables["events"]] == list(range(1, 8))


async def test_rows_match_declared_schemas():
    tables = await collect(await _populated_store())
    for name, rows in tables.items():
        columns = [field for field, _, _ in SCHEMAS[name]]
        for row in rows:
            assert list(row) == columns


async def test_timestamps_normalize_to_iso8601():
    when = datetime.datetime(2026, 8, 12, 3, 0, tzinfo=datetime.timezone.utc)
    assert session_row({"id": "s", "created_at": when})["created_at"] == when.isoformat()
    assert event_row("s", {"seq": 1, "ts": when.timestamp()})["ts"] == when.isoformat()
    assert tool_call_row("s", {})["ts"] is None
    assert approval_row("s", {"requested_at": when})["requested_at"] == when.isoformat()


async def test_workspace_column_reads_from_options():
    assert session_row({"id": "s", "options": {"workspace": "shared"}})["workspace"] == "shared"
    assert session_row({"id": "s"})["workspace"] is None
    assert ("workspace", "STRING", "NULLABLE") in SCHEMAS["sessions"]


async def test_unknown_cost_stays_null_not_zero():
    assert session_row({"id": "s"})["cost_usd"] is None
    assert session_row({"id": "s", "cost_usd": 0.0})["cost_usd"] == 0.0


async def test_json_payloads_survive_exotic_leaves():
    when = datetime.datetime(2026, 8, 12, tzinfo=datetime.timezone.utc)
    row = tool_call_row("s", {"input": {"deadline": when, "args": ["ls"]}})
    assert row["input"] == {"deadline": str(when), "args": ["ls"]}
