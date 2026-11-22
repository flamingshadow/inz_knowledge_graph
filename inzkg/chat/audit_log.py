"""Append-only chat log.

Every chat turn is written as one JSON line: the question, how it was read, each query run
with its values, the rule versions returned, and the answer. Each record carries the hash
of the record before it, so any edit or deletion breaks the chain and ``verify`` finds it.
In production, ship these files to INZ's records system under the Public Records Act
disposal schedule; the hash chain still proves nothing was changed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import threading
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


class ChatLog:
    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, day: dt.date) -> Path:
        return self.dir / f"chat-{day.isoformat()}.jsonl"

    def _last_hash(self, path: Path) -> str:
        if not path.exists():
            return GENESIS
        last = None
        with open(path, "rb") as fh:
            for line in fh:
                if line.strip():
                    last = line
        return json.loads(last)["hash"] if last else GENESIS

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        now = dt.datetime.now(dt.timezone.utc)
        with self._lock:
            path = self._path(now.date())
            body = {"at": now.isoformat(), **record, "prevHash": self._last_hash(path)}
            body["hash"] = _hash(body)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(body, default=str, ensure_ascii=False) + "\n")
                fh.flush()
        return body


def _hash(body: dict) -> str:
    data = {k: v for k, v in body.items() if k != "hash"}
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def verify(path: str | Path) -> tuple[bool, int, str]:
    """Check one log file. Returns (ok, records checked, message)."""
    prev = GENESIS
    n = 0
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate((l for l in fh if l.strip()), start=1):
            rec = json.loads(line)
            if rec.get("prevHash") != prev:
                return False, n, f"record {n}: chain broken (prevHash mismatch)"
            if _hash(rec) != rec.get("hash"):
                return False, n, f"record {n}: contents changed after writing"
            prev = rec["hash"]
    return True, n, "ok"
