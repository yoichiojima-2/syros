"""The built-in BigQuery tool: dry-run guard, caps, and result shaping.

Everything runs against a local fake client — the only google.cloud.bigquery
touch is constructing QueryJobConfig, which is offline.
"""

import datetime
import decimal
import json

from syros.analytics import SCHEMAS
from syros.bigquery import _cell, build_server, describe, run_query
from syros.env import BigQueryEnv

CONFIG = BigQueryEnv(project="p", dataset="syros", max_bytes=1000, max_rows=3, max_result_bytes=400)


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
