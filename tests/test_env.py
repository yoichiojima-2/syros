from pathlib import Path

import pytest

from syros import env


def test_find_project_explicit_wins(monkeypatch):
    monkeypatch.setenv("SYROS_PROJECT", "from-env")
    assert env.find_project("explicit") == "explicit"


def test_find_project_chain(monkeypatch):
    assert env.find_project() is None
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "gcp-proj")
    assert env.find_project() == "gcp-proj"
    monkeypatch.setenv("SYROS_PROJECT", "syros-proj")
    assert env.find_project() == "syros-proj"


def test_default_bucket(monkeypatch):
    assert env.default_bucket(None, "p") == "p-syros"
    monkeypatch.setenv("SYROS_BUCKET", "custom")
    assert env.default_bucket(None, "p") == "custom"
    assert env.default_bucket("explicit", "p") == "explicit"


def test_approval_timeout(monkeypatch):
    assert env.approval_timeout() == env.DEFAULT_APPROVAL_TIMEOUT
    monkeypatch.setenv("SYROS_APPROVAL_TIMEOUT", "42")
    assert env.approval_timeout() == 42.0


def test_runner_env_defaults(monkeypatch):
    monkeypatch.setenv("SYROS_PROJECT", "proj")
    config = env.RunnerEnv.from_env()
    assert config.project == "proj"
    assert config.bucket == "proj-syros"
    assert config.stay_alive == 60.0
    assert config.lease_ttl == 3600.0
    assert config.work_dir == Path("/work")


def test_runner_env_overrides(monkeypatch):
    monkeypatch.setenv("SYROS_PROJECT", "proj")
    monkeypatch.setenv("SYROS_BUCKET", "b")
    monkeypatch.setenv("SYROS_STAY_ALIVE", "5")
    monkeypatch.setenv("SYROS_LEASE_TTL", "10")
    monkeypatch.setenv("SYROS_WORK_DIR", "/tmp/w")
    config = env.RunnerEnv.from_env()
    assert (config.bucket, config.stay_alive, config.lease_ttl) == ("b", 5.0, 10.0)
    assert config.work_dir == Path("/tmp/w")


def test_runner_env_requires_project():
    with pytest.raises(SystemExit):
        env.RunnerEnv.from_env()
