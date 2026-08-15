"""syros exceptions."""

from __future__ import annotations


class SyrosError(Exception):
    """Base class for all syros errors."""


class OptionsError(SyrosError):
    """An AgentOptions value is invalid or unsupported in the sandbox."""


class SessionExists(SyrosError):
    """A session document with that id is already there — the loser's half of a
    create race, which a caller holding a pre-assigned id may want to tolerate."""


class SessionTerminated(SyrosError):
    """The remote session is terminated and cannot accept further input."""
