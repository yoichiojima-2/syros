"""The built-in BigQuery tool: dry-run guard, caps, and result shaping.

Everything runs against a local fake client — the only google.cloud.bigquery
touch is constructing QueryJobConfig, which is offline.
"""

import datetime
import decimal
import json
from dataclasses import replace

import pytest

from syros.analytics import SCHEMAS
from mcp import types

from syros.bigquery import _cell, _write_tools, build_server, describe, run_query
from syros.env import BigQueryEnv

CONFIG = BigQueryEnv(project="p", dataset="syros", max_bytes=1000, max_rows=3, max_result_bytes=400)
# The same deployment with the agents' own dataset in play. Tiny insert caps
# so the cap tests do not have to build a real 4 MB payload.
WRITE_CONFIG = replace(CONFIG, max_insert_rows=3, max_insert_bytes=300)


class FakeJob:
    def __init__(self, *, statement_type="SELECT", processed=0, billed=0, rows=None, error=None):
        self.statement_type = statement_type
        self.total_bytes_processed = processed
        self.total_bytes_billed = billed
        self._rows = rows or []
        self._error = error
        self.result_kwargs = None

    def result(self, max_results=None, timeout=None):
        self.result_kwargs = {"max_results": max_results, "timeout": timeout}
        if self._error:
            raise self._error
        return iter(self._rows[:max_results])


class FakeBQ:
    """Hands out one FakeJob per query() call, recording (sql, job_config)."""

    def __init__(self, *jobs):
        self._jobs = list(jobs)
        self.calls = []

    def query(self, sql, job_config=None):
        self.calls.append((sql, job_config))
        return self._jobs.pop(0)


def _payload(result):
    assert result["is_error"] is False
    return json.loads(result["content"][0]["text"])


async def test_rows_come_back_as_json():
    client = FakeBQ(
        FakeJob(processed=42),
        FakeJob(rows=[{"a": 1}, {"a": 2}], billed=100),
    )
    payload = _payload(await run_query(client, "SELECT a FROM t", CONFIG))
    assert payload["rows"] == [{"a": 1}, {"a": 2}]
    assert payload["row_count"] == 2
    assert payload["bytes_processed"] == 42
    assert payload["bytes_billed"] == 100
    assert "truncated" not in payload


async def test_empty_result_is_not_an_error():
    client = FakeBQ(FakeJob(), FakeJob())
    payload = _payload(await run_query(client, "SELECT a FROM t WHERE false", CONFIG))
    assert payload["rows"] == []
    assert payload["row_count"] == 0


async def test_non_select_never_runs():
    client = FakeBQ(FakeJob(statement_type="INSERT"))
    result = await run_query(client, "INSERT INTO t VALUES (1)", CONFIG)
    assert result["is_error"] is True
    assert "INSERT" in result["content"][0]["text"]
    assert len(client.calls) == 1  # the write was never submitted


async def test_script_is_refused():
    client = FakeBQ(FakeJob(statement_type="SCRIPT"))
    result = await run_query(client, "CREATE TEMP FUNCTION f() AS (1); SELECT f()", CONFIG)
    assert result["is_error"] is True
    assert "SCRIPT" in result["content"][0]["text"]


async def test_estimate_over_cap_refuses_before_spending():
    client = FakeBQ(FakeJob(processed=5000))
    result = await run_query(client, "SELECT * FROM big", CONFIG)
    assert result["is_error"] is True
    assert "5000" in result["content"][0]["text"]
    assert "1000" in result["content"][0]["text"]
    assert len(client.calls) == 1


async def test_real_job_carries_maximum_bytes_billed():
    client = FakeBQ(FakeJob(), FakeJob(rows=[{"a": 1}]))
    await run_query(client, "SELECT a FROM t", CONFIG)
    dry_config = client.calls[0][1]
    assert dry_config.dry_run is True
    job_config = client.calls[1][1]
    assert not job_config.dry_run
    assert job_config.maximum_bytes_billed == CONFIG.max_bytes
    assert job_config.use_legacy_sql is False


async def test_row_cap_truncates_and_says_so():
    rows = [{"n": i} for i in range(5)]
    exec_job = FakeJob(rows=rows)
    client = FakeBQ(FakeJob(), exec_job)
    payload = _payload(await run_query(client, "SELECT n FROM t", CONFIG))
    assert payload["row_count"] == CONFIG.max_rows
    assert "truncated" in payload
    # max_rows + 1 requested so truncation is detectable without a count query
    assert exec_job.result_kwargs["max_results"] == CONFIG.max_rows + 1


async def test_byte_cap_truncates():
    config = BigQueryEnv(
        project="p", dataset="d", max_bytes=1000, max_rows=100, max_result_bytes=30
    )
    client = FakeBQ(FakeJob(), FakeJob(rows=[{"text": "x" * 20} for _ in range(5)]))
    payload = _payload(await run_query(client, "SELECT text FROM t", config))
    assert payload["row_count"] < 5
    assert "truncated" in payload


async def test_client_errors_become_is_error_not_exceptions():
    client = FakeBQ(FakeJob(), FakeJob(error=RuntimeError("quota exceeded")))
    result = await run_query(client, "SELECT a FROM t", CONFIG)
    assert result["is_error"] is True
    assert "quota exceeded" in result["content"][0]["text"]


async def test_bad_sql_at_dry_run_is_an_error():
    class Rejecting:
        def query(self, sql, job_config=None):
            raise ValueError("Syntax error at [1:1]")

    result = await run_query(Rejecting(), "SELCT", CONFIG)
    assert result["is_error"] is True
    assert "Syntax error" in result["content"][0]["text"]


async def test_missing_sql_is_an_error():
    result = await run_query(FakeBQ(), "  ", CONFIG)
    assert result["is_error"] is True


def test_cells_coerce_datetime_decimal_bytes_and_nesting():
    ts = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)
    row = {
        "ts": ts,
        "day": datetime.date(2026, 1, 2),
        "cost": decimal.Decimal("1.5"),
        "blob": b"\x00\x01",
        "nested": {"inner": ts},
        "repeated": [decimal.Decimal("2"), ts],
    }
    coerced = {k: _cell(v) for k, v in row.items()}
    assert coerced["ts"] == ts.isoformat()
    assert coerced["day"] == "2026-01-02"
    assert coerced["cost"] == 1.5
    assert coerced["blob"] == "AAE="
    assert coerced["nested"] == {"inner": ts.isoformat()}
    assert coerced["repeated"] == [2.0, ts.isoformat()]
    json.dumps(coerced)  # everything is a plain JSON leaf now


def test_description_covers_every_exported_column():
    text = describe(CONFIG)
    for name, fields in SCHEMAS.items():
        assert name in text
        for column, _, _ in fields:
            assert column in text


def test_description_names_the_dataset_and_the_caps():
    text = describe(CONFIG)
    assert "p.syros" in text
    assert str(CONFIG.max_bytes) in text
    assert str(CONFIG.max_rows) in text
    assert "syros export" in text  # the snapshot caveat


def test_build_server_does_not_construct_a_client():
    def exploding_factory(project):
        raise AssertionError("client constructed at build time")

    build_server("bq", CONFIG, client_factory=exploding_factory)


def test_build_server_is_an_sdk_server_named_after_the_key():
    server = build_server("bq", CONFIG, client_factory=lambda project: None)
    # The SDK represents an in-process server as this dict; the name is the
    # mcp_servers key, which is what makes the tool mcp__bq__query.
    assert server["type"] == "sdk"
    assert server["name"] == "bq"


def test_config_from_env_defaults(monkeypatch):
    config = BigQueryEnv.from_env("p")
    assert config == BigQueryEnv(
        project="p", dataset="syros", max_bytes=1 << 30, max_rows=200, max_result_bytes=32_000
    )


def test_config_from_env_overrides(monkeypatch):
    monkeypatch.setenv("SYROS_DATASET", "audit")
    monkeypatch.setenv("SYROS_BQ_MAX_BYTES", "5")
    monkeypatch.setenv("SYROS_BQ_MAX_ROWS", "7")
    monkeypatch.setenv("SYROS_BQ_MAX_RESULT_BYTES", "9")
    config = BigQueryEnv.from_env("p")
    assert (config.dataset, config.max_bytes, config.max_rows, config.max_result_bytes) == (
        "audit",
        5,
        7,
        9,
    )


def test_description_points_cost_questions_at_the_audit_log():
    """The snapshot loses deleted sessions; run_log doesn't. An agent asked
    'what did last month cost' has to be told which one to trust."""
    from syros.analytics import RUN_LOG_SCHEMA

    text = describe(CONFIG)
    assert "run_log" in text
    for column, _, _ in RUN_LOG_SCHEMA:
        assert column in text
    # and which cost column is summable: cost_usd is a running total
    assert "run_cost_usd" in text


# --- the agent's own dataset ----------------------------------------------


class FakeTableRef:
    def __init__(self, table_id):
        self.table_id = table_id
        self.reference = self


class FakeTable:
    def __init__(self, table_id, schema=(), rows=0, description=None, partition=None):
        self.table_id = table_id
        self.schema = list(schema)
        self.num_rows = rows
        self.description = description
        self.time_partitioning = partition


class FakeWriteBQ:
    """Records every write call instead of making it; the recorded table ids
    are what the isolation tests assert on."""

    def __init__(self, *jobs, tables=(), error=None, insert_errors=None):
        self._jobs = list(jobs)
        self._tables = list(tables)
        self._error = error
        self._insert_errors = insert_errors
        self.calls = []
        self.created = []
        self.inserted = []
        self.deleted = []

    def query(self, sql, job_config=None):
        self.calls.append((sql, job_config))
        return self._jobs.pop(0)

    def list_tables(self, dataset_ref, max_results=None):
        self.listed = (dataset_ref.project, dataset_ref.dataset_id, max_results)
        return iter([FakeTableRef(t.table_id) for t in self._tables[:max_results]])

    def get_table(self, ref):
        return next(t for t in self._tables if t.table_id == ref.table_id)

    def create_table(self, table, timeout=None):
        if self._error:
            raise self._error
        self.created.append(table)
        return table

    def insert_rows_json(self, table_id, rows, timeout=None):
        self.inserted.append((table_id, rows))
        return self._insert_errors or []

    def delete_table(self, table_id, not_found_ok=None, timeout=None):
        if self._error:
            raise self._error
        self.deleted.append((table_id, not_found_ok))


def _text(result):
    return result["content"][0]["text"]


async def _tool_names(server):
    """What the session actually sees, asked of the live MCP server rather
    than of the list we handed it."""
    handler = server["instance"].request_handlers[types.ListToolsRequest]
    listed = await handler(types.ListToolsRequest(method="tools/list"))
    return [tool.name for tool in listed.root.tools]


def _server(client, config=WRITE_CONFIG):
    """The write tools bound to a fake client, keyed by name."""
    return {tool.name: tool.handler for tool in _write_tools(config, lambda: client)}


def _call(server, name, args):
    return server[name](args)


def _qualified(ref):
    """google.cloud.bigquery normalizes a table id string into a
    TableReference; this reads it back as the id that was passed in."""
    return f"{ref.project}.{ref.dataset_id}.{ref.table_id}"


async def test_write_tools_are_absent_unless_the_session_asked_for_them():
    read_only = build_server("bq", CONFIG, client_factory=lambda project: None)
    assert await _tool_names(read_only) == ["query"]


async def test_write_tools_are_present_when_the_session_asked():
    server = build_server("bq", WRITE_CONFIG, write=True, client_factory=lambda project: None)
    assert await _tool_names(server) == [
        "query",
        "tables",
        "create_table",
        "insert",
        "query_into",
        "drop_table",
    ]


async def test_the_builtin_advertises_exactly_the_tools_it_builds():
    """options names the tools for the callers that pre-allow them (cli,
    console); the server is where they actually come from."""
    from syros.options import BIGQUERY_TOOLS, BIGQUERY_WRITE_TOOLS

    server = build_server("bq", WRITE_CONFIG, write=True, client_factory=lambda project: None)
    assert await _tool_names(server) == [*BIGQUERY_TOOLS, *BIGQUERY_WRITE_TOOLS]


async def test_a_dataset_qualified_table_name_is_refused():
    """The one test that matters: the write tools pin project and dataset, and
    the table argument is the only part the model controls."""
    client = FakeWriteBQ()
    server = _server(client)
    for name in ("syros.run_log", "p.syros.run_log", "run_log`; DROP", "Run_Log", "", "../x"):
        result = await _call(server, "drop_table", {"table": name})
        assert result["is_error"] is True, name
        assert "invalid table name" in _text(result)
    assert client.deleted == []


async def test_every_write_tool_resolves_into_the_write_dataset():
    client = FakeWriteBQ(FakeJob(processed=1), FakeJob(rows=[]))
    server = _server(client)
    await _call(
        server,
        "create_table",
        {"table": "notes", "columns": [{"name": "a", "type": "STRING"}]},
    )
    await _call(server, "insert", {"table": "notes", "rows": [{"a": "x"}]})
    await _call(server, "query_into", {"table": "notes", "sql": "SELECT 1"})
    await _call(server, "drop_table", {"table": "notes"})
    expected = "p.syros_data.notes"
    assert _qualified(client.created[0].reference) == expected
    assert client.inserted[0][0] == expected
    assert _qualified(client.calls[-1][1].destination) == expected
    assert client.deleted[0][0] == expected


def test_write_dataset_may_not_be_the_audit_dataset():
    with pytest.raises(ValueError, match="cannot be the one holding"):
        BigQueryEnv(
            project="p",
            dataset="syros",
            max_bytes=1,
            max_rows=1,
            max_result_bytes=1,
            write_dataset="syros",
        )


async def test_tables_lists_the_write_dataset_with_schemas():
    from google.cloud import bigquery

    client = FakeWriteBQ(
        tables=[
            FakeTable(
                "notes",
                schema=[bigquery.SchemaField("a", "STRING", mode="NULLABLE")],
                rows=3,
                description="scratch",
            )
        ]
    )
    server = _server(client)
    payload = json.loads(_text(await _call(server, "tables", {})))
    assert payload["tables"] == [
        {
            "table": "notes",
            "columns": [{"name": "a", "type": "STRING", "mode": "NULLABLE"}],
            "rows": 3,
            "description": "scratch",
            "partition_field": None,
        }
    ]
    assert client.listed[:2] == ("p", "syros_data")


async def test_create_table_builds_the_schema_and_partitioning():
    client = FakeWriteBQ()
    server = _server(client)
    result = await _call(
        server,
        "create_table",
        {
            "table": "runs",
            "columns": [
                {"name": "day", "type": "TIMESTAMP", "mode": "REQUIRED"},
                {"name": "note", "type": "STRING"},
            ],
            "description": "one row per run",
            "partition_field": "day",
        },
    )
    assert result["is_error"] is False
    (table,) = client.created
    assert [(f.name, f.field_type, f.mode) for f in table.schema] == [
        ("day", "TIMESTAMP", "REQUIRED"),
        ("note", "STRING", "NULLABLE"),
    ]
    assert table.description == "one row per run"
    assert table.time_partitioning.field == "day"


async def test_create_table_rejects_unknown_types_and_bad_partition_fields():
    server = _server(FakeWriteBQ())
    bad_type = await _call(
        server, "create_table", {"table": "t", "columns": [{"name": "a", "type": "TEXT"}]}
    )
    assert bad_type["is_error"] is True
    assert "unknown type" in _text(bad_type)

    bad_partition = await _call(
        server,
        "create_table",
        {
            "table": "t",
            "columns": [{"name": "a", "type": "STRING"}],
            "partition_field": "a",
        },
    )
    assert bad_partition["is_error"] is True
    assert "partition_field" in _text(bad_partition)


async def test_create_table_reports_a_conflict_instead_of_raising():
    server = _server(FakeWriteBQ(error=RuntimeError("Already Exists: Table p:syros_data.t")))
    result = await _call(
        server, "create_table", {"table": "t", "columns": [{"name": "a", "type": "STRING"}]}
    )
    assert result["is_error"] is True
    assert "Already Exists" in _text(result)


async def test_insert_appends_rows_and_reports_the_count():
    client = FakeWriteBQ()
    server = _server(client)
    payload = json.loads(
        _text(await _call(server, "insert", {"table": "notes", "rows": [{"a": 1}, {"a": 2}]}))
    )
    assert payload == {"inserted": 2, "table": "p.syros_data.notes"}
    assert client.inserted == [("p.syros_data.notes", [{"a": 1}, {"a": 2}])]


async def test_insert_refuses_a_batch_over_the_row_cap():
    client = FakeWriteBQ()
    server = _server(client)
    rows = [{"a": n} for n in range(WRITE_CONFIG.max_insert_rows + 1)]
    result = await _call(server, "insert", {"table": "notes", "rows": rows})
    assert result["is_error"] is True
    assert "per-call cap" in _text(result)
    assert client.inserted == []  # refused before the round trip


async def test_insert_refuses_a_batch_over_the_byte_cap():
    client = FakeWriteBQ()
    server = _server(client)
    rows = [{"a": "x" * 200}, {"a": "y" * 200}]
    result = await _call(server, "insert", {"table": "notes", "rows": rows})
    assert result["is_error"] is True
    assert "bytes" in _text(result)
    assert client.inserted == []


async def test_insert_reports_per_row_errors_as_a_failure():
    """A partial insert the agent believes succeeded is the worse outcome."""
    client = FakeWriteBQ(insert_errors=[{"index": 0, "errors": [{"reason": "invalid"}]}])
    server = _server(client)
    result = await _call(server, "insert", {"table": "notes", "rows": [{"a": 1}]})
    assert result["is_error"] is True
    assert "invalid" in _text(result)


async def test_insert_rejects_rows_that_are_not_objects():
    server = _server(FakeWriteBQ())
    for rows in ([], "not a list", [1, 2]):
        result = await _call(server, "insert", {"table": "notes", "rows": rows})
        assert result["is_error"] is True


async def test_query_into_sets_the_destination_and_disposition():
    client = FakeWriteBQ(FakeJob(processed=10), FakeJob(rows=[{"a": 1}], billed=20))
    server = _server(client)
    payload = json.loads(
        _text(
            await _call(
                server,
                "query_into",
                {"table": "summary", "sql": "SELECT 1", "mode": "truncate"},
            )
        )
    )
    assert payload["table"] == "p.syros_data.summary"
    assert payload["mode"] == "truncate"
    assert payload["bytes_billed"] == 20
    _, job_config = client.calls[-1]
    assert _qualified(job_config.destination) == "p.syros_data.summary"
    assert job_config.write_disposition == "WRITE_TRUNCATE"
    assert job_config.maximum_bytes_billed == WRITE_CONFIG.max_bytes


async def test_query_into_keeps_the_select_only_guard():
    client = FakeWriteBQ(FakeJob(statement_type="DELETE"))
    server = _server(client)
    result = await _call(server, "query_into", {"table": "t", "sql": "DELETE FROM x"})
    assert result["is_error"] is True
    assert "must be a SELECT" in _text(result)
    assert len(client.calls) == 1  # the dry run, and nothing after it


async def test_query_into_keeps_the_byte_cap():
    client = FakeWriteBQ(FakeJob(processed=WRITE_CONFIG.max_bytes + 1))
    server = _server(client)
    result = await _call(server, "query_into", {"table": "t", "sql": "SELECT 1"})
    assert result["is_error"] is True
    assert "over the" in _text(result)
    assert len(client.calls) == 1


async def test_query_into_validates_the_table_before_spending_a_dry_run():
    client = FakeWriteBQ()
    server = _server(client)
    result = await _call(server, "query_into", {"table": "syros.run_log", "sql": "SELECT 1"})
    assert result["is_error"] is True
    assert client.calls == []


async def test_query_into_rejects_an_unknown_mode():
    server = _server(FakeWriteBQ())
    result = await _call(server, "query_into", {"table": "t", "sql": "SELECT 1", "mode": "replace"})
    assert result["is_error"] is True
    assert "mode must be" in _text(result)


async def test_drop_table_deletes_only_from_the_write_dataset():
    client = FakeWriteBQ()
    server = _server(client)
    payload = json.loads(_text(await _call(server, "drop_table", {"table": "notes"})))
    assert payload == {"dropped": "p.syros_data.notes"}
    assert client.deleted == [("p.syros_data.notes", False)]


def test_write_description_names_the_write_dataset_not_the_audit_one():
    text = describe(WRITE_CONFIG, write=True)
    assert "p.syros_data" in text
    assert "read-only no matter what" in text
    # and the read-only description says nothing about writing
    assert "syros_data" not in describe(WRITE_CONFIG)


def test_data_dataset_from_env(monkeypatch):
    assert BigQueryEnv.from_env("p").write_dataset == "syros_data"
    monkeypatch.setenv("SYROS_BQ_DATA_DATASET", "scratch")
    monkeypatch.setenv("SYROS_BQ_MAX_INSERT_ROWS", "11")
    monkeypatch.setenv("SYROS_BQ_MAX_INSERT_BYTES", "13")
    config = BigQueryEnv.from_env("p")
    assert (config.write_dataset, config.max_insert_rows, config.max_insert_bytes) == (
        "scratch",
        11,
        13,
    )


async def test_tables_says_when_the_listing_is_truncated():
    """A capped listing that looks complete would have an agent conclude a
    table does not exist and create a second one beside it."""
    from syros.bigquery import LIST_TABLES_LIMIT

    client = FakeWriteBQ(tables=[FakeTable(f"t{n}") for n in range(LIST_TABLES_LIMIT + 1)])
    server = _server(client)
    payload = json.loads(_text(await _call(server, "tables", {})))
    assert len(payload["tables"]) == LIST_TABLES_LIMIT
    assert "there are more" in payload["truncated"]
