"""skills.py: the catalog prefix, installs, the tarball sync, and the
one-time promotion of pre-catalog workspace skills."""

import io
import tarfile

import pytest

from syros import skills
from syros.errors import OptionsError

from .fakes import FakeStore


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
        lambda project, bucket, name, file, data: written.append((name, file, data)),
    )
    return written


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


# --- installs ---


async def test_install_and_uninstall_on_a_workspace():
    store = FakeStore()

    assert await skills.install(store, ["pdf", "xlsx"], workspace="research") == ["pdf", "xlsx"]
    # upserted: a workspace can exist as a bare GCS directory with no doc yet
    assert (await store.get_workspace("research"))["options"]["skills"] == ["pdf", "xlsx"]
    assert await skills.installed(store, workspace="research") == ["pdf", "xlsx"]
    # nothing leaked onto the global default
    assert await skills.installed(store) == []

    # idempotent, and the rest of the stored options survive an install
    await store.update_workspace("research", options={"model": "opus", "skills": ["pdf", "xlsx"]})
    assert await skills.install(store, ["pdf"], workspace="research") == ["pdf", "xlsx"]
    assert (await store.get_workspace("research"))["options"]["model"] == "opus"

    assert await skills.uninstall(store, ["pdf"], workspace="research") == ["xlsx"]
    with pytest.raises(skills.SkillError, match="not installed"):
        await skills.uninstall(store, ["pdf"], workspace="research")


async def test_install_globally_writes_settings():
    store = FakeStore()
    await store.update_settings({"options": {"model": "sonnet"}})

    assert await skills.install(store, ["pdf"], workspace=None) == ["pdf"]
    settings = await store.get_settings()
    assert settings["options"] == {"model": "sonnet", "skills": ["pdf"]}
    assert await skills.installed(store) == ["pdf"]


async def test_install_rejects_bad_names():
    with pytest.raises(OptionsError):
        await skills.install(FakeStore(), ["../etc"])


# --- migration off the pre-catalog workspace prefix ---


class FakeBlob:
    def __init__(self, bucket, name):
        self.bucket = bucket
        self.name = name

    def delete(self):
        self.bucket.objects.pop(self.name, None)


class FakeBucket:
    def __init__(self, objects):
        self.objects = dict(objects)

    def list_blobs(self, prefix=""):
        return [FakeBlob(self, n) for n in sorted(self.objects) if n.startswith(prefix)]

    def copy_blob(self, blob, destination_bucket, new_name):
        destination_bucket.objects[new_name] = self.objects[blob.name]


@pytest.fixture
def bucket(monkeypatch):
    def make(objects):
        fake = FakeBucket(objects)
        monkeypatch.setattr(skills, "_bucket", lambda project, bucket_name: fake)
        return fake

    return make


def test_promote_legacy_moves_workspace_skills_into_the_catalog(bucket):
    fake = bucket(
        {
            "skills/pdf/SKILL.md": b"global pdf",
            "team-skills/research/notes/SKILL.md": b"notes",
            "team-skills/research/notes/ref/x.md": b"x",
            # collides with a catalog skill: keeps its content under a suffix
            "team-skills/research/pdf/SKILL.md": b"workspace pdf",
            "team-skills/legal/notes/SKILL.md": b"other notes",
            "team-skills/stray.txt": b"not a skill",
        }
    )

    summary = skills.promote_legacy("p", "b")

    assert summary["files"] == 4
    assert summary["promoted"] == {
        # blobs are walked in name order, so legal's "notes" lands first and
        # research's — a different skill that merely shares the name — is suffixed
        "legal": {"notes": "notes"},
        "research": {"notes": "notes-research", "pdf": "pdf-research"},
    }
    assert fake.objects["skills/notes/SKILL.md"] == b"other notes"
    assert fake.objects["skills/notes-research/SKILL.md"] == b"notes"
    assert fake.objects["skills/notes-research/ref/x.md"] == b"x"
    assert fake.objects["skills/pdf-research/SKILL.md"] == b"workspace pdf"
    # the global skill is untouched, and every promoted blob is gone from the old prefix
    assert fake.objects["skills/pdf/SKILL.md"] == b"global pdf"
    assert [n for n in fake.objects if n.startswith("team-skills/")] == ["team-skills/stray.txt"]

    # idempotent: nothing left to move
    assert skills.promote_legacy("p", "b") == {"promoted": {}, "files": 0}


def test_catalog_lists_skill_directories(bucket):
    bucket({"skills/pdf/SKILL.md": b"p", "skills/xlsx/SKILL.md": b"x", "skills/loose.md": b"?"})
    assert skills.catalog("p", "b") == ["pdf", "xlsx"]
