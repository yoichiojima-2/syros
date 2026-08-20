import pytest

import syros.remote

ENV_VARS = (
    "SYROS_PROJECT",
    "SYROS_REGION",
    "SYROS_BUCKET",
    "SYROS_JOB",
    "GOOGLE_CLOUD_PROJECT",
    "CLOUD_ML_REGION",
    "SYROS_MODEL_BACKEND",
    "ANTHROPIC_API_KEY",
    "SYROS_APPROVAL_TIMEOUT",
    "SYROS_STAY_ALIVE",
    "SYROS_LEASE_TTL",
    "SYROS_HEARTBEAT",
    "SYROS_WORK_DIR",
    "SYROS_DATASET",
    "SYROS_TITLE_MODEL",
    "SYROS_BQ_DATA_DATASET",
    "SYROS_BQ_MAX_BYTES",
    "SYROS_BQ_MAX_ROWS",
    "SYROS_BQ_MAX_RESULT_BYTES",
    "SYROS_BQ_MAX_INSERT_ROWS",
    "SYROS_BQ_MAX_INSERT_BYTES",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


class TriggeredJobs(list):
    """The (project, region, job, session_id) tuples fake-triggered in a test."""

    @property
    def session_ids(self) -> list[str]:
        return [session_id for _, _, _, session_id in self]


@pytest.fixture(autouse=True)
def no_job_trigger(monkeypatch):
    """No test may launch a real Cloud Run job; record the attempts instead."""
    triggered = TriggeredJobs()

    async def fake_trigger(project, region, job, session_id):
        triggered.append((project, region, job, session_id))

    monkeypatch.setattr(syros.remote, "_trigger_job", fake_trigger)
    return triggered
