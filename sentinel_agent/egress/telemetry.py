"""Append-only JSONL records with a locally verifiable SHA-256 hash chain."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


def canonical(record):
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def read_verified(path: Path) -> list[dict]:
    records = []
    previous = "0" * 64
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            record = json.loads(line)
            signature = record.pop("hash")
            expected = hashlib.sha256((previous + canonical(record)).encode()).hexdigest()
            if record["prev_hash"] != previous or signature != expected:
                raise ValueError()
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"Audit chain invalid at record {index}") from exc
        record["hash"] = signature
        records.append(record)
        previous = signature
    return records


class AuditLog:
    def __init__(self, run_id: str, root: Path = Path("artifacts/egress")):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
            raise ValueError("Invalid audit run ID")
        self.run_id = run_id
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / f"{run_id}.jsonl"
        existing = read_verified(self.path) if self.path.exists() else []
        self.previous = existing[-1]["hash"] if existing else "0" * 64
        self.path.touch(exist_ok=True)

    def write(self, **fields):
        # Callers supply only connection metadata and value-free DLP summaries.
        allowed = {"kind", "method", "host", "port", "sni", "resolved_ip", "decision",
                   "rule", "dlp", "bytes_up", "bytes_down", "duration_ms"}
        if set(fields) - allowed:
            raise ValueError("Unsupported audit field")
        record = {"ts": datetime.now(timezone.utc).isoformat(), "run_id": self.run_id,
                  **fields, "prev_hash": self.previous}
        signature = hashlib.sha256((self.previous + canonical(record)).encode()).hexdigest()
        record["hash"] = signature
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(canonical(record) + "\n")
        self.previous = signature


def percentile(values, quantile):
    if not values:
        return 0.0
    ordered = sorted(values)
    offset = (len(ordered) - 1) * quantile
    low = int(offset)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (offset - low)


def report(path: Path):
    records = read_verified(path)
    connections = [r for r in records if r.get("kind") == "connection"]
    latencies = [r.get("duration_ms", 0) for r in connections]
    return {
        "records": len(records), "connections": len(connections),
        "decisions_by_rule": dict(Counter(f"{r.get('decision')}:{r.get('rule')}" for r in records)),
        "top_destinations": Counter(r.get("host", "") for r in connections).most_common(10),
        "bytes_up": sum(r.get("bytes_up", 0) for r in connections),
        "bytes_down": sum(r.get("bytes_down", 0) for r in connections),
        "p50_ms": percentile(latencies, .5), "p95_ms": percentile(latencies, .95),
    }
