"""The built-in BigQuery tool: read-only SQL over the project, from inside the sandbox.

`syros export` puts the control plane in BigQuery; this is the other half — an
in-process MCP server so an agent can *ask questions of it*, which is what makes
an unattended "audit last night's runs" schedule possible without a host-side
relay.

Enabled by reference, never by object: options travel through Firestore, so a
session asks for {"type": "builtin", "name": "bigquery"} in mcp_servers and the
runner swaps in the live server (runner.resolve_mcp_servers). Read-only is IAM's
job — the runner identity gets dataViewer at most, and only when the deployment
opts in (infra: sandbox_bigquery). Everything here is the second layer: refuse
what BigQuery's own dry run doesn't classify as a SELECT, refuse an estimate
over the byte cap before spending it, set maximum_bytes_billed on the real job,
and cap the rows and bytes that come back so one query can't eat the turn.
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import decimal
import json
from collections.abc import Callable
from typing import Any

from .analytics import SCHEMAS
from .env import BigQueryEnv

SERVER_VERSION = "1.0.0"
QUERY_TIMEOUT_SECONDS = 120.0  # a runaway scan must not hold the whole turn
ERROR_CHARS = 2000  # BigQuery errors are actionable; keep them, bounded


def _client(project: str) -> Any:
    from google.cloud import bigquery  # lazy, like analytics.load

    return bigquery.Client(project=project)


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": False}


def _error(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def _reason(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:ERROR_CHARS]


def describe(config: BigQueryEnv) -> str:
    """Tool description, with the audit tables read straight out of
    analytics.SCHEMAS — the export's column list is the only copy, so a column
    added to the snapshot documents itself here for free."""
    tables = "\n".join(
        f"  {name}({', '.join(f'{column} {type_}' for column, type_, _ in fields)})"
        for name, fields in SCHEMAS.items()
    )
    return (
        f"Run one read-only BigQuery SELECT in project {config.project} and get the"
        " rows back as JSON.\n\n"
        "Only SELECT is accepted (the identity behind this tool can read, never"
        f" write). The query is dry-run first and refused above {config.max_bytes}"
        f" bytes scanned; results are capped at {config.max_rows} rows /"
        f" {config.max_result_bytes} bytes, so aggregate and LIMIT in SQL rather"
        " than paging.\n\n"
        f"syros's own audit tables live in `{config.project}.{config.dataset}`:\n"
        f"{tables}\n"
        "They are a snapshot written by `syros export`, not a live feed — check"
        f" MAX(updated_at) FROM `{config.project}.{config.dataset}.sessions` before"
        " concluding anything about the last few hours. JSON columns (options,"
        " message, input) are queried with JSON_VALUE/JSON_QUERY.\n"
        "Any other dataset in the project is queryable too if the identity can"
        " read it; INFORMATION_SCHEMA.SCHEMATA lists them."
    )


def _dry_run(client: Any, sql: str) -> tuple[str, int]:
    """One call buys both guards: BigQuery's own parse (statement_type — a
    multi-statement script comes back as SCRIPT, not SELECT) and the scan
    estimate."""
    from google.cloud import bigquery

    job = client.query(sql, job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False))
    return (job.statement_type or "UNKNOWN"), int(job.total_bytes_processed or 0)


def _execute(client: Any, sql: str, config: BigQueryEnv) -> tuple[list[dict[str, Any]], int]:
    from google.cloud import bigquery

    job = client.query(
        sql,
        job_config=bigquery.QueryJobConfig(
            use_legacy_sql=False,
            # the dry run is advice; this is the ceiling BigQuery enforces
            maximum_bytes_billed=config.max_bytes,
            labels={"syros": "agent-query"},  # spend shows up per-label in billing
        ),
    )
    # max_rows + 1 so truncation is detectable without a second count query
    iterator = job.result(max_results=config.max_rows + 1, timeout=QUERY_TIMEOUT_SECONDS)
    rows = [dict(row) for row in iterator]
    return rows, int(job.total_bytes_billed or 0)


def _cell(value: Any) -> Any:
    """BigQuery hands back rich Python objects (datetime/date, Decimal, bytes,
    dict for STRUCT, list for REPEATED); JSON needs plain leaves, and timestamps
    should read the same as in the exported tables (analytics._timestamp)."""
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    if isinstance(value, dict):
        return {k: _cell(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_cell(v) for v in value]
    return value


def _fit(rows: list[dict[str, Any]], max_bytes: int) -> tuple[list[dict[str, Any]], bool]:
    """Keep rows while the serialized payload fits — one pass, not one dumps()
    per candidate prefix."""
    kept: list[dict[str, Any]] = []
    size = 0
    for row in rows:
        size += len(json.dumps(row, default=str).encode()) + 1
        if size > max_bytes:
            return kept, True
        kept.append(row)
    return kept, False


def format_result(
    rows: list[dict[str, Any]], estimate: int, billed: int, config: BigQueryEnv
) -> str:
    """The JSON envelope the agent reads: rows plus what was elided and why."""
    coerced = [{k: _cell(v) for k, v in row.items()} for row in rows]
    over_row_cap = len(coerced) > config.max_rows
    coerced, over_byte_cap = _fit(coerced[: config.max_rows], config.max_result_bytes)
    payload: dict[str, Any] = {
        "rows": coerced,
        "row_count": len(coerced),
        "bytes_processed": estimate,
        "bytes_billed": billed,
    }
    if over_row_cap or over_byte_cap:
        payload["truncated"] = (
            f"returned {len(coerced)} rows (cap {config.max_rows} rows /"
            f" {config.max_result_bytes} bytes) — there are more; use LIMIT,"
            " ORDER BY, or aggregate in SQL"
        )
    return json.dumps(payload, default=str, ensure_ascii=False)


async def run_query(client: Any, sql: str, config: BigQueryEnv) -> dict[str, Any]:
    """Dry-run guard + capped query, as an SDK tool result. Never raises: a
    failure the agent can fix (bad SQL, too many bytes, denied dataset) is worth
    more to it as text than as a crashed turn."""
    if not sql.strip():
        return _error("sql is required")
    try:
        statement_type, estimate = await asyncio.to_thread(_dry_run, client, sql)
    except Exception as exc:
        return _error(f"BigQuery rejected the query: {_reason(exc)}")
    if statement_type != "SELECT":
        return _error(
            f"only SELECT is allowed here (BigQuery parsed this as {statement_type});"
            " the sandbox identity has read access only"
        )
    if estimate > config.max_bytes:
        return _error(
            f"query would scan {estimate} bytes, over the {config.max_bytes} cap —"
            " narrow the columns, filter on a partition column, or aggregate in SQL"
        )
    try:
        rows, billed = await asyncio.to_thread(_execute, client, sql, config)
    except Exception as exc:
        return _error(f"query failed: {_reason(exc)}")
    return _ok(format_result(rows, estimate, billed, config))


def build_server(key: str, config: BigQueryEnv, *, client_factory: Callable[[str], Any] = _client):
    """The in-process SDK server for one session; `key` is the mcp_servers key,
    so the agent sees the tool as mcp__{key}__query. The client is constructed
    on first query, not here — a session that enables the tool but never uses
    it must not pay an auth round-trip."""
    from claude_agent_sdk import ToolAnnotations, create_sdk_mcp_server, tool

    client: Any = None

    def bq() -> Any:
        nonlocal client
        if client is None:
            client = client_factory(config.project)
        return client

    @tool(
        "query",
        describe(config),
        {"sql": str},  # dict schemas make every key required; caps stay env-side
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    async def _query(args: dict[str, Any]) -> dict[str, Any]:
        return await run_query(bq(), str(args.get("sql") or ""), config)

    return create_sdk_mcp_server(name=key, version=SERVER_VERSION, tools=[_query])
