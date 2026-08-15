import pytest

from syros.errors import OptionsError
from syros.options import AgentOptions, build_sdk_options, model_env, options_from_doc


def test_requires_project():
    with pytest.raises(OptionsError, match="project"):
        AgentOptions().validate()


def test_project_from_env(monkeypatch):
    monkeypatch.setenv("SYROS_PROJECT", "proj-1")
    options = AgentOptions()
    options.validate()
    assert options.resolved_project() == "proj-1"
    assert options.resolved_bucket() == "proj-1-syros"
    assert options.resolved_job() == "syros-runner"
    assert options.resolved_region() == "asia-northeast1"


def test_rejects_stdio_mcp_server():
    options = AgentOptions(project="p", mcp_servers={"fs": {"type": "stdio", "command": "server"}})
    with pytest.raises(OptionsError, match="stdio"):
        options.validate()


def test_accepts_http_mcp_server():
    AgentOptions(
        project="p", mcp_servers={"gh": {"type": "http", "url": "https://example.com/mcp"}}
    ).validate()


def test_accepts_builtin_bigquery_server():
    AgentOptions(
        project="p", mcp_servers={"bq": {"type": "builtin", "name": "bigquery"}}
    ).validate()


def test_rejects_unknown_builtin_name():
    options = AgentOptions(project="p", mcp_servers={"x": {"type": "builtin", "name": "spanner"}})
    with pytest.raises(OptionsError, match="unknown builtin"):
        options.validate()


def test_rejects_extra_keys_in_builtin_config():
    options = AgentOptions(
        project="p",
        mcp_servers={"bq": {"type": "builtin", "name": "bigquery", "dataset": "other"}},
    )
    with pytest.raises(OptionsError, match="unknown builtin key"):
        options.validate()


def test_rejects_path_like_mcp_key_for_builtin():
    # The key becomes part of the tool name (mcp__{key}__query), so it has to
    # be a plain addressable name.
    options = AgentOptions(
        project="p", mcp_servers={"My BQ": {"type": "builtin", "name": "bigquery"}}
    )
    with pytest.raises(OptionsError, match="mcp server"):
        options.validate()


def test_builtin_survives_serialize_round_trip():
    from syros.options import options_from_doc

    options = AgentOptions(project="p", mcp_servers={"bq": {"type": "builtin", "name": "bigquery"}})
    restored = options_from_doc(options.serialize())
    restored.project = "p"
    restored.validate()
    assert restored.mcp_servers == {"bq": {"type": "builtin", "name": "bigquery"}}


def test_machine_local_options_are_type_errors():
    with pytest.raises(TypeError):
        AgentOptions(cwd="/tmp")  # not a syros option
    with pytest.raises(TypeError):
        AgentOptions(hooks={})


def test_workspace_valid_names():
    for name in ("data-pipeline", "a", "a" * 64, "snake_case-1"):
        AgentOptions(project="p", workspace=name).validate()


def test_workspace_path_like_is_rejected():
    # Path-shaped values stay eager errors, before any GCP call — the spirit
    # of the old TypeError on workspace, now with a helpful message.
    for name in ("/tmp", "a/b", "../x", "", "Upper", ".", "a" * 65):
        with pytest.raises(OptionsError, match="workspace"):
            AgentOptions(project="p", workspace=name).validate()


def test_serialize_round_trip_subset():
    options = AgentOptions(
        system_prompt="be careful",
        model="claude-sonnet-5",
        allowed_tools=["Read"],
        max_turns=5,
        max_budget_usd=1.5,
        workspace="shared-ws",
        can_use_tool=lambda *a: None,  # callables never serialize
    )
    doc = options.serialize()
    assert doc["system_prompt"] == "be careful"
    assert doc["workspace"] == "shared-ws"
    assert doc["max_budget_usd"] == 1.5
    assert "can_use_tool" not in doc
    assert "sandbox" not in doc
    restored = AgentOptions(**doc)
    assert restored.model == "claude-sonnet-5"


def test_build_sdk_options_maps_fields():
    options = AgentOptions(
        system_prompt="sp",
        model="m",
        tools=["Bash"],
        allowed_tools=["Bash"],
        disallowed_tools=["WebSearch"],
        permission_mode="default",
        max_turns=7,
        max_budget_usd=2.0,
    )
    sdk = build_sdk_options(options, cwd="/w", resume="uuid", env={"A": "1"})
    assert sdk.system_prompt == "sp"
    assert sdk.model == "m"
    assert sdk.tools == ["Bash"]
    assert sdk.disallowed_tools == ["WebSearch"]
    assert sdk.permission_mode == "default"
    assert sdk.max_turns == 7
    assert sdk.max_budget_usd == 2.0
    assert sdk.cwd == "/w"
    assert sdk.resume == "uuid"
    assert sdk.env == {"A": "1"}
    assert sdk.setting_sources is None  # no filesystem settings unless the caller opts in

    sdk = build_sdk_options(options, setting_sources=["user"])
    assert sdk.setting_sources == ["user"]


def test_model_env_with_project():
    env = model_env(AgentOptions(project="proj-1"))
    assert env == {
        "CLAUDE_CODE_USE_VERTEX": "1",
        "ANTHROPIC_VERTEX_PROJECT_ID": "proj-1",
        "CLOUD_ML_REGION": "global",
    }


def test_model_env_anthropic_backend_bypasses_vertex(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    env = model_env(AgentOptions(project="proj-1", model_backend="anthropic"))
    assert env == {"ANTHROPIC_API_KEY": "sk-test"}


def test_model_env_anthropic_backend_from_environment(monkeypatch):
    """The sandbox reads its backend from the job env, not from serialized options."""
    monkeypatch.setenv("SYROS_MODEL_BACKEND", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert model_env(AgentOptions(project="proj-1")) == {"ANTHROPIC_API_KEY": "sk-test"}


def test_model_env_anthropic_backend_without_key_raises():
    with pytest.raises(OptionsError, match="ANTHROPIC_API_KEY"):
        model_env(AgentOptions(project="proj-1", model_backend="anthropic"))


def test_model_env_ignores_ambient_key_on_vertex_backend(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert model_env(AgentOptions(project="proj-1"))["CLAUDE_CODE_USE_VERTEX"] == "1"


def test_artifacts_str_means_one_rw_space():
    options = AgentOptions(project="proj-1", artifacts="workspace")
    options.validate()
    assert options.resolved_artifacts() == {"workspace": "rw"}
    assert options.serialize()["artifacts"] == "workspace"


def test_artifacts_dict_modes():
    options = AgentOptions(project="proj-1", artifacts={"workspace": "rw", "inputs": "ro"})
    options.validate()
    assert options.resolved_artifacts() == {"workspace": "rw", "inputs": "ro"}


def test_artifacts_bad_name_raises():
    with pytest.raises(OptionsError, match="artifact space"):
        AgentOptions(project="proj-1", artifacts="Workspace/Reports").validate()


def test_artifacts_bad_mode_raises():
    with pytest.raises(OptionsError, match="mode must be"):
        AgentOptions(project="proj-1", artifacts={"workspace": "write"}).validate()


def test_connectors_serialize_and_validate():
    options = AgentOptions(project="p", connectors=["slack", "google"])
    options.validate()
    doc = options.serialize()
    assert doc["connectors"] == ["slack", "google"]
    assert options_from_doc({"connectors": ["github"]}).connectors == ["github"]
    # old docs predating the field still rebuild (missing key, not unknown key)
    assert options_from_doc({"model": "m"}).connectors is None


def test_skills_serialize_and_validate():
    options = AgentOptions(project="p", skills=["pdf", "xlsx", "pdf"])
    options.validate()
    assert options.serialize()["skills"] == ["pdf", "xlsx", "pdf"]
    # what the runner mounts is de-duplicated, in install order
    assert options.resolved_skills() == ["pdf", "xlsx"]
    # old docs predating the field still rebuild (missing key, not unknown key)
    assert options_from_doc({"model": "m"}).skills is None
    assert options_from_doc({"skills": ["pdf"]}).skills == ["pdf"]


def test_skills_bad_name_rejected():
    # a skill name is a catalog name, never a path into the bucket
    for bad in ("../etc", "a/b", "Upper", ""):
        with pytest.raises(OptionsError):
            AgentOptions(project="p", skills=[bad]).validate()


def test_connectors_unknown_name_rejected():
    with pytest.raises(OptionsError):
        AgentOptions(project="p", connectors=["jira"]).validate()


def test_connectors_mcp_server_collision_rejected():
    options = AgentOptions(
        project="p",
        connectors=["google"],
        mcp_servers={"gmail": {"type": "http", "url": "https://x"}},
    )
    with pytest.raises(OptionsError):
        options.validate()


def test_options_from_doc_maps_legacy_team_to_workspace():
    # Pre-rename session docs stored "team"; resume must keep working.
    restored = options_from_doc({"team": "shared"})
    assert restored.workspace == "shared"
    original = {"team": "shared"}
    options_from_doc(original)
    assert original == {"team": "shared"}  # caller's dict untouched
    # Canonical key wins when both are present.
    assert options_from_doc({"workspace": "new", "team": "old"}).workspace == "new"
