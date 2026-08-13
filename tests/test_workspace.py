from syros.workspace import session_prefix, workspace_prefix


def test_session_prefix():
    assert session_prefix("sess_x", "ws") == "sessions/sess_x/state/ws/"
    assert session_prefix("sess_x", "home") == "sessions/sess_x/state/home/"


def test_workspace_prefix():
    assert workspace_prefix("data") == "workspaces/data/"
