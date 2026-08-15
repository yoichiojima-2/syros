"""CLI option assembly — the flags that become AgentOptions for agents/workflows."""

import sys
from types import SimpleNamespace

import pytest

from syros.cli import _run_options, main
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


def test_skills_push_without_a_path_is_a_usage_error(monkeypatch, capsys):
    """The one arity check argparse can't express: `args` is a catch-all nargs="*"."""
    monkeypatch.setattr(sys, "argv", ["syros", "skills", "push"])
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 2
    assert "push requires a skill directory" in capsys.readouterr().err
