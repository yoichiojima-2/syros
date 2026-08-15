"""The preset catalog and its installer.

The catalog is hand-written data that only fails at install time against a real
project, so most of what follows is integrity checking: every options dict, task
list, and cross-reference is validated here rather than in someone's console.
"""

import pytest

from syros import presets
from syros.names import NAME
from syros.options import AgentOptions, options_from_doc
from syros.workflows import normalize_tasks, result_refs

from .fakes import FakeObjects, FakeStore

OPTIONS = AgentOptions(project="proj-1")


async def install(names=None, *, store=None, objects=None, force=False):
    store = store if store is not None else FakeStore()
    objects = objects if objects is not None else FakeObjects()
    summary = await presets.install(
        names, store=store, objects=objects, options=OPTIONS, created_by="tester", force=force
    )
    return summary, store, objects


def named(rows):
    return [row["name"] for row in rows]


# --- catalog integrity ------------------------------------------------------


def test_preset_names_are_unique_and_valid():
    names = [preset.name for preset in presets.CATALOG]
    assert len(names) == len(set(names))
    for preset in presets.CATALOG:
        assert NAME.fullmatch(preset.name), preset.name
        assert NAME.fullmatch(preset.object_name), preset.object_name
        assert preset.kind in presets.KINDS
        assert preset.description.strip()


def test_every_options_dict_round_trips():
    """options_from_doc rejects unknown keys, so this catches a typo'd option
    that would otherwise fail at install time against a real project."""
    for preset in presets.CATALOG:
        if "options" not in preset.spec:
            continue
        run_options = options_from_doc(dict(preset.spec["options"]))
        run_options.project = "proj-1"
        run_options.validate()


def test_workflow_task_lists_normalize():
    for preset in presets.CATALOG:
        if preset.kind != "workflow":
            continue
        tasks = normalize_tasks([dict(task) for task in preset.spec["tasks"]])
        assert tasks, preset.name
        assert all(task["prompt"].strip() for task in tasks)


def test_requires_names_resolve():
    for preset in presets.CATALOG:
        for required in preset.requires:
            presets.get(required)  # raises PresetError if it does not exist


def test_workflow_tasks_only_name_agents_the_preset_requires():
    """A workflow whose task names an agent it does not pull in would fail at
    install with 'no such agent' on a fresh project."""
    for preset in presets.CATALOG:
        if preset.kind != "workflow":
            continue
        closure = {p.name for p in presets.resolve([preset.name])}
        agent_objects = {p.object_name for p in presets.resolve([preset.name]) if p.kind == "agent"}
        assert preset.name in closure
        for task in preset.spec["tasks"]:
            if task.get("agent"):
                assert task["agent"] in agent_objects, (preset.name, task["id"])


def test_result_refs_point_at_real_upstream_tasks():
    for preset in presets.CATALOG:
        if preset.kind != "workflow":
            continue
        ids = {task["id"] for task in preset.spec["tasks"]}
        for task in preset.spec["tasks"]:
            assert result_refs(task["prompt"]) <= ids, (preset.name, task["id"])


def test_every_preset_with_files_ships_them():
    for preset in presets.CATALOG:
        source = preset.spec.get("files")
        if not source:
            continue
        shipped = dict(presets.files(source))
        assert shipped, preset.name
        if preset.kind == "skill":
            assert "SKILL.md" in shipped
            assert shipped["SKILL.md"].startswith(b"---"), "skills need frontmatter"


def test_preset_files_sit_at_the_prefix_they_install_to():
    """data/ mirrors the bucket, so a preset's `files` is its destination.

    This is what keeps the two workspace scopes from nesting: the research
    workspace's files come from workspaces/research/ws, so the recursive walk
    that collects them cannot reach the skill sitting beside them under
    workspaces/research/skills.
    """
    from syros import layout

    for preset in presets.CATALOG:
        source = preset.spec.get("files")
        if not source:
            continue
        if preset.kind == "workspace":
            expected = layout.workspace_prefix(preset.object_name)
        elif preset.kind == "skill":
            expected = layout.skill_prefix(preset.object_name, preset.workspace)
        else:
            raise AssertionError(f"{preset.kind} presets ship no files")
        assert source + "/" == expected, preset.name

    workspace_files = dict(presets.files("workspaces/research/ws"))
    assert set(workspace_files) == {"CLAUDE.md"}  # the skill beside it is not swept in


def test_scheduled_workflows_install_paused():
    """A fresh install must not start spending because someone clicked Install."""
    for preset in presets.CATALOG:
        if preset.kind == "workflow" and preset.spec.get("cron"):
            assert preset.spec["enabled"] is False, preset.name


def test_agents_referencing_a_workspace_require_it():
    workspaces = {p.object_name for p in presets.CATALOG if p.kind == "workspace"}
    for preset in presets.CATALOG:
        named_workspace = (preset.spec.get("options") or {}).get("workspace")
        if named_workspace in workspaces:
            closure = {
                p.object_name for p in presets.resolve([preset.name]) if p.kind == "workspace"
            }
            assert named_workspace in closure, preset.name


def test_catalog_lists_in_install_order():
    kinds = [row["kind"] for row in presets.catalog()]
    assert kinds == sorted(kinds, key=presets.KINDS.index)


def test_definition_includes_the_spec():
    definition = presets.definition("research-pipeline")
    assert definition["kind"] == "workflow"
    assert [task["id"] for task in definition["spec"]["tasks"]] == [
        "plan",
        "sources",
        "landscape",
        "synthesize",
        "review",
    ]


def test_unknown_preset_is_an_error():
    with pytest.raises(presets.PresetError, match="no such preset"):
        presets.get("nope")
    with pytest.raises(presets.PresetError, match="no such preset"):
        presets.resolve(["nope"])


def test_missing_package_data_is_an_error():
    with pytest.raises(presets.PresetError, match="preset data missing"):
        presets.files("skills/does-not-exist")


# --- resolution -------------------------------------------------------------


def test_resolve_expands_the_dependency_closure():
    closure = [preset.name for preset in presets.resolve(["research-pipeline"])]
    assert {"researcher", "writer", "reviewer", "research", "brief"} <= set(closure)
    # ...but only the closure: the standalone presets stay out.
    assert "analyst" not in closure and "daily-brief" not in closure


def test_resolve_orders_workflows_last():
    closure = [p.kind for p in presets.resolve(["research-pipeline"])]
    assert closure[-1] == "workflow"
    assert closure.index("workspace") < closure.index("agent")


def test_resolve_none_is_the_whole_catalog():
    assert len(presets.resolve()) == len(presets.CATALOG)


# --- installation -----------------------------------------------------------


async def test_install_creates_every_kind():
    summary, store, objects = await install()

    assert summary["skipped"] == []
    assert set(store.agents) == {"researcher", "writer", "reviewer", "analyst"}
    assert set(store.workflows) == {"daily-brief", "research-pipeline"}
    assert set(store.workspaces) == {"research"}
    assert set(objects.skills) == {"brief"}
    assert set(objects.workspace_skills["research"]) == {"brief"}


async def test_install_writes_the_workspace_claude_md():
    _summary, _store, objects = await install()
    claude_md = objects.workspaces["research"]["CLAUDE.md"]
    assert b"# research" in claude_md
    assert b"setting_sources" in claude_md  # it explains why it loads


async def test_the_two_brief_skills_are_distinct_and_scoped():
    """Same object name, different scope — the shadowing demo only works if the
    installer keeps them apart."""
    _summary, _store, objects = await install()
    global_skill = objects.skills["brief"]["SKILL.md"]
    workspace_skill = objects.workspace_skills["research"]["brief"]["SKILL.md"]
    assert global_skill != workspace_skill
    assert b"shadows the global" in workspace_skill
    assert set(objects.skills["brief"]) == {"SKILL.md", "reference/structure.md"}


async def test_install_reports_files_written():
    summary, _store, objects = await install()
    written = (
        len(objects.workspaces["research"])
        + len(objects.skills["brief"])
        + len(objects.workspace_skills["research"]["brief"])
    )
    assert summary["files"] == written


async def test_scheduled_workflow_is_stored_paused_with_its_schedule():
    _summary, store, _objects = await install()
    workflow = store.workflows["daily-brief"]
    assert workflow["enabled"] is False
    assert workflow["schedule"] == {"cron": "0 9 * * *", "timezone": "UTC"}
    # The slot is still computed, so resuming has something to re-base from.
    assert workflow["next_run_at"] > 0


async def test_manual_workflow_has_no_schedule():
    _summary, store, _objects = await install()
    workflow = store.workflows["research-pipeline"]
    assert workflow["schedule"] is None
    assert workflow["next_run_at"] == 0


async def test_installed_agent_keeps_its_options():
    _summary, store, _objects = await install()
    options = store.agents["reviewer"]["options"]
    assert options["allowed_tools"] == ["Read", "Grep", "Glob"]
    assert options["disallowed_tools"] == ["Write", "Edit", "Bash"]
    assert options["workspace"] == "research"
    assert store.agents["reviewer"]["created_by"] == "tester"


async def test_workspace_members_are_derived_from_the_installed_agents():
    from syros import workspaces

    _summary, store, _objects = await install()
    members = workspaces.members("research", await store.list_agents())
    assert members == ["reviewer", "writer"]


async def test_parallel_branches_share_an_artifact_space_not_the_workspace():
    """docs/workflow-design.md: a workspace lease would serialize the fan-out."""
    _summary, store, _objects = await install()
    workflow = store.workflows["research-pipeline"]
    assert workflow["options"]["artifacts"] == {"research": "rw"}
    assert workflow["options"]["workspace"] is None

    tasks = {task["id"]: task for task in workflow["tasks"]}
    assert tasks["sources"]["depends_on"] == ["plan"]
    assert tasks["landscape"]["depends_on"] == ["plan"]
    assert tasks["synthesize"]["depends_on"] == ["sources", "landscape"]


async def test_installing_one_workflow_pulls_in_the_agents_it_names():
    """The ordering proof: workflows.create resolves task agents against the
    store, so this raises unless the agents were installed first."""
    summary, store, _objects = await install(["research-pipeline"])

    assert "research-pipeline" in store.workflows
    assert set(store.agents) == {"researcher", "writer", "reviewer"}
    assert "analyst" not in store.agents
    assert "daily-brief" not in store.workflows
    assert named(summary["installed"])[-1] == "research-pipeline"


# --- idempotency ------------------------------------------------------------


async def test_second_install_skips_everything_and_changes_nothing():
    _summary, store, objects = await install()
    before = {
        "agents": dict(store.agents),
        "workflows": dict(store.workflows),
        "skill": dict(objects.skills["brief"]),
    }

    summary, _store, _objects = await install(store=store, objects=objects)

    assert summary["installed"] == []
    assert summary["files"] == 0
    assert {row["reason"] for row in summary["skipped"]} == {"already exists"}
    assert named(summary["skipped"]) == named(presets.catalog())
    assert store.agents == before["agents"]
    assert store.workflows == before["workflows"]
    assert objects.skills["brief"] == before["skill"]


async def test_install_completes_a_partial_install():
    summary, store, objects = await install(["researcher"])
    assert named(summary["installed"]) == ["researcher"]

    summary, _store, _objects = await install(store=store, objects=objects)
    assert named(summary["skipped"]) == ["researcher"]
    assert "reviewer" in named(summary["installed"])


async def test_local_edits_survive_reinstall():
    _summary, store, objects = await install()
    objects.skills["brief"]["SKILL.md"] = b"---\nname: brief\n---\nmine now"
    await store.update_agent("reviewer", **{"options": {"model": "claude-opus-5"}})

    await install(store=store, objects=objects)

    assert objects.skills["brief"]["SKILL.md"] == b"---\nname: brief\n---\nmine now"
    assert store.agents["reviewer"]["options"] == {"model": "claude-opus-5"}


async def test_force_replaces_definitions_and_files():
    _summary, store, objects = await install()
    objects.skills["brief"]["SKILL.md"] = b"mine now"
    await store.update_agent("reviewer", **{"options": {"model": "claude-opus-5"}})

    summary, _store, _objects = await install(store=store, objects=objects, force=True)

    assert summary["skipped"] == []
    assert all(row["replaced"] for row in summary["installed"])
    assert objects.skills["brief"]["SKILL.md"].startswith(b"---")
    assert store.agents["reviewer"]["options"]["allowed_tools"] == ["Read", "Grep", "Glob"]


async def test_force_leaves_a_resumed_workflow_running():
    """Replacing a definition should not silently re-pause a schedule someone
    deliberately resumed."""
    _summary, store, objects = await install()
    await store.update_workflow("daily-brief", enabled=True)

    await install(store=store, objects=objects, force=True)

    assert store.workflows["daily-brief"]["enabled"] is True


# --- status -----------------------------------------------------------------


async def test_status_flags_what_is_installed():
    store, objects = FakeStore(), FakeObjects()
    rows = await presets.status(store=store, objects=objects)
    assert not any(row["installed"] for row in rows)

    await install(["researcher"], store=store, objects=objects)

    rows = {
        row["name"]: row["installed"] for row in await presets.status(store=store, objects=objects)
    }
    assert rows["researcher"] is True
    assert rows["reviewer"] is False


async def test_status_distinguishes_the_two_brief_scopes():
    store, objects = FakeStore(), FakeObjects()
    await install(["brief"], store=store, objects=objects)

    rows = {row["name"]: row for row in await presets.status(store=store, objects=objects)}
    assert rows["brief"]["installed"] is True
    assert rows["brief"]["workspace"] is None
    assert rows["research-brief"]["installed"] is False
    assert rows["research-brief"]["workspace"] == "research"


# --- not clobbering what is already there -----------------------------------


async def test_a_bare_bucket_workspace_counts_as_installed():
    """A workspace is real as a GCS directory with no Firestore doc — the
    console lists those. Installing must not attach the preset's stored options
    to one someone is already using, nor rewrite their CLAUDE.md."""
    objects = FakeObjects(workspaces={"research": {"CLAUDE.md": b"MY OWN NOTES"}})
    summary, store, _objects = await install(["research"], objects=objects)

    assert named(summary["installed"]) == []
    assert named(summary["skipped"]) == ["research"]
    assert objects.workspaces["research"]["CLAUDE.md"] == b"MY OWN NOTES"
    assert "research" not in store.workspaces


async def test_existing_files_are_kept_and_counted():
    objects = FakeObjects(skills={"brief": {"SKILL.md": b"mine"}})
    summary, _store, _objects = await install(["brief"], objects=objects)

    assert objects.skills["brief"]["SKILL.md"] == b"mine"
    assert summary["kept"] == 1
    # ...but the file the skill was missing is still written.
    assert "reference/structure.md" in objects.skills["brief"]
    assert summary["files"] == 1


async def test_install_repairs_a_document_whose_files_never_landed():
    """The failure mode is real: create the doc, then die before the upload.
    Re-running has to finish the job rather than skip it forever."""
    _summary, store, objects = await install(["research"])
    del objects.workspaces["research"]["CLAUDE.md"]

    summary, _store, _objects = await install(["research"], store=store, objects=objects)

    assert named(summary["skipped"]) == ["research"]  # the doc is left alone
    assert "CLAUDE.md" in objects.workspaces["research"]  # the file comes back
    assert summary["files"] == 1


async def test_writing_workspace_files_refuses_a_busy_workspace():
    """Same guard the console applies: a live run checkpoints its whole ws/
    directory at idle, so a file written under it mid-run is lost."""
    import time

    store, objects = FakeStore(), FakeObjects()
    store.workspaces["research"] = {
        "options": {},
        "runtime": {"lease_expires": time.time() + 60},
        "lease_session_id": "sess_live",
    }

    with pytest.raises(presets.PresetError, match="busy"):
        await install(["research"], store=store, objects=objects)


async def test_a_busy_workspace_is_fine_when_there_is_nothing_to_write():
    import time

    _summary, store, objects = await install(["research"])
    store.workspaces["research"]["runtime"] = {"lease_expires": time.time() + 60}

    summary, _store, _objects = await install(["research"], store=store, objects=objects)
    assert named(summary["skipped"]) == ["research"]


# --- concurrency and aliasing -----------------------------------------------


async def test_losing_a_creation_race_is_a_skip_not_an_error(monkeypatch):
    """Two console tabs, or a CLI install racing a click: the button promises
    clicking twice is safe, so a create that loses has to land as a skip."""
    store, objects = FakeStore(), FakeObjects()
    await install(["researcher"], store=store, objects=objects)

    # Pretend the snapshot was taken before that install, so the create runs
    # against a name that has since been taken.
    monkeypatch.setattr(presets._Present, "has", lambda self, preset: False)

    summary = await presets.install(
        ["researcher"], store=store, objects=objects, options=OPTIONS, created_by="tester"
    )
    assert named(summary["installed"]) == []
    assert named(summary["skipped"]) == ["researcher"]


async def test_a_kind_with_no_installer_fails_loudly():
    """Adding a kind to KINDS and forgetting its branch must not report success
    for an object that was never created."""
    store = FakeStore()
    with pytest.raises(presets.PresetError, match="no installer for kind"):
        await presets._create(
            presets.Preset(name="x", kind="connector", description="d", spec={"name": "x"}),
            store=store,
            options=OPTIONS,
            created_by=None,
            replace=False,
        )


async def test_installed_documents_do_not_alias_the_catalog():
    """CATALOG is module-level state in a long-lived console process."""
    _summary, store, _objects = await install(["researcher"])
    stored = store.agents["researcher"]["options"]
    catalog_options = presets.get("researcher").spec["options"]

    assert stored["allowed_tools"] == catalog_options["allowed_tools"]
    assert stored["allowed_tools"] is not catalog_options["allowed_tools"]
    assert stored["artifacts"] is not catalog_options["artifacts"]

    stored["allowed_tools"].append("Bash")
    assert "Bash" not in catalog_options["allowed_tools"]


def test_definition_hands_out_a_copy():
    first = presets.definition("research-pipeline")
    first["spec"]["tasks"][0]["prompt"] = "clobbered"
    assert presets.definition("research-pipeline")["spec"]["tasks"][0]["prompt"] != "clobbered"


def test_presets_are_hashable():
    """frozen=True over a dict field would generate a __hash__ that raises."""
    assert len({presets.get("researcher"), presets.get("writer")}) == 2


# --- ordering ---------------------------------------------------------------


def test_dependencies_within_one_kind_install_in_order():
    """Nothing in the catalog needs this yet; the ordering guarantee should not
    depend on that staying true."""
    make = lambda name, requires=(): presets.Preset(  # noqa: E731
        name=name, kind="agent", description="d", spec={"name": name}, requires=requires
    )
    base, derived = make("base"), make("derived", ("base",))
    assert [p.name for p in presets._by_dependency([derived, base])] == ["base", "derived"]


def test_a_requirement_cycle_is_reported():
    make = lambda name, requires: presets.Preset(  # noqa: E731
        name=name, kind="agent", description="d", spec={"name": name}, requires=requires
    )
    with pytest.raises(presets.PresetError, match="cycle"):
        presets._by_dependency([make("a", ("b",)), make("b", ("a",))])


def test_kind_order_still_dominates_dependency_order():
    ordered = presets.resolve()
    kinds = [preset.kind for preset in ordered]
    assert kinds == sorted(kinds, key=presets.KINDS.index)
    # every requirement lands before the preset that needs it
    placed: set[str] = set()
    for preset in ordered:
        assert all(required in placed for required in preset.requires)
        placed.add(preset.name)
