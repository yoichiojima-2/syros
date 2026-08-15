"""Firestore → BigQuery snapshot export — analysis without another service.

Firestore is the control plane, but it is a poor analysis surface: no SQL,
no joins, no aggregates. `syros export` reads every session document tree
and loads five flat tables into one BigQuery dataset:

    sessions    one row per session (status, cost, timing, options)
    events      one row per journal record, every branch (envelope + rendered
                message as JSON)
    tool_calls  the audit trail, read out of the journal's tool_call records
    approvals   the approval queue (status, decider, latency columns)
    agents      the stored run configurations (name, description, options);
                sessions.agent joins a run back to the persona it ran as

Each run replaces the tables (WRITE_TRUNCATE), so the export is idempotent
and needs no watermark state. It runs with the caller's identity — the
sandbox's service account gains nothing.
"""

from __future__ import annotations

import asyncio
import datetime
import json
from collections.abc import Callable
from typing import Any

from .journal import MAIN_BRANCH, event_message
from .store import StoreProtocol, runtime


def _timestamp(value: Any) -> str | None:
    """Firestore hands back datetimes for SERVER_TIMESTAMP fields and floats
    for time.time() fields; BigQuery TIMESTAMP accepts either as ISO 8601."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        value = datetime.datetime.fromtimestamp(value, tz=datetime.timezone.utc)
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return str(value)


def _json(value: Any) -> Any:
    """Round-trip through json so any exotic leaf (datetime, bytes) becomes a
    plain string instead of failing the load job's serialization."""
    if value is None:
        return None
    return json.loads(json.dumps(value, default=str))


# One spec per column: (name, bigquery type, mode, extractor). Both the table
# schema and the row builder derive from it, so a column can't be added to one
# without the other.
Field = tuple[str, str, str, Callable[[dict[str, Any]], Any]]


def _get(name: str) -> Callable[[dict[str, Any]], Any]:
    return lambda doc: doc.get(name)


def _ts(name: str) -> Callable[[dict[str, Any]], Any]:
    return lambda doc: _timestamp(doc.get(name))


def _opt_workspace(doc: dict[str, Any]) -> Any:
    # Stored options predating the rename carry "team"; same column, new name.
    options = doc.get("options") or {}
    return options.get("workspace") or options.get("team")


SESSION_FIELDS: list[Field] = [
    ("session_id", "STRING", "REQUIRED", lambda s: s["id"]),
    ("status", "STRING", "NULLABLE", lambda s: runtime(s).get("status")),
    ("stop_reason", "STRING", "NULLABLE", lambda s: runtime(s).get("stop_reason")),
    ("disabled", "BOOL", "NULLABLE", lambda s: bool(s.get("disabled"))),
    (
        "cost_usd",
        "FLOAT64",
        "NULLABLE",
        lambda s: float(s["cost_usd"]) if s.get("cost_usd") is not None else None,
    ),
    ("seq_head", "INT64", "NULLABLE", _get("seq_head")),
    ("model", "STRING", "NULLABLE", lambda s: (s.get("options") or {}).get("model")),
    ("workspace", "STRING", "NULLABLE", lambda s: _opt_workspace(s)),
    ("created_by", "STRING", "NULLABLE", _get("created_by")),
    ("workflow", "STRING", "NULLABLE", _get("workflow")),
    ("run_id", "STRING", "NULLABLE", _get("run_id")),
    ("task", "STRING", "NULLABLE", _get("task")),
    ("agent", "STRING", "NULLABLE", _get("agent")),
    ("trigger", "STRING", "NULLABLE", _get("trigger")),
    ("created_at", "TIMESTAMP", "NULLABLE", _ts("created_at")),
    ("updated_at", "TIMESTAMP", "NULLABLE", _ts("updated_at")),
    ("options", "JSON", "NULLABLE", lambda s: _json(s.get("options") or {})),
]

EVENT_FIELDS: list[Field] = [
    ("session_id", "STRING", "REQUIRED", _get("session_id")),
    ("uuid", "STRING", "NULLABLE", _get("uuid")),
    ("parent_uuid", "STRING", "NULLABLE", _get("parent_uuid")),
    ("branch", "STRING", "NULLABLE", _get("branch")),
    ("seq", "INT64", "NULLABLE", _get("seq")),
    ("type", "STRING", "NULLABLE", _get("type")),
    ("ts", "TIMESTAMP", "NULLABLE", _ts("ts")),
    # kind/message describe the record's message rendering (None for
    # journal-only records); payload/context are the envelope itself.
    ("kind", "STRING", "NULLABLE", lambda e: (event_message(e) or {}).get("kind")),
    ("message", "JSON", "NULLABLE", lambda e: _json(event_message(e) or {})),
    ("payload", "JSON", "NULLABLE", lambda e: _json(e.get("payload") or {})),
    ("context", "JSON", "NULLABLE", lambda e: _json(e.get("context") or {})),
]

TOOL_CALL_FIELDS: list[Field] = [
    ("session_id", "STRING", "REQUIRED", _get("session_id")),
    ("uuid", "STRING", "NULLABLE", _get("uuid")),
    ("branch", "STRING", "NULLABLE", _get("branch")),
    ("seq", "INT64", "NULLABLE", _get("seq")),
    ("ts", "TIMESTAMP", "NULLABLE", _ts("ts")),
    ("tool_name", "STRING", "NULLABLE", _get("tool_name")),
    ("decision", "STRING", "NULLABLE", _get("decision")),
    ("call_hash", "STRING", "NULLABLE", _get("call_hash")),
    ("tool_use_id", "STRING", "NULLABLE", _get("tool_use_id")),
    ("input", "JSON", "NULLABLE", lambda c: _json(c.get("input"))),
]

APPROVAL_FIELDS: list[Field] = [
    ("session_id", "STRING", "REQUIRED", _get("session_id")),
    ("call_hash", "STRING", "NULLABLE", _get("call_hash")),
    ("tool_name", "STRING", "NULLABLE", _get("tool_name")),
    ("status", "STRING", "NULLABLE", _get("status")),
    ("decided_by", "STRING", "NULLABLE", _get("decided_by")),
    ("deny_message", "STRING", "NULLABLE", _get("deny_message")),
    ("requested_at", "TIMESTAMP", "NULLABLE", _ts("requested_at")),
    ("decided_at", "TIMESTAMP", "NULLABLE", _ts("decided_at")),
    ("input", "JSON", "NULLABLE", lambda a: _json(a.get("input"))),
]

AGENT_FIELDS: list[Field] = [
    ("name", "STRING", "REQUIRED", lambda a: a["name"]),
    ("description", "STRING", "NULLABLE", _get("description")),
    ("created_by", "STRING", "NULLABLE", _get("created_by")),
    ("model", "STRING", "NULLABLE", lambda a: (a.get("options") or {}).get("model")),
    ("workspace", "STRING", "NULLABLE", lambda a: _opt_workspace(a)),
    ("created_at", "TIMESTAMP", "NULLABLE", _ts("created_at")),
    ("updated_at", "TIMESTAMP", "NULLABLE", _ts("updated_at")),
    ("options", "JSON", "NULLABLE", lambda a: _json(a.get("options") or {})),
]

FIELDS: dict[str, list[Field]] = {
    "sessions": SESSION_FIELDS,
    "events": EVENT_FIELDS,
    "tool_calls": TOOL_CALL_FIELDS,
    "approvals": APPROVAL_FIELDS,
    "agents": AGENT_FIELDS,
}

SCHEMAS: dict[str, list[tuple[str, str, str]]] = {
    name: [(n, t, m) for n, t, m, _ in fields] for name, fields in FIELDS.items()
}


def _row(fields: list[Field], doc: dict[str, Any]) -> dict[str, Any]:
    return {name: extract(doc) for name, _, _, extract in fields}


def session_row(session: dict[str, Any]) -> dict[str, Any]:
    return _row(SESSION_FIELDS, session)


def event_row(session_id: str, event: dict[str, Any]) -> dict[str, Any]:
    return _row(EVENT_FIELDS, {**event, "session_id": session_id})


def tool_call_row(session_id: str, call: dict[str, Any]) -> dict[str, Any]:
    return _row(TOOL_CALL_FIELDS, {**call, "session_id": session_id})


def approval_row(session_id: str, approval: dict[str, Any]) -> dict[str, Any]:
    return _row(APPROVAL_FIELDS, {**approval, "session_id": session_id})


def agent_row(agent: dict[str, Any]) -> dict[str, Any]:
    return _row(AGENT_FIELDS, agent)


async def _all_events(
    store: StoreProtocol, session: dict[str, Any], page_size: int
) -> list[dict[str, Any]]:
    session_id = session["id"]
    rows: list[dict[str, Any]] = []
    for branch in sorted(session.get("branches") or {MAIN_BRANCH: {}}):
        cursor = 0
        while True:
            events = await store.list_events(session_id, branch, after=cursor, limit=page_size)
            for event in events:
                cursor = int(event["seq"])
                rows.append(event_row(session_id, event))
            if len(events) < page_size:
                break
    return rows


async def collect(store: StoreProtocol, page_size: int = 500) -> dict[str, list[dict[str, Any]]]:
    """Read the full session tree from the store into flat row lists."""
    sessions = await store.list_sessions(limit=None)
    tables: dict[str, list[dict[str, Any]]] = {
        "sessions": [session_row(s) for s in sessions],
        "events": [],
        "tool_calls": [],
        "approvals": [],
        "agents": [agent_row(a) for a in await store.list_agents()],
    }
    for session in sessions:
        session_id = session["id"]
        events, calls, approvals = await asyncio.gather(
            _all_events(store, session, page_size),
            store.list_tool_calls(session_id),
            store.list_approvals(session_id),
        )
        tables["events"].extend(events)
        tables["tool_calls"].extend(tool_call_row(session_id, c) for c in calls)
        tables["approvals"].extend(approval_row(session_id, a) for a in approvals)
    return tables


def load(
    project: str, tables: dict[str, list[dict[str, Any]]], *, dataset: str = "syros"
) -> dict[str, int]:
    """Load the collected rows into BigQuery, replacing each table."""
    from google.cloud import bigquery

    # The dataset itself is Terraform's (infra/main.tf pins its location and
    # lifecycle); creating it here would demand project-level create rights
    # the documented caller roles don't include.
    client = bigquery.Client(project=project)

    counts: dict[str, int] = {}
    for name, rows in tables.items():
        schema = [bigquery.SchemaField(n, t, mode=m) for n, t, m in SCHEMAS[name]]
        table_id = f"{project}.{dataset}.{name}"
        if not rows:
            # A load job can't truncate to zero rows; recreate the table empty
            # so an emptied collection doesn't leave stale rows behind.
            client.delete_table(table_id, not_found_ok=True)
            client.create_table(bigquery.Table(table_id, schema=schema))
            counts[name] = 0
            continue
        job = client.load_table_from_json(
            rows,
            table_id,
            job_config=bigquery.LoadJobConfig(
                schema=schema,
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            ),
        )
        job.result()
        counts[name] = len(rows)
    return counts
