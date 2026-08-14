"""CLI option assembly — the flags that become AgentOptions for agents/deployments."""

from types import SimpleNamespace

from syros.cli import _run_options


def args(**overrides):
    base = dict(
        model=None,
        system_prompt=None,
        allow=None,
        permission_mode=None,
        workspace=None,
        artifacts=None,
        max_turns=None,
        max_budget_usd=None,
        bigquery=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_bigquery_flag_sets_server_and_pre_allows_tool():
    options = _run_options(args(bigquery=True))
    assert options.mcp_servers == {"bq": {"type": "builtin", "name": "bigquery"}}
    assert options.allowed_tools == ["mcp__bq__query"]


def test_bigquery_flag_does_not_duplicate_an_explicit_allow():
    options = _run_options(args(bigquery=True, allow=["Read", "mcp__bq__query"]))
    assert options.allowed_tools == ["Read", "mcp__bq__query"]


def test_without_flag_no_mcp_servers():
    options = _run_options(args(allow=["Read"]))
    assert options.mcp_servers == {}
    assert options.allowed_tools == ["Read"]


def test_run_options_validate_with_bigquery():
    options = _run_options(args(bigquery=True))
    options.project = "p"
    options.validate()  # the flag emits exactly what validate() accepts
