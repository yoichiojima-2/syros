"""In-memory fakes for the two backends: Firestore (FakeStore) and GCS
(FakeObjects). Both mirror the protocols in syros.store / syros.console.objects,
which is what lets the whole suite run without touching GCP."""

from __future__ import annotations

import secrets
import time
from typing import Any

from syros.errors import SessionExists
from syros.names import validate_file
from syros.skills import parse_description, skill_prefix
from syros.store import RUNTIME_FIELDS, lease_active
from syros.workspace import workspace_prefix


def _set_path(doc: dict[str, Any], key: str, value: Any) -> None:
    """Apply one dotted-path update the way Firestore's update() does."""
    parts = key.split(".")
    for part in parts[:-1]:
        doc = doc.setdefault(part, {})
    doc[parts[-1]] = value


async def append_message(store, session_id, seq, doc, *, branch="main", parent_uuid=None):
    """Test shorthand: journal one message document at a given seq."""
    from syros.journal import make_event

    event = make_event("message", doc, parent_uuid=parent_uuid, branch=branch, seq=seq)
    await store.append_event(session_id, event)
    return event


class FakeStore:
    """Implements syros.store.StoreProtocol (asserted in tests/test_store.py)."""

    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.events: dict[str, dict[str, dict[str, Any]]] = {}  # sid -> uuid -> event
        self.inbox: dict[str, list[dict[str, Any]]] = {}
        self.approvals: dict[str, dict[str, dict[str, Any]]] = {}
        self.workspaces: dict[str, dict[str, Any]] = {}
        self.settings: dict[str, Any] | None = None
        self.workflows: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, dict[str, dict[str, Any]]] = {}  # workflow -> run_id -> doc
        self.agents: dict[str, dict[str, Any]] = {}

    async def create_session(
        self,
        session_id,
        options,
        created_by=None,
        workflow=None,
        run_id=None,
        task=None,
        trigger="api",
        agent=None,
    ):
        if session_id in self.sessions:
            raise SessionExists(f"session {session_id} exists")
        self.sessions[session_id] = {
            "options": options,
            "disabled": False,
            "cost_usd": 0.0,
            "claude_session_id": None,
            "branches": {
                "main": {
                    "created_at": time.time(),
                    "base_uuid": None,
                    "base_seq": 0,
                    "claude_session_id": None,
                }
            },
            "active_branch": "main",
            "tip_uuid": None,
            "seq_head": 0,
            "runtime": {
                "status": "queued",
                "stop_reason": None,
                "lease_id": None,
                "lease_expires": 0.0,
                "heartbeat_at": 0.0,
                "triggered_at": 0.0,
            },
            "created_by": created_by,
            "workflow": workflow,
            "run_id": run_id,
            "task": task,
            "trigger": trigger,
            "agent": agent,
            "created_at": time.time(),
            "updated_at": time.time(),
        }

    async def get_session(self, session_id):
        session = self.sessions.get(session_id)
        return dict(session) if session else None

    async def update_session(self, session_id, **fields):
        session = self.sessions[session_id]
        for key, value in fields.items():
            if key in RUNTIME_FIELDS:
                key = f"runtime.{key}"
            _set_path(session, key, value)
        session["updated_at"] = time.time()

    async def list_sessions(self, limit=20):
        rows = [{"id": k, **v} for k, v in self.sessions.items()]
        return rows if limit is None else rows[:limit]

    async def delete_session(self, session_id):
        self.sessions.pop(session_id, None)
        self.events.pop(session_id, None)
        self.inbox.pop(session_id, None)
        self.approvals.pop(session_id, None)

    async def mark_starting(self, session_id):
        session = self.sessions.get(session_id)
        if not session or session.get("disabled"):
            return
        status = session["runtime"]["status"]
        if status == "terminated" or (status == "running" and lease_active(session)):
            return
        session["runtime"].update(status="starting", triggered_at=time.time())
        session["updated_at"] = time.time()

    async def claim_session(self, session_id, lease_id, ttl_seconds):
        session = self.sessions.get(session_id)
        if not session or session["runtime"]["status"] == "terminated" or session.get("disabled"):
            return None
        runtime = session["runtime"]
        now = time.time()
        if float(runtime.get("lease_expires") or 0) > now and runtime.get("lease_id") != lease_id:
            return None
        runtime.update(
            status="running",
            lease_id=lease_id,
            lease_expires=now + ttl_seconds,
            heartbeat_at=now,
        )
        return dict(session)

    async def renew_lease(self, session_id, lease_id, ttl_seconds):
        session = self.sessions.get(session_id)
        if not session or session["runtime"]["status"] == "terminated" or session.get("disabled"):
            return False
        if session["runtime"].get("lease_id") != lease_id:
            return False
        now = time.time()
        session["runtime"].update(lease_expires=now + ttl_seconds, heartbeat_at=now)
        return True

    async def release_session(self, session_id, *, status, stop_reason, **fields):
        await self.update_session(
            session_id,
            status=status,
            stop_reason=stop_reason,
            lease_id=None,
            lease_expires=0.0,
            **fields,
        )

    async def create_branch(self, session_id, branch_id, *, base_uuid, base_seq, claude_session_id):
        session = self.sessions.get(session_id)
        if not session:
            raise KeyError(f"session {session_id} not found")
        if float(session["runtime"].get("lease_expires") or 0) > time.time():
            raise RuntimeError(f"session {session_id} is running — interrupt it first")
        if branch_id in session["branches"]:
            raise ValueError(f"branch {branch_id} already exists")
        session["branches"][branch_id] = {
            "created_at": time.time(),
            "base_uuid": base_uuid,
            "base_seq": base_seq,
            "claude_session_id": claude_session_id,
        }
        session.update(active_branch=branch_id, tip_uuid=base_uuid, seq_head=base_seq)
        session["updated_at"] = time.time()

    async def append_event(self, session_id, event):
        # Keyed by uuid, like the real store's doc id: idempotent on retry,
        # and a stale-seq rewrite lands as a second record, never an overwrite.
        self.events.setdefault(session_id, {})[event["uuid"]] = {**event, "ts": time.time()}

    async def list_events(self, session_id, branch, after, limit=200):
        rows = [
            e
            for e in self.events.get(session_id, {}).values()
            if e.get("branch") == branch and e["seq"] > after
        ]
        return sorted(rows, key=lambda e: e["seq"])[:limit]

    async def get_event(self, session_id, uuid):
        event = self.events.get(session_id, {}).get(uuid)
        return dict(event) if event else None

    async def recover_head(self, session_id, branch):
        rows = [e for e in self.events.get(session_id, {}).values() if e.get("branch") == branch]
        if not rows:
            return 0, None
        head = max(rows, key=lambda e: e["seq"])
        return int(head["seq"]), head.get("uuid")

    async def push_inbox(self, session_id, kind, text=None):
        message_id = secrets.token_hex(10)
        self.inbox.setdefault(session_id, []).append(
            {"id": message_id, "kind": kind, "text": text, "ts": time.time(), "consumed": False}
        )
        return message_id

    async def peek_messages(self, session_id):
        return [
            {"id": item["id"], "text": item["text"] or "", "ts": item["ts"]}
            for item in self.inbox.get(session_id, [])
            if item["kind"] == "message" and not item["consumed"]
        ]

    async def consume_message(self, session_id, message_id):
        for item in self.inbox.get(session_id, []):
            if item["id"] == message_id:
                item["consumed"] = True

    async def take_interrupt(self, session_id):
        taken = False
        for item in self.inbox.get(session_id, []):
            if item["kind"] == "interrupt" and not item["consumed"]:
                item["consumed"] = True
                taken = True
        return taken

    async def request_approval(
        self, session_id, call_hash, tool_name, tool_input, tool_use_id=None
    ):
        self.approvals.setdefault(session_id, {})[call_hash] = {
            "call_hash": call_hash,
            "tool_name": tool_name,
            "input": tool_input,
            "tool_use_id": tool_use_id,
            "status": "pending",
            "deny_message": None,
            "decided_by": None,
            "requested_at": time.time(),
            "decided_at": None,
        }

    async def get_approval(self, session_id, call_hash):
        approval = self.approvals.get(session_id, {}).get(call_hash)
        return dict(approval) if approval else None

    async def decide_approval(self, session_id, call_hash, *, allow, decided_by, deny_message=None):
        self.approvals[session_id][call_hash].update(
            status="allow" if allow else "deny",
            decided_by=decided_by,
            deny_message=deny_message,
            decided_at=time.time(),
        )

    async def list_pending_approvals(self, session_id):
        return [
            dict(a) for a in self.approvals.get(session_id, {}).values() if a["status"] == "pending"
        ]

    async def list_approvals(self, session_id):
        return [dict(a) for a in self.approvals.get(session_id, {}).values()]

    async def list_all_pending_approvals(self):
        return [
            {"session_id": sid, **a}
            for sid, rows in self.approvals.items()
            for a in rows.values()
            if a["status"] == "pending"
        ]

    async def list_tool_calls(self, session_id):
        from syros.store import _tool_call_row

        rows = [e for e in self.events.get(session_id, {}).values() if e.get("type") == "tool_call"]
        return [_tool_call_row(e) for e in sorted(rows, key=lambda e: e["ts"])]

    async def claim_workspace(self, name, session_id, ttl_seconds):
        doc = self.workspaces.get(name)
        if (
            doc
            and float(doc.get("lease_expires") or 0) > time.time()
            and doc.get("lease_session_id") != session_id
        ):
            return False
        self.workspaces.setdefault(name, {}).update(
            lease_session_id=session_id,
            lease_expires=time.time() + ttl_seconds,
        )
        return True

    async def release_workspace(self, name, session_id):
        doc = self.workspaces.get(name)
        if doc and doc.get("lease_session_id") == session_id:
            doc.update(lease_session_id=None, lease_expires=0.0)

    async def create_workspace(self, name, doc):
        if name in self.workspaces:
            raise ValueError(f"workspace {name} exists")
        self.workspaces[name] = {**doc, "created_at": time.time(), "updated_at": time.time()}

    async def get_workspace(self, name):
        doc = self.workspaces.get(name)
        return {"name": name, **doc} if doc else None

    async def update_workspace(self, name, **fields):
        self.workspaces[name].update(fields, updated_at=time.time())

    async def list_workspaces(self):
        return [{"name": k, **v} for k, v in self.workspaces.items()]

    async def delete_workspace(self, name):
        self.workspaces.pop(name, None)

    async def get_settings(self):
        return self.settings

    async def update_settings(self, doc):
        self.settings = dict(doc)

    async def create_workflow(self, name, doc):
        if name in self.workflows:
            raise ValueError(f"workflow {name} exists")
        self.workflows[name] = {**doc, "created_at": time.time(), "updated_at": time.time()}

    async def get_workflow(self, name):
        workflow = self.workflows.get(name)
        return {"name": name, **workflow} if workflow else None

    async def update_workflow(self, name, **fields):
        self.workflows[name].update(fields, updated_at=time.time())

    async def list_workflows(self):
        return [{"name": k, **v} for k, v in self.workflows.items()]

    async def delete_workflow(self, name):
        self.workflows.pop(name, None)
        self.runs.pop(name, None)

    async def create_run(self, workflow, run_id, doc):
        runs = self.runs.setdefault(workflow, {})
        if run_id in runs:
            raise ValueError(f"run {run_id} exists")
        runs[run_id] = {**doc, "created_at": time.time(), "updated_at": time.time()}

    async def get_run(self, workflow, run_id):
        run = self.runs.get(workflow, {}).get(run_id)
        return {"id": run_id, **run} if run else None

    async def list_runs(self, workflow, limit=50):
        rows = [{"id": k, **v} for k, v in self.runs.get(workflow, {}).items()]
        rows.sort(key=lambda r: r.get("started_at") or 0, reverse=True)
        return rows[:limit]

    async def transition_run(self, workflow, run_id, mutate):
        runs = self.runs.get(workflow, {})
        current = runs.get(run_id)
        if current is None:
            return None
        doc = mutate(dict(current))
        if doc is None:
            return None
        runs[run_id] = {**doc, "updated_at": time.time()}
        return {"id": run_id, **runs[run_id]}

    async def claim_slot(self, name, due, following):
        workflow = self.workflows.get(name)
        if not workflow or not workflow.get("enabled"):
            return False
        if float(workflow.get("next_run_at") or 0) != due:
            return False
        workflow["next_run_at"] = following
        return True

    async def create_agent(self, name, doc):
        if name in self.agents:
            raise ValueError(f"agent {name} exists")
        self.agents[name] = {**doc, "created_at": time.time(), "updated_at": time.time()}

    async def get_agent(self, name):
        agent = self.agents.get(name)
        return {"name": name, **agent} if agent else None

    async def update_agent(self, name, **fields):
        self.agents[name].update(fields, updated_at=time.time())

    async def list_agents(self):
        return [{"name": k, **v} for k, v in self.agents.items()]

    async def delete_agent(self, name):
        self.agents.pop(name, None)


class FakeObjects:
    """Implements syros.console.objects.ObjectStoreProtocol over a name->bytes dict."""

    def __init__(self, workspaces=None, spaces=None, skills=None, workspace_skills=None):
        # workspaces' shared directories live under the workspaces/ GCS prefix
        self.workspaces: dict[str, dict[str, bytes]] = workspaces or {}
        self.workspace_skills: dict[str, dict[str, dict[str, bytes]]] = workspace_skills or {}
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

    def _skill_pool(self, workspace, create=False):
        """The skill dict for a scope. Reads must not `setdefault`: listing a
        workspace's skills would then leave an empty entry behind, and a test
        asserting nothing was created for that workspace would see a phantom."""
        if workspace is None:
            return self.skills
        if create:
            return self.workspace_skills.setdefault(workspace, {})
        return self.workspace_skills.get(workspace, {})

    @staticmethod
    def _skill_stat(files):
        stat = FakeObjects._stats(files)
        described = parse_description(files.get("SKILL.md", b""))
        return {**stat, "description": described} if described else stat

    async def skill_stats(self, workspace=None):
        pool = self._skill_pool(workspace)
        return {name: self._skill_stat(files) for name, files in pool.items()}

    async def workspace_skill_stats(self):
        return {
            owner: {name: self._skill_stat(files) for name, files in pool.items()}
            for owner, pool in self.workspace_skills.items()
            if pool
        }

    async def skill_files(self, name, workspace=None):
        files = self._skill_pool(workspace).get(name, {})
        return [{"name": n, "size": len(b), "updated": None} for n, b in files.items()]

    async def read_skill_file(self, name, file, workspace=None):
        import mimetypes

        self._check_skill(name, file)
        files = self._skill_pool(workspace).get(name, {})
        if file not in files:
            raise FileNotFoundError(file)
        if len(files[file]) > 100:
            raise ValueError("too large")
        return files[file], mimetypes.guess_type(file)[0] or "application/octet-stream"

    async def write_skill_file(self, name, file, data, workspace=None):
        self._check_skill(name, file)
        self._skill_pool(workspace, create=True).setdefault(name, {})[file] = data

    async def delete_skill_file(self, name, file, workspace=None):
        self._check_skill(name, file)
        files = self._skill_pool(workspace).get(name, {})
        if file not in files:
            raise FileNotFoundError(file)
        del files[file]

    async def delete_skill(self, name, workspace=None):
        skill_prefix(name)
        files = self._skill_pool(workspace).pop(name, None)
        if not files:
            raise FileNotFoundError(name)
        return len(files)

    async def sync_official_skills(self):
        self.skills.setdefault("pdf", {})["SKILL.md"] = b"# pdf"
        return {"skills": ["pdf"], "files": 1, "skipped": []}


class FakeBlob:
    """One blob handle over a FakeBucket record ({"data", "metadata", and
    "content_type" for string uploads}) — the subset of google.cloud.storage
    the syros GCS modules touch."""

    def __init__(self, bucket: FakeBucket, name: str) -> None:
        self._bucket = bucket
        self.name = name
        self.metadata: dict[str, Any] | None = None
        self.updated = None
        # None until the handle is reloaded (which is what a listing does). A
        # handle that knows a generation reads *that* one, like GCS: the object
        # being rewritten underneath it is a 404, not a fresh download.
        self.generation: int | None = None

    @property
    def size(self) -> int | None:
        record = self._bucket.objects.get(self.name)
        return len(record["data"]) if record else None

    def exists(self) -> bool:
        return self.name in self._bucket.objects

    def reload(self) -> None:
        record = self._bucket.objects[self.name]
        self.metadata = dict(record["metadata"]) if record.get("metadata") else None
        self.generation = self._bucket.generations.get(self.name)

    def upload_from_string(self, data: bytes, content_type: str | None = None) -> None:
        self._bucket.objects[self.name] = {
            "data": data,
            "metadata": None,
            "content_type": content_type,
        }
        self._bucket.bump(self.name)

    def upload_from_filename(self, path) -> None:
        # like GCS, an upload replaces the object; metadata set on this handle
        # (and nothing else) survives onto the new object
        from pathlib import Path

        metadata = {k: v for k, v in (self.metadata or {}).items() if v is not None}
        self._bucket.objects[self.name] = {
            "data": Path(path).read_bytes(),
            "metadata": metadata or None,
        }
        self._bucket.bump(self.name)

    def download_as_bytes(self) -> bytes:
        return self._bucket.objects[self.name]["data"]

    def download_to_filename(self, path) -> None:
        from pathlib import Path

        from google.api_core.exceptions import NotFound

        current = self._bucket.generations.get(self.name)
        # GCS opens the destination before it can know the read will fail, so a
        # 404 leaves an empty file behind. Mirror it: callers have to clean up.
        Path(path).write_bytes(b"")
        if current is None or (self.generation is not None and self.generation != current):
            raise NotFound(f"404 no such object: {self.name}")
        Path(path).write_bytes(self._bucket.objects[self.name]["data"])

    def patch(self) -> None:
        record = self._bucket.objects[self.name]
        record["metadata"] = {
            k: v for k, v in (self.metadata or {}).items() if v is not None
        } or None

    def delete(self) -> None:
        del self._bucket.objects[self.name]


class FakeListing(list):
    def __init__(self, blobs, prefixes):
        super().__init__(blobs)
        self.prefixes = prefixes


class FakeBucket:
    """In-memory GCS bucket, seedable with {name: (data, metadata)}."""

    def __init__(self, objects: dict[str, tuple[bytes, dict | None]] | None = None) -> None:
        self.objects: dict[str, dict[str, Any]] = {
            name: {"data": data, "metadata": metadata}
            for name, (data, metadata) in (objects or {}).items()
        }
        # Generations live beside the objects rather than in them, so a rewrite
        # is visible to a stale handle without changing the record shape tests
        # compare against.
        self.generations: dict[str, int] = dict.fromkeys(self.objects, 1)

    def bump(self, name: str) -> None:
        self.generations[name] = self.generations.get(name, 0) + 1

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self, name)

    def list_blobs(self, prefix: str = "", delimiter: str | None = None) -> FakeListing:
        names = sorted(n for n in self.objects if n.startswith(prefix))
        if delimiter is None:
            blobs = []
            for name in names:
                blob = FakeBlob(self, name)
                blob.reload()
                blobs.append(blob)
            return FakeListing(blobs, set())
        blobs, prefixes = [], set()
        for name in names:
            rest = name[len(prefix) :]
            if delimiter in rest:
                prefixes.add(prefix + rest.split(delimiter)[0] + delimiter)
            else:
                blobs.append(FakeBlob(self, name))
        return FakeListing(blobs, prefixes)

    def copy_blob(self, blob: FakeBlob, destination_bucket: FakeBucket, new_name: str) -> None:
        record = self.objects[blob.name]
        destination_bucket.objects[new_name] = {
            **record,
            "metadata": dict(record["metadata"] or {}) or None,
        }
        destination_bucket.bump(new_name)
