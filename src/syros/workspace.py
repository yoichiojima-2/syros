"""Workspace persistence: GCS <-> the sandbox's local state directory.

The sandbox work dir contains ws/ (the agent's working directory) and home/
(HOME for the harness, so claude_agent_sdk transcripts checkpoint too,
enabling resume). home/ always syncs to a per-session prefix; ws/ syncs to the
session prefix, or to the workspace's shared directory when the session names
a workspace. Prefixes come from `layout.py`. Synchronous on purpose — callers
wrap in asyncio.to_thread.

(This module does the GCS file operations; the sibling `workspaces.py`,
plural, manages workspace documents in Firestore.)
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from .layout import workspace_prefix
from .names import validate_file, validate_tags

# GCS custom-metadata key holding a file's tags, comma-joined. Tag names are
# comma-free by validation, so the join is unambiguous.
TAGS_KEY = "syros-tags"


def _bucket(project: str, bucket_name: str):
    from google.cloud import storage

    return storage.Client(project=project).bucket(bucket_name)


def restore(project: str, bucket_name: str, prefix: str, root: Path) -> int:
    """Download everything under the prefix into root; returns the file count.

    A listing is a snapshot, and the blobs it yields carry the generation they
    had when they were listed — downloading one of *those* pins that generation.
    Anything rewritten under the prefix while the restore runs (the console
    syncing official skills, someone editing a workspace file) would then 404
    and, at session start, take the whole run down with it. So the download goes
    through a fresh, generation-less handle, and a file that disappears
    mid-restore is skipped instead of fatal.
    """
    from google.api_core.exceptions import NotFound

    bucket = _bucket(project, bucket_name)
    count = 0
    for blob in bucket.list_blobs(prefix=prefix):
        relative = blob.name[len(prefix) :]
        if not relative:
            continue
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            bucket.blob(blob.name).download_to_filename(target)
        except NotFound:
            # Deleted between the listing and now. A failed download can leave
            # the opened file behind, so don't hand the sandbox an empty stub.
            target.unlink(missing_ok=True)
            continue
        count += 1
    return count


def checkpoint(
    project: str, bucket_name: str, prefix: str, root: Path, exclude: tuple[str, ...] = ()
) -> int:
    """Upload root to the prefix, skipping relative paths under any exclude prefix
    (mounted artifact spaces checkpoint to their own prefix, not into session state)."""
    bucket = _bucket(project, bucket_name)
    # upload_from_filename replaces the whole object, custom metadata included;
    # carry existing metadata (e.g. console-set tags) forward so a run that
    # rewrites a file does not silently strip it.
    existing = {b.name: b.metadata for b in bucket.list_blobs(prefix=prefix) if b.metadata}
    count = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if any(str(path.relative_to(root)).startswith(skip) for skip in exclude):
            continue
        blob = bucket.blob(prefix + str(path.relative_to(root)))
        blob.metadata = existing.get(blob.name)
        blob.upload_from_filename(path)
        count += 1
    return count


# --- single-file access, for humans editing a shared workspace from the console ---
#
# checkpoint() only ever uploads, so a file deleted inside a run comes back on
# the next restore(). delete_file() is the one path that actually removes a
# blob, which is why the console can fix what a session cannot.


def read_file(
    project: str, bucket_name: str, name: str, file: str, *, max_bytes: int
) -> tuple[bytes, str]:
    return read_in_prefix(
        project, bucket_name, workspace_prefix(name), "workspace file", file, max_bytes=max_bytes
    )


def write_file(project: str, bucket_name: str, name: str, file: str, data: bytes) -> None:
    write_in_prefix(project, bucket_name, workspace_prefix(name), "workspace file", file, data)


def delete_file(project: str, bucket_name: str, name: str, file: str) -> None:
    delete_in_prefix(project, bucket_name, workspace_prefix(name), "workspace file", file)


# --- prefix-generic helpers, shared with skills.py and artifacts.py ---


def content_type(file: str) -> str:
    return mimetypes.guess_type(file)[0] or "application/octet-stream"


def read_in_prefix(
    project: str, bucket_name: str, prefix: str, kind: str, file: str, *, max_bytes: int
) -> tuple[bytes, str]:
    """Download one file under the prefix: (data, content type). Raises
    FileNotFoundError for a missing blob and ValueError over max_bytes."""
    blob = _bucket(project, bucket_name).blob(prefix + validate_file(kind, file))
    if not blob.exists():
        raise FileNotFoundError(f"gs://{bucket_name}/{prefix}{file}")
    blob.reload()
    if (blob.size or 0) > max_bytes:
        raise ValueError(f"{file} is {blob.size} bytes (limit {max_bytes})")
    return blob.download_as_bytes(), content_type(file)


def write_in_prefix(
    project: str, bucket_name: str, prefix: str, kind: str, file: str, data: bytes
) -> None:
    blob = _bucket(project, bucket_name).blob(prefix + validate_file(kind, file))
    blob.upload_from_string(data, content_type=content_type(file))


def delete_in_prefix(project: str, bucket_name: str, prefix: str, kind: str, file: str) -> None:
    blob = _bucket(project, bucket_name).blob(prefix + validate_file(kind, file))
    if not blob.exists():
        raise FileNotFoundError(f"gs://{bucket_name}/{prefix}{file}")
    blob.delete()


def rename_in_prefix(
    project: str, bucket_name: str, prefix: str, kind: str, src: str, dst: str
) -> None:
    """Server-side copy then delete. copy_blob preserves custom metadata, so
    tags travel with the rename. Raises FileNotFoundError for a missing source
    and FileExistsError when the destination is already taken."""
    validate_file(kind, src)
    validate_file(kind, dst)
    bucket = _bucket(project, bucket_name)
    source = bucket.blob(prefix + src)
    if not source.exists():
        raise FileNotFoundError(f"gs://{bucket_name}/{prefix}{src}")
    if src != dst and bucket.blob(prefix + dst).exists():
        raise FileExistsError(f"gs://{bucket_name}/{prefix}{dst}")
    if src == dst:
        return
    bucket.copy_blob(source, bucket, prefix + dst)
    source.delete()


def set_tags_in_prefix(
    project: str, bucket_name: str, prefix: str, kind: str, file: str, tags: list[str]
) -> None:
    """Store tags as custom metadata on the blob. An empty list removes the key."""
    validate_file(kind, file)
    tags = validate_tags(tags)
    blob = _bucket(project, bucket_name).blob(prefix + file)
    if not blob.exists():
        raise FileNotFoundError(f"gs://{bucket_name}/{prefix}{file}")
    blob.reload()
    blob.metadata = {**(blob.metadata or {}), TAGS_KEY: ",".join(tags) if tags else None}
    blob.patch()


def delete_prefix(project: str, bucket_name: str, prefix: str, *, max_files: int) -> int:
    """Delete every blob under the prefix. Refuses (ValueError) above max_files
    so a console call cannot silently start an unbounded cascade."""
    bucket = _bucket(project, bucket_name)
    blobs = list(bucket.list_blobs(prefix=prefix))
    if len(blobs) > max_files:
        raise ValueError(
            f"{prefix} holds {len(blobs)} files (limit {max_files}); delete via gsutil/CLI instead"
        )
    for blob in blobs:
        blob.delete()
    return len(blobs)


def rename_file(project: str, bucket_name: str, name: str, src: str, dst: str) -> None:
    rename_in_prefix(project, bucket_name, workspace_prefix(name), "workspace file", src, dst)


def set_tags(project: str, bucket_name: str, name: str, file: str, tags: list[str]) -> None:
    set_tags_in_prefix(project, bucket_name, workspace_prefix(name), "workspace file", file, tags)
