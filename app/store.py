"""
Store for LLM-generated output (test cases).

DEVIATION FROM SUGGESTED STACK: the assignment suggests MongoDB. This uses
a flat JSON file instead. Justification (see APPROACH.md for the full
version):
  - The generation records here are small, don't need cross-document
    joins/aggregation, and the only query patterns required by the spec
    are "by selection_id" and "by node_id" - both trivial linear scans
    or dict lookups at this data volume (single-manual, internship-scope
    project, not production traffic).
  - Standing up a Mongo instance (local daemon or Atlas account/network
    dependency) adds setup friction for a reviewer running this in an
    hour, for no behavioral difference at this scale.
  - The interface below (`GenerationStore`) is narrow and intentionally
    Mongo-shaped (insert_one-ish / find-ish), so swapping in
    pymongo/motor later is a small, isolated change - not a rewrite.

If this were going to production or the document corpus grew large /
concurrent-write-heavy, this justification would NOT hold and I'd want a
real document DB (or just Postgres JSONB, honestly) - noted in APPROACH.md
decision log Q2.
"""
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

_LOCK = threading.Lock()
_STORE_PATH = Path(os.environ.get("GENERATIONS_STORE_PATH", "generations_store.json"))


def _read_all() -> list[dict]:
    if not _STORE_PATH.exists():
        return []
    with open(_STORE_PATH, "r") as f:
        return json.load(f)


def _write_all(records: list[dict]) -> None:
    with open(_STORE_PATH, "w") as f:
        json.dump(records, f, indent=2, default=str)


class GenerationStore:
    def insert(self, record: dict) -> dict:
        record = dict(record)
        record["id"] = record.get("id") or str(uuid.uuid4())
        record["created_at"] = record.get("created_at") or datetime.now(timezone.utc).isoformat()
        with _LOCK:
            records = _read_all()
            records.append(record)
            _write_all(records)
        return record

    def find_by_selection(self, selection_id: str) -> list[dict]:
        with _LOCK:
            return [r for r in _read_all() if r["selection_id"] == selection_id]

    def find_by_node(self, node_id: str) -> list[dict]:
        with _LOCK:
            return [
                r for r in _read_all()
                if node_id in (r.get("source_node_hashes") or {})
            ]

    def get(self, generation_id: str) -> dict | None:
        with _LOCK:
            for r in _read_all():
                if r["id"] == generation_id:
                    return r
        return None


store = GenerationStore()
