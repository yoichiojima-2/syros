"""Naming rules for the GCS prefixes syros owns.

Workspace names and artifact space names follow the same rule — one short,
lowercase, path-free segment — and the files inside them follow another. Both
live here so options.py (client-side validation), artifacts.py, and
workspace.py cannot drift apart: every prefix built from user input goes
through these two functions.
"""

from __future__ import annotations

import re

from .errors import OptionsError

NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def validate_name(kind: str, value: str) -> str:
    """Check a workspace or artifact space name. `kind` names it in the error."""
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise OptionsError(
            f"{kind} must be a short name matching [a-z0-9][a-z0-9_-]* (max 64 chars), not a path"
        )
    return value


def validate_file(kind: str, value: str) -> str:
    """Check a path relative to a workspace/space prefix.

    Names legitimately contain "/" — the prefixes are flat GCS listings of
    nested paths — so this rejects only what would escape the prefix or
    address the bucket root.
    """
    if not isinstance(value, str) or not value or value.startswith("/"):
        raise OptionsError(f"invalid {kind} name {value!r}")
    if ".." in value.split("/") or value.endswith("/"):
        raise OptionsError(f"invalid {kind} name {value!r}")
    return value
