import pytest

from syros import workspace
from syros.errors import OptionsError
from syros.workspace import session_prefix, workspace_prefix


def test_session_prefix():
    assert session_prefix("sess_x", "ws") == "sessions/sess_x/state/ws/"
    assert session_prefix("sess_x", "home") == "sessions/sess_x/state/home/"


def test_workspace_prefix():
    assert workspace_prefix("data") == "workspaces/data/"


def test_workspace_prefix_rejects_bad_names():
    # the console takes the name from a URL segment or a JSON body, so the
    # prefix builder is the last place that can catch a path
    for bad in ("/tmp", "a/b", "../x", "", "Upper", ".", "a" * 65):
        with pytest.raises(OptionsError):
            workspace_prefix(bad)


# --- blob-level ops against an in-memory bucket ---


class FakeBlob:
    def __init__(self, bucket, name):
        self._bucket = bucket
        self.name = name
        self.metadata = None

    def exists(self):
        return self.name in self._bucket.objects

    def reload(self):
        record = self._bucket.objects[self.name]
        self.metadata = dict(record["metadata"]) if record["metadata"] else None

    def upload_from_filename(self, path):
        # like GCS, an upload replaces the object; metadata set on this handle
        # (and nothing else) survives onto the new object
        metadata = {k: v for k, v in (self.metadata or {}).items() if v is not None}
        self._bucket.objects[self.name] = {
            "data": path.read_bytes(),
            "metadata": metadata or None,
        }

    def patch(self):
        record = self._bucket.objects[self.name]
        record["metadata"] = {
            k: v for k, v in (self.metadata or {}).items() if v is not None
        } or None

    def delete(self):
        del self._bucket.objects[self.name]


class FakeBucket:
    def __init__(self, objects=None):
        self.objects = {
            name: {"data": data, "metadata": metadata}
            for name, (data, metadata) in (objects or {}).items()
        }

    def blob(self, name):
        return FakeBlob(self, name)

    def list_blobs(self, prefix=""):
        blobs = []
        for name in sorted(self.objects):
            if name.startswith(prefix):
                blob = FakeBlob(self, name)
                blob.reload()
                blobs.append(blob)
        return blobs

    def copy_blob(self, blob, bucket, new_name):
        bucket.objects[new_name] = {
            "data": self.objects[blob.name]["data"],
            "metadata": dict(self.objects[blob.name]["metadata"] or {}) or None,
        }


@pytest.fixture
def bucket(monkeypatch):
    fake = FakeBucket(
        {
            "workspaces/team/a.md": (b"aa", {"syros-tags": "draft"}),
            "workspaces/team/sub/b.md": (b"bb", None),
        }
    )
    monkeypatch.setattr(workspace, "_bucket", lambda project, bucket_name: fake)
    return fake


def test_checkpoint_preserves_tags(bucket, tmp_path):
    (tmp_path / "a.md").write_bytes(b"rewritten")
    (tmp_path / "new.md").write_bytes(b"new")

    count = workspace.checkpoint("proj", "bkt", "workspaces/team/", tmp_path)

    assert count == 2
    assert bucket.objects["workspaces/team/a.md"] == {
        "data": b"rewritten",
        "metadata": {"syros-tags": "draft"},
    }
    assert bucket.objects["workspaces/team/new.md"]["metadata"] is None


def test_rename_file(bucket):
    workspace.rename_file("proj", "bkt", "team", "a.md", "docs/a.md")
    assert "workspaces/team/a.md" not in bucket.objects
    assert bucket.objects["workspaces/team/docs/a.md"] == {
        "data": b"aa",
        "metadata": {"syros-tags": "draft"},
    }

    with pytest.raises(FileNotFoundError):
        workspace.rename_file("proj", "bkt", "team", "gone.md", "x.md")
    with pytest.raises(FileExistsError):
        workspace.rename_file("proj", "bkt", "team", "docs/a.md", "sub/b.md")
    with pytest.raises(OptionsError):
        workspace.rename_file("proj", "bkt", "team", "docs/a.md", "../escape")


def test_set_tags(bucket):
    workspace.set_tags("proj", "bkt", "team", "sub/b.md", ["x", "y"])
    assert bucket.objects["workspaces/team/sub/b.md"]["metadata"] == {"syros-tags": "x,y"}

    workspace.set_tags("proj", "bkt", "team", "a.md", [])
    assert bucket.objects["workspaces/team/a.md"]["metadata"] is None

    with pytest.raises(FileNotFoundError):
        workspace.set_tags("proj", "bkt", "team", "gone.md", ["x"])
    with pytest.raises(OptionsError):
        workspace.set_tags("proj", "bkt", "team", "a.md", ["Bad Tag"])


def test_delete_prefix(bucket):
    with pytest.raises(ValueError, match="limit"):
        workspace.delete_prefix("proj", "bkt", "workspaces/team/", max_files=1)

    assert workspace.delete_prefix("proj", "bkt", "workspaces/team/sub/", max_files=10) == 1
    assert workspace.delete_prefix("proj", "bkt", "workspaces/team/", max_files=10) == 1
    assert bucket.objects == {}
