import pytest

from syros import artifacts
from syros.errors import OptionsError

from .fakes import FakeBucket


@pytest.fixture
def bucket(monkeypatch):
    from google.cloud import storage

    fake = FakeBucket()

    class FakeClient:
        def __init__(self, project=None):
            pass

        def bucket(self, name):
            return fake

    monkeypatch.setattr(storage, "Client", FakeClient)
    return fake


def test_space_prefix():
    assert artifacts.space_prefix("reports") == "artifacts/reports/"
    for bad in ("", "Reports", "a/b", "-x", "a" * 65):
        with pytest.raises(OptionsError):
            artifacts.space_prefix(bad)


def test_push_pull_roundtrip(bucket, tmp_path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "report.md").write_text("hello")
    (src / "sub" / "data.csv").write_text("1,2")
    single = tmp_path / "note.txt"
    single.write_text("note")

    assert artifacts.push("p", "b", "workspace", [src, single]) == 3
    assert set(bucket.objects) == {
        "artifacts/workspace/report.md",
        "artifacts/workspace/sub/data.csv",
        "artifacts/workspace/note.txt",
    }

    dest = tmp_path / "dest"
    assert artifacts.pull("p", "b", "workspace", dest) == 3
    assert (dest / "sub" / "data.csv").read_text() == "1,2"

    listing = artifacts.list_artifacts("p", "b", "workspace")
    assert {a.name for a in listing} == {"report.md", "sub/data.csv", "note.txt"}
    assert artifacts.list_spaces("p", "b") == ["workspace"]


def test_push_missing_path(bucket, tmp_path):
    with pytest.raises(FileNotFoundError):
        artifacts.push("p", "b", "workspace", [tmp_path / "nope"])


def test_publish_copies_from_session_state(bucket):
    bucket.objects["sessions/sess_x/state/ws/report.md"] = {"data": b"done", "metadata": None}
    assert artifacts.publish("p", "b", "workspace", "sessions/sess_x/state/ws/", ["report.md"]) == 1
    assert bucket.objects["artifacts/workspace/report.md"]["data"] == b"done"
    with pytest.raises(FileNotFoundError):
        artifacts.publish("p", "b", "workspace", "sessions/sess_x/state/ws/", ["missing.md"])


def test_checkpoint_excludes_mounted_spaces(bucket, tmp_path):
    from syros import workspace

    ws = tmp_path / "ws"
    (ws / "artifacts" / "workspace").mkdir(parents=True)
    (ws / "notes.md").write_text("mine")
    (ws / "artifacts" / "workspace" / "report.md").write_text("shared")

    assert workspace.checkpoint("p", "b", "sessions/s/state/ws/", ws, ("artifacts/",)) == 1
    assert set(bucket.objects) == {"sessions/s/state/ws/notes.md"}


def test_read_artifact(bucket):
    bucket.objects["artifacts/workspace/sub/report.html"] = {
        "data": b"<h1>hi</h1>",
        "metadata": None,
    }

    data, content_type = artifacts.read_artifact(
        "p", "b", "workspace", "sub/report.html", max_bytes=100
    )
    assert data == b"<h1>hi</h1>"
    assert content_type == "text/html"

    with pytest.raises(FileNotFoundError):
        artifacts.read_artifact("p", "b", "workspace", "missing.html", max_bytes=100)
    with pytest.raises(ValueError):
        artifacts.read_artifact("p", "b", "workspace", "sub/report.html", max_bytes=2)
    for bad in ("", "/abs", "a/../b", ".."):
        with pytest.raises(OptionsError):
            artifacts.read_artifact("p", "b", "workspace", bad, max_bytes=100)


def test_mount_prompt_lists_spaces_with_modes():
    prompt = artifacts.mount_prompt({"workspace": "rw", "inputs": "ro"})
    assert "./artifacts/workspace/ (read-write" in prompt
    assert "published at the end of every turn" in prompt
    assert "./artifacts/inputs/ (read-only" in prompt
    assert "discarded" in prompt


def test_mount_prompt_names_the_single_rw_space_as_deliverable_target():
    prompt = artifacts.mount_prompt({"workspace": "rw", "inputs": "ro"})
    assert "Save every deliverable" in prompt
    assert "directly under ./artifacts/workspace/" in prompt


def test_mount_prompt_points_at_rw_spaces_generically_when_several():
    prompt = artifacts.mount_prompt({"workspace": "rw", "reports": "rw"})
    assert "Save every deliverable" in prompt
    assert "one of the read-write spaces above" in prompt
    assert "directly under ./artifacts/" not in prompt


def test_mount_prompt_skips_deliverable_instruction_without_rw_space():
    prompt = artifacts.mount_prompt({"inputs": "ro"})
    assert "Save every deliverable" not in prompt


def test_mount_prompt_empty_without_spaces():
    assert artifacts.mount_prompt({}) is None
