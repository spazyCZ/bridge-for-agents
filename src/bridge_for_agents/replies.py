"""Parsing a typed reply from the phone.

Tapping a button is unambiguous. Typing is not, and this is the only place
where what someone typed becomes a decision — so both functions here are pure,
and both fail towards the safe answer.

**Affirmatives must be bare.** `yes` allows; `yes but only the first one` does
not. The asymmetry is deliberate: mis-reading a hedge as approval runs the
command, while mis-reading it as a denial only sends you back to the terminal.
The phone message says `y / n` so the strict form is the discoverable one.

For a question, a leading option number selects that option, and anything after
a separator is kept as a comment — `2 - but check the migration first` picks
option two and passes the caveat along. A number with no separator is left
alone, so "3 replicas" stays an answer rather than becoming option three.
"""
from __future__ import annotations

import re

AFFIRM = frozenset({"y", "yes", "yep", "yeah", "ok", "okay", "allow", "approve",
                    "go", "sure", "do it", "👍", "✅"})
NEGATE = frozenset({"n", "no", "nope", "nah", "deny", "stop", "cancel", "reject",
                    "👎", "❌", "🛑"})

# "2", "2 - comment", "2) comment", "2: comment". A separator is required
# before a comment so that "3 replicas" is not read as choosing option three.
_CHOICE = re.compile(r"^\s*(\d{1,2})\s*(?:$|[).:,\-–—]\s*(.*)$)", re.S)


def _norm(text: str) -> str:
    return text.strip().lower().rstrip(".!… ")


def permission(text: str) -> tuple[str, str]:
    """A typed reply to a permission prompt -> (behavior, reason).

    Returns ``("allow", "")``, ``("deny", "")`` or ``("deny", reason)``.
    Anything that is not a bare affirmative or negative denies with the whole
    message as the reason, which is what Claude then reads.
    """
    stripped = text.strip()
    norm = _norm(stripped)
    if norm in AFFIRM:
        return "allow", ""
    if norm in NEGATE:
        return "deny", ""

    # "no, wrong branch" — a negative with the reason attached.
    head, _, rest = stripped.partition(" ")
    if _norm(head).strip(",:;-") in NEGATE and rest.strip():
        return "deny", rest.strip(" ,:;-—–")

    return "deny", stripped


def choice(text: str, labels: list[str]) -> tuple[str | None, str]:
    """A typed reply to a question -> (chosen label or None, comment).

    `None` means it did not name an option and the text is the answer itself.
    """
    stripped = text.strip()
    if not stripped:
        return None, ""

    if m := _CHOICE.match(stripped):
        n = int(m.group(1))
        if 1 <= n <= len(labels):
            return labels[n - 1], (m.group(2) or "").strip(" ,:;-—–")
        return None, stripped          # out of range: an answer, not a choice

    # "postgres" or "postgres - but check the migration" against a label.
    low = stripped.lower()
    for label in labels:
        ll = label.lower()
        if low == ll:
            return label, ""
        if low.startswith(ll):
            rest = stripped[len(label):].strip(" ,:;-—–")
            if not rest or rest[0].isspace() or stripped[len(label)] in " ,:;-–—":
                return label, rest

    return None, stripped


def with_comment(label: str, comment: str) -> str:
    """How a chosen option and its comment reach Claude as one answer."""
    return f"{label} — {comment}" if comment else label
