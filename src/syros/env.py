"""Environment resolution — the single home for syros env-var defaults.

Every SYROS_* lookup lives here so a default is defined exactly once. Callers
keep their own failure modes: find_project returns None and each caller decides
whether that is an OptionsError (SDK), a SystemExit (CLI), or "no Vertex
routing" (model_env).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_APPROVAL_TIMEOUT = 300.0
DEFAULT_DATASET = "syros"
DEFAULT_BQ_MAX_BYTES = 1 << 30  # bytes scanned per agent query
DEFAULT_BQ_MAX_ROWS = 200  # rows returned to the agent per query
DEFAULT_BQ_MAX_RESULT_BYTES = 32_000  # serialized result payload per query


def find_project(explicit: str | None = None) -> str | None:
    return explicit or os.environ.get("SYROS_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")


def default_bucket(explicit: str | None, project: str) -> str:
    return explicit or os.environ.get("SYROS_BUCKET") or f"{project}-syros"


def dataset(explicit: str | None = None) -> str:
    return explicit or os.environ.get("SYROS_DATASET") or DEFAULT_DATASET


def approval_timeout() -> float:
    return float(os.environ.get("SYROS_APPROVAL_TIMEOUT") or DEFAULT_APPROVAL_TIMEOUT)


@dataclass(frozen=True)
class BigQueryEnv:
    """Deployment policy for the built-in BigQuery tool. The caps are the
    deployment's, not the agent's: the tool takes `sql` and nothing else."""

    project: str
    dataset: str
    max_bytes: int
    max_rows: int
    max_result_bytes: int

    @classmethod
    def from_env(cls, project: str) -> BigQueryEnv:
        return cls(
            project=project,
            dataset=dataset(),
            max_bytes=int(os.environ.get("SYROS_BQ_MAX_BYTES") or DEFAULT_BQ_MAX_BYTES),
            max_rows=int(os.environ.get("SYROS_BQ_MAX_ROWS") or DEFAULT_BQ_MAX_ROWS),
            max_result_bytes=int(
                os.environ.get("SYROS_BQ_MAX_RESULT_BYTES") or DEFAULT_BQ_MAX_RESULT_BYTES
            ),
        )


@dataclass(frozen=True)
class RunnerEnv:
    """The sandbox runner's deployment-scoped configuration."""

    project: str
    bucket: str
    stay_alive: float
    lease_ttl: float
    work_dir: Path

    @classmethod
    def from_env(cls) -> RunnerEnv:
        project = find_project()
        if not project:
            raise SystemExit("runner requires $SYROS_PROJECT")
        return cls(
            project=project,
            bucket=default_bucket(None, project),
            stay_alive=float(os.environ.get("SYROS_STAY_ALIVE", "60")),
            lease_ttl=float(os.environ.get("SYROS_LEASE_TTL", "3600")),
            work_dir=Path(os.environ.get("SYROS_WORK_DIR", "/work")),
        )
