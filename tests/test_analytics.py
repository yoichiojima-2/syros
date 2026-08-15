"""The export flattens the Firestore session tree into BigQuery-shaped rows."""

from __future__ import annotations

import datetime

from syros.analytics import (
    SCHEMAS,
    agent_row,
    approval_row,
    collect,
    event_row,
    session_row,
    tool_call_row,
)
from .fakes import FakeStore, append_message


async def _populated_store() -> FakeStore:
    store = FakeStore()
    await store.create_session("sess_a", {"model": "claude-sonnet-5"}, created_by="alice")
    await store.update_session("sess_a", status="idle", cost_usd=0.42, seq_head=2)
    await append_message(store, "sess_a", 1, {"kind": "assistant", "content": [{"type": "text"}]})
    await append_message(store, "sess_a", 2, {"kind": "result", "total_cost_usd": 0.42})
    from syros.journal import make_event

    await store.append_event(
        "sess_a",
        make_event(
            "tool_call",
            {
                "tool_name": "Bash",
                "input": {"command": "ls"},
                "call_hash": "h1",
                "decision": "allowed",
            },
            parent_uuid=None,
            branch="main",
            seq=3,
        ),
    )
    await store.request_approval("sess_a", "h1", "Bash", {"command": "ls"})
    await store.decide_approval("sess_a", "h1", allow=True, decided_by="alice")
    await store.create_session("sess_b", {"model": "claude-haiku-4-5"}, agent="reviewer")
    await store.create_agent(
        "reviewer",
        {
            "options": {"model": "claude-haiku-4-5", "workspace": "shared"},
            "description": "nightly review persona",
            "created_by": "alice",
        },
    )
    return store


async def test_collect_flattens_every_collection():
    tables = await collect(await _populated_store())
    assert {r["session_id"] for r in tables["sessions"]} == {"sess_a", "sess_b"}
    assert [r["seq"] for r in tables["events"]] == [1, 2, 3]
    assert tables["events"][1]["kind"] == "result"
    assert tables["events"][2]["type"] == "tool_call"
    assert tables["events"][2]["kind"] is None
    assert tables["tool_calls"][0]["tool_name"] == "Bash"
    assert tables["approvals"][0]["status"] == "allow"
    assert all(r["session_id"] == "sess_a" for r in tables["events"])
    assert [r["name"] for r in tables["agents"]] == ["reviewer"]


async def test_agent_rows_flatten_the_stored_persona():
    tables = await collect(await _populated_store())
    agent = tables["agents"][0]
    assert agent["description"] == "nightly review persona"
    assert agent["model"] == "claude-haiku-4-5"
    assert agent["workspace"] == "shared"
    assert agent["options"] == {"model": "claude-haiku-4-5", "workspace": "shared"}
    # the sessions table carries the join key back to the persona a run used
    by_id = {r["session_id"]: r for r in tables["sessions"]}
    assert by_id["sess_b"]["agent"] == "reviewer"
    assert by_id["sess_a"]["agent"] is None
    assert ("agent", "STRING", "NULLABLE") in SCHEMAS["sessions"]


async def test_agent_timestamps_normalize_to_iso8601():
    when = datetime.datetime(2026, 8, 12, 3, 0, tzinfo=datetime.timezone.utc)
    row = agent_row({"name": "a", "created_at": when, "updated_at": when.timestamp()})
    assert row["created_at"] == when.isoformat()
    assert row["updated_at"] == when.isoformat()


async def test_collect_pages_through_long_event_feeds():
    store = FakeStore()
    await store.create_session("sess_a", {})
    for seq in range(1, 8):
        await append_message(store, "sess_a", seq, {"kind": "assistant"})
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


def test_run_log_row_matches_declared_schema():
    from syros.analytics import RUN_LOG_SCHEMA, run_log_row

    row = run_log_row(
        "sess_a",
        {
            "options": {"model": "m", "workspace": "shared"},
            "created_by": "alice",
            "workflow": "nightly",
            "run_id": "r1",
            "task": "review",
            "agent": "reviewer",
            "trigger": "cron",
        },
        stop_reason="success",
        run_cost_usd=0.25,
        cost_usd=1.25,
        seq_head=6,
        released_at=1700000000.0,
    )
    assert set(row) == {name for name, _, _ in RUN_LOG_SCHEMA}
    assert row["session_id"] == "sess_a"
    assert row["model"] == "m"
    assert row["workspace"] == "shared"
    assert row["run_cost_usd"] == 0.25
    assert row["cost_usd"] == 1.25
    assert row["workflow"] == "nightly"
    assert row["released_at"] == "2023-11-14T22:13:20+00:00"


def test_run_log_row_reads_legacy_team_as_workspace():
    from syros.analytics import run_log_row

    row = run_log_row(
        "sess_old",
        {"options": {"team": "legacy"}},
        stop_reason="success",
        run_cost_usd=0.0,
        cost_usd=0.0,
        seq_head=1,
        released_at=1700000000.0,
    )
    assert row["workspace"] == "legacy"
