"""Workspace persistence: GCS <-> the sandbox's local state directory.

The sandbox work dir contains ws/ (the agent's working directory) and home/
(HOME for the harness, so claude_agent_sdk transcripts checkpoint too,
enabling resume). home/ always syncs to a per-session prefix; ws/ syncs to the
session prefix, or to a shared workspaces/{name}/ prefix when the session
names a workspace. Synchronous on purpose — callers wrap in asyncio.to_thread.
"""

from __future__ import annotations

from pathlib import Path


def session_prefix(session_id: str, subdir: str) -> str:
    return f"sessions/{session_id}/state/{subdir}/"


def workspace_prefix(name: str) -> str:
    return f"workspaces/{name}/"


def restore(project: str, bucket_name: str, prefix: str, root: Path) -> int:
    from google.cloud import storage

    bucket = storage.Client(project=project).bucket(bucket_name)
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
    from google.cloud import storage

    bucket = storage.Client(project=project).bucket(bucket_name)
    count = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if any(str(path.relative_to(root)).startswith(skip) for skip in exclude):
            continue
        bucket.blob(prefix + str(path.relative_to(root))).upload_from_filename(path)
        count += 1
    return count
