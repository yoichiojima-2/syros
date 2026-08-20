import pytest

from syros import workspace
from syros.errors import OptionsError
from .fakes import FakeBucket
from syros.layout import (
    session_prefix,
    skill_prefix,
    skills_root,
    space_prefix,
    workspace_prefix,
    workspace_root,
    workspace_skills_root,
)


def test_session_prefix():
    assert session_prefix("sess_x", "ws") == "sessions/sess_x/state/ws/"
    assert session_prefix("sess_x", "home") == "sessions/sess_x/state/home/"


def test_workspace_owns_one_prefix_holding_its_files_and_its_skills():
    assert workspace_root("data") == "workspaces/data/"
    assert workspace_prefix("data") == "workspaces/data/ws/"
    assert workspace_skills_root("data") == "workspaces/data/skills/"
    assert skill_prefix("pdf", "data") == "workspaces/data/skills/pdf/"
    # a workspace's skills sit beside its files, never inside the agent's cwd
    assert not skills_root("data").startswith(workspace_prefix("data"))


def test_prefix_builders_reject_bad_names():
    # the console takes the name from a URL segment or a JSON body, so the
    # prefix builder is the last place that can catch a path
    for bad in ("/tmp", "a/b", "../x", "", "Upper", ".", "a" * 65):
        for build in (workspace_root, workspace_prefix, workspace_skills_root, space_prefix):
            with pytest.raises(OptionsError):
                build(bad)
        if bad:  # an empty workspace means "global", the same as None
            with pytest.raises(OptionsError):
                skill_prefix("pdf", bad)


# --- blob-level ops against an in-memory bucket ---


@pytest.fixture
def bucket(monkeypatch):
    fake = FakeBucket(
        {
            "workspaces/workspace/ws/a.md": (b"aa", {"syros-tags": "draft"}),
            "workspaces/workspace/ws/sub/b.md": (b"bb", None),
        }
    )
    monkeypatch.setattr(workspace, "_bucket", lambda project, bucket_name: fake)
    return fake


def test_restore_reads_the_live_object_not_the_listed_generation(bucket, tmp_path):
    """A listing is a snapshot, and its blobs carry the generation they had when
    they were listed. Downloading through one of those pins that generation, so
    anything rewritten under the prefix mid-restore 404s — which at session
    start took the whole run down (the console's skills sync rewrites thousands
    of blobs and runs for minutes)."""
    listed = bucket.list_blobs

    def list_then_rewrite(prefix=""):
        blobs = listed(prefix=prefix)
        bucket.objects["workspaces/workspace/ws/a.md"]["data"] = b"rewritten"
        bucket.bump("workspaces/workspace/ws/a.md")
        return blobs

    bucket.list_blobs = list_then_rewrite

    count = workspace.restore("proj", "bkt", "workspaces/workspace/ws/", tmp_path)

    assert count == 2
    assert (tmp_path / "a.md").read_bytes() == b"rewritten"


def test_restore_skips_a_file_deleted_mid_restore(bucket, tmp_path):
    listed = bucket.list_blobs

    def list_then_delete(prefix=""):
        blobs = listed(prefix=prefix)
        del bucket.objects["workspaces/workspace/ws/a.md"]
        del bucket.generations["workspaces/workspace/ws/a.md"]
        return blobs

    bucket.list_blobs = list_then_delete

    count = workspace.restore("proj", "bkt", "workspaces/workspace/ws/", tmp_path)

    assert count == 1
    assert (tmp_path / "sub" / "b.md").read_bytes() == b"bb"
    # and no empty stub where the vanished file would have been
    assert not (tmp_path / "a.md").exists()


def test_checkpoint_preserves_tags(bucket, tmp_path):
    (tmp_path / "a.md").write_bytes(b"rewritten")
    (tmp_path / "new.md").write_bytes(b"new")

    count = workspace.checkpoint("proj", "bkt", "workspaces/workspace/ws/", tmp_path)

    assert count == 2
    assert bucket.objects["workspaces/workspace/ws/a.md"] == {
        "data": b"rewritten",
        "metadata": {"syros-tags": "draft"},
    }
    assert bucket.objects["workspaces/workspace/ws/new.md"]["metadata"] is None


def test_rename_file(bucket):
    workspace.rename_file("proj", "bkt", "workspace", "a.md", "docs/a.md")
    assert "workspaces/workspace/ws/a.md" not in bucket.objects
    assert bucket.objects["workspaces/workspace/ws/docs/a.md"] == {
        "data": b"aa",
        "metadata": {"syros-tags": "draft"},
    }

    with pytest.raises(FileNotFoundError):
        workspace.rename_file("proj", "bkt", "workspace", "gone.md", "x.md")
    with pytest.raises(FileExistsError):
        workspace.rename_file("proj", "bkt", "workspace", "docs/a.md", "sub/b.md")
    with pytest.raises(OptionsError):
        workspace.rename_file("proj", "bkt", "workspace", "docs/a.md", "../escape")


def test_set_tags(bucket):
    workspace.set_tags("proj", "bkt", "workspace", "sub/b.md", ["x", "y"])
    assert bucket.objects["workspaces/workspace/ws/sub/b.md"]["metadata"] == {"syros-tags": "x,y"}

    workspace.set_tags("proj", "bkt", "workspace", "a.md", [])
    assert bucket.objects["workspaces/workspace/ws/a.md"]["metadata"] is None

    with pytest.raises(FileNotFoundError):
        workspace.set_tags("proj", "bkt", "workspace", "gone.md", ["x"])
    with pytest.raises(OptionsError):
        workspace.set_tags("proj", "bkt", "workspace", "a.md", ["Bad Tag"])


def test_delete_prefix(bucket):
    with pytest.raises(ValueError, match="limit"):
        workspace.delete_prefix("proj", "bkt", "workspaces/workspace/ws/", max_files=1)

    assert workspace.delete_prefix("proj", "bkt", "workspaces/workspace/ws/sub/", max_files=10) == 1
    assert workspace.delete_prefix("proj", "bkt", "workspaces/workspace/ws/", max_files=10) == 1
    assert bucket.objects == {}


def test_workspace_members_derived_from_agent_docs():
    from syros import workspaces

    agent_docs = [
        {"name": "writer", "options": {"workspace": "shared"}},
        {"name": "critic", "options": {"workspace": "shared"}},
        {"name": "loner", "options": {}},
        {"name": "other", "options": {"workspace": "elsewhere"}},
    ]
    assert workspaces.members("shared", agent_docs) == ["critic", "writer"]
    assert workspaces.members("empty", agent_docs) == []
