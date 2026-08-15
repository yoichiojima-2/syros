"""One-shot migration of an installation onto the current data layout.

The workspace concept used to be called a team, and the rename left three
things behind in deployed data: skills under a top-level `team-skills/` GCS
prefix, workspace docs in a `teams/` Firestore collection, and a `"team"` key
inside stored option dicts. Workspace files also sat directly under
`workspaces/{name}/`, before a workspace gained a second subdirectory of its
own. This module moves all four forward, once:

    team-skills/{ws}/{skill}/…   ->  workspaces/{ws}/skills/{skill}/…
    workspaces/{ws}/{file}       ->  workspaces/{ws}/ws/{file}
    teams/{name}                 ->  workspaces/{name}
    options {"team": x}          ->  options {"workspace": x}

Nothing else reads the old names — the code carries no fallbacks — so this
runs against an installation deployed before the change, and is a no-op
afterwards. It is idempotent: every step skips what is already in place, so a
re-run (or a run interrupted halfway) is safe.

It refuses to run while a workspace lease is live, because a session holding
one is checkpointing to the prefix being moved out from under it. Stored
options are rewritten everywhere they are read back through `options_from_doc`
— sessions, agents, workspaces, settings, workflows, and the options each
in-flight run captured at launch.

Blob moves are server-side copy-then-delete, which preserves custom metadata,
so file tags travel with the move. The planners are pure and the drivers thin
on purpose: what moves where is decided by code that can be tested against a
list of names, not against a bucket.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from . import layout
from .errors import SyrosError
from .names import NAME
from .store import lease_active


class MigrationError(SyrosError):
    """The installation is not in a state where migrating is safe."""


LEGACY_SKILLS = "team-skills/"
LEGACY_TEAMS = "teams"
LEGACY_WORKSPACE_KEY = "team"

# The subdirectories a workspace prefix is allowed to hold. A blob already
# under one of these is either migrated or (before the move) a folder whose
# name collides with them — either way, the migration leaves it alone.
WORKSPACE_SUBDIRS = (layout.WS_SUBDIR, layout.SKILLS_SUBDIR)


def plan_moves(names: Iterable[str]) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    """Plan the blob moves for a full bucket listing.

    Returns (moves, in_place, unmovable):

    - `moves` are (source, destination) pairs.
    - `in_place` names blobs already under a workspace's `ws/` or `skills/`.
      On a second run that is every workspace blob and nothing moves. On the
      first run it means the shared directory held a folder named `ws/` or
      `skills/` before the migration, which is reported rather than moved —
      the caller cannot tell the two cases apart, and guessing would shuffle
      a user's files.
    - `unmovable` names blobs whose workspace segment is not a name syros
      would ever write. Reported and left alone rather than raised on: one
      hand-uploaded object must not block the whole migration.
    """
    moves: list[tuple[str, str]] = []
    in_place: list[str] = []
    unmovable: list[str] = []
    for name in names:
        legacy_skill = name.startswith(LEGACY_SKILLS)
        root = LEGACY_SKILLS if legacy_skill else layout.WORKSPACES
        if not legacy_skill and not name.startswith(layout.WORKSPACES):
            continue
        workspace, _, tail = name[len(root) :].partition("/")
        if not workspace or not tail:
            continue  # a prefix marker, not a file
        if not NAME.fullmatch(workspace):
            unmovable.append(name)
            continue
        if legacy_skill:
            moves.append((name, f"{layout.workspace_skills_root(workspace)}{tail}"))
        elif tail.split("/", 1)[0] in WORKSPACE_SUBDIRS:
            in_place.append(name)
        else:
            moves.append((name, f"{layout.workspace_prefix(workspace)}{tail}"))
    return moves, in_place, unmovable


def migrate_bucket(
    project: str, bucket_name: str, *, dry_run: bool = False, bucket: Any = None
) -> dict[str, Any]:
    """Move every blob onto the current layout. Synchronous on purpose —
    callers wrap in asyncio.to_thread (same contract as workspace.py)."""
    from .workspace import _bucket

    bucket = bucket if bucket is not None else _bucket(project, bucket_name)
    listing = list(bucket.list_blobs(prefix=LEGACY_SKILLS)) + list(
        bucket.list_blobs(prefix=layout.WORKSPACES)
    )
    moves, in_place, unmovable = plan_moves(blob.name for blob in listing)
    if not dry_run:
        for source, destination in moves:
            blob = bucket.blob(source)
            bucket.copy_blob(blob, bucket, destination)
            blob.delete()
    return {
        "moved": [{"from": source, "to": destination} for source, destination in moves],
        "in_place": len(in_place),
        "unmovable": unmovable,
    }


def rewrite_options(options: dict[str, Any] | None) -> dict[str, Any] | None:
    """Fold a stored options dict's legacy "team" key into "workspace", or
    None when there is nothing to change. An options dict carrying both keeps
    the canonical value — that is what the read-through fallback did."""
    if not isinstance(options, dict) or LEGACY_WORKSPACE_KEY not in options:
        return None
    rewritten = dict(options)
    legacy = rewritten.pop(LEGACY_WORKSPACE_KEY)
    rewritten["workspace"] = rewritten.get("workspace") or legacy
    return rewritten


# Fields holding a list of task dicts, each with its own options override: a
# workflow's editable `tasks`, and the `spec` a run captured at launch. (A run
# doc's own `tasks` is a status map, not a task list — hence the isinstance.)
TASK_LIST_FIELDS = ("tasks", "spec")


def rewrite_doc(doc: dict[str, Any]) -> dict[str, Any] | None:
    """The fields to update on one document, or None when it is already clean.

    Covers every place a stored options dict lives: the document's own
    `options`, and the per-task overrides inside a task list.
    """
    fields: dict[str, Any] = {}
    if (options := rewrite_options(doc.get("options"))) is not None:
        fields["options"] = options
    for field in TASK_LIST_FIELDS:
        tasks = doc.get(field)
        if not isinstance(tasks, list):
            continue
        rewrites = [
            rewrite_options(task.get("options")) if isinstance(task, dict) else None
            for task in tasks
        ]
        if any(rewrite is not None for rewrite in rewrites):
            fields[field] = [
                task if rewrite is None else {**task, "options": rewrite}
                for task, rewrite in zip(tasks, rewrites)
            ]
    return fields or None


# Every collection whose documents carry a stored options dict. Session docs
# are included so a resumed session keeps its workspace; workflow runs so an
# in-flight run's captured options still deserialize when the next task starts.
OPTION_COLLECTIONS = ("sessions", "agents", "workspaces", "workflows", "settings")
RUNS_SUBCOLLECTION = ("workflows", "runs")


async def migrate_firestore(db: Any, *, dry_run: bool = False) -> dict[str, Any]:
    """Fold legacy `teams/` docs into `workspaces/` and rewrite stored options.

    Takes the Firestore async client rather than a Store: this reads a
    collection the store no longer knows about, and it is maintenance, not the
    control plane's CRUD.
    """
    adopted: list[str] = []
    skipped: list[str] = []
    # Drained before writing: the loop deletes out of the collection it reads.
    legacy = [s async for s in db.collection(LEGACY_TEAMS).stream()]
    for snapshot in legacy:
        name = snapshot.id
        existing = await db.collection("workspaces").document(name).get()
        if existing.exists:
            # A workspace written since the rename wins; the legacy doc is
            # stale config, and merging it could resurrect an old option.
            skipped.append(name)
        else:
            adopted.append(name)
            if not dry_run:
                await db.collection("workspaces").document(name).set(dict(snapshot.to_dict()))
        if not dry_run:
            await db.collection(LEGACY_TEAMS).document(name).delete()

    rewritten: list[str] = []

    async def _rewrite(collection: Any, path: str) -> None:
        pending = [
            (s.id, fields)
            async for s in collection.stream()
            if (fields := rewrite_doc(s.to_dict() or {})) is not None
        ]
        for doc_id, fields in pending:
            rewritten.append(f"{path}/{doc_id}")
            if not dry_run:
                await collection.document(doc_id).update(fields)

    for name in OPTION_COLLECTIONS:
        await _rewrite(db.collection(name), name)
    # Runs live in a subcollection of their workflow, and hold their own copy
    # of the options captured at launch. An in-flight run reads it back through
    # options_from_doc when it starts its next task, so a run left un-migrated
    # would fail that task (and its downstream cone) with an unknown option.
    parent, child = RUNS_SUBCOLLECTION
    workflow_ids = [s.id async for s in db.collection(parent).stream()]
    for workflow_id in workflow_ids:
        runs = db.collection(parent).document(workflow_id).collection(child)
        await _rewrite(runs, f"{parent}/{workflow_id}/{child}")
    return {"adopted": adopted, "skipped": skipped, "rewritten": rewritten}


async def busy_workspaces(db: Any) -> list[str]:
    """Workspaces holding a live lease, in either collection.

    A migration is not safe while one runs: the session holding the lease is
    checkpointing to the pre-migration prefix, so its writes would land where
    nothing reads them — and a runner on the old image claims its lease in a
    collection the new code no longer looks at, so two sessions could hold the
    same workspace across the deploy window.
    """
    busy = []
    for collection in ("workspaces", LEGACY_TEAMS):
        async for snapshot in db.collection(collection).stream():
            if lease_active(snapshot.to_dict() or {}):
                busy.append(snapshot.id)
    return sorted(set(busy))


def _client(project: str):
    from google.cloud import firestore

    return firestore.AsyncClient(project=project)


async def run(
    project: str,
    bucket_name: str,
    *,
    dry_run: bool = False,
    force: bool = False,
    db: Any = None,
    bucket: Any = None,
) -> dict[str, Any]:
    """Both halves, GCS then Firestore. Reported, not printed — the CLI owns
    the output format."""
    import asyncio

    db = db if db is not None else _client(project)
    if busy := await busy_workspaces(db):
        if not (dry_run or force):
            raise MigrationError(
                f"workspace(s) {', '.join(busy)} have a live lease — a running session"
                " checkpoints to the old prefix. Wait for them to idle, or pass --force."
            )
    objects = await asyncio.to_thread(
        migrate_bucket, project, bucket_name, dry_run=dry_run, bucket=bucket
    )
    docs = await migrate_firestore(db, dry_run=dry_run)
    return {"dry_run": dry_run, "busy": busy, **objects, **docs}
