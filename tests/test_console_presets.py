"""Console API tests for the presets surface."""

import pytest

from syros.console.api import ConsoleAPI
from syros.options import AgentOptions

from .fakes import FakeObjects, FakeStore


def make_api(store=None, objects=None):
    return ConsoleAPI(
        store or FakeStore(),
        AgentOptions(project="proj-1"),
        objects=objects or FakeObjects(),
    )


async def test_presets_lists_the_catalog_uninstalled():
    payload = await make_api().presets()

    assert "now" in payload
    rows = {row["name"]: row for row in payload["presets"]}
    assert set(rows) >= {"research", "brief", "research-brief", "researcher", "research-pipeline"}
    assert not any(row["installed"] for row in rows.values())
    assert rows["research-brief"]["workspace"] == "research"


async def test_install_creates_everything_and_reports_it():
    store, objects = FakeStore(), FakeObjects()
    api = make_api(store, objects)

    payload = await api.install_presets({})

    assert payload["ok"] is True
    assert payload["skipped"] == []
    assert {row["kind"] for row in payload["installed"]} == {
        "workspace",
        "skill",
        "agent",
        "workflow",
    }
    assert payload["files"] > 0
    assert "researcher" in store.agents
    assert store.workflows["daily-brief"]["enabled"] is False


async def test_install_marks_rows_installed_afterwards():
    store, objects = FakeStore(), FakeObjects()
    api = make_api(store, objects)
    await api.install_presets({"names": ["researcher"]})

    rows = {row["name"]: row for row in (await api.presets())["presets"]}
    assert rows["researcher"]["installed"] is True
    assert rows["analyst"]["installed"] is False


async def test_install_a_subset_pulls_in_its_dependencies():
    store, objects = FakeStore(), FakeObjects()
    api = make_api(store, objects)

    payload = await api.install_presets({"names": ["research-pipeline"]})

    installed = [row["name"] for row in payload["installed"]]
    assert installed[-1] == "research-pipeline"
    assert {"researcher", "writer", "reviewer", "research"} <= set(installed)
    assert "analyst" not in installed


async def test_second_install_skips_rather_than_conflicting():
    store, objects = FakeStore(), FakeObjects()
    api = make_api(store, objects)
    await api.install_presets({})

    payload = await api.install_presets({})

    assert payload["installed"] == []
    assert payload["files"] == 0
    assert {row["reason"] for row in payload["skipped"]} == {"already exists"}


async def test_force_replaces_an_edited_preset():
    store, objects = FakeStore(), FakeObjects()
    api = make_api(store, objects)
    await api.install_presets({})
    objects.skills["brief"]["SKILL.md"] = b"mine"

    payload = await api.install_presets({"force": True})

    assert payload["skipped"] == []
    assert objects.skills["brief"]["SKILL.md"].startswith(b"---")


async def test_install_records_the_console_user():
    store, objects = FakeStore(), FakeObjects()
    await make_api(store, objects).install_presets({"names": ["researcher"]})
    assert store.agents["researcher"]["created_by"]


async def test_bad_names_payload_is_rejected():
    with pytest.raises(ValueError, match="names must be a list"):
        await make_api().install_presets({"names": "researcher"})


async def test_unknown_preset_name_is_an_error():
    from syros.presets import PresetError

    with pytest.raises(PresetError, match="no such preset"):
        await make_api().install_presets({"names": ["nope"]})


async def test_payload_is_json_safe():
    import json

    api = make_api()
    json.dumps(await api.presets())
    json.dumps(await api.install_presets({}))
