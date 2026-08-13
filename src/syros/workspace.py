"""Workspace persistence: GCS <-> the sandbox's local state directory.

The sandbox work dir contains ws/ (the agent's working directory) and home/
(HOME for the harness, so claude_agent_sdk transcripts checkpoint too,
enabling resume). home/ always syncs to a per-session prefix; ws/ syncs to the
session prefix, or to a shared workspaces/{name}/ prefix when the session
names a workspace. Synchronous on purpose — callers wrap in asyncio.to_thread.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from .names import validate_file, validate_name


def _bucket(project: str, bucket_name: str):
    from google.cloud import storage

    return storage.Client(project=project).bucket(bucket_name)


def session_prefix(session_id: str, subdir: str) -> str:
    return f"sessions/{session_id}/state/{subdir}/"


def workspace_prefix(name: str) -> str:
    return f"workspaces/{validate_name('workspace', name)}/"


def restore(project: str, bucket_name: str, prefix: str, root: Path) -> int:
    bucket = _bucket(project, bucket_name)
    count = 0
    for blob in bucket.list_blobs(prefix=prefix):
        relative = blob.name[len(prefix) :]
        if not relative:
            continue
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(target)
        count += 1
    return count


def checkpoint(
    project: str, bucket_name: str, prefix: str, root: Path, exclude: tuple[str, ...] = ()
) -> int:
    """Upload root to the prefix, skipping relative paths under any exclude prefix
    (mounted artifact spaces checkpoint to their own prefix, not into session state)."""
    bucket = _bucket(project, bucket_name)
    count = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if any(str(path.relative_to(root)).startswith(skip) for skip in exclude):
            continue
        bucket.blob(prefix + str(path.relative_to(root))).upload_from_filename(path)
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
    """Download one workspace file: (data, content type). Raises FileNotFoundError
    for a missing blob and ValueError when it exceeds max_bytes."""
    prefix = workspace_prefix(name)
    blob = _bucket(project, bucket_name).blob(prefix + validate_file("workspace file", file))
    if not blob.exists():
        raise FileNotFoundError(f"gs://{bucket_name}/{prefix}{file}")
    blob.reload()
    if (blob.size or 0) > max_bytes:
        raise ValueError(f"{file} is {blob.size} bytes (limit {max_bytes})")
    return blob.download_as_bytes(), mimetypes.guess_type(file)[0] or "application/octet-stream"


def write_file(project: str, bucket_name: str, name: str, file: str, data: bytes) -> None:
    prefix = workspace_prefix(name)
    blob = _bucket(project, bucket_name).blob(prefix + validate_file("workspace file", file))
    blob.upload_from_string(
        data, content_type=mimetypes.guess_type(file)[0] or "application/octet-stream"
    )


def delete_file(project: str, bucket_name: str, name: str, file: str) -> None:
    prefix = workspace_prefix(name)
    blob = _bucket(project, bucket_name).blob(prefix + validate_file("workspace file", file))
    if not blob.exists():
        raise FileNotFoundError(f"gs://{bucket_name}/{prefix}{file}")
    blob.delete()
