"""Workspace persistence: GCS <-> the sandbox's local state directory.

The state root contains ws/ (the agent's working directory) and home/ (HOME for
the harness, so claude_agent_sdk transcripts checkpoint too, enabling resume).
Synchronous on purpose — callers wrap in asyncio.to_thread.
"""

from __future__ import annotations

from pathlib import Path


def _prefix(session_id: str) -> str:
    return f"sessions/{session_id}/state/"


def restore(project: str, bucket_name: str, session_id: str, root: Path) -> int:
    from google.cloud import storage

    bucket = storage.Client(project=project).bucket(bucket_name)
    prefix = _prefix(session_id)
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


def checkpoint(project: str, bucket_name: str, session_id: str, root: Path) -> int:
    from google.cloud import storage

    bucket = storage.Client(project=project).bucket(bucket_name)
    prefix = _prefix(session_id)
    count = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        bucket.blob(prefix + str(path.relative_to(root))).upload_from_filename(path)
        count += 1
    return count
