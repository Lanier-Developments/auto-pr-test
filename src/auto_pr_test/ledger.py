"""Evidence ledger: append-only, hash-chained JSONL.

Each entry hashes its own canonical JSON plus the previous entry's hash, so editing
or deleting a past line invalidates everything after it. Tamper-evident, not
tamper-proof — someone who can rewrite the whole file in order can forge a chain,
and the honest claim is that quiet edits are detectable, nothing more.

Digests only. The ledger records SHA-256 of the diff and of each review, never the
text, so a file that must live forever in the repo never becomes a copy of the
code or the reviewers' commentary about it. Anyone holding the PR can verify.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

GENESIS = "0" * 64


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _digest(payload: dict[str, Any], prev_hash: str) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(f"{prev_hash}\x00{canonical}")


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def entries(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return iter(())
        return (json.loads(ln) for ln in self.path.read_text().splitlines() if ln.strip())

    def tail_hash(self) -> str:
        last = None
        for last in self.entries():
            pass
        return last["entry_hash"] if last else GENESIS

    def append(self, **payload: Any) -> dict[str, Any]:
        prev = self.tail_hash()
        seq = sum(1 for _ in self.entries()) + 1
        body = {
            "entry_id": str(uuid.uuid4()),
            "seq": seq,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **payload,
        }
        entry = {**body, "prev_hash": prev, "entry_hash": _digest(body, prev)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as fh:
            fh.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
        return entry

    def verify(self) -> tuple[bool, str]:
        """Walk the chain. Returns (ok, message)."""
        prev = GENESIS
        for i, e in enumerate(self.entries(), start=1):
            body = {k: v for k, v in e.items() if k not in ("prev_hash", "entry_hash")}
            if e.get("prev_hash") != prev:
                return False, f"entry {i}: prev_hash mismatch"
            if e.get("seq") != i:
                return False, f"entry {i}: seq is {e.get('seq')}"
            if _digest(body, prev) != e.get("entry_hash"):
                return False, f"entry {i}: content hash mismatch (edited?)"
            prev = e["entry_hash"]
        return True, "chain intact"
