"""Secrets must not reach the chat; ordinary commands must stay readable.

Every credential below is fabricated — shaped like the real thing, valid
nowhere.
"""
from __future__ import annotations

import pytest

from bridge_for_agents.redact import Redactor

r = Redactor()


def scrub(text: str) -> str:
    return r.scrub(text)[0]


def count(text: str) -> int:
    return r.scrub(text)[1]


# --- shaped credentials ---------------------------------------------------
@pytest.mark.parametrize("secret,label", [
    ("AKIAIOSFODNN7EXAMPLE", "aws key"),
    ("ASIAY34FZKBOKMUTVV7A", "aws key"),
    ("ghp_" + "a" * 36, "github token"),
    ("gho_" + "b" * 40, "github token"),
    ("sk-" + "c" * 32, "openai key"),
    ("sk-proj-" + "d" * 40, "openai key"),
    ("xoxb-1234567890-abcdefghij", "slack token"),
    ("AIza" + "e" * 35, "google key"),
    ("8784009547:AAFZ9Mb7OzluIN2kqViXZU8mBXxgGA1gT8U", "telegram token"),
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27u", "jwt"),
])
def test_shaped_credentials_are_hidden(secret, label):
    out = scrub(f"curl -H 'x: {secret}' https://api.example.com")
    assert secret not in out
    assert f"[redacted {label}]" in out


def test_private_key_block_is_hidden_whole():
    text = ("-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEAx7Zq\nabcdef\n"
            "-----END RSA PRIVATE KEY-----")
    out = scrub(text)
    assert "MIIEowIBAAKCAQEAx7Zq" not in out
    assert out == "[redacted private key]"


# --- context-identified secrets: keep the name, hide the value ------------
@pytest.mark.parametrize("text,keeps", [
    ("export DB_PASSWORD=hunter2ísveryLong", "DB_PASSWORD="),
    ("export API_KEY=abc123def456", "API_KEY="),
    ('psql "password=s3cr3t host=db"', "password="),
    ("curl -H 'Authorization: Bearer abcdefghijklmnop123'", "Bearer "),
    ("mysql --password=hunter2", "--password="),
    ("deploy --token abcdefghijklmnop", "--token "),
    ("git clone https://user:pw123@github.com/x/y", "https://"),
])
def test_value_is_hidden_but_the_name_survives(text, keeps):
    out = scrub(text)
    assert keeps in out, out
    assert "redacted" in out
    for leaked in ("hunter2", "abc123def456", "s3cr3t", "abcdefghijklmnop", "pw123"):
        assert leaked not in out, f"{leaked!r} leaked in {out!r}"


# --- the part that decides whether people keep it on ----------------------
@pytest.mark.parametrize("safe", [
    "pytest -q tests/",
    "git commit -m 'fix the parser'",
    "rm -rf ./playground/scratch",
    "git checkout 3f2a1b9c4d5e6f708192a3b4c5d6e7f809a1b2c3",     # a SHA is not a secret
    "docker build -t myapp:latest .",
    "echo 'aGVsbG8gd29ybGQ='",                                    # base64 is not a secret
    "ls -la /etc/passwd",                                         # the word, not a value
    "curl https://api.example.com/v1/users",
])
def test_ordinary_commands_are_left_alone(safe):
    assert scrub(safe) == safe
    assert count(safe) == 0


# --- behaviour ------------------------------------------------------------
def test_count_reports_how_many_were_hidden():
    assert count("API_KEY=" + "a" * 40 + " and NAME=plain") == 1
    assert count("PASSWORD=x TOKEN=yyyyyyyyyyyyyyyyyy") == 2


def test_an_opaque_value_under_an_innocent_name_is_left_alone():
    """Deliberate: only the name marks it as a secret, so A=<40 chars> stays.

    Hiding every long string would hide half of every command, which is how a
    redactor gets switched off."""
    assert count("A=" + "a" * 40) == 0


def test_redacted_text_is_not_redacted_again():
    once = scrub("API_KEY=sk-" + "a" * 32)
    assert once.count("redacted") == 1, once
    assert scrub(once) == once           # idempotent


def test_disabled_redactor_passes_text_through():
    off = Redactor(enabled=False)
    assert off.scrub("API_KEY=sk-" + "a" * 32) == ("API_KEY=sk-" + "a" * 32, 0)


def test_empty_text_is_safe():
    assert r.scrub("") == ("", 0)


def test_failure_withholds_the_text_rather_than_leaking_it(monkeypatch):
    """Fail closed: a broken redactor must never emit the original."""
    boom = Redactor()

    class Exploding:
        groupindex: dict = {}

        def subn(self, *_a, **_k):
            raise RuntimeError("boom")

    boom.patterns = [("bad", Exploding())]
    out, n = boom.scrub("API_KEY=sk-" + "a" * 32)
    assert "sk-" not in out
    assert out == "[redaction failed — check the terminal]"
    assert n == 1


# --- custom patterns ------------------------------------------------------
def test_extra_patterns_are_loaded_from_a_file(tmp_path):
    f = tmp_path / "patterns.txt"
    f.write_text("# internal ticket ids\nACME-[0-9]{6}\n\n")
    red = Redactor(extra_path=str(f))
    assert "ACME-123456" not in red.scrub("see ACME-123456")[0]


def test_a_broken_custom_pattern_is_skipped_not_fatal(tmp_path, caplog):
    f = tmp_path / "patterns.txt"
    f.write_text("[unclosed\nACME-[0-9]{6}\n")
    red = Redactor(extra_path=str(f))
    assert "ACME-123456" not in red.scrub("see ACME-123456")[0]   # the good one still works


def test_a_missing_pattern_file_is_not_fatal(tmp_path):
    red = Redactor(extra_path=str(tmp_path / "nope.txt"))
    assert red.scrub("AKIAIOSFODNN7EXAMPLE")[1] == 1              # built-ins still apply


# --- wiring into the bridge ----------------------------------------------
def test_bash_commands_are_redacted_on_the_way_out(bridge):
    text, hidden = bridge.summarize_counted("Bash", {"command": "export API_KEY=sk-" + "a" * 32})
    assert "sk-" not in text
    assert hidden == 1


def test_write_never_exposes_file_content(bridge):
    text, _ = bridge.summarize_counted("Write", {"file_path": "/app/.env",
                                                 "content": "API_KEY=sk-" + "a" * 32})
    assert text == "/app/.env"


def test_unknown_tools_dump_json_but_still_redact(bridge):
    text, hidden = bridge.summarize_counted("SomeTool", {"headers": {"Authorization":
                                                                     "Bearer " + "z" * 30}})
    assert "z" * 30 not in text
    assert hidden == 1


def test_truncation_cannot_rescue_a_secret_straddling_the_cut(bridge):
    """Redact first, then truncate — the other order can leave half a key visible."""
    cmd = "echo " + "x" * 590 + " AKIAIOSFODNN7EXAMPLE"
    text, hidden = bridge.summarize_counted("Bash", {"command": cmd})
    assert "AKIAIOSFODNN7EXAMPLE" not in text
    assert hidden == 1


def test_the_note_tells_the_user_something_was_hidden():
    from bridge_for_agents.redact import note
    assert note(0) == ""
    assert "1 secret hidden" in note(1)
    assert "3 secrets hidden" in note(3)


def test_redaction_can_be_switched_off(bridge, monkeypatch):
    from bridge_for_agents.redact import Redactor
    monkeypatch.setattr(bridge, "REDACTOR", Redactor(enabled=False))
    text, hidden = bridge.summarize_counted("Bash", {"command": "export API_KEY=sk-" + "a" * 32})
    assert "sk-" in text and hidden == 0
