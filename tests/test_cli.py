"""CLI option assembly — the flags that become AgentOptions for agents/workflows."""

from types import SimpleNamespace

import pytest

from syros.cli import _run_options
from syros.errors import OptionsError


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
        claude_code=False,
        connector=None,
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


def test_claude_code_flag_asks_for_the_preset():
    options = _run_options(args(claude_code=True))
    assert options.system_prompt == {"type": "preset", "preset": "claude_code"}
    options.project = "p"
    options.validate()  # the flag emits exactly what validate() accepts


def test_claude_code_flag_appends_the_system_prompt_instead_of_replacing():
    options = _run_options(args(claude_code=True, system_prompt="Be terse."))
    assert options.system_prompt["append"] == "Be terse."


def test_system_prompt_without_the_flag_stays_a_plain_replacement():
    assert _run_options(args(system_prompt="Be terse.")).system_prompt == "Be terse."


def test_connector_flag_repeatable_and_comma_separated():
    options = _run_options(args(connector=["slack", "github,google"]))
    assert options.connectors == ["slack", "github", "google"]


def test_connector_flag_absent_stays_none():
    assert _run_options(args()).connectors is None


def test_connector_flag_unknown_name_rejected_by_validate():
    options = _run_options(args(connector=["jira"]))
    options.project = "p"
    with pytest.raises(OptionsError, match="jira"):
        options.validate()
