"""skills.py: prefix rules and the official-skills tarball sync."""

import io
import tarfile

import pytest

from syros import skills
from syros.errors import OptionsError


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
