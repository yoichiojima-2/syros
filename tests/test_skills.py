"""skills.py: prefix rules, local-directory push, and the official-skills tarball sync."""

import io
import tarfile

import pytest

from syros import skills
from syros.errors import OptionsError

# The real write, captured before the autouse fixture below stubs it out — one
# test drives push all the way down to the blobs.
REAL_WRITE_FILE = skills.write_file


def test_skill_prefix():
    assert skills.skill_prefix("pdf") == "skills/pdf/"
    for name in ("", "Upper", "a/b", "../etc"):
        with pytest.raises(OptionsError):
            skills.skill_prefix(name)


def make_tarball(files: dict[str, bytes]) -> bytes:
    """Build a gzipped tarball like GitHub's: everything under one top dir."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def capture_writes(monkeypatch):
    written: list[tuple[str, str, bytes]] = []
    monkeypatch.setattr(
        skills,
        "write_file",
        # workspace is optional so both sync_official (global only) and push land here
        lambda project, bucket, name, file, data, workspace=None: written.append(
            (name, file, data)
        ),
    )
    return written


def make_skill_dir(root, name: str = "pdf", files: dict[str, bytes] | None = None):
    """A local skill directory: SKILL.md plus whatever else, nesting as needed."""
    path = root / name
    path.mkdir(parents=True)
    (path / "SKILL.md").write_bytes(b"# pdf")
    for file, data in (files or {}).items():
        target = path / file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return path


def test_push_uploads_the_directory(tmp_path, capture_writes):
    path = make_skill_dir(tmp_path, files={"scripts/fill.py": b"print()", "notes.md": b"hi"})
    summary = skills.push("p", "b", path, max_bytes=1024)
    assert summary == {"skill": "pdf", "files": 3, "deleted": 0, "skipped": []}
    assert sorted(f for _, f, _ in capture_writes) == ["SKILL.md", "notes.md", "scripts/fill.py"]
    assert all(name == "pdf" for name, _, _ in capture_writes)
    assert ("pdf", "scripts/fill.py", b"print()") in capture_writes


def test_push_names_the_skill_after_the_directory(tmp_path, capture_writes):
    path = make_skill_dir(tmp_path, name="my-skill")
    assert skills.push("p", "b", path, max_bytes=1024)["skill"] == "my-skill"
    # an override wins, and both paths go through validate_name
    assert skills.push("p", "b", path, max_bytes=1024, name="other")["skill"] == "other"
    with pytest.raises(OptionsError):
        skills.push("p", "b", path, max_bytes=1024, name="Bad Name")


def test_push_rejects_a_directory_named_like_a_path(tmp_path, capture_writes):
    path = make_skill_dir(tmp_path, name="Upper")
    with pytest.raises(OptionsError):
        skills.push("p", "b", path, max_bytes=1024)
    assert capture_writes == []


def test_push_requires_skill_md(tmp_path, capture_writes):
    path = tmp_path / "pdf"
    path.mkdir()
    (path / "notes.md").write_bytes(b"no skill here")
    with pytest.raises(ValueError, match="SKILL.md"):
        skills.push("p", "b", path, max_bytes=1024)
    assert capture_writes == []


def test_push_rejects_a_missing_path_or_a_plain_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        skills.push("p", "b", tmp_path / "nope", max_bytes=1024)
    file = tmp_path / "SKILL.md"
    file.write_bytes(b"# not a directory")
    with pytest.raises(NotADirectoryError):
        skills.push("p", "b", file, max_bytes=1024)


def test_push_scopes_to_a_workspace(tmp_path, monkeypatch):
    scopes: list[str | None] = []
    monkeypatch.setattr(
        skills,
        "write_file",
        lambda project, bucket, name, file, data, workspace=None: scopes.append(workspace),
    )
    skills.push("p", "b", make_skill_dir(tmp_path), max_bytes=1024, workspace="ws")
    assert scopes == ["ws"]


def test_push_skips_symlinks_and_tooling_state(tmp_path, capture_writes):
    path = make_skill_dir(
        tmp_path,
        files={
            ".git/config": b"[core]",
            ".DS_Store": b"junk",
            "scripts/__pycache__/fill.pyc": b"bytecode",
            "scripts/node_modules/left-pad/index.js": b"module.exports",
            "scripts/fill.py": b"print()",
        },
    )
    (path / "evil").symlink_to("/etc/passwd")
    summary = skills.push("p", "b", path, max_bytes=1024)
    assert summary["files"] == 2
    assert sorted(f for _, f, _ in capture_writes) == ["SKILL.md", "scripts/fill.py"]


def test_push_skips_oversized_files(tmp_path, capture_writes):
    path = make_skill_dir(tmp_path, files={"big.bin": b"x" * 2000})
    summary = skills.push("p", "b", path, max_bytes=1024)
    assert summary["files"] == 1
    assert summary["skipped"] == [{"file": "big.bin", "size": 2000}]
    assert [f for _, f, _ in capture_writes] == ["SKILL.md"]


class FakeBlob:
    def __init__(self, bucket, name):
        self.bucket = bucket
        self.name = name

    def upload_from_string(self, data, content_type=None):
        self.bucket.objects[self.name] = (data, content_type)

    def exists(self):
        return self.name in self.bucket.objects

    def delete(self):
        del self.bucket.objects[self.name]


class FakeBucket:
    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}

    def blob(self, name):
        return FakeBlob(self, name)

    def list_blobs(self, prefix=""):
        return [FakeBlob(self, n) for n in sorted(self.objects) if n.startswith(prefix)]


@pytest.fixture
def bucket(monkeypatch):
    """A bucket behind the real write, so push runs end to end."""
    from google.cloud import storage

    fake = FakeBucket()
    monkeypatch.setattr(
        storage, "Client", lambda project=None: type("C", (), {"bucket": lambda s, n: fake})()
    )
    monkeypatch.setattr(skills, "write_file", REAL_WRITE_FILE)
    return fake


def test_push_lands_blobs_under_the_skill_prefix(tmp_path, bucket):
    path = make_skill_dir(tmp_path, files={"scripts/fill.py": b"print()"})

    skills.push("p", "b", path, max_bytes=1024)
    assert sorted(bucket.objects) == ["skills/pdf/SKILL.md", "skills/pdf/scripts/fill.py"]
    assert bucket.objects["skills/pdf/SKILL.md"] == (b"# pdf", "text/markdown")

    skills.push("p", "b", path, max_bytes=1024, workspace="ws")
    assert "team-skills/ws/pdf/SKILL.md" in bucket.objects


def test_push_merges_unless_replacing(tmp_path, bucket):
    path = make_skill_dir(tmp_path, files={"scripts/fill.py": b"print()"})
    skills.push("p", "b", path, max_bytes=1024)
    skills.push("p", "b", path, max_bytes=1024, workspace="ws")

    (path / "scripts" / "fill.py").unlink()
    assert skills.push("p", "b", path, max_bytes=1024)["deleted"] == 0  # a merge keeps it
    assert "skills/pdf/scripts/fill.py" in bucket.objects

    summary = skills.push("p", "b", path, max_bytes=1024, replace=True)
    assert summary["deleted"] == 1
    assert sorted(n for n in bucket.objects if n.startswith("skills/")) == ["skills/pdf/SKILL.md"]
    assert "team-skills/ws/pdf/scripts/fill.py" in bucket.objects  # the other scope is untouched


def test_push_replace_keeps_a_file_it_had_to_skip(tmp_path, bucket):
    """The dangerous case: prune what the walk covered, never what it skipped.
    Clearing the prefix first would delete the bucket's only copy of a file
    that grew past the limit — and report success while doing it."""
    path = make_skill_dir(tmp_path, files={"big.bin": b"x" * 100})
    skills.push("p", "b", path, max_bytes=1024)
    assert "skills/pdf/big.bin" in bucket.objects

    (path / "big.bin").write_bytes(b"x" * 2000)  # now over the limit
    summary = skills.push("p", "b", path, max_bytes=1024, replace=True)
    assert summary["skipped"] == [{"file": "big.bin", "size": 2000}]
    assert summary["deleted"] == 0
    assert sorted(bucket.objects) == ["skills/pdf/SKILL.md", "skills/pdf/big.bin"]


def test_push_replace_on_a_skill_that_does_not_exist_yet(tmp_path, bucket):
    summary = skills.push("p", "b", make_skill_dir(tmp_path), max_bytes=1024, replace=True)
    assert (summary["files"], summary["deleted"]) == (1, 0)
    assert sorted(bucket.objects) == ["skills/pdf/SKILL.md"]


def test_sync_official_detects_skills(capture_writes):
    tarball = make_tarball(
        {
            "skills-main/README.md": b"repo readme",  # outside skills/: not a skill file
            "skills-main/template/SKILL.md": b"scaffold",  # scaffolding, not under skills/
            "skills-main/skills/pdf/SKILL.md": b"# pdf",
            "skills-main/skills/pdf/scripts/fill.py": b"print()",
            "skills-main/skills/xlsx/SKILL.md": b"# xlsx",
        }
    )
    summary = skills.sync_official("p", "b", max_bytes=1024, fetch=lambda: tarball)
    assert summary == {"skills": ["pdf", "xlsx"], "files": 3, "skipped": []}
    assert ("pdf", "SKILL.md", b"# pdf") in capture_writes
    assert ("pdf", "scripts/fill.py", b"print()") in capture_writes
    assert ("xlsx", "SKILL.md", b"# xlsx") in capture_writes


def test_sync_official_skips_oversized(capture_writes):
    tarball = make_tarball(
        {
            "r/skills/pdf/SKILL.md": b"# pdf",
            "r/skills/pdf/big.bin": b"x" * 2000,
        }
    )
    summary = skills.sync_official("p", "b", max_bytes=1024, fetch=lambda: tarball)
    assert summary["files"] == 1
    assert summary["skipped"] == [{"skill": "pdf", "file": "big.bin", "size": 2000}]


def test_sync_official_ignores_bad_names_and_traversal(capture_writes):
    tarball = make_tarball(
        {
            "r/skills/Bad Name/SKILL.md": b"nope",  # invalid skill name
            "r/skills/ok/SKILL.md": b"ok",
            "r/skills/ok/sub/../x": b"weird",  # ".." segment: never uploaded
        }
    )
    summary = skills.sync_official("p", "b", max_bytes=1024, fetch=lambda: tarball)
    assert summary["skills"] == ["ok"]
    assert all(name == "ok" for name, _, _ in capture_writes)


def test_sync_official_skips_non_regular_members(capture_writes):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        info = tarfile.TarInfo("r/skills/pdf/SKILL.md")
        data = b"# pdf"
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("r/skills/pdf/evil")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tar.addfile(link)
    summary = skills.sync_official("p", "b", max_bytes=1024, fetch=lambda: buffer.getvalue())
    assert summary["files"] == 1
    assert [f for _, f, _ in capture_writes] == ["SKILL.md"]


def test_parse_description_reads_frontmatter():
    body = b"---\nname: pdf\ndescription: Merge, split, and OCR PDF files\n---\n# pdf\n"
    assert skills.parse_description(body) == "Merge, split, and OCR PDF files"


def test_parse_description_handles_quotes_and_folded_blocks():
    assert skills.parse_description(b'---\ndescription: "Quoted: with a colon"\n---\n') == (
        "Quoted: with a colon"
    )
    folded = b"---\nname: x\ndescription: >-\n  first half\n  second half\nother: 1\n---\n"
    assert skills.parse_description(folded) == "first half second half"


def test_parse_description_survives_a_truncated_read():
    """The console reads only the head of SKILL.md, so the closing --- may be gone."""
    head = b"---\nname: canvas-design\ndescription: Create beautiful visual art\nlicense: on"
    assert skills.parse_description(head) == "Create beautiful visual art"


def test_parse_description_absent_or_nested():
    assert skills.parse_description(b"# no frontmatter\ndescription: nope\n") is None
    assert skills.parse_description(b"---\nname: pdf\n---\n") is None
    # indented: belongs to some nested key, not the skill itself
    assert skills.parse_description(b"---\ntool:\n  description: inner\n---\n") is None
