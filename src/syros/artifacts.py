"""Shared artifact spaces: a publish surface in the session bucket.

An artifact space is just a GCS prefix (artifacts/{space}/) in the bucket the
sessions already use. Anyone with read access on the bucket can pull, so
sharing with other users is the existing IAM story — grant
roles/storage.objectViewer on the bucket, nothing new to run. Synchronous on
purpose — callers wrap in asyncio.to_thread (same contract as workspace.py).
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .names import validate_file, validate_name


def space_prefix(space: str) -> str:
    return f"artifacts/{validate_name('artifact space', space)}/"


def mount_prompt(spaces: dict[str, str]) -> str | None:
    """System prompt fragment describing mounted spaces to the agent.

    The mounts are harness-made: nothing else in the agent's context says they
    exist or that only the working directory survives the run, so a session
    can finish "successfully" having written its output to /tmp and published
    nothing. Injected by the runner whenever spaces are mounted.
    """
    if not spaces:
        return None
    lines = ["Shared artifact spaces are mounted in your working directory:"]
    for space, mode in spaces.items():
        detail = (
            "read-write: files written here are published when the session ends"
            if mode == "rw"
            else "read-only: local changes are discarded"
        )
        lines.append(f"- ./artifacts/{space}/ ({detail})")
    lines.append(
        "Only the working directory persists; anything outside it (/tmp, $HOME)"
        " is discarded when the session ends. Write deliverables into a"
        " read-write artifact space to publish them."
    )
    lines.append(
        "Published files are visible to humans in the syros console and to other"
        " sessions that mount the same space — prefer self-contained HTML (inline"
        " CSS/JS, no external fetches) for reports and dashboards."
    )
    return "\n".join(lines)


@dataclass(frozen=True)
class Artifact:
    name: str
    size: int
    updated: datetime | None


def _bucket(project: str, bucket_name: str):
    from google.cloud import storage

    return storage.Client(project=project).bucket(bucket_name)


def list_spaces(project: str, bucket_name: str) -> list[str]:
    iterator = _bucket(project, bucket_name).list_blobs(prefix="artifacts/", delimiter="/")
    list(iterator)  # prefixes populate only after iteration
    return sorted(p[len("artifacts/") :].rstrip("/") for p in iterator.prefixes)


def list_artifacts(project: str, bucket_name: str, space: str) -> list[Artifact]:
    prefix = space_prefix(space)
    return [
        Artifact(name=blob.name[len(prefix) :], size=blob.size or 0, updated=blob.updated)
        for blob in _bucket(project, bucket_name).list_blobs(prefix=prefix)
        if blob.name != prefix
    ]


def read_artifact(
    project: str, bucket_name: str, space: str, name: str, *, max_bytes: int
) -> tuple[bytes, str]:
    """Download one artifact: (data, content type). Raises FileNotFoundError for
    a missing blob and ValueError when it exceeds max_bytes (download instead)."""
    validate_file("artifact", name)
    prefix = space_prefix(space)
    blob = _bucket(project, bucket_name).blob(prefix + name)
    if not blob.exists():
        raise FileNotFoundError(f"gs://{bucket_name}/{prefix}{name}")
    blob.reload()
    if (blob.size or 0) > max_bytes:
        raise ValueError(f"{name} is {blob.size} bytes (limit {max_bytes})")
    return blob.download_as_bytes(), mimetypes.guess_type(name)[0] or "application/octet-stream"


def push(project: str, bucket_name: str, space: str, paths: list[Path]) -> int:
    """Upload local files (or directories, recursively) into the space."""
    prefix = space_prefix(space)
    bucket = _bucket(project, bucket_name)
    count = 0
    for path in paths:
        if path.is_dir():
            for file in sorted(path.rglob("*")):
                if not file.is_file() or file.is_symlink():
                    continue
                bucket.blob(prefix + str(file.relative_to(path))).upload_from_filename(file)
                count += 1
        elif path.is_file():
            bucket.blob(prefix + path.name).upload_from_filename(path)
            count += 1
        else:
            raise FileNotFoundError(path)
    return count


def pull(project: str, bucket_name: str, space: str, dest: Path) -> int:
    from . import workspace

    return workspace.restore(project, bucket_name, space_prefix(space), dest)


def publish(
    project: str, bucket_name: str, space: str, source_prefix: str, names: list[str]
) -> int:
    """Server-side copy files from a session/workspace state prefix into the space."""
    prefix = space_prefix(space)
    bucket = _bucket(project, bucket_name)
    count = 0
    for name in names:
        blob = bucket.blob(source_prefix + name)
        if not blob.exists():
            raise FileNotFoundError(f"gs://{bucket_name}/{source_prefix}{name}")
        bucket.copy_blob(blob, bucket, prefix + name)
        count += 1
    return count
