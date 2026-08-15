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
        skill=None,
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


def test_skill_flag_installs_catalog_names():
    assert _run_options(args(skill=["pdf,xlsx", "docx"])).skills == ["pdf", "xlsx", "docx"]
    # unset stays None, so the stored layer below still decides what is installed
    assert _run_options(args()).skills is None


def test_run_options_validate_with_bigquery():
    options = _run_options(args(bigquery=True))
    options.project = "p"
    options.validate()  # the flag emits exactly what validate() accepts


def test_system_prompt_is_a_plain_replacement():
    """No flag for the default prompt: unset is what resolves to it, and a
    string here is the persona that replaces it."""
    assert _run_options(args(system_prompt="Be terse.")).system_prompt == "Be terse."
    assert _run_options(args()).system_prompt is None


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


async def test_skills_list_shows_where_each_is_installed(monkeypatch, capsys):
    """The catalog listing answers the question a bare listing hid: a skill
    nobody installs mounts nowhere."""
    from syros import cli
    from syros.console import objects

    from .fakes import FakeStore

    class FakeObjects:
        def __init__(self, project, bucket):
            pass

        async def skill_stats(self):
            return {
                "deploy": {"file_count": 1, "total_size": 12, "updated": None},
                "unused": {"file_count": 1, "total_size": 3, "updated": None},
            }

    store = FakeStore()
    await store.update_settings({"options": {"skills": ["deploy"]}})
    await store.create_workspace("ops", {"options": {"skills": ["deploy"]}})
    monkeypatch.setattr(objects, "GcsObjects", FakeObjects)
    monkeypatch.setattr(cli, "_store", lambda args: store)

    await cli._skills(
        SimpleNamespace(action="list", args=[], bucket="b", project="p", workspace=None)
    )

    out = capsys.readouterr().out
    assert "everywhere, ops" in out
    assert "(not installed)" in out


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        # the arity checks argparse can't express: `args` is a catch-all nargs="*"
        (["skills", "push"], "push requires a skill directory"),
        (["skills", "push", "a", "b", "--name", "x"], "--name names one skill"),
    ],
)
def test_skills_push_usage_errors(monkeypatch, capsys, argv, message):
    monkeypatch.setattr(sys, "argv", ["syros", *argv])
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 2
    assert message in capsys.readouterr().err


async def test_skills_push_walks_every_directory_given(monkeypatch, capsys):
    """A glob like ./skills/* expands to many directories — push them all."""
    from syros import cli

    pushed = []
    monkeypatch.setattr(
        cli.env, "default_bucket", lambda bucket, project: bucket or f"{project}-syros"
    )
    monkeypatch.setattr(
        "syros.skills.push",
        lambda project, bucket, path, **kw: (
            pushed.append(path.name)
            or {"skill": path.name, "files": 1, "deleted": 0, "skipped": []}
        ),
    )
    await cli._skills(
        SimpleNamespace(
            action="push",
            args=["one", "two"],
            bucket="b",
            project="p",
            workspace=None,
            name=None,
            replace=False,
        )
    )
    assert pushed == ["one", "two"]
    assert capsys.readouterr().out.count("pushed 1 file(s)") == 2
