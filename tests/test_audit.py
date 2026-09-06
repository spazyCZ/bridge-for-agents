"""The audit log: what it records, and whether tampering is detectable."""
from __future__ import annotations

import json
import os
import stat

import pytest

from bridge_for_agents.audit import GENESIS, AuditLog, main, verify

KEY = "k" * 64


@pytest.fixture
def logfile(tmp_path):
    return tmp_path / "audit.jsonl"


def read(path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def written(path, *records, key: str = KEY) -> AuditLog:
    """A closed log containing the given records."""
    a = AuditLog(path, key)
    for r in records:
        a.write(*r) if isinstance(r, tuple) else a.write(r)
    a.close()
    return a


# --- basics ---------------------------------------------------------------
def test_disabled_log_writes_nothing(tmp_path):
    a = AuditLog(None)
    assert a.enabled is False
    assert a.write("request_open") == 0
    assert list(tmp_path.iterdir()) == []


def test_records_are_numbered_and_timestamped(logfile):
    a = AuditLog(logfile)
    assert a.write("bridge_start") == 1
    assert a.write("request_open", tool="Bash") == 2
    recs = read(logfile)
    assert [r["seq"] for r in recs] == [1, 2]
    assert recs[1]["type"] == "request_open"
    assert recs[1]["tool"] == "Bash"
    assert recs[0]["ts"].endswith("Z")


def test_the_file_is_private(logfile):
    AuditLog(logfile).write("bridge_start")
    assert stat.S_IMODE(os.stat(logfile).st_mode) == 0o600


def test_the_full_input_is_kept_not_the_redacted_summary(logfile):
    a = AuditLog(logfile)
    a.write("request_open", input={"command": "export API_KEY=sk-" + "a" * 32})
    # The chat sees a redacted summary; the record is evidence and keeps it all.
    assert "sk-" + "a" * 32 in read(logfile)[0]["input"]["command"]


def test_sequence_and_chain_continue_across_a_restart(logfile):
    written(logfile, "bridge_start", "request_open")
    written(logfile, "bridge_start")
    recs = read(logfile)
    assert [r["seq"] for r in recs] == [1, 2, 3]
    assert recs[2]["prev"] == recs[1]["mac"]
    assert verify(logfile, KEY)[0]


def test_an_unwritable_path_does_not_break_the_bridge(tmp_path):
    a = AuditLog(tmp_path / "nope" / "x.jsonl")
    (tmp_path / "nope").write_text("i am a file, not a directory")
    assert a.write("request_open") == 0     # logged and swallowed
    assert a.enabled is False               # and it stops trying


# --- chaining -------------------------------------------------------------
def test_without_a_key_records_are_unchained(logfile):
    AuditLog(logfile).write("bridge_start")
    rec = read(logfile)[0]
    assert "mac" not in rec and "prev" not in rec
    ok, msg = verify(logfile, KEY)
    assert not ok and "not chained" in msg


def test_a_clean_chain_verifies(logfile):
    a = AuditLog(logfile, KEY)
    for i in range(5):
        a.write("request_open", tool="Bash", input={"command": f"echo {i}"})
    a.close()
    ok, msg = verify(logfile, KEY)
    assert ok, msg
    assert "5 records verified" in msg
    assert read(logfile)[0]["prev"] == GENESIS


def test_a_flipped_byte_is_caught_and_the_seq_named(logfile):
    """The plan's exit criterion for this phase."""
    a = AuditLog(logfile, KEY)
    for i in range(5):
        a.write("request_open", tool="Bash", input={"command": f"echo {i}"})
    a.close()

    lines = logfile.read_text().splitlines()
    rec = json.loads(lines[2])
    rec["input"]["command"] = "echo 99"          # seq 3, quietly altered
    lines[2] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    logfile.write_text("\n".join(lines) + "\n")

    ok, msg = verify(logfile, KEY)
    assert not ok
    assert "seq 3" in msg and "modified" in msg


def test_a_deleted_line_is_caught(logfile):
    a = AuditLog(logfile, KEY)
    for i in range(4):
        a.write("request_open", n=i)
    a.close()
    lines = logfile.read_text().splitlines()
    del lines[1]
    logfile.write_text("\n".join(lines) + "\n")
    ok, msg = verify(logfile, KEY)
    assert not ok
    assert "broken link" in msg or "sequence jumped" in msg


def test_the_wrong_key_does_not_verify(logfile):
    written(logfile, "bridge_start")
    assert not verify(logfile, "x" * 64)[0]


def test_records_appended_without_the_key_are_caught(logfile):
    """Someone who cannot compute a mac cannot append convincingly either."""
    written(logfile, "bridge_start")
    with open(logfile, "a") as f:
        f.write(json.dumps({"seq": 2, "ts": "2026-01-01T00:00:00.000Z",
                            "type": "decision", "outcome": "allow",
                            "prev": "0" * 64, "mac": "f" * 64}) + "\n")
    ok, msg = verify(logfile, KEY)
    assert not ok and "seq 2" in msg


# --- the verifier CLI -----------------------------------------------------
def test_cli_exits_nonzero_on_a_broken_chain(logfile, capsys):
    written(logfile, "bridge_start")
    logfile.write_text(logfile.read_text().replace("bridge_start", "bridge_stop"))
    assert main([str(logfile), "--key", KEY]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_cli_exits_zero_on_a_clean_chain(logfile, capsys):
    written(logfile, "bridge_start")
    assert main([str(logfile), "--key", KEY]) == 0
    assert "OK" in capsys.readouterr().out


def test_cli_reports_a_missing_file(tmp_path, capsys):
    assert main([str(tmp_path / "nope.jsonl")]) == 2
    assert "no audit log" in capsys.readouterr().out


def test_cli_can_print_one_session(logfile, capsys):
    a = AuditLog(logfile, KEY)
    a.write("request_open", session_id="aaaa1111", tool="Bash")
    a.write("request_open", session_id="bbbb2222", tool="Read")
    a.close()
    assert main([str(logfile), "--session", "aaaa"]) == 0
    out = capsys.readouterr().out
    assert "aaaa1111" in out and "bbbb2222" not in out
    assert "mac" not in out          # noise removed when reading for review
