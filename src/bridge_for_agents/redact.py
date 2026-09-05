"""Best-effort removal of secrets from text on its way to the chat.

`tool_input` reaches Telegram's servers verbatim, so a command that carries an
API key hands that key to a third party. This module hides the obvious cases
before the text leaves the host.

**It is best effort and nothing more.** A secret in a shape not listed here
passes straight through. Treat it as a reduction in accidental leakage, never
as a guarantee — the terminal remains the only place a value was certainly not
transmitted.

Two kinds of pattern are used, and the distinction matters:

* **Shaped credentials** — `AKIA…`, `ghp_…`, a JWT. These are recognisable on
  their own and almost never produce a false positive.
* **Assignments** — `PASSWORD=…`, `--token …`, `Authorization: Bearer …`. Here
  the *context* identifies the secret, so the name is kept and only the value
  is hidden, which leaves the line readable enough to decide on.

Entropy scoring is deliberately absent. It flags git SHAs, base64 payloads and
checksums, and a redactor that hides the interesting half of every command is
one people turn off.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger("bridge.redact")

PLACEHOLDER = "[redacted {}]"

# Anything already redacted must not be re-matched by a later, vaguer pattern.
_DONE = r"(?!\[redacted)"

# Ordered most specific first. A pattern may mark a prefix it wants preserved
# with a group named `keep`; everything else it matches is replaced.
BUILTIN: list[tuple[str, re.Pattern[str]]] = [
    ("private key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("aws key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("openai key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("telegram token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("jwt", re.compile(
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("url credentials", re.compile(
        r"(?P<keep>\b[a-zA-Z][\w+.-]*://)[^/\s:@]+:[^/\s@]+(?=@)")),
    ("bearer token", re.compile(
        rf"(?P<keep>(?i:bearer|token)\s+){_DONE}[A-Za-z0-9._~+/=-]{{16,}}")),
    ("assignment", re.compile(
        r"(?P<keep>(?i:[\w.-]*(?:password|passwd|secret|token|api[_-]?key|"
        rf"access[_-]?key|private[_-]?key|credential)[\w.-]*)\s*[=:]\s*){_DONE}"
        r"(?:\"[^\"]+\"|'[^']+'|\S+)")),
    ("flag value", re.compile(
        rf"(?P<keep>--(?i:password|passwd|token|api-?key|secret)[= ]){_DONE}\S+")),
]


def _load_extra(path: str) -> list[tuple[str, re.Pattern[str]]]:
    """One regex per line; blank lines and `#` comments ignored."""
    out: list[tuple[str, re.Pattern[str]]] = []
    try:
        lines = Path(path).read_text().splitlines()
    except OSError as e:
        log.warning("BRIDGE_REDACT_EXTRA: cannot read %s (%s) — using built-ins only", path, e)
        return out
    for n, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            out.append(("custom", re.compile(line)))
        except re.error as e:
            # A bad line must not take the bridge down with it.
            log.warning("BRIDGE_REDACT_EXTRA %s:%d is not a valid regex (%s) — skipped",
                        path, n, e)
    if out:
        log.info("loaded %d custom redaction pattern(s) from %s", len(out), path)
    return out


class Redactor:
    def __init__(self, enabled: bool = True, extra_path: str = "") -> None:
        self.enabled = enabled
        self.patterns = list(BUILTIN) + (_load_extra(extra_path) if extra_path else [])

    def scrub(self, text: str) -> tuple[str, int]:
        """Return the text with secrets hidden, and how many were hidden."""
        if not self.enabled or not text:
            return text, 0
        try:
            total = 0
            for label, pat in self.patterns:
                def repl(m: re.Match[str], _label: str = label) -> str:
                    keep = m.group("keep") if "keep" in m.re.groupindex else None
                    return (keep or "") + PLACEHOLDER.format(_label)

                text, n = pat.subn(repl, text)
                total += n
            return text, total
        except Exception:
            # Fail closed: never fall back to emitting the original text.
            log.exception("redaction failed — withholding the value")
            return "[redaction failed — check the terminal]", 1


def note(count: int) -> str:
    """A line for the chat message, so a hidden value is never silent."""
    if not count:
        return ""
    return f"\n<i>{count} secret{'s' if count > 1 else ''} hidden — full value in the terminal</i>"
