import pytest

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
