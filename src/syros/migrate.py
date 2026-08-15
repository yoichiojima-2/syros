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

Blob moves are server-side copy-then-delete, which preserves custom metadata,
so file tags travel with the move. The planners are pure and the drivers thin
on purpose: what moves where is decided by code that can be tested against a
list of names, not against a bucket.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from . import layout

LEGACY_SKILLS = "team-skills/"
LEGACY_TEAMS = "teams"
LEGACY_WORKSPACE_KEY = "team"

# The subdirectories a workspace prefix is allowed to hold. A blob already
# under one of these is either migrated or (before the move) a folder whose
# name collides with them — either way, the migration leaves it alone.
WORKSPACE_SUBDIRS = (layout.WS_SUBDIR, layout.SKILLS_SUBDIR)


def plan_moves(names: Iterable[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """Plan the blob moves for a full bucket listing.

    Returns (moves, reserved): `moves` are (source, destination) pairs, and
    `reserved` names the blobs already sitting under a `ws/` or `skills/`
    subdirectory of a workspace. On a second run every workspace blob is
    reserved and nothing moves. On the first run a reserved blob means the
    shared directory held a folder named `ws/` or `skills/` before the
    migration, which is reported rather than moved — the caller cannot tell
    the two cases apart, and guessing would shuffle a user's files.
    """
    moves: list[tuple[str, str]] = []
    reserved: list[str] = []
    for name in names:
        if name.startswith(LEGACY_SKILLS):
            rest = name[len(LEGACY_SKILLS) :]
            workspace, _, tail = rest.partition("/")
            if workspace and tail:
                moves.append((name, f"{layout.workspace_skills_root(workspace)}{tail}"))
            continue
        if not name.startswith(layout.WORKSPACES):
            continue
        workspace, _, tail = name[len(layout.WORKSPACES) :].partition("/")
        if not workspace or not tail:
            continue
        if tail.split("/", 1)[0] in WORKSPACE_SUBDIRS:
            reserved.append(name)
            continue
        moves.append((name, f"{layout.workspace_prefix(workspace)}{tail}"))
    return moves, reserved


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
    moves, reserved = plan_moves(blob.name for blob in listing)
    if not dry_run:
        for source, destination in moves:
            blob = bucket.blob(source)
            bucket.copy_blob(blob, bucket, destination)
            blob.delete()
    return {
        "moved": [{"from": source, "to": destination} for source, destination in moves],
        "in_place": len(reserved),
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


def rewrite_doc(doc: dict[str, Any]) -> dict[str, Any] | None:
    """The fields to update on one document, or None when it is already clean.

    Covers both places a stored options dict lives: the document's own
    `options`, and the per-task overrides a workflow carries.
    """
    fields: dict[str, Any] = {}
    if (options := rewrite_options(doc.get("options"))) is not None:
        fields["options"] = options
    tasks = doc.get("tasks")
    if isinstance(tasks, list):
        rewrites = [
            rewrite_options(task.get("options")) if isinstance(task, dict) else None
            for task in tasks
        ]
        if any(r is not None for r in rewrites):
            fields["tasks"] = [
                task if rewrite is None else {**task, "options": rewrite}
                for task, rewrite in zip(tasks, rewrites)
            ]
    return fields or None


# Every collection whose documents carry a stored options dict. Session docs
# are included so a resumed session keeps its workspace.
OPTION_COLLECTIONS = ("sessions", "agents", "workspaces", "workflows", "settings")


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
    for collection in OPTION_COLLECTIONS:
        pending = [
            (s.id, fields)
            async for s in db.collection(collection).stream()
            if (fields := rewrite_doc(s.to_dict() or {})) is not None
        ]
        for doc_id, fields in pending:
            rewritten.append(f"{collection}/{doc_id}")
            if not dry_run:
                await db.collection(collection).document(doc_id).update(fields)
    return {"adopted": adopted, "skipped": skipped, "rewritten": rewritten}


def _client(project: str):
    from google.cloud import firestore

    return firestore.AsyncClient(project=project)


async def run(
    project: str, bucket_name: str, *, dry_run: bool = False, db: Any = None, bucket: Any = None
) -> dict[str, Any]:
    """Both halves, GCS then Firestore. Reported, not printed — the CLI owns
    the output format."""
    import asyncio

    objects = await asyncio.to_thread(
        migrate_bucket, project, bucket_name, dry_run=dry_run, bucket=bucket
    )
    docs = await migrate_firestore(db if db is not None else _client(project), dry_run=dry_run)
    return {"dry_run": dry_run, **objects, **docs}
