"""Pure formatting helpers used to build the chat messages."""
from __future__ import annotations


def test_esc_escapes_html(bridge):
    assert bridge.esc("<b>&</b>") == "&lt;b&gt;&amp;&lt;/b&gt;"


def test_summarize_bash_shows_the_command(bridge):
    assert bridge.summarize_tool("Bash", {"command": "rm -rf /tmp/x"}) == "rm -rf /tmp/x"


def test_summarize_file_tools_show_the_path(bridge):
    for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit", "Read"):
        assert bridge.summarize_tool(tool, {"file_path": "/etc/hosts"}) == "/etc/hosts"


def test_summarize_unknown_tool_falls_back_to_json(bridge):
    assert bridge.summarize_tool("Grep", {"pattern": "x"}) == '{"pattern": "x"}'


def test_summarize_truncates_long_input(bridge):
    out = bridge.summarize_tool("Grep", {"pattern": "x" * 2000})
    assert len(out) == 601
    assert out.endswith("…")


def test_session_tag_is_empty_in_session_scope(bridge):
    assert bridge.session_tag({"cwd": "/repo", "session_id": "abcdef123456"}) == ""


def test_session_tag_names_cwd_and_session_in_flat_scope(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "SCOPE", "flat")
    tag = bridge.session_tag({"cwd": "/home/me/myrepo/", "session_id": "abcdef123456"})
    assert "myrepo" in tag
    assert "abcdef12" in tag
    assert "abcdef123456" not in tag
