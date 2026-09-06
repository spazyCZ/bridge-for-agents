"""Append-only record of every request and every decision.

The phone message is a UI; this is the evidence. It stores the **full** tool
input, not the redacted summary that goes to the chat, and it distinguishes
what you approved from what the bridge merely declined to decide.

One JSON object per line, `0600`, `fsync` on every write:

    {"seq": 41, "ts": "2026-09-06T14:22:31.104Z", "type": "decision",
     "ref": 40, "outcome": "allow", "source": "button", "latency_ms": 8104}

**Tamper-evidence is optional.** With `BRIDGE_AUDIT_KEY` set, each record
carries `prev` and `mac`, where

    mac = HMAC-SHA256(key, prev || canonical_json(record without prev and mac))

and `canonical_json` is sorted-keys with no whitespace. That detects an edited
or deleted line by anyone who does not hold the key. It does **not** stop
someone holding both the key and the file from rewriting the chain from
scratch — no local-only scheme can, and the honest answer is an external
anchor, which is not built yet.

Run `bridge-audit-verify <file>` to check a chain and read it back.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

log = logging.getLogger("bridge.audit")

GENESIS = "0" * 64
DEFAULT_PATH = Path.home() / ".local/state/claude-bridge/audit.jsonl"


def canonical(rec: dict) -> str:
    return json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _mac(key: bytes, prev: str, rec: dict) -> str:
    body = {k: v for k, v in rec.items() if k not in ("prev", "mac")}
    return hmac.new(key, (prev + canonical(body)).encode(), hashlib.sha256).hexdigest()


class AuditLog:
    """Opens lazily, so importing the module touches no disk."""

    def __init__(self, path: str | os.PathLike[str] | None, key: str = "") -> None:
        self.path = Path(path) if path else None
        self.key = key.encode() if key else b""
        self._fh: TextIO | None = None
        self._seq = 0
        self._prev = GENESIS

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def _open(self) -> TextIO | None:
        if self._fh or not self.path:
            return self._fh
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._resume()
            fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
            self._fh = os.fdopen(fd, "a", encoding="utf-8")
            log.info("audit log at %s (chained: %s)", self.path, "yes" if self.key else "no")
        except OSError:
            log.exception("cannot open audit log %s — continuing without it", self.path)
            self.path = None
        return self._fh

    def _resume(self) -> None:
        """Continue the sequence and the chain across a restart."""
        try:
            last = None
            with open(self.path, encoding="utf-8") as f:      # type: ignore[arg-type]
                for line in f:
                    if line.strip():
                        last = line
            if last:
                rec = json.loads(last)
                self._seq = int(rec.get("seq", 0))
                self._prev = rec.get("mac", GENESIS)
        except FileNotFoundError:
            pass
        except Exception:
            log.exception("could not read the tail of %s — starting a new chain", self.path)

    def write(self, type: str, **fields: Any) -> int:
        """Append one record. Returns its seq, for use as a later `ref`.

        Never raises: an audit failure must not take a decision down with it.
        """
        fh = self._open()
        if fh is None:
            return 0
        try:
            self._seq += 1
            rec: dict[str, Any] = {
                "seq": self._seq,
                "ts": datetime.now(UTC).isoformat(timespec="milliseconds")
                          .replace("+00:00", "Z"),
                "type": type,
                **fields,
            }
            if self.key:
                rec["prev"] = self._prev
                rec["mac"] = self._prev = _mac(self.key, self._prev, rec)
            fh.write(canonical(rec) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
            return self._seq
        except Exception:
            log.exception("could not write audit record %r", type)
            return 0

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


# --------------------------------------------------------------------------
def verify(path: Path, key: str) -> tuple[bool, str]:
    """Recompute the chain. Returns (ok, message naming the first bad seq)."""
    if not key:
        return False, "no key given — set BRIDGE_AUDIT_KEY or pass --key"
    k = key.encode()
    prev, n, expect_seq = GENESIS, 0, 0
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            n += 1
            expect_seq += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                return False, f"line {lineno}: not valid JSON ({e})"
            if "mac" not in rec:
                return False, f"line {lineno} (seq {rec.get('seq')}): record is not chained"
            if rec.get("prev") != prev:
                return False, (f"line {lineno} (seq {rec.get('seq')}): broken link — "
                               f"a record before this one was changed or removed")
            if _mac(k, prev, rec) != rec["mac"]:
                return False, f"line {lineno} (seq {rec.get('seq')}): record was modified"
            if rec.get("seq") != expect_seq:
                return False, (f"line {lineno}: sequence jumped to {rec.get('seq')}, "
                               f"expected {expect_seq}")
            prev = rec["mac"]
    return True, f"{n} records verified, head {prev[:16]}…"


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="bridge-audit-verify", description=__doc__.split("\n")[0])
    ap.add_argument("path", nargs="?", default=str(DEFAULT_PATH), type=Path)
    ap.add_argument("--key", default=os.environ.get("BRIDGE_AUDIT_KEY", ""))
    ap.add_argument("--session", help="print this session's records instead of verifying")
    a = ap.parse_args(argv)

    if not a.path.exists():
        print(f"no audit log at {a.path}")
        return 2

    if a.session:
        for line in a.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("session_id", "").startswith(a.session):
                print(canonical({k: v for k, v in rec.items() if k not in ("prev", "mac")}))
        return 0

    ok, msg = verify(a.path, a.key)
    print(("OK   " if ok else "FAIL ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
