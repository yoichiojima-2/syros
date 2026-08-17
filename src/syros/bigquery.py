"""The built-in BigQuery tools: read-only SQL over the project, plus the
agent's own dataset to keep structured results in.

`syros export` puts the control plane in BigQuery; `query` is the other half —
an in-process MCP server so an agent can *ask questions of it*, which is what
makes an unattended "audit last night's runs" schedule possible without a
host-side relay. The write tools (`tables`, `create_table`, `insert`,
`query_into`, `drop_table`) are the durable other end: somewhere an agent can
put a table it builds up run after run, which neither the session journal nor
an artifact file can be.

Enabled by reference, never by object: options travel through Firestore, so a
session asks for {"type": "builtin", "name": "bigquery"} in mcp_servers and the
runner swaps in the live server (runner.resolve_mcp_servers); `"write": true`
adds the write tools.

Two datasets, and the split is the security model. Read-only over the project
is IAM's job — the runner identity gets dataViewer at most, and only when the
deployment opts in (infra: sandbox_bigquery). Writes are IAM's job too:
dataEditor scoped to *one* dataset (infra: sandbox_bigquery_write), never the
one holding syros's own sessions/events/run_log tables. That matters because
the agent shares the runner's service account and has a shell — anything these
tools refuse, `bq` would still do. So the guards here are the second layer, not
the boundary: refuse what BigQuery's dry run doesn't classify as a SELECT,
refuse an estimate over the byte cap before spending it, set
maximum_bytes_billed on the real job, cap the rows and bytes that come back,
and build every write target from the deployment's dataset plus a validated
bare table name so no argument to these tools can name a table in the other
dataset.
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import decimal
import json
from collections.abc import Callable
from typing import Any

from .analytics import RUN_LOG_SCHEMA, RUN_LOG_TABLE, SCHEMAS
from .env import BigQueryEnv
from .errors import OptionsError

SERVER_VERSION = "1.1.0"
QUERY_TIMEOUT_SECONDS = 120.0  # a runaway scan must not hold the whole turn
WRITE_TIMEOUT_SECONDS = 60.0  # metadata calls and streaming inserts are quick
ERROR_CHARS = 2000  # BigQuery errors are actionable; keep them, bounded
LIST_TABLES_LIMIT = 100  # tables described by `tables`, in BigQuery's name order

# Scalar BigQuery types create_table accepts. An allowlist, so a typo comes
# back as an actionable tool error instead of a 400 from the API; STRUCT and
# ARRAY are out because the flat (name, type, mode) column spec cannot
# describe their subfields anyway.
COLUMN_TYPES = (
    "STRING",
    "BYTES",
    "INT64",
    "FLOAT64",
    "NUMERIC",
    "BIGNUMERIC",
    "BOOL",
    "DATE",
    "DATETIME",
    "TIME",
    "TIMESTAMP",
    "JSON",
)
COLUMN_MODES = ("NULLABLE", "REQUIRED", "REPEATED")
WRITE_MODES = {"append": "WRITE_APPEND", "truncate": "WRITE_TRUNCATE"}


def _client(project: str) -> Any:
    from google.cloud import bigquery  # lazy, like analytics.load

    return bigquery.Client(project=project)


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": False}


def _error(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def _reason(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:ERROR_CHARS]


def _columns(fields: list[tuple[str, str, str]]) -> str:
    return ", ".join(f"{column} {type_}" for column, type_, _ in fields)


def describe(config: BigQueryEnv, *, write: bool = False) -> str:
    """Tool description, with the audit tables read straight out of
    analytics.SCHEMAS — the export's column list is the only copy, so a column
    added to the snapshot documents itself here for free."""
    tables = "\n".join(f"  {name}({_columns(fields)})" for name, fields in SCHEMAS.items())
    own = (
        "\n\nYour own tables are in"
        f" `{config.project}.{config.write_dataset}` — query them from here, and"
        " use the create_table/insert/query_into tools to write them. That"
        f" dataset is the only one you can write to; `{config.dataset}` above is"
        " read-only no matter what."
        if write
        else ""
    )
    return (
        f"Run one read-only BigQuery SELECT in project {config.project} and get the"
        " rows back as JSON.\n\n"
        "Only SELECT is accepted ("
        + (
            "writing is what the create_table/insert/query_into tools are for"
            if write
            else "the identity behind this tool can read, never write"
        )
        + f"). The query is dry-run first and refused above {config.max_bytes}"
        f" bytes scanned; results are capped at {config.max_rows} rows /"
        f" {config.max_result_bytes} bytes, so aggregate and LIMIT in SQL rather"
        " than paging.\n\n"
        f"syros's own audit tables live in `{config.project}.{config.dataset}`:\n"
        f"{tables}\n"
        "They are a snapshot written by `syros export`, not a live feed — check"
        f" MAX(updated_at) FROM `{config.project}.{config.dataset}.sessions` before"
        " concluding anything about the last few hours. JSON columns (options,"
        " message, input) are queried with JSON_VALUE/JSON_QUERY.\n\n"
        f"  {RUN_LOG_TABLE}({_columns(RUN_LOG_SCHEMA)})\n"
        "is the exception: append-only, written live by every run as it ends, and"
        " it keeps rows for sessions since deleted from the control plane. Prefer"
        " it for cost and audit questions — the snapshot above only ever shows"
        " sessions that still exist. Sum run_cost_usd (this run alone); cost_usd"
        " is the session's running total and double-counts if summed. It is"
        " partitioned on released_at, so filter on it to keep the scan small.\n"
        "Any other dataset in the project is queryable too if the identity can"
        " read it; INFORMATION_SCHEMA.SCHEMATA lists them." + own
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


# --- the agent's own dataset ---------------------------------------------
#
# Every function below resolves its table through config.table(), which pins
# project and dataset from deployment config and runs the bare name through
# names.validate_table. That is the only table-id construction in this module:
# no argument to a write tool can address the syros audit dataset.


def _describe_tables(config: BigQueryEnv) -> str:
    return (
        "List the tables in your own BigQuery dataset"
        f" `{config.project}.{config.write_dataset}`, with each one's columns and"
        " row count. Start here before writing — tables persist across sessions,"
        " so the one you want may already exist (and may have been made by"
        " another agent). Read the rows back with the query tool."
    )


def _describe_create_table(config: BigQueryEnv) -> str:
    return (
        "Create a table in your own BigQuery dataset"
        f" `{config.project}.{config.write_dataset}` to keep structured results"
        " in. Give a bare table name (lowercase letters, digits, underscores) and"
        " a flat column list; the dataset is fixed, so do not qualify the name.\n"
        f"Column types: {', '.join(COLUMN_TYPES)}. Column modes:"
        f" {', '.join(COLUMN_MODES)} (default NULLABLE). Nested STRUCT/ARRAY"
        " columns are not supported — use a JSON column for anything that does"
        " not fit flat columns.\n"
        "Set partition_field to a DATE or TIMESTAMP column when the table will"
        " grow over time: queries then filter on it and stay cheap. Fails if the"
        " table already exists — call tables first."
    )


def _describe_insert(config: BigQueryEnv) -> str:
    return (
        "Append rows to one of your tables in"
        f" `{config.project}.{config.write_dataset}`. Rows are JSON objects keyed"
        " by column name; every key must exist in the table's schema, and missing"
        f" columns are written as NULL. At most {config.max_insert_rows} rows and"
        f" {config.max_insert_bytes} bytes per call — split a larger load into"
        " several calls.\n"
        "This is a streaming insert: rows appear in queries within seconds, and"
        " there is no deduplication, so re-sending the same batch after a"
        " successful call duplicates it. Nothing here can update or delete an"
        " existing row."
    )


def _describe_query_into(config: BigQueryEnv) -> str:
    return (
        "Run a SELECT and write its rows straight into one of your tables in"
        f" `{config.project}.{config.write_dataset}`, creating the table from the"
        " query's schema if it does not exist. This is how to summarize a large"
        " table without paging it through the conversation — the rows never come"
        " back to you, only a count.\n"
        "The query may read anything you can read (including syros's own audit"
        f" tables in `{config.dataset}`); only the destination is restricted to"
        " your dataset. Same guards as the query tool: SELECT only, dry-run"
        f" costed, refused over {config.max_bytes} bytes scanned.\n"
        "mode 'append' (default) adds rows; 'truncate' replaces the table's"
        " contents and schema with this query's."
    )


def _describe_drop_table(config: BigQueryEnv) -> str:
    return (
        "Delete one of your tables in"
        f" `{config.project}.{config.write_dataset}`, and every row in it. This"
        " cannot be undone, and the table may be one another agent depends on —"
        " check tables first and prefer leaving data in place. Only your own"
        " dataset can be dropped from; syros's audit tables are not reachable"
        " from this tool."
    )


def _list_tables(client: Any, config: BigQueryEnv) -> dict[str, Any]:
    """Table metadata only — get_table reads schema and row counts without a
    query job, so discovery costs nothing and needs no jobUser."""
    from google.cloud import bigquery

    dataset_ref = bigquery.DatasetReference(config.project, config.write_dataset)
    # One over the cap, so a truncated listing is detectable and can be said
    # out loud rather than looking like the whole dataset.
    listed = list(client.list_tables(dataset_ref, max_results=LIST_TABLES_LIMIT + 1))
    described = []
    for item in listed[:LIST_TABLES_LIMIT]:
        table = client.get_table(item.reference)
        described.append(
            {
                "table": table.table_id,
                "columns": [
                    {"name": field.name, "type": field.field_type, "mode": field.mode}
                    for field in table.schema
                ],
                "rows": table.num_rows,
                "description": table.description,
                "partition_field": getattr(table.time_partitioning, "field", None),
            }
        )
    payload: dict[str, Any] = {"tables": described}
    if len(listed) > LIST_TABLES_LIMIT:
        payload["truncated"] = (
            f"showing the first {LIST_TABLES_LIMIT} tables by name; there are more —"
            " query INFORMATION_SCHEMA.TABLES for the full list"
        )
    return payload


def _schema(columns: list[dict[str, Any]]) -> list[Any]:
    """Validate the agent's column spec into SchemaFields. Raises OptionsError
    (the module's own "you got the arguments wrong" error) so the caller can
    hand the message back as a tool error."""
    from google.cloud import bigquery

    if not columns:
        raise OptionsError("columns is required: a table needs at least one column")
    fields = []
    for column in columns:
        if not isinstance(column, dict):
            raise OptionsError(f"column {column!r} must be an object with name and type")
        name, type_ = column.get("name"), str(column.get("type") or "").upper()
        mode = str(column.get("mode") or "NULLABLE").upper()
        if not isinstance(name, str) or not name:
            raise OptionsError(f"column {column!r} is missing a name")
        if type_ not in COLUMN_TYPES:
            raise OptionsError(
                f"column {name!r}: unknown type {column.get('type')!r} —"
                f" available: {', '.join(COLUMN_TYPES)}"
            )
        if mode not in COLUMN_MODES:
            raise OptionsError(f"column {name!r}: mode must be one of {', '.join(COLUMN_MODES)}")
        fields.append(
            bigquery.SchemaField(name, type_, mode=mode, description=column.get("description"))
        )
    return fields


def _create_table(client: Any, config: BigQueryEnv, args: dict[str, Any]) -> dict[str, Any]:
    from google.cloud import bigquery

    table_id = config.table(str(args.get("table") or ""))
    table = bigquery.Table(table_id, schema=_schema(args.get("columns") or []))
    if description := args.get("description"):
        table.description = str(description)
    if partition := args.get("partition_field"):
        names = {field.name: field.field_type for field in table.schema}
        if names.get(str(partition)) not in ("DATE", "TIMESTAMP", "DATETIME"):
            raise OptionsError(
                f"partition_field {partition!r} must be one of this table's DATE,"
                " DATETIME or TIMESTAMP columns"
            )
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY, field=str(partition)
        )
    created = client.create_table(table, timeout=WRITE_TIMEOUT_SECONDS)
    return {"created": table_id, "columns": len(created.schema)}


def _insert(client: Any, config: BigQueryEnv, table: str, rows: list[Any]) -> dict[str, Any]:
    table_id = config.table(table)
    errors = client.insert_rows_json(table_id, rows, timeout=WRITE_TIMEOUT_SECONDS)
    if errors:
        # Per-row errors; the whole call is reported as failed because a
        # partial insert the agent thinks succeeded is the worse outcome.
        raise RuntimeError(json.dumps(errors, default=str)[:ERROR_CHARS])
    return {"inserted": len(rows), "table": table_id}


def _query_into(
    client: Any, config: BigQueryEnv, table: str, sql: str, mode: str
) -> dict[str, Any]:
    from google.cloud import bigquery

    table_id = config.table(table)
    job = client.query(
        sql,
        job_config=bigquery.QueryJobConfig(
            use_legacy_sql=False,
            destination=table_id,
            write_disposition=WRITE_MODES[mode],
            maximum_bytes_billed=config.max_bytes,
            labels={"syros": "agent-query-into"},
        ),
    )
    iterator = job.result(timeout=QUERY_TIMEOUT_SECONDS)
    return {
        "table": table_id,
        "mode": mode,
        "rows_written": int(getattr(iterator, "total_rows", 0) or 0),
        "bytes_billed": int(job.total_bytes_billed or 0),
    }


def _drop_table(client: Any, config: BigQueryEnv, table: str) -> dict[str, Any]:
    table_id = config.table(table)
    client.delete_table(table_id, not_found_ok=False, timeout=WRITE_TIMEOUT_SECONDS)
    return {"dropped": table_id}


async def _write(what: str, call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run one blocking BigQuery write off the event loop and shape both
    outcomes as tool results. Same contract as run_query: an argument the agent
    can fix, or an IAM denial it cannot, is worth more as text than as a
    crashed turn."""
    try:
        payload = await asyncio.to_thread(call)
    except OptionsError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f"{what} failed: {_reason(exc)}")
    return _ok(json.dumps(payload, default=str, ensure_ascii=False))


async def run_insert(client: Any, table: str, rows: Any, config: BigQueryEnv) -> dict[str, Any]:
    """Cap the batch before spending a round trip on it."""
    if not isinstance(rows, list) or not rows:
        return _error("rows is required: a non-empty list of JSON objects")
    if bad := next((r for r in rows if not isinstance(r, dict)), None):
        return _error(f"every row must be a JSON object keyed by column name, not {bad!r}")
    if len(rows) > config.max_insert_rows:
        return _error(
            f"{len(rows)} rows is over the {config.max_insert_rows} per-call cap —"
            " split the load into several insert calls"
        )
    size = len(json.dumps(rows, default=str).encode())
    if size > config.max_insert_bytes:
        return _error(
            f"rows serialize to {size} bytes, over the {config.max_insert_bytes}"
            " per-call cap — split the load into several insert calls"
        )
    return await _write("insert", lambda: _insert(client, config, table, rows))


async def run_query_into(
    client: Any, table: str, sql: str, mode: str, config: BigQueryEnv
) -> dict[str, Any]:
    """The query guards, then the same SELECT with a destination table. The
    dry run runs first for the same reason as in run_query: a statement that
    is not a SELECT, or one that would scan more than the cap, must be refused
    before it costs anything."""
    if not sql.strip():
        return _error("sql is required")
    if mode not in WRITE_MODES:
        return _error(f"mode must be one of {', '.join(sorted(WRITE_MODES))}")
    try:
        config.table(table)
    except OptionsError as exc:
        return _error(str(exc))
    try:
        statement_type, estimate = await asyncio.to_thread(_dry_run, client, sql)
    except Exception as exc:
        return _error(f"BigQuery rejected the query: {_reason(exc)}")
    if statement_type != "SELECT":
        return _error(
            f"sql must be a SELECT (BigQuery parsed this as {statement_type}); the"
            " destination table is set by the `table` argument, not by the query"
        )
    if estimate > config.max_bytes:
        return _error(
            f"query would scan {estimate} bytes, over the {config.max_bytes} cap —"
            " narrow the columns, filter on a partition column, or aggregate in SQL"
        )
    return await _write("query_into", lambda: _query_into(client, config, table, sql, mode))


def _write_tools(config: BigQueryEnv, bq: Callable[[], Any]) -> list[Any]:
    """The five tools that touch the agent's own dataset. Built only when the
    session asked for write access — a tool that would always come back with
    an IAM denial is worse than no tool at all."""
    from claude_agent_sdk import ToolAnnotations, tool

    @tool(
        "tables",
        _describe_tables(config),
        {"type": "object", "properties": {}},
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    async def _tables(args: dict[str, Any]) -> dict[str, Any]:
        return await _write("tables", lambda: _list_tables(bq(), config))

    @tool(
        "create_table",
        _describe_create_table(config),
        {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "bare table name, not qualified"},
                "columns": {
                    "type": "array",
                    "description": "flat column list, in order",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "type": {"type": "string", "enum": list(COLUMN_TYPES)},
                            "mode": {"type": "string", "enum": list(COLUMN_MODES)},
                            "description": {"type": "string"},
                        },
                        "required": ["name", "type"],
                    },
                },
                "description": {"type": "string", "description": "what the table holds"},
                "partition_field": {
                    "type": "string",
                    "description": "a DATE/TIMESTAMP column to partition by day on",
                },
            },
            "required": ["table", "columns"],
        },
    )
    async def _create(args: dict[str, Any]) -> dict[str, Any]:
        return await _write("create_table", lambda: _create_table(bq(), config, args))

    @tool(
        "insert",
        _describe_insert(config),
        {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "bare table name, not qualified"},
                "rows": {
                    "type": "array",
                    "description": "rows as JSON objects keyed by column name",
                    "items": {"type": "object"},
                },
            },
            "required": ["table", "rows"],
        },
    )
    async def _insert_rows(args: dict[str, Any]) -> dict[str, Any]:
        return await run_insert(bq(), str(args.get("table") or ""), args.get("rows"), config)

    @tool(
        "query_into",
        _describe_query_into(config),
        {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "destination, bare table name"},
                "sql": {"type": "string", "description": "a SELECT"},
                "mode": {"type": "string", "enum": sorted(WRITE_MODES)},
            },
            "required": ["table", "sql"],
        },
    )
    async def _into(args: dict[str, Any]) -> dict[str, Any]:
        return await run_query_into(
            bq(),
            str(args.get("table") or ""),
            str(args.get("sql") or ""),
            str(args.get("mode") or "append"),
            config,
        )

    @tool(
        "drop_table",
        _describe_drop_table(config),
        {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "bare table name, not qualified"},
            },
            "required": ["table"],
        },
        annotations=ToolAnnotations(destructiveHint=True),
    )
    async def _drop(args: dict[str, Any]) -> dict[str, Any]:
        return await _write(
            "drop_table", lambda: _drop_table(bq(), config, str(args.get("table") or ""))
        )

    return [_tables, _create, _insert_rows, _into, _drop]


def build_server(
    key: str,
    config: BigQueryEnv,
    *,
    write: bool = False,
    client_factory: Callable[[str], Any] = _client,
):
    """The in-process SDK server for one session; `key` is the mcp_servers key,
    so the agent sees the tools as mcp__{key}__query and friends. `write` adds
    the tools that touch the agent's own dataset — it is the session's
    convenience switch, not a permission boundary (that is the deployment's
    sandbox_bigquery_write IAM grant). The client is constructed on first call,
    not here — a session that enables the tools but never uses them must not
    pay an auth round-trip."""
    from claude_agent_sdk import ToolAnnotations, create_sdk_mcp_server, tool

    client: Any = None

    def bq() -> Any:
        nonlocal client
        if client is None:
            client = client_factory(config.project)
        return client

    @tool(
        "query",
        describe(config, write=write),
        {"sql": str},  # dict schemas make every key required; caps stay env-side
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    async def _query(args: dict[str, Any]) -> dict[str, Any]:
        return await run_query(bq(), str(args.get("sql") or ""), config)

    tools = [_query, *(_write_tools(config, bq) if write else [])]
    return create_sdk_mcp_server(name=key, version=SERVER_VERSION, tools=tools)
