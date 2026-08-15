import asyncio
import base64
import http.client
import json
import threading
import time
from datetime import UTC, datetime

import pytest

import syros.remote
from syros.console.api import (
    MAX_BULK_DELETE,
    Conflict,
    ConsoleAPI,
    NotFound,
    TooLarge,
    derived_state,
    to_jsonable,
)
from syros.errors import OptionsError, SyrosError
from syros.names import validate_file
from syros.options import AgentOptions
from syros.skills import skill_prefix
from syros.workspace import workspace_prefix

from .fakes import FakeStore, append_message


class FakeObjects:
    """Implements syros.console.objects.ObjectStoreProtocol over a name->bytes dict."""

    def __init__(self, workspaces=None, spaces=None, skills=None):
        self.workspaces: dict[str, dict[str, bytes]] = workspaces or {}
        self.spaces: dict[str, dict[str, bytes]] = spaces or {}
        self.skills: dict[str, dict[str, bytes]] = skills or {}
        # tags live beside the bytes, keyed (kind, owner, file) — mirrors GCS
        # custom metadata surviving independently of content rewrites
        self.tags: dict[tuple[str, str, str], list[str]] = {}

    @staticmethod
    def _stats(files):
        return {
            "file_count": len(files),
            "total_size": sum(map(len, files.values())),
            "updated": None,
        }

    async def workspace_stats(self):
        return {name: self._stats(files) for name, files in self.workspaces.items()}

    async def workspace_files(self, name):
        files = self.workspaces.get(name, {})
        return [
            {"name": n, "size": len(b), "updated": None, "tags": self.tags.get(("ws", name, n), [])}
            for n, b in files.items()
        ]

    @staticmethod
    def _check(name, file):
        """GcsObjects validates by building the prefix; mirror that so the fake
        rejects the same names the real object store would."""
        workspace_prefix(name)
        validate_file("workspace file", file)

    async def read_workspace_file(self, name, file):
        import mimetypes

        self._check(name, file)
        files = self.workspaces.get(name, {})
        if file not in files:
            raise FileNotFoundError(file)
        if len(files[file]) > 100:
            raise ValueError("too large")
        return files[file], mimetypes.guess_type(file)[0] or "application/octet-stream"

    async def write_workspace_file(self, name, file, data):
        self._check(name, file)
        self.workspaces.setdefault(name, {})[file] = data

    async def delete_workspace_file(self, name, file):
        self._check(name, file)
        files = self.workspaces.get(name, {})
        if file not in files:
            raise FileNotFoundError(file)
        del files[file]
        self.tags.pop(("ws", name, file), None)

    def _rename(self, kind, files, owner, src, dst):
        if src not in files:
            raise FileNotFoundError(src)
        if src != dst and dst in files:
            raise FileExistsError(dst)
        files[dst] = files.pop(src)
        if (kind, owner, src) in self.tags:
            self.tags[(kind, owner, dst)] = self.tags.pop((kind, owner, src))

    def _delete_prefix(self, files, kind, owner, subpath, max_files):
        matching = [n for n in files if subpath is None or n.startswith(subpath)]
        if len(matching) > max_files:
            raise ValueError(f"{len(matching)} files (limit {max_files})")
        for n in matching:
            del files[n]
            self.tags.pop((kind, owner, n), None)
        return len(matching)

    async def rename_workspace_file(self, name, src, dst):
        self._check(name, src)
        validate_file("workspace file", dst)
        self._rename("ws", self.workspaces.get(name, {}), name, src, dst)

    async def set_workspace_tags(self, name, file, tags):
        self._check(name, file)
        if file not in self.workspaces.get(name, {}):
            raise FileNotFoundError(file)
        self.tags[("ws", name, file)] = tags

    async def delete_workspace_prefix(self, name, subpath, max_files):
        workspace_prefix(name)
        return self._delete_prefix(self.workspaces.get(name, {}), "ws", name, subpath, max_files)

    async def space_stats(self):
        return {name: self._stats(files) for name, files in self.spaces.items()}

    async def list_artifacts(self, space):
        files = self.spaces.get(space, {})
        return [
            {
                "name": n,
                "size": len(b),
                "updated": None,
                "tags": self.tags.get(("space", space, n), []),
            }
            for n, b in files.items()
        ]

    async def read_artifact(self, space, name):
        import mimetypes

        files = self.spaces.get(space, {})
        if name not in files:
            raise FileNotFoundError(name)
        if len(files[name]) > 100:
            raise ValueError("too large")
        return files[name], mimetypes.guess_type(name)[0] or "application/octet-stream"

    async def write_artifact_file(self, space, name, data):
        validate_file("artifact", name)
        self.spaces.setdefault(space, {})[name] = data

    async def delete_artifact_file(self, space, name):
        validate_file("artifact", name)
        files = self.spaces.get(space, {})
        if name not in files:
            raise FileNotFoundError(name)
        del files[name]
        self.tags.pop(("space", space, name), None)

    async def rename_artifact_file(self, space, src, dst):
        validate_file("artifact", src)
        validate_file("artifact", dst)
        self._rename("space", self.spaces.get(space, {}), space, src, dst)

    async def set_artifact_tags(self, space, name, tags):
        validate_file("artifact", name)
        if name not in self.spaces.get(space, {}):
            raise FileNotFoundError(name)
        self.tags[("space", space, name)] = tags

    async def delete_artifact_prefix(self, space, subpath, max_files):
        count = self._delete_prefix(self.spaces.get(space, {}), "space", space, subpath, max_files)
        if count and not self.spaces.get(space):
            self.spaces.pop(space, None)
        return count

    @staticmethod
    def _check_skill(name, file):
        skill_prefix(name)
        validate_file("skill file", file)

    async def skill_stats(self):
        return {name: self._stats(files) for name, files in self.skills.items()}

    async def skill_files(self, name):
        files = self.skills.get(name, {})
        return [{"name": n, "size": len(b), "updated": None} for n, b in files.items()]

    async def read_skill_file(self, name, file):
        import mimetypes

        self._check_skill(name, file)
        files = self.skills.get(name, {})
        if file not in files:
            raise FileNotFoundError(file)
        if len(files[file]) > 100:
            raise ValueError("too large")
        return files[file], mimetypes.guess_type(file)[0] or "application/octet-stream"

    async def write_skill_file(self, name, file, data):
        self._check_skill(name, file)
        self.skills.setdefault(name, {})[file] = data

    async def delete_skill_file(self, name, file):
        self._check_skill(name, file)
        files = self.skills.get(name, {})
        if file not in files:
            raise FileNotFoundError(file)
        del files[file]

    async def delete_skill(self, name):
        skill_prefix(name)
        files = self.skills.pop(name, None)
        if not files:
            raise FileNotFoundError(name)
        return len(files)

    async def sync_official_skills(self):
        self.skills.setdefault("pdf", {})["SKILL.md"] = b"# pdf"
        return {"skills": ["pdf"], "files": 1, "skipped": []}


@pytest.fixture(autouse=True)
def no_job_trigger(monkeypatch):
    triggered = []

    async def fake_trigger(project, region, job, session_id):
        triggered.append((project, region, job, session_id))

    monkeypatch.setattr(syros.remote, "_trigger_job", fake_trigger)
    return triggered


def api(store, **kwargs):
    return ConsoleAPI(store, AgentOptions(project="proj-1"), **kwargs)


# --- derived_state ---


def test_derived_state():
    assert derived_state({"status": "running", "lease_expires": time.time() + 60}) == "running"
    assert derived_state({"status": "running", "lease_expires": time.time() - 1}) == "stalled"
    assert derived_state({"status": "running", "disabled": True}) == "terminated"
    assert derived_state({"status": "terminated"}) == "terminated"
    assert derived_state({"status": "queued"}) == "queued"
    assert derived_state({"status": "idle"}) == "idle"
    # starting: fresh trigger = on its way; a stale one is a job that never came up
    assert derived_state({"status": "starting", "triggered_at": time.time()}) == "starting"
    assert derived_state({"status": "starting", "triggered_at": time.time() - 3600}) == "stalled"
    assert derived_state({"status": "starting"}) == "stalled"
    assert derived_state({"status": "starting", "disabled": True}) == "terminated"


def test_to_jsonable():
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    out = to_jsonable({"a": stamp, "b": [stamp, {"c": 1}], "d": None})
    assert out == {"a": stamp.timestamp(), "b": [stamp.timestamp(), {"c": 1}], "d": None}


# --- poll ---


async def test_poll_events_after_cursor_and_approval_deadline():
    store = FakeStore()
    await store.create_session("sess_1", {"model": "claude-sonnet-5"})
    for seq in (1, 2, 3):
        await append_message(store, "sess_1", seq, {"kind": "assistant", "content": []})
    await store.request_approval("sess_1", "hash1", "Bash", {"command": "rm"})

    result = await api(store, approval_timeout=10.0).poll("sess_1", after=1)

    assert [e["seq"] for e in result["events"]] == [2, 3]
    assert result["session"]["model"] == "claude-sonnet-5"
    assert result["session"]["workspace"] is None
    assert result["session"]["state"] == "queued"
    (approval,) = result["approvals"]
    assert approval["tool_name"] == "Bash"
    assert approval["deadline"] == pytest.approx(result["now"] + 10.0, abs=1.0)


async def test_poll_unknown_session():
    with pytest.raises(NotFound):
        await api(FakeStore()).poll("sess_missing", after=0)


# --- decide ---


async def test_decide_deny_with_message():
    store = FakeStore()
    await store.create_session("sess_1", {})
    await store.request_approval("sess_1", "hash1", "Bash", {"command": "rm"})

    await api(store).decide("sess_1", "hash1", allow=False, message="nope")

    approval = await store.get_approval("sess_1", "hash1")
    assert approval["status"] == "deny"
    assert approval["deny_message"] == "nope"
    assert approval["decided_by"]


async def test_decide_unknown_approval():
    store = FakeStore()
    await store.create_session("sess_1", {})
    with pytest.raises(NotFound):
        await api(store).decide("sess_1", "nope", allow=True)


# --- approvals across sessions ---


async def test_approvals_across_sessions():
    store = FakeStore()
    await store.create_session("sess_1", {})
    await store.create_session("sess_2", {})
    await store.request_approval("sess_1", "hash1", "Bash", {"command": "rm"})
    await store.request_approval("sess_2", "hash2", "Write", {"path": "/x"})
    await store.request_approval("sess_2", "hash3", "Read", {"path": "/y"})
    await store.decide_approval("sess_2", "hash3", allow=True, decided_by="t")

    result = await api(store, approval_timeout=10.0).approvals()

    rows = {a["call_hash"]: a for a in result["approvals"]}
    assert set(rows) == {"hash1", "hash2"}
    assert rows["hash1"]["session_id"] == "sess_1"
    assert rows["hash2"]["session_id"] == "sess_2"
    assert rows["hash1"]["deadline"] == pytest.approx(result["now"] + 10.0, abs=1.0)


# --- create session ---


async def test_create_session_queues_prompt_and_triggers_job(no_job_trigger):
    store = FakeStore()

    result = await api(store).create_session(
        {
            "prompt": "profile the CSVs",
            "options": {"model": "claude-sonnet-5", "workspace": "team", "allowed_tools": ["Read"]},
        }
    )

    sid = result["session_id"]
    assert result["ok"] is True
    session = store.sessions[sid]
    assert session["options"]["model"] == "claude-sonnet-5"
    assert session["options"]["workspace"] == "team"
    assert session["options"]["allowed_tools"] == ["Read"]
    assert session["trigger"] == "console"
    assert session["created_by"]
    (queued,) = store.inbox[sid]
    assert (queued["kind"], queued["text"], queued["consumed"]) == (
        "message",
        "profile the CSVs",
        False,
    )
    assert no_job_trigger == [("proj-1", "asia-northeast1", "syros-runner", sid)]
    # and it shows up as an ordinary session, on its way up
    assert (await api(store).poll(sid, after=0))["session"]["state"] == "starting"


async def test_create_session_carries_connectors(no_job_trigger):
    store = FakeStore()
    # The session form sends connector names only; expansion (URLs + tokens)
    # happens inside the sandbox, so the stored options carry just the list.
    result = await api(store).create_session(
        {"prompt": "post the summary to slack", "options": {"connectors": ["slack", "github"]}}
    )
    session = store.sessions[result["session_id"]]
    assert session["options"]["connectors"] == ["slack", "github"]


async def test_create_session_carries_builtin_bigquery_server(no_job_trigger):
    # What the form's BigQuery toggle posts: the builtin reference plus its
    # pre-allowed tool, passing through options_from_doc/validate unchanged.
    store = FakeStore()

    result = await api(store).create_session(
        {
            "prompt": "audit yesterday",
            "options": {
                "mcp_servers": {"bq": {"type": "builtin", "name": "bigquery"}},
                "allowed_tools": ["mcp__bq__query"],
            },
        }
    )

    session = store.sessions[result["session_id"]]
    assert session["options"]["mcp_servers"] == {"bq": {"type": "builtin", "name": "bigquery"}}
    assert session["options"]["allowed_tools"] == ["mcp__bq__query"]


async def test_create_session_resolves_agent(no_job_trigger):
    store = FakeStore()
    await store.create_agent(
        "reviewer", {"options": {"model": "claude-sonnet-5", "allowed_tools": ["Read", "Grep"]}}
    )

    result = await api(store).create_session(
        {"prompt": "review the diff", "agent": "reviewer", "options": {"model": "claude-opus-5"}}
    )

    session = store.sessions[result["session_id"]]
    assert session["agent"] == "reviewer"
    # stored options are the defaults; the form's explicit model overrides
    assert session["options"]["model"] == "claude-opus-5"
    assert session["options"]["allowed_tools"] == ["Read", "Grep"]


async def test_create_session_unknown_agent(no_job_trigger):
    store = FakeStore()
    with pytest.raises(SyrosError):
        await api(store).create_session({"prompt": "go", "agent": "ghost", "options": {}})
    assert store.sessions == {}


async def test_create_session_requires_a_prompt(no_job_trigger):
    store = FakeStore()
    with pytest.raises(ValueError):
        await api(store).create_session({"prompt": "   ", "options": {}})
    assert store.sessions == {}


async def test_create_session_rejects_unknown_option(no_job_trigger):
    store = FakeStore()
    with pytest.raises(OptionsError):
        await api(store).create_session({"prompt": "go", "options": {"cwd": "/tmp"}})
    assert store.sessions == {}


async def test_create_session_rejects_invalid_option(no_job_trigger):
    store = FakeStore()
    with pytest.raises(OptionsError):
        await api(store).create_session({"prompt": "go", "options": {"workspace": "../escape"}})
    assert store.sessions == {}


# --- prompt / interrupt / kill ---


async def test_prompt_triggers_job_when_idle(no_job_trigger):
    store = FakeStore()
    await store.create_session("sess_1", {})

    result = await api(store).prompt("sess_1", "hello")

    assert result["triggered"] is True
    assert no_job_trigger == [("proj-1", "asia-northeast1", "syros-runner", "sess_1")]
    (queued,) = store.inbox["sess_1"]
    assert (queued["kind"], queued["text"], queued["consumed"]) == ("message", "hello", False)
    assert (await api(store).poll("sess_1", after=0))["session"]["state"] == "starting"


async def test_prompt_skips_trigger_when_lease_active(no_job_trigger):
    store = FakeStore()
    await store.create_session("sess_1", {})
    await store.update_session("sess_1", status="running", lease_expires=time.time() + 60)

    result = await api(store).prompt("sess_1", "hello")

    assert result["triggered"] is False
    assert no_job_trigger == []


async def test_prompt_terminated_session_conflicts(no_job_trigger):
    store = FakeStore()
    await store.create_session("sess_1", {})
    await store.update_session("sess_1", status="terminated")
    with pytest.raises(Conflict):
        await api(store).prompt("sess_1", "hello")
    with pytest.raises(ValueError):
        await api(store).prompt("sess_1", "   ")


async def test_interrupt_and_kill():
    store = FakeStore()
    await store.create_session("sess_1", {})

    await api(store).interrupt("sess_1")
    (queued,) = store.inbox["sess_1"]
    assert (queued["kind"], queued["text"], queued["consumed"]) == ("interrupt", None, False)

    await api(store).kill("sess_1")
    session = await store.get_session("sess_1")
    assert session["runtime"]["status"] == "terminated"
    assert session["disabled"] is True


# --- delete ---


async def test_delete_removes_session_and_history():
    store = FakeStore()
    await store.create_session("sess_1", {})
    await append_message(store, "sess_1", 1, {"kind": "user", "content": "hi"})
    await store.request_approval("sess_1", "hash1", "Bash", {"command": "ls"})

    result = await api(store).delete("sess_1")

    assert result == {"ok": True}
    assert await store.get_session("sess_1") is None
    assert "sess_1" not in store.events
    assert "sess_1" not in store.approvals


async def test_delete_running_session_conflicts():
    store = FakeStore()
    await store.create_session("sess_1", {})
    await store.update_session("sess_1", status="running", lease_expires=time.time() + 60)
    with pytest.raises(Conflict):
        await api(store).delete("sess_1")

    # a stalled session (expired lease) is a dead job — deletable
    await store.update_session("sess_1", lease_expires=time.time() - 1)
    await api(store).delete("sess_1")
    assert await store.get_session("sess_1") is None


async def test_delete_starting_session_conflicts():
    store = FakeStore()
    await store.create_session("sess_1", {})
    await store.mark_starting("sess_1")
    with pytest.raises(Conflict):
        await api(store).delete("sess_1")

    # the execution never came up: past the grace window the row is deletable
    await store.update_session("sess_1", triggered_at=time.time() - 3600)
    await api(store).delete("sess_1")
    assert await store.get_session("sess_1") is None


async def test_delete_unknown_session():
    with pytest.raises(NotFound):
        await api(FakeStore()).delete("sess_missing")


# --- bulk delete ---


async def test_delete_many_removes_every_session():
    store = FakeStore()
    for sid in ("sess_1", "sess_2", "sess_3"):
        await store.create_session(sid, {})
    await append_message(store, "sess_2", 1, {"kind": "user", "content": "hi"})

    result = await api(store).delete_many(["sess_1", "sess_2"])

    assert result == {"ok": True, "deleted": ["sess_1", "sess_2"], "failed": []}
    assert set(store.sessions) == {"sess_3"}
    assert "sess_2" not in store.events


async def test_delete_many_reports_per_session_failures():
    store = FakeStore()
    await store.create_session("sess_ok", {})
    await store.create_session("sess_busy", {})
    await store.update_session("sess_busy", status="running", lease_expires=time.time() + 60)

    result = await api(store).delete_many(["sess_ok", "sess_busy", "sess_gone"])

    # The deletable one still goes; the rest come back with a reason each.
    assert result["ok"] is False
    assert result["deleted"] == ["sess_ok"]
    assert [f["id"] for f in result["failed"]] == ["sess_busy", "sess_gone"]
    assert "running" in result["failed"][0]["error"]
    assert "not found" in result["failed"][1]["error"]
    assert set(store.sessions) == {"sess_busy"}


async def test_delete_many_deduplicates_ids():
    store = FakeStore()
    await store.create_session("sess_1", {})

    result = await api(store).delete_many(["sess_1", "sess_1"])

    assert result == {"ok": True, "deleted": ["sess_1"], "failed": []}


async def test_delete_many_rejects_bad_input():
    store = FakeStore()
    for bad in (None, "sess_1", ["sess_1", 2], []):
        with pytest.raises(ValueError):
            await api(store).delete_many(bad)


async def test_delete_many_rejects_oversized_batch():
    ids = [f"sess_{i}" for i in range(MAX_BULK_DELETE + 1)]
    with pytest.raises(ValueError):
        await api(FakeStore()).delete_many(ids)


# --- workspaces ---


async def test_workspaces_merges_gcs_leases_and_sessions():
    store = FakeStore()
    await store.create_session("sess_1", {"workspace": "team"})
    await store.claim_workspace("team", "sess_1", ttl_seconds=60)
    await store.claim_workspace("stale", "sess_2", ttl_seconds=-1)  # expired lease
    objects = FakeObjects(workspaces={"team": {"a.md": b"aa"}, "orphan": {"b.md": b"b"}})

    result = await api(store, objects=objects).workspaces()

    rows = {w["name"]: w for w in result["workspaces"]}
    assert set(rows) == {"team", "stale", "orphan"}
    assert rows["team"]["busy"] is True
    assert rows["team"]["lease_session_id"] == "sess_1"
    assert rows["team"]["file_count"] == 1
    assert rows["team"]["total_size"] == 2
    assert [s["id"] for s in rows["team"]["sessions"]] == ["sess_1"]
    assert rows["stale"]["busy"] is False
    assert rows["stale"]["lease_session_id"] is None
    assert rows["orphan"]["busy"] is False
    assert rows["orphan"]["sessions"] == []


async def test_workspace_files_and_unknown():
    store = FakeStore()
    objects = FakeObjects(workspaces={"team": {"b.md": b"bb", "a.md": b"a"}})
    console = api(store, objects=objects)

    result = await console.workspace_files("team")
    assert [f["name"] for f in result["files"]] == ["a.md", "b.md"]

    with pytest.raises(NotFound):
        await console.workspace_files("nope")

    # a leased-but-empty workspace exists
    await store.claim_workspace("empty", "sess_1", ttl_seconds=60)
    assert (await console.workspace_files("empty"))["files"] == []


async def test_workspace_file_read_write_delete():
    import mimetypes

    objects = FakeObjects(workspaces={"team": {"notes.md": b"hello"}})
    console = api(FakeStore(), objects=objects)

    data, content_type = await console.workspace_file("team", "notes.md")
    assert data == b"hello"
    assert content_type == mimetypes.guess_type("notes.md")[0]

    result = await console.write_workspace_file("team", "notes.md", "goodbye")
    assert result["ok"] is True and result["size"] == 7
    assert objects.workspaces["team"]["notes.md"] == b"goodbye"

    # creates as well as overwrites, and nested paths are ordinary names here
    await console.write_workspace_file("team", "sub/new.txt", "fresh")
    assert objects.workspaces["team"]["sub/new.txt"] == b"fresh"

    await console.delete_workspace_file("team", "notes.md")
    assert "notes.md" not in objects.workspaces["team"]

    with pytest.raises(NotFound):
        await console.delete_workspace_file("team", "notes.md")
    with pytest.raises(NotFound):
        await console.workspace_file("team", "gone.md")


async def test_workspace_writes_blocked_while_leased():
    store = FakeStore()
    objects = FakeObjects(workspaces={"team": {"notes.md": b"hello"}})
    console = api(store, objects=objects)
    await store.claim_workspace("team", "sess_1", ttl_seconds=60)

    with pytest.raises(Conflict, match="busy"):
        await console.write_workspace_file("team", "notes.md", "edited")
    with pytest.raises(Conflict, match="busy"):
        await console.delete_workspace_file("team", "notes.md")

    # reads stay open while a run holds the lease — only writes would be clobbered
    assert (await console.workspace_file("team", "notes.md"))[0] == b"hello"

    await store.release_workspace("team", "sess_1")
    await console.write_workspace_file("team", "notes.md", "edited")
    assert objects.workspaces["team"]["notes.md"] == b"edited"


async def test_workspace_write_encodings_and_limits(monkeypatch):
    objects = FakeObjects()
    console = api(FakeStore(), objects=objects)

    await console.write_workspace_file(
        "team", "logo.bin", base64.b64encode(b"\xff\xfe\x00").decode(), "base64"
    )
    assert objects.workspaces["team"]["logo.bin"] == b"\xff\xfe\x00"

    with pytest.raises(ValueError, match="base64"):
        await console.write_workspace_file("team", "x.bin", "not base64!", "base64")
    with pytest.raises(ValueError, match="encoding"):
        await console.write_workspace_file("team", "x.bin", "hi", "rot13")

    monkeypatch.setattr("syros.console.api.MAX_PREVIEW_BYTES", 4)
    with pytest.raises(TooLarge):
        await console.write_workspace_file("team", "big.txt", "toolong")


async def test_workspace_file_rejects_bad_names():
    console = api(FakeStore(), objects=FakeObjects(workspaces={"team": {"a.md": b"a"}}))

    for workspace in ("../etc", "Upper", "a/b", ""):
        with pytest.raises(OptionsError):
            await console.workspace_file(workspace, "a.md")

    for file in ("", "/abs", "../escape", "sub/../../escape"):
        with pytest.raises(OptionsError):
            await console.write_workspace_file("team", file, "x")


async def test_workspace_rename():
    store = FakeStore()
    objects = FakeObjects(workspaces={"team": {"a.md": b"aa", "b.md": b"bb"}})
    console = api(store, objects=objects)
    objects.tags[("ws", "team", "a.md")] = ["keep-me"]

    result = await console.rename_workspace_file("team", "a.md", "sub/a.md")
    assert result["ok"] is True and result["file"] == "sub/a.md"
    assert objects.workspaces["team"]["sub/a.md"] == b"aa"
    assert "a.md" not in objects.workspaces["team"]
    assert objects.tags[("ws", "team", "sub/a.md")] == ["keep-me"]

    with pytest.raises(NotFound):
        await console.rename_workspace_file("team", "gone.md", "x.md")
    with pytest.raises(Conflict, match="destination"):
        await console.rename_workspace_file("team", "sub/a.md", "b.md")

    await store.claim_workspace("team", "sess_1", ttl_seconds=60)
    with pytest.raises(Conflict, match="busy"):
        await console.rename_workspace_file("team", "b.md", "c.md")


async def test_workspace_tags():
    store = FakeStore()
    objects = FakeObjects(workspaces={"team": {"a.md": b"aa"}})
    console = api(store, objects=objects)

    result = await console.set_workspace_file_tags("team", "a.md", ["draft", "q3"])
    assert result["tags"] == ["draft", "q3"]
    files = (await console.workspace_files("team"))["files"]
    assert files[0]["tags"] == ["draft", "q3"]

    # empty list clears; duplicates collapse
    await console.set_workspace_file_tags("team", "a.md", ["x", "x"])
    assert objects.tags[("ws", "team", "a.md")] == ["x"]
    await console.set_workspace_file_tags("team", "a.md", [])
    assert objects.tags[("ws", "team", "a.md")] == []

    with pytest.raises(OptionsError):
        await console.set_workspace_file_tags("team", "a.md", ["Bad Tag"])
    with pytest.raises(OptionsError):
        await console.set_workspace_file_tags("team", "a.md", [f"t{i}" for i in range(17)])
    with pytest.raises(NotFound):
        await console.set_workspace_file_tags("team", "gone.md", ["x"])

    await store.claim_workspace("team", "sess_1", ttl_seconds=60)
    with pytest.raises(Conflict, match="busy"):
        await console.set_workspace_file_tags("team", "a.md", ["x"])


async def test_workspace_bulk_delete():
    store = FakeStore()
    objects = FakeObjects(workspaces={"team": {"a.md": b"a", "b.md": b"b"}})
    console = api(store, objects=objects)

    result = await console.delete_workspace_files("team", ["a.md", "a.md", "gone.md"])
    assert result["deleted"] == ["a.md"]
    assert result["ok"] is False
    assert [f["name"] for f in result["failed"]] == ["gone.md"]
    assert objects.workspaces["team"] == {"b.md": b"b"}

    with pytest.raises(ValueError, match="list of strings"):
        await console.delete_workspace_files("team", "a.md")
    with pytest.raises(ValueError, match="too many"):
        await console.delete_workspace_files("team", [f"f{i}" for i in range(51)])

    await store.claim_workspace("team", "sess_1", ttl_seconds=60)
    with pytest.raises(Conflict, match="busy"):
        await console.delete_workspace_files("team", ["b.md"])


async def test_workspace_folder_delete(monkeypatch):
    objects = FakeObjects(
        workspaces={
            "team": {"docs/.keep": b"", "docs/a.md": b"a", "docs/sub/b.md": b"b", "c.md": b"c"}
        }
    )
    console = api(FakeStore(), objects=objects)

    result = await console.delete_workspace_folder("team", "docs/")
    assert result["count"] == 3
    assert objects.workspaces["team"] == {"c.md": b"c"}

    with pytest.raises(NotFound):
        await console.delete_workspace_folder("team", "docs")
    with pytest.raises(ValueError):
        await console.delete_workspace_folder("team", "/")

    monkeypatch.setattr("syros.console.api.MAX_PREFIX_DELETE", 1)
    objects.workspaces["team"] = {"big/a": b"a", "big/b": b"b"}
    with pytest.raises(ValueError, match="limit"):
        await console.delete_workspace_folder("team", "big")


async def test_create_and_delete_workspace():
    store = FakeStore()
    objects = FakeObjects(workspaces={"team": {"a.md": b"a"}})
    console = api(store, objects=objects)

    result = await console.create_workspace("fresh")
    assert result["ok"] is True
    assert objects.workspaces["fresh"] == {".keep": b""}

    with pytest.raises(Conflict, match="exists"):
        await console.create_workspace("team")
    # a lease doc alone also reserves the name
    await store.claim_workspace("leased-only", "sess_1", ttl_seconds=-1)
    with pytest.raises(Conflict, match="exists"):
        await console.create_workspace("leased-only")
    with pytest.raises(OptionsError):
        await console.create_workspace("Bad Name")

    await store.claim_workspace("team", "sess_1", ttl_seconds=60)
    with pytest.raises(Conflict, match="busy"):
        await console.delete_workspace("team")
    await store.release_workspace("team", "sess_1")

    result = await console.delete_workspace("team")
    assert result["count"] == 1
    assert objects.workspaces["team"] == {}
    assert "team" not in {w["name"] for w in await store.list_workspaces()}


# --- artifact spaces ---


async def test_artifact_spaces_and_files():
    objects = FakeObjects(spaces={"reports": {"r.html": b"<h1>hi</h1>", "d.csv": b"1,2"}})
    console = api(FakeStore(), objects=objects)

    result = await console.artifact_spaces()
    assert result["spaces"] == [
        {"name": "reports", "file_count": 2, "total_size": 14, "updated": None}
    ]

    listing = await console.artifacts("reports")
    assert [f["name"] for f in listing["artifacts"]] == ["d.csv", "r.html"]

    data, content_type = await console.artifact_file("reports", "r.html")
    assert data == b"<h1>hi</h1>"
    assert content_type == "text/html"

    with pytest.raises(NotFound):
        await console.artifact_file("reports", "missing.html")


async def test_artifact_file_too_large():
    from syros.console.api import TooLarge

    objects = FakeObjects(spaces={"reports": {"big.html": b"x" * 200}})
    with pytest.raises(TooLarge):
        await api(FakeStore(), objects=objects).artifact_file("reports", "big.html")


async def test_artifact_write_delete_rename_tags():
    store = FakeStore()
    objects = FakeObjects(spaces={"reports": {"r.html": b"<h1>hi</h1>"}})
    console = api(store, objects=objects)

    # no lease concept: writes go through even while sessions run
    await store.claim_workspace("reports", "sess_1", ttl_seconds=60)
    result = await console.write_artifact("reports", "new.csv", "1,2")
    assert result["ok"] is True
    assert objects.spaces["reports"]["new.csv"] == b"1,2"

    await console.set_artifact_tags("reports", "new.csv", ["data"])
    listing = (await console.artifacts("reports"))["artifacts"]
    assert {f["name"]: f["tags"] for f in listing} == {"new.csv": ["data"], "r.html": []}

    await console.rename_artifact("reports", "new.csv", "data/new.csv")
    assert objects.tags[("space", "reports", "data/new.csv")] == ["data"]
    with pytest.raises(Conflict, match="destination"):
        await console.rename_artifact("reports", "data/new.csv", "r.html")

    result = await console.delete_artifacts("reports", ["data/new.csv", "gone.md"])
    assert result["deleted"] == ["data/new.csv"] and result["ok"] is False

    await console.write_artifact("reports", "docs/a.md", "a")
    assert (await console.delete_artifact_folder("reports", "docs"))["count"] == 1
    with pytest.raises(NotFound):
        await console.delete_artifact("reports", "docs/a.md")


async def test_create_and_delete_space():
    objects = FakeObjects(spaces={"reports": {"r.html": b"x"}})
    console = api(FakeStore(), objects=objects)

    await console.create_space("fresh")
    assert objects.spaces["fresh"] == {".keep": b""}
    with pytest.raises(Conflict, match="exists"):
        await console.create_space("reports")
    with pytest.raises(OptionsError):
        await console.create_space("Bad Name")

    result = await console.delete_space("reports")
    assert result["count"] == 1
    assert "reports" not in objects.spaces
    with pytest.raises(NotFound):
        await console.delete_space("reports")


# --- skills ---


async def test_skills_stats_and_files():
    objects = FakeObjects(skills={"pdf": {"SKILL.md": b"# pdf", "ref/x.md": b"xx"}})
    console = api(FakeStore(), objects=objects)

    result = await console.skills()
    assert result["skills"] == [{"name": "pdf", "file_count": 2, "total_size": 7, "updated": None}]

    listing = await console.skill_files("pdf")
    assert [f["name"] for f in listing["files"]] == ["SKILL.md", "ref/x.md"]

    with pytest.raises(NotFound):
        await console.skill_files("nope")


async def test_skill_file_read_write_delete():
    objects = FakeObjects(skills={"pdf": {"SKILL.md": b"# pdf"}})
    console = api(FakeStore(), objects=objects)

    data, content_type = await console.skill_file("pdf", "SKILL.md")
    assert data == b"# pdf"
    assert content_type == "text/markdown"

    result = await console.write_skill_file("pdf", "SKILL.md", "# edited")
    assert result["ok"] is True and result["size"] == 8
    assert objects.skills["pdf"]["SKILL.md"] == b"# edited"

    await console.write_skill_file(
        "pdf", "logo.bin", base64.b64encode(b"\xff\x00").decode(), "base64"
    )
    assert objects.skills["pdf"]["logo.bin"] == b"\xff\x00"
    with pytest.raises(ValueError, match="base64"):
        await console.write_skill_file("pdf", "x.bin", "not base64!", "base64")
    with pytest.raises(ValueError, match="encoding"):
        await console.write_skill_file("pdf", "x.bin", "hi", "rot13")

    await console.delete_skill_file("pdf", "logo.bin")
    assert "logo.bin" not in objects.skills["pdf"]
    with pytest.raises(NotFound):
        await console.delete_skill_file("pdf", "logo.bin")


async def test_skill_file_too_large():
    objects = FakeObjects(skills={"pdf": {"big.md": b"x" * 200}})
    with pytest.raises(TooLarge):
        await api(FakeStore(), objects=objects).skill_file("pdf", "big.md")


async def test_skill_rejects_bad_names():
    console = api(FakeStore(), objects=FakeObjects(skills={"pdf": {"SKILL.md": b"p"}}))

    for skill in ("../etc", "Upper", "a/b", ""):
        with pytest.raises(OptionsError):
            await console.skill_file(skill, "SKILL.md")

    for file in ("", "/abs", "../escape", "sub/../../escape"):
        with pytest.raises(OptionsError):
            await console.write_skill_file("pdf", file, "x")


async def test_delete_skill_and_sync():
    objects = FakeObjects(skills={"pdf": {"SKILL.md": b"p", "ref/x.md": b"x"}})
    console = api(FakeStore(), objects=objects)

    result = await console.delete_skill("pdf")
    assert result["ok"] is True and result["deleted"] == 2
    assert "pdf" not in objects.skills
    with pytest.raises(NotFound):
        await console.delete_skill("pdf")

    result = await console.sync_official_skills()
    assert result["ok"] is True and result["skills"] == ["pdf"]
    assert objects.skills["pdf"]["SKILL.md"] == b"# pdf"


# --- http smoke ---


async def test_http_smoke():
    from syros.console.server import create_server

    store = FakeStore()
    await store.create_session("sess_1", {})
    # The built frontend is generated (make console), not committed — inject a
    # stand-in so static serving is exercised without a Node build.
    static = {"index.html": b"<html>syros</html>", "404.html": b"<html>404</html>"}
    server = create_server(api(store), asyncio.get_running_loop(), "127.0.0.1", 0, static=static)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def fetch(method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(method, path, body=json.dumps(body) if body else None)
        response = conn.getresponse()
        return response.status, response.read()

    try:
        status, body = await asyncio.to_thread(fetch, "GET", "/")
        assert status == 200 and b"syros" in body

        status, body = await asyncio.to_thread(fetch, "GET", "/api/sessions")
        assert status == 200
        assert [s["id"] for s in json.loads(body)["sessions"]] == ["sess_1"]

        status, body = await asyncio.to_thread(fetch, "GET", "/api/sessions/sess_x/poll")
        assert status == 404

        # create: POST on the collection, next to its GET
        status, body = await asyncio.to_thread(
            fetch, "POST", "/api/sessions", {"prompt": "hello", "options": {"model": "m"}}
        )
        assert status == 200
        created = json.loads(body)["session_id"]
        assert store.sessions[created]["options"]["model"] == "m"

        status, body = await asyncio.to_thread(fetch, "POST", "/api/sessions", {"prompt": ""})
        assert status == 400

        status, body = await asyncio.to_thread(
            fetch, "POST", "/api/sessions/sess_1/prompt", {"text": "hi"}
        )
        assert status == 200 and json.loads(body)["ok"] is True

        status, body = await asyncio.to_thread(fetch, "GET", "/api/approvals")
        assert status == 200 and json.loads(body)["approvals"] == []

        # the prompt above triggered the job, so the session is starting —
        # it has to be killed before it can be deleted
        status, body = await asyncio.to_thread(fetch, "POST", "/api/sessions/sess_1/kill")
        assert status == 200 and json.loads(body)["ok"] is True

        # bulk delete: its route must not be shadowed by /api/sessions/{sid}/...
        status, body = await asyncio.to_thread(
            fetch, "POST", "/api/sessions/delete", {"ids": ["sess_1", "sess_x"]}
        )
        assert status == 200
        payload = json.loads(body)
        assert payload["deleted"] == ["sess_1"]
        assert [f["id"] for f in payload["failed"]] == ["sess_x"]

        status, body = await asyncio.to_thread(fetch, "POST", "/api/sessions/delete", {"ids": []})
        assert status == 400
    finally:
        server.shutdown()


async def test_http_artifact_and_workspace_routes():
    from syros.console.server import create_server

    store = FakeStore()
    objects = FakeObjects(
        workspaces={"team": {"notes.md": b"nn"}},
        spaces={"reports": {"sub/r.html": b"<h1>hi</h1>", "big.bin": b"x" * 200}},
        skills={"pdf": {"SKILL.md": b"# pdf"}},
    )
    server = create_server(api(store, objects=objects), asyncio.get_running_loop(), "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def fetch(path):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", path)
        response = conn.getresponse()
        return response.status, response.read(), dict(response.getheaders())

    def post(path, payload):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", path, json.dumps(payload), {"Content-Type": "application/json"})
        response = conn.getresponse()
        return response.status, response.read()

    try:
        status, body, _ = await asyncio.to_thread(fetch, "/api/workspaces")
        assert status == 200
        assert [w["name"] for w in json.loads(body)["workspaces"]] == ["team"]

        status, body, _ = await asyncio.to_thread(fetch, "/api/workspaces/team/files")
        assert status == 200
        assert [f["name"] for f in json.loads(body)["files"]] == ["notes.md"]

        status, body, _ = await asyncio.to_thread(fetch, "/api/workspaces/team/file?name=notes.md")
        assert status == 200 and body == b"nn"

        status, body = await asyncio.to_thread(
            post, "/api/workspaces/team/file", {"name": "notes.md", "content": "edited"}
        )
        assert status == 200 and json.loads(body)["ok"] is True
        assert objects.workspaces["team"]["notes.md"] == b"edited"

        await store.claim_workspace("team", "sess_1", ttl_seconds=60)
        status, body = await asyncio.to_thread(
            post, "/api/workspaces/team/file", {"name": "notes.md", "content": "again"}
        )
        assert status == 409 and "busy" in json.loads(body)["error"]
        await store.release_workspace("team", "sess_1")

        status, body = await asyncio.to_thread(
            post, "/api/workspaces/team/file/delete", {"name": "notes.md"}
        )
        assert status == 200
        assert "notes.md" not in objects.workspaces["team"]

        status, body, _ = await asyncio.to_thread(fetch, "/api/artifacts")
        assert status == 200
        assert [s["name"] for s in json.loads(body)["spaces"]] == ["reports"]

        status, body, _ = await asyncio.to_thread(fetch, "/api/artifacts/reports")
        assert status == 200
        assert [f["name"] for f in json.loads(body)["artifacts"]] == ["big.bin", "sub/r.html"]

        # raw download: name (with a slash) rides the query string
        status, body, headers = await asyncio.to_thread(
            fetch, "/api/artifacts/reports/file?name=sub%2Fr.html"
        )
        assert (status, body) == (200, b"<h1>hi</h1>")
        assert headers["Content-Type"] == "text/html"
        assert headers["X-Content-Type-Options"] == "nosniff"

        status, _, _ = await asyncio.to_thread(fetch, "/api/artifacts/reports/file?name=nope")
        assert status == 404

        status, _, _ = await asyncio.to_thread(fetch, "/api/artifacts/reports/file?name=big.bin")
        assert status == 413

        # new management routes: rename, tags, bulk, folder, create/delete
        objects.workspaces["team"]["notes.md"] = b"nn"
        status, body = await asyncio.to_thread(
            post, "/api/workspaces/team/file/rename", {"from": "notes.md", "to": "sub/notes.md"}
        )
        assert status == 200 and "sub/notes.md" in objects.workspaces["team"]

        status, body = await asyncio.to_thread(
            post, "/api/workspaces/team/file/tags", {"name": "sub/notes.md", "tags": ["draft"]}
        )
        assert status == 200 and json.loads(body)["tags"] == ["draft"]

        status, body = await asyncio.to_thread(
            post, "/api/workspaces/team/folder/delete", {"folder": "sub"}
        )
        assert status == 200 and json.loads(body)["count"] == 1

        status, body = await asyncio.to_thread(post, "/api/workspaces", {"name": "fresh"})
        assert status == 200 and objects.workspaces["fresh"] == {".keep": b""}
        status, body = await asyncio.to_thread(post, "/api/workspaces/fresh/delete", {})
        assert status == 200 and objects.workspaces["fresh"] == {}

        status, body = await asyncio.to_thread(
            post, "/api/artifacts/reports/file", {"name": "new.txt", "content": "hi"}
        )
        assert status == 200 and objects.spaces["reports"]["new.txt"] == b"hi"
        status, body = await asyncio.to_thread(
            post, "/api/artifacts/reports/files/delete", {"names": ["new.txt"]}
        )
        assert status == 200 and json.loads(body)["deleted"] == ["new.txt"]

        status, body = await asyncio.to_thread(post, "/api/artifacts", {"name": "fresh"})
        assert status == 200 and objects.spaces["fresh"] == {".keep": b""}
        status, body = await asyncio.to_thread(post, "/api/artifacts/fresh/delete", {})
        assert status == 200 and "fresh" not in objects.spaces

        status, body, _ = await asyncio.to_thread(fetch, "/api/skills")
        assert status == 200
        assert [s["name"] for s in json.loads(body)["skills"]] == ["pdf"]

        status, body, _ = await asyncio.to_thread(fetch, "/api/skills/pdf/files")
        assert status == 200
        assert [f["name"] for f in json.loads(body)["files"]] == ["SKILL.md"]

        status, body, _ = await asyncio.to_thread(fetch, "/api/skills/pdf/file?name=SKILL.md")
        assert status == 200 and body == b"# pdf"

        status, body = await asyncio.to_thread(
            post, "/api/skills/pdf/file", {"name": "SKILL.md", "content": "# edited"}
        )
        assert status == 200 and json.loads(body)["ok"] is True
        assert objects.skills["pdf"]["SKILL.md"] == b"# edited"

        status, body = await asyncio.to_thread(
            post, "/api/skills/pdf/file/delete", {"name": "SKILL.md"}
        )
        assert status == 200
        assert "SKILL.md" not in objects.skills["pdf"]

        # sync must not be shadowed by the /api/skills/{name}/... wildcards
        status, body = await asyncio.to_thread(post, "/api/skills/sync", {})
        assert status == 200 and json.loads(body)["skills"] == ["pdf"]

        status, body = await asyncio.to_thread(post, "/api/skills/pdf/delete", {})
        assert status == 200 and json.loads(body)["deleted"] == 1
        assert "pdf" not in objects.skills
    finally:
        server.shutdown()


def test_route_match():
    from syros.console.server import _match

    assert _match(("api", "sessions"), ("api", "sessions")) == []
    assert _match(("api", "sessions", None, "poll"), ("api", "sessions", "sess_1", "poll")) == [
        "sess_1"
    ]
    assert _match(("api", "sessions", None, "approvals", None), ("api", "x")) is None
    assert _match(("api", "sessions"), ("api", "approvals")) is None
    assert _match(("api", "sessions"), ("api", "sessions", "extra")) is None


async def test_http_post_errors():
    from syros.console.server import create_server

    store = FakeStore()
    await store.create_session("sess_1", {})
    server = create_server(api(store), asyncio.get_running_loop(), "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def fetch(method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(method, path, body=body)
        response = conn.getresponse()
        return response.status, response.read()

    try:
        status, body = await asyncio.to_thread(fetch, "POST", "/api/nope")
        assert status == 404

        status, body = await asyncio.to_thread(fetch, "POST", "/api/sessions/sess_1/unknown")
        assert status == 404

        status, body = await asyncio.to_thread(
            fetch, "POST", "/api/sessions/sess_1/prompt", "not json"
        )
        assert status == 400 and b"invalid JSON" in body
    finally:
        server.shutdown()


async def test_static_serving_next_export():
    from syros.console.server import create_server

    static = {
        "index.html": b"<h1>syros</h1>",
        "sessions.html": b"sessions-page",
        "404.html": b"not-found-page",
        "_next/static/x.js": b"js",
    }
    server = create_server(
        api(FakeStore()), asyncio.get_running_loop(), "127.0.0.1", 0, static=static
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def fetch(path):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", path)
        response = conn.getresponse()
        return response.status, response.read(), dict(response.getheaders())

    try:
        status, body, headers = await asyncio.to_thread(fetch, "/")
        assert (status, body) == (200, b"<h1>syros</h1>")
        assert headers["Cache-Control"] == "no-cache"

        # exported page routes fall back to their flat html files
        status, body, headers = await asyncio.to_thread(fetch, "/sessions")
        assert (status, body) == (200, b"sessions-page")
        assert headers["Cache-Control"] == "no-cache"
        assert headers["Content-Type"].startswith("text/html")

        status, _, headers = await asyncio.to_thread(fetch, "/_next/static/x.js")
        assert status == 200
        assert "immutable" in headers["Cache-Control"]

        status, body, _ = await asyncio.to_thread(fetch, "/nope")
        assert (status, body) == (404, b"not-found-page")

        status, body, _ = await asyncio.to_thread(fetch, "/api/nope")
        assert status == 404 and b"error" in body
    finally:
        server.shutdown()
