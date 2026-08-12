"""Firestore → BigQuery snapshot export — analysis without another service.

Firestore is the control plane, but it is a poor analysis surface: no SQL,
no joins, no aggregates. `syros export` reads every session document tree
and loads four flat tables into one BigQuery dataset:

    sessions    one row per session (status, cost, timing, options)
    events      one row per mirrored message (kind + full message as JSON)
    tool_calls  the audit trail (tool, decision, input as JSON)
    approvals   the approval queue (status, decider, latency columns)

Each run replaces the tables (WRITE_TRUNCATE), so the export is idempotent
and needs no watermark state. It runs with the caller's identity — the
sandbox's service account gains nothing.
"""

from __future__ import annotations

import asyncio
import datetime
import json
from typing import Any

SESSIONS_SCHEMA = [
    ("session_id", "STRING", "REQUIRED"),
    ("status", "STRING", "NULLABLE"),
    ("stop_reason", "STRING", "NULLABLE"),
    ("disabled", "BOOL", "NULLABLE"),
    ("cost_usd", "FLOAT64", "NULLABLE"),
    ("seq_head", "INT64", "NULLABLE"),
    ("model", "STRING", "NULLABLE"),
    ("created_by", "STRING", "NULLABLE"),
    ("created_at", "TIMESTAMP", "NULLABLE"),
    ("updated_at", "TIMESTAMP", "NULLABLE"),
    ("options", "JSON", "NULLABLE"),
]

EVENTS_SCHEMA = [
    ("session_id", "STRING", "REQUIRED"),
    ("seq", "INT64", "NULLABLE"),
    ("ts", "TIMESTAMP", "NULLABLE"),
    ("kind", "STRING", "NULLABLE"),
    ("message", "JSON", "NULLABLE"),
]

TOOL_CALLS_SCHEMA = [
    ("session_id", "STRING", "REQUIRED"),
    ("ts", "TIMESTAMP", "NULLABLE"),
    ("tool_name", "STRING", "NULLABLE"),
    ("decision", "STRING", "NULLABLE"),
    ("call_hash", "STRING", "NULLABLE"),
    ("tool_use_id", "STRING", "NULLABLE"),
    ("input", "JSON", "NULLABLE"),
]

APPROVALS_SCHEMA = [
    ("session_id", "STRING", "REQUIRED"),
    ("call_hash", "STRING", "NULLABLE"),
    ("tool_name", "STRING", "NULLABLE"),
    ("status", "STRING", "NULLABLE"),
    ("decided_by", "STRING", "NULLABLE"),
    ("deny_message", "STRING", "NULLABLE"),
    ("requested_at", "TIMESTAMP", "NULLABLE"),
    ("decided_at", "TIMESTAMP", "NULLABLE"),
    ("input", "JSON", "NULLABLE"),
]

SCHEMAS: dict[str, list[tuple[str, str, str]]] = {
    "sessions": SESSIONS_SCHEMA,
    "events": EVENTS_SCHEMA,
    "tool_calls": TOOL_CALLS_SCHEMA,
    "approvals": APPROVALS_SCHEMA,
}


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


def session_row(session: dict[str, Any]) -> dict[str, Any]:
    options = session.get("options") or {}
    cost = session.get("cost_usd")
    return {
        "session_id": session["id"],
        "status": session.get("status"),
        "stop_reason": session.get("stop_reason"),
        "disabled": bool(session.get("disabled")),
        "cost_usd": float(cost) if cost is not None else None,
        "seq_head": session.get("seq_head"),
        "model": options.get("model"),
        "created_by": session.get("created_by"),
        "created_at": _timestamp(session.get("created_at")),
        "updated_at": _timestamp(session.get("updated_at")),
        "options": _json(options),
    }


def event_row(session_id: str, event: dict[str, Any]) -> dict[str, Any]:
    message = event.get("message") or {}
    return {
        "session_id": session_id,
        "seq": event.get("seq"),
        "ts": _timestamp(event.get("ts")),
        "kind": message.get("kind"),
        "message": _json(message),
    }


def tool_call_row(session_id: str, call: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "ts": _timestamp(call.get("ts")),
        "tool_name": call.get("tool_name"),
        "decision": call.get("decision"),
        "call_hash": call.get("call_hash"),
        "tool_use_id": call.get("tool_use_id"),
        "input": _json(call.get("input")),
    }


def approval_row(session_id: str, approval: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "call_hash": approval.get("call_hash"),
        "tool_name": approval.get("tool_name"),
        "status": approval.get("status"),
        "decided_by": approval.get("decided_by"),
        "deny_message": approval.get("deny_message"),
        "requested_at": _timestamp(approval.get("requested_at")),
        "decided_at": _timestamp(approval.get("decided_at")),
        "input": _json(approval.get("input")),
    }


async def _all_events(store: Any, session_id: str, page_size: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = 0
    while True:
        events = await store.list_events(session_id, after=cursor, limit=page_size)
        for event in events:
            cursor = int(event["seq"])
            rows.append(event_row(session_id, event))
        if len(events) < page_size:
            return rows


async def collect(store: Any, page_size: int = 500) -> dict[str, list[dict[str, Any]]]:
    """Read the full session tree from the store into flat row lists."""
    sessions = await store.list_sessions(limit=None)
    tables: dict[str, list[dict[str, Any]]] = {
        "sessions": [session_row(s) for s in sessions],
        "events": [],
        "tool_calls": [],
        "approvals": [],
    }
    for session in sessions:
        session_id = session["id"]
        events, calls, approvals = await asyncio.gather(
            _all_events(store, session_id, page_size),
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
