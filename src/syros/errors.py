"""syros exceptions."""

from __future__ import annotations


class SyrosError(Exception):
    """Base class for all syros errors."""


class OptionsError(SyrosError):
    """An AgentOptions value is invalid or unsupported in the sandbox."""


class SessionTerminated(SyrosError):
    """The remote session is terminated and cannot accept further input."""
