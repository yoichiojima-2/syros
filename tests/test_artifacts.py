from pathlib import Path

import pytest

from syros import artifacts
from syros.errors import OptionsError


class FakeBlob:
    def __init__(self, bucket, name):
        self.bucket = bucket
        self.name = name
        self.updated = None

    @property
    def size(self):
        return len(self.bucket.objects.get(self.name, b""))

    def upload_from_filename(self, path):
        self.bucket.objects[self.name] = Path(path).read_bytes()

    def download_to_filename(self, path):
        Path(path).write_bytes(self.bucket.objects[self.name])

    def exists(self):
        return self.name in self.bucket.objects

    def reload(self):
        pass

    def download_as_bytes(self):
        return self.bucket.objects[self.name]


class FakeListing(list):
    def __init__(self, blobs, prefixes):
        super().__init__(blobs)
        self.prefixes = prefixes


class FakeBucket:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def blob(self, name):
        return FakeBlob(self, name)

    def list_blobs(self, prefix="", delimiter=None):
        names = sorted(n for n in self.objects if n.startswith(prefix))
        if delimiter is None:
            return FakeListing([FakeBlob(self, n) for n in names], set())
        blobs, prefixes = [], set()
        for name in names:
            rest = name[len(prefix) :]
            if delimiter in rest:
                prefixes.add(prefix + rest.split(delimiter)[0] + delimiter)
            else:
                blobs.append(FakeBlob(self, name))
        return FakeListing(blobs, prefixes)

    def copy_blob(self, blob, destination_bucket, new_name):
        destination_bucket.objects[new_name] = self.objects[blob.name]


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

    assert artifacts.push("p", "b", "team", [src, single]) == 3
    assert set(bucket.objects) == {
        "artifacts/team/report.md",
        "artifacts/team/sub/data.csv",
        "artifacts/team/note.txt",
    }

    dest = tmp_path / "dest"
    assert artifacts.pull("p", "b", "team", dest) == 3
    assert (dest / "sub" / "data.csv").read_text() == "1,2"

    listing = artifacts.list_artifacts("p", "b", "team")
    assert {a.name for a in listing} == {"report.md", "sub/data.csv", "note.txt"}
    assert artifacts.list_spaces("p", "b") == ["team"]


def test_push_missing_path(bucket, tmp_path):
    with pytest.raises(FileNotFoundError):
        artifacts.push("p", "b", "team", [tmp_path / "nope"])


def test_publish_copies_from_session_state(bucket):
    bucket.objects["sessions/sess_x/state/ws/report.md"] = b"done"
    assert artifacts.publish("p", "b", "team", "sessions/sess_x/state/ws/", ["report.md"]) == 1
    assert bucket.objects["artifacts/team/report.md"] == b"done"
    with pytest.raises(FileNotFoundError):
        artifacts.publish("p", "b", "team", "sessions/sess_x/state/ws/", ["missing.md"])


def test_checkpoint_excludes_mounted_spaces(bucket, tmp_path):
    from syros import workspace

    ws = tmp_path / "ws"
    (ws / "artifacts" / "team").mkdir(parents=True)
    (ws / "notes.md").write_text("mine")
    (ws / "artifacts" / "team" / "report.md").write_text("shared")

    assert workspace.checkpoint("p", "b", "sessions/s/state/ws/", ws, ("artifacts/",)) == 1
    assert set(bucket.objects) == {"sessions/s/state/ws/notes.md"}


def test_read_artifact(bucket):
    bucket.objects["artifacts/team/sub/report.html"] = b"<h1>hi</h1>"

    data, content_type = artifacts.read_artifact("p", "b", "team", "sub/report.html", max_bytes=100)
    assert data == b"<h1>hi</h1>"
    assert content_type == "text/html"

    with pytest.raises(FileNotFoundError):
        artifacts.read_artifact("p", "b", "team", "missing.html", max_bytes=100)
    with pytest.raises(ValueError):
        artifacts.read_artifact("p", "b", "team", "sub/report.html", max_bytes=2)
    for bad in ("", "/abs", "a/../b", ".."):
        with pytest.raises(OptionsError):
            artifacts.read_artifact("p", "b", "team", bad, max_bytes=100)


def test_mount_prompt_lists_spaces_with_modes():
    prompt = artifacts.mount_prompt({"team": "rw", "inputs": "ro"})
    assert "./artifacts/team/ (read-write" in prompt
    assert "published at the end of every turn" in prompt
    assert "./artifacts/inputs/ (read-only" in prompt
    assert "discarded" in prompt


def test_mount_prompt_empty_without_spaces():
    assert artifacts.mount_prompt({}) is None
