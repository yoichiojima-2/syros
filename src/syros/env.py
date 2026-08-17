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

from .errors import OptionsError
from .names import validate_table

DEFAULT_APPROVAL_TIMEOUT = 300.0
DEFAULT_DATASET = "syros"
# The agents' own dataset — deliberately not DEFAULT_DATASET. The control-plane
# snapshot and the audit log live in that one, and the sandbox identity must
# never hold write IAM on the dataset holding its own audit trail.
DEFAULT_DATA_DATASET = "syros_data"
DEFAULT_BQ_MAX_BYTES = 1 << 30  # bytes scanned per agent query
DEFAULT_BQ_MAX_ROWS = 200  # rows returned to the agent per query
DEFAULT_BQ_MAX_RESULT_BYTES = 32_000  # serialized result payload per query
DEFAULT_BQ_MAX_INSERT_ROWS = 500  # rows per insert call
# Payload per insert call. BigQuery's own streaming limit is 10 MB per request;
# staying well under it keeps a rejected insert a syros error message the agent
# can act on rather than an opaque 413 from the API.
DEFAULT_BQ_MAX_INSERT_BYTES = 4_000_000


def find_project(explicit: str | None = None) -> str | None:
    return explicit or os.environ.get("SYROS_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")


def default_bucket(explicit: str | None, project: str) -> str:
    return explicit or os.environ.get("SYROS_BUCKET") or f"{project}-syros"


def dataset(explicit: str | None = None) -> str:
    return explicit or os.environ.get("SYROS_DATASET") or DEFAULT_DATASET


def data_dataset(explicit: str | None = None) -> str:
    return explicit or os.environ.get("SYROS_BQ_DATA_DATASET") or DEFAULT_DATA_DATASET


def approval_timeout() -> float:
    return float(os.environ.get("SYROS_APPROVAL_TIMEOUT") or DEFAULT_APPROVAL_TIMEOUT)


@dataclass(frozen=True)
class BigQueryEnv:
    """Deployment policy for the built-in BigQuery tool. The caps and both
    dataset names are the deployment's, not the agent's: the read tool takes
    `sql` and nothing else, and the write tools take a bare table name that
    only ever resolves inside `write_dataset`."""

    project: str
    dataset: str
    max_bytes: int
    max_rows: int
    max_result_bytes: int
    write_dataset: str = DEFAULT_DATA_DATASET
    max_insert_rows: int = DEFAULT_BQ_MAX_INSERT_ROWS
    max_insert_bytes: int = DEFAULT_BQ_MAX_INSERT_BYTES

    def writable_dataset(self) -> str:
        """The dataset the write tools address, or a refusal.

        The separation of the two datasets is the whole security model: the
        sandbox identity holds write IAM on write_dataset, so a deployment that
        set both names the same would have the write tools pointed at syros's
        own audit log. Terraform refuses that combination up front
        (google_bigquery_dataset.agent_data's precondition); this is the second
        line, and it fails closed inside the tool rather than at session setup
        so the run survives to report it.
        """
        if self.write_dataset == self.dataset:
            raise OptionsError(
                "this deployment is misconfigured and no table can be written:"
                f" SYROS_BQ_DATA_DATASET and SYROS_DATASET are both {self.dataset!r},"
                " and the agent-writable dataset must not be the one holding syros's"
                " own audit tables. Report this to whoever runs the deployment."
            )
        return self.write_dataset

    def table(self, table: str) -> str:
        """The fully-qualified id of an agent table. The single place a
        model-supplied name becomes a table id — `names.validate_table` is what
        stops that name from naming a dataset."""
        return f"{self.project}.{self.writable_dataset()}.{validate_table(table)}"

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
            write_dataset=data_dataset(),
            max_insert_rows=int(
                os.environ.get("SYROS_BQ_MAX_INSERT_ROWS") or DEFAULT_BQ_MAX_INSERT_ROWS
            ),
            max_insert_bytes=int(
                os.environ.get("SYROS_BQ_MAX_INSERT_BYTES") or DEFAULT_BQ_MAX_INSERT_BYTES
            ),
        )


@dataclass(frozen=True)
class RunnerEnv:
    """The sandbox runner's deployment-scoped configuration."""

    project: str
    bucket: str
    stay_alive: float
    lease_ttl: float
    heartbeat: float
    work_dir: Path

    @classmethod
    def from_env(cls) -> RunnerEnv:
        project = find_project()
        if not project:
            raise SystemExit("runner requires $SYROS_PROJECT")
        # The lease is short and heartbeat-renewed (default every ttl/3), so a
        # dead runner is detected within minutes instead of masquerading as
        # "running" for the rest of a long ttl.
        lease_ttl = float(os.environ.get("SYROS_LEASE_TTL", "180"))
        return cls(
            project=project,
            bucket=default_bucket(None, project),
            stay_alive=float(os.environ.get("SYROS_STAY_ALIVE", "60")),
            lease_ttl=lease_ttl,
            heartbeat=float(os.environ.get("SYROS_HEARTBEAT", str(lease_ttl / 3))),
            work_dir=Path(os.environ.get("SYROS_WORK_DIR", "/work")),
        )
