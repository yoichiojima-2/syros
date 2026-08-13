import pytest

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
    "SYROS_WORK_DIR",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
