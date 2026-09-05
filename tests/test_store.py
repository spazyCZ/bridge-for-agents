"""The in-memory store behind the admin page."""
from __future__ import annotations

from bridge_for_agents.store import Store, current_event, plain


def ev(session="s1" * 8, name="PermissionRequest", **kw):
    return {"session_id": session, "hook_event_name": name, "cwd": "/home/me/myrepo", **kw}


def test_plain_strips_telegram_markup():
    assert plain("🔐 <b>Bash</b>\n<pre>rm &amp; ls</pre>") == "🔐 Bash\nrm & ls"


def test_record_creates_and_updates_a_session():
    st = Store()
    st.record(ev(), "rm -rf /tmp/x")
    st.record(ev(name="Stop"), "done")
    snap = st.snapshot()
    assert snap["totals"] == {"sessions": 1, "active": 1, "events": 2, "waiting": 0}
    s = snap["sessions"][0]
    assert s["project"] == "myrepo"
    assert s["counts"] == {"PermissionRequest": 1, "Stop": 1}


def test_session_end_marks_the_session_ended():
    st = Store()
    st.record(ev())
    st.record(ev(name="SessionEnd"))
    assert st.snapshot()["totals"]["active"] == 0
    assert st.snapshot()["sessions"][0]["ended"] is True


def test_complete_records_the_outcome_and_duration():
    st = Store()
    rec = st.record(ev(), "ls")
    st.complete(rec, "allow")
    e = st.snapshot()["feed"][0]
    assert e["outcome"] == "allow"
    assert e["duration_ms"] >= 0


def test_feed_is_newest_first_across_sessions():
    st = Store()
    st.record(ev(session="aaa"), "first")
    st.record(ev(session="bbb"), "second")
    assert [e["summary"] for e in st.snapshot()["feed"]] == ["second", "first"]


def test_selecting_a_session_returns_only_its_events():
    st = Store()
    st.record(ev(session="aaa"), "mine")
    st.record(ev(session="bbb"), "theirs")
    sel = st.snapshot("aaa")["selected"]
    assert [e["summary"] for e in sel["events"]] == ["mine"]


def test_events_per_session_are_bounded():
    st = Store(max_events=5)
    for i in range(20):
        st.record(ev(), f"cmd {i}")
    sel = st.snapshot("s1" * 8)["selected"]
    assert len(sel["events"]) == 5
    assert sel["events"][0]["summary"] == "cmd 19"


def test_least_recently_active_sessions_are_evicted():
    st = Store(max_sessions=3)
    for i in range(5):
        st.record(ev(session=f"s{i}"))
    ids = {s["session_id"] for s in st.snapshot()["sessions"]}
    assert ids == {"s2", "s3", "s4"}


def test_open_prompt_is_attributed_to_the_current_event():
    st = Store()
    token = current_event.set(ev(session="live-session"))
    try:
        st.open_prompt("r1", "<b>Bash</b>", ["Allow", "Deny"], timeout=540)
    finally:
        current_event.reset(token)
    w = st.snapshot()["waiting"][0]
    assert w["session_id"] == "live-session"
    assert w["text"] == "Bash"
    assert w["options"] == ["Allow", "Deny"]
    assert 0 < w["remaining"] <= 540


def test_close_prompt_clears_it():
    st = Store()
    st.open_prompt("r1", "x", [], 10)
    st.close_prompt("r1")
    assert st.snapshot()["waiting"] == []
    st.close_prompt("r1")  # idempotent
