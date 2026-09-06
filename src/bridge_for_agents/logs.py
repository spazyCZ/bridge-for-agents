"""Operational logging — the diagnostic log, not the audit log.

Two logs, opposite rules, and confusing them would be a bug:

* The **audit log** (`audit.py`) is evidence. It keeps the full tool input,
  secrets and all, and is written to one place you control.
* This log is for working out what the daemon is doing. It can be turned up to
  DEBUG, tailed, shipped somewhere, or read over someone's shoulder — so every
  record goes through the redactor on its way out. A command that reaches the
  chat redacted must not land in a log file in the clear.

Configured by `BRIDGE_LOG_LEVEL`, `BRIDGE_LOG_FILE` and `BRIDGE_LOG_ACCESS`.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Any

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
MAX_BYTES = 10 * 1024 * 1024
BACKUPS = 5


class RedactingFilter(logging.Filter):
    """Runs every formatted message through the redactor.

    A filter rather than a formatter, so it applies to every handler at once
    and cannot be bypassed by adding another one.
    """

    def __init__(self, redactor: Any) -> None:
        super().__init__()
        self.redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True                     # malformed record: let it through as-is
        scrubbed, hidden = self.redactor.scrub(message)
        if hidden:
            record.msg = scrubbed
            record.args = ()
        return True


class PrivateRotatingFileHandler(RotatingFileHandler):
    """Rotating handler whose files are `0600`, including after a rollover."""

    def _open(self):  # noqa: ANN202 - matches the stdlib signature
        stream = super()._open()
        try:
            os.chmod(self.baseFilename, 0o600)
        except OSError:
            logging.getLogger("bridge.logs").warning(
                "could not set 0600 on %s", self.baseFilename)
        return stream


def setup(level: str = "INFO", file: str = "", access: bool = False,
          redactor: Any = None) -> logging.Logger | None:
    """Configure the root logger. Returns the aiohttp access logger, or None.

    `None` is what `web.AppRunner(access_log=...)` wants in order to stay quiet
    — worth having off by default, because the admin page polls every two
    seconds and would otherwise write about 1,800 lines an hour saying so.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for old in list(root.handlers):
        root.removeHandler(old)

    formatter = logging.Formatter(FORMAT)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if file:
        try:
            handlers.append(PrivateRotatingFileHandler(
                file, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"))
        except OSError as e:
            # A bad log path must not stop the bridge starting.
            logging.getLogger("bridge.logs").error(
                "cannot open log file %s (%s) — logging to stderr only", file, e)

    redacting = RedactingFilter(redactor) if redactor is not None else None
    for h in handlers:
        h.setFormatter(formatter)
        if redacting:
            h.addFilter(redacting)
        root.addHandler(h)

    return logging.getLogger("aiohttp.access") if access else None
