"""GCS reads for the console — workspaces/ and artifacts/ prefixes.

ConsoleAPI is typed against ObjectStoreProtocol so tests run on an in-memory
fake; GcsObjects is the real thing, wrapping the sync helpers in
artifacts.py-style listings via asyncio.to_thread (same contract as the CLI).
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

from .. import artifacts

# One artifact preview per request; anything bigger is a download, not a view.
MAX_PREVIEW_BYTES = 10 * 1024 * 1024


@runtime_checkable
class ObjectStoreProtocol(Protocol):
    async def workspace_stats(self) -> dict[str, dict[str, Any]]: ...
    async def workspace_files(self, name: str) -> list[dict[str, Any]]: ...
    async def space_stats(self) -> dict[str, dict[str, Any]]: ...
    async def list_artifacts(self, space: str) -> list[dict[str, Any]]: ...
    async def read_artifact(self, space: str, name: str) -> tuple[bytes, str]: ...


def _stats(blobs, prefix: str) -> dict[str, dict[str, Any]]:
    """Aggregate one full listing under prefix into per-name {file_count,
    total_size, updated} — a single GCS call instead of one per name."""
    stats: dict[str, dict[str, Any]] = {}
    for blob in blobs:
        rest = blob.name[len(prefix) :]
        if "/" not in rest:
            continue
        name = rest.split("/", 1)[0]
        row = stats.setdefault(name, {"file_count": 0, "total_size": 0, "updated": None})
        row["file_count"] += 1
        row["total_size"] += blob.size or 0
        if blob.updated and (row["updated"] is None or blob.updated > row["updated"]):
            row["updated"] = blob.updated
    return stats


def _files(blobs, prefix: str) -> list[dict[str, Any]]:
    return [
        {"name": blob.name[len(prefix) :], "size": blob.size or 0, "updated": blob.updated}
        for blob in blobs
        if blob.name != prefix
    ]


class GcsObjects:
    def __init__(self, project: str, bucket: str) -> None:
        self._project = project
        self._bucket = bucket

    def _list(self, prefix: str):
        return artifacts._bucket(self._project, self._bucket).list_blobs(prefix=prefix)

    async def workspace_stats(self) -> dict[str, dict[str, Any]]:
        return await asyncio.to_thread(lambda: _stats(self._list("workspaces/"), "workspaces/"))

    async def workspace_files(self, name: str) -> list[dict[str, Any]]:
        from .. import workspace

        prefix = workspace.workspace_prefix(name)
        return await asyncio.to_thread(lambda: _files(self._list(prefix), prefix))

    async def space_stats(self) -> dict[str, dict[str, Any]]:
        return await asyncio.to_thread(lambda: _stats(self._list("artifacts/"), "artifacts/"))

    async def list_artifacts(self, space: str) -> list[dict[str, Any]]:
        prefix = artifacts.space_prefix(space)
        return await asyncio.to_thread(lambda: _files(self._list(prefix), prefix))

    async def read_artifact(self, space: str, name: str) -> tuple[bytes, str]:
        return await asyncio.to_thread(
            artifacts.read_artifact,
            self._project,
            self._bucket,
            space,
            name,
            max_bytes=MAX_PREVIEW_BYTES,
        )
