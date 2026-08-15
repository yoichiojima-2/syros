"""The one-shot layout migration: planning, blob moves, and doc rewrites."""

from typing import Any

import pytest

from syros import migrate

from .test_workspace import FakeBucket

# --- planning (pure) ---


def test_plan_moves_folds_legacy_prefixes_into_the_workspace():
    moves, reserved = migrate.plan_moves(
        [
            "team-skills/shared/pdf/SKILL.md",
            "team-skills/shared/pdf/ref/notes.md",
            "workspaces/shared/report.md",
            "workspaces/shared/sub/deep/a.txt",
            "skills/pdf/SKILL.md",  # global skills already sit where they belong
            "artifacts/reports/out.md",
            "sessions/sess_1/state/ws/a.md",
        ]
    )

    assert moves == [
        ("team-skills/shared/pdf/SKILL.md", "workspaces/shared/skills/pdf/SKILL.md"),
        ("team-skills/shared/pdf/ref/notes.md", "workspaces/shared/skills/pdf/ref/notes.md"),
        ("workspaces/shared/report.md", "workspaces/shared/ws/report.md"),
        ("workspaces/shared/sub/deep/a.txt", "workspaces/shared/ws/sub/deep/a.txt"),
    ]
    assert reserved == []


def test_plan_moves_is_idempotent():
    migrated = [
        "workspaces/shared/ws/report.md",
        "workspaces/shared/skills/pdf/SKILL.md",
    ]
    moves, reserved = migrate.plan_moves(migrated)
    assert moves == []
    assert reserved == migrated


def test_plan_moves_ignores_prefix_markers_and_bare_names():
    moves, reserved = migrate.plan_moves(["workspaces/shared/", "workspaces/", "team-skills/ws/"])
    assert moves == [] and reserved == []


# --- blob moves ---


@pytest.fixture
def bucket(monkeypatch):
    fake = FakeBucket(
        {
            "team-skills/shared/pdf/SKILL.md": (b"skill", None),
            "workspaces/shared/report.md": (b"report", {"syros-tags": "draft"}),
            "workspaces/shared/ws/already.md": (b"done", None),
            "artifacts/reports/out.md": (b"out", None),
        }
    )
    monkeypatch.setattr("syros.workspace._bucket", lambda project, bucket_name: fake)
    return fake


def test_migrate_bucket_moves_blobs_and_keeps_tags(bucket):
    result = migrate.migrate_bucket("proj", "bkt")

    assert bucket.objects["workspaces/shared/skills/pdf/SKILL.md"]["data"] == b"skill"
    assert bucket.objects["workspaces/shared/ws/report.md"] == {
        "data": b"report",
        "metadata": {"syros-tags": "draft"},  # file tags travel with the move
    }
    assert "team-skills/shared/pdf/SKILL.md" not in bucket.objects
    assert "workspaces/shared/report.md" not in bucket.objects
    assert bucket.objects["artifacts/reports/out.md"]["data"] == b"out"  # untouched
    assert len(result["moved"]) == 2
    assert result["in_place"] == 1

    # a second run has nothing left to do
    assert migrate.migrate_bucket("proj", "bkt")["moved"] == []


def test_migrate_bucket_dry_run_changes_nothing(bucket):
    before = dict(bucket.objects)
    result = migrate.migrate_bucket("proj", "bkt", dry_run=True)
    assert bucket.objects == before
    assert len(result["moved"]) == 2


# --- option rewrites (pure) ---


def test_rewrite_options():
    assert migrate.rewrite_options({"team": "shared"}) == {"workspace": "shared"}
    # the canonical key wins when a doc somehow carries both
    assert migrate.rewrite_options({"workspace": "new", "team": "old"}) == {"workspace": "new"}
    assert migrate.rewrite_options({"model": "opus"}) is None
    assert migrate.rewrite_options({}) is None
    assert migrate.rewrite_options(None) is None


def test_rewrite_options_leaves_the_callers_dict_alone():
    original = {"team": "shared", "model": "opus"}
    assert migrate.rewrite_options(original) == {"model": "opus", "workspace": "shared"}
    assert original == {"team": "shared", "model": "opus"}


def test_rewrite_doc_covers_workflow_task_overrides():
    fields = migrate.rewrite_doc(
        {
            "options": {"team": "shared"},
            "tasks": [
                {"id": "a", "prompt": "go", "options": {"team": "other"}},
                {"id": "b", "prompt": "stop", "options": {"model": "opus"}},
            ],
        }
    )
    assert fields["options"] == {"workspace": "shared"}
    assert fields["tasks"] == [
        {"id": "a", "prompt": "go", "options": {"workspace": "other"}},
        {"id": "b", "prompt": "stop", "options": {"model": "opus"}},  # untouched
    ]


def test_rewrite_doc_returns_none_for_a_clean_doc():
    assert migrate.rewrite_doc({"options": {"workspace": "shared"}, "tasks": []}) is None
    assert migrate.rewrite_doc({}) is None


# --- Firestore, against a fake client ---


class FakeSnapshot:
    def __init__(self, doc_id: str, doc: dict[str, Any] | None) -> None:
        self.id = doc_id
        self._doc = doc

    @property
    def exists(self) -> bool:
        return self._doc is not None

    def to_dict(self) -> dict[str, Any] | None:
        return dict(self._doc) if self._doc is not None else None


class FakeDocument:
    def __init__(self, collection: dict[str, Any], doc_id: str) -> None:
        self._collection = collection
        self._id = doc_id

    async def get(self) -> FakeSnapshot:
        return FakeSnapshot(self._id, self._collection.get(self._id))

    async def set(self, doc: dict[str, Any]) -> None:
        self._collection[self._id] = dict(doc)

    async def update(self, fields: dict[str, Any]) -> None:
        self._collection[self._id].update(fields)

    async def delete(self) -> None:
        self._collection.pop(self._id, None)


class FakeCollection:
    def __init__(self, docs: dict[str, Any]) -> None:
        self._docs = docs

    def document(self, doc_id: str) -> FakeDocument:
        return FakeDocument(self._docs, doc_id)

    async def stream(self):
        for doc_id in sorted(self._docs):
            yield FakeSnapshot(doc_id, self._docs[doc_id])


class FakeDb:
    """Only the Firestore surface migrate_firestore touches."""

    def __init__(self, **collections: dict[str, Any]) -> None:
        self.data: dict[str, dict[str, Any]] = collections

    def collection(self, name: str) -> FakeCollection:
        return FakeCollection(self.data.setdefault(name, {}))


async def test_migrate_firestore_adopts_legacy_team_docs_and_rewrites_options():
    db = FakeDb(
        teams={
            "shared": {"options": {"model": "opus"}, "description": "old"},
            "stale": {"options": {"model": "haiku"}},
        },
        workspaces={"stale": {"options": {"model": "sonnet"}}},
        agents={"critic": {"options": {"team": "shared"}}, "loner": {"options": {}}},
        sessions={"sess_1": {"options": {"team": "shared", "model": "opus"}}},
        workflows={"nightly": {"options": {}, "tasks": [{"id": "a", "options": {"team": "x"}}]}},
        settings={"global": {"options": {"team": "shared"}}},
    )

    result = await migrate.migrate_firestore(db)

    # the legacy doc is adopted only where no workspace doc exists yet
    assert result["adopted"] == ["shared"]
    assert result["skipped"] == ["stale"]
    assert db.data["workspaces"]["shared"]["description"] == "old"
    assert db.data["workspaces"]["stale"]["options"] == {"model": "sonnet"}
    assert db.data["teams"] == {}
    # every stored options dict is folded forward, tasks included
    assert db.data["agents"]["critic"]["options"] == {"workspace": "shared"}
    assert db.data["sessions"]["sess_1"]["options"] == {"workspace": "shared", "model": "opus"}
    assert db.data["workflows"]["nightly"]["tasks"][0]["options"] == {"workspace": "x"}
    assert db.data["settings"]["global"]["options"] == {"workspace": "shared"}
    assert sorted(result["rewritten"]) == [
        "agents/critic",
        "sessions/sess_1",
        "settings/global",
        "workflows/nightly",
    ]


async def test_migrate_firestore_is_idempotent():
    db = FakeDb(agents={"critic": {"options": {"team": "shared"}}})
    await migrate.migrate_firestore(db)
    assert await migrate.migrate_firestore(db) == {
        "adopted": [],
        "skipped": [],
        "rewritten": [],
    }


async def test_migrate_firestore_dry_run_changes_nothing():
    db = FakeDb(
        teams={"shared": {"options": {}}},
        agents={"critic": {"options": {"team": "shared"}}},
    )

    result = await migrate.migrate_firestore(db, dry_run=True)

    assert result["adopted"] == ["shared"] and result["rewritten"] == ["agents/critic"]
    assert db.data["teams"] == {"shared": {"options": {}}}
    assert db.data["workspaces"] == {}
    assert db.data["agents"]["critic"]["options"] == {"team": "shared"}


async def test_run_reports_both_halves(bucket):
    db = FakeDb(teams={"shared": {"options": {}}})

    result = await migrate.run("proj", "bkt", db=db, bucket=bucket)

    assert result["dry_run"] is False
    assert len(result["moved"]) == 2
    assert result["adopted"] == ["shared"]
