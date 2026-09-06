"""Operational logging: level, file, redaction, and access-log noise.

The property that matters: a secret redacted on its way to the chat must not
reappear in a log file in the clear. The audit log is the deliberate exception.
"""
from __future__ import annotations

import logging
import os
import stat

import pytest

from bridge_for_agents import logs
from bridge_for_agents.redact import Redactor

SECRET = "sk-" + "a" * 32


@pytest.fixture(autouse=True)
def restore_root_logger():
    root = logging.getLogger()
    saved, level = list(root.handlers), root.level
    yield
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in saved:
        root.addHandler(h)
    root.setLevel(level)


# --- redaction ------------------------------------------------------------
def test_secrets_are_stripped_from_log_records():
    f = logs.RedactingFilter(Redactor())
    rec = logging.LogRecord("bridge", logging.INFO, __file__, 1,
                            "running %s", (f"deploy --token {SECRET}",), None)
    assert f.filter(rec) is True
    assert SECRET not in rec.getMessage()
    assert "[redacted" in rec.getMessage()


def test_ordinary_messages_are_untouched():
    f = logs.RedactingFilter(Redactor())
    rec = logging.LogRecord("bridge", logging.INFO, __file__, 1,
                            "hook %s -> %s", ("Stop", "sent"), None)
    f.filter(rec)
    assert rec.getMessage() == "hook Stop -> sent"
    assert rec.args == ("Stop", "sent")     # untouched records keep their args


def test_a_malformed_record_is_not_dropped():
    f = logs.RedactingFilter(Redactor())
    rec = logging.LogRecord("bridge", logging.INFO, __file__, 1,
                            "%s %s", ("only-one-arg",), None)   # getMessage() raises
    assert f.filter(rec) is True


def test_a_secret_does_not_reach_the_log_file(tmp_path):
    path = tmp_path / "bridge.log"
    logs.setup("INFO", str(path), redactor=Redactor())
    logging.getLogger("bridge").info("running %s", f"deploy --token {SECRET}")
    logging.shutdown()
    body = path.read_text()
    assert SECRET not in body
    assert "[redacted" in body


# --- file handling --------------------------------------------------------
def test_the_log_file_is_private(tmp_path):
    path = tmp_path / "bridge.log"
    logs.setup("INFO", str(path))
    logging.getLogger("bridge").info("hello")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_a_bad_log_path_does_not_stop_startup(tmp_path):
    (tmp_path / "wall").write_text("not a directory")
    logs.setup("INFO", str(tmp_path / "wall" / "bridge.log"))
    logging.getLogger("bridge").info("still logging")   # to stderr, no exception


def test_rotation_keeps_the_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "MAX_BYTES", 200)
    path = tmp_path / "bridge.log"
    logs.setup("INFO", str(path))
    for i in range(60):
        logging.getLogger("bridge").info("filling the log %d", i)
    rotated = list(tmp_path.glob("bridge.log*"))
    assert len(rotated) > 1, "never rotated"
    for f in rotated:
        assert stat.S_IMODE(os.stat(f).st_mode) == 0o600, f


# --- level and access log -------------------------------------------------
@pytest.mark.parametrize("level,expected", [
    ("DEBUG", logging.DEBUG), ("INFO", logging.INFO),
    ("warning", logging.WARNING), ("nonsense", logging.INFO),
])
def test_level_is_configurable_and_falls_back_safely(level, expected):
    logs.setup(level)
    assert logging.getLogger().level == expected


def test_access_log_is_off_by_default():
    """The admin page polls every 2s; on by default it drowns everything."""
    assert logs.setup("INFO") is None
    assert logs.setup("INFO", access=True) is logging.getLogger("aiohttp.access")


def test_setup_replaces_handlers_rather_than_stacking_them():
    logs.setup("INFO")
    first = len(logging.getLogger().handlers)
    logs.setup("INFO")
    assert len(logging.getLogger().handlers) == first
