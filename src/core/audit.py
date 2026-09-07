"""Decision Trace — append-only audit ledger.

Every significant decision in the pipeline writes exactly one entry here.
The ledger is intentionally simple: append-only, hash-chained, serialisable
to JSON lines.  In production this would back onto the eventsourcing library;
for the MVP it is an in-process list backed by a JSON-lines file.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.ml.versioning import stable_hash, utc_now


@dataclass(frozen=True)
class DecisionTraceEntry:
    """One immutable record in the audit ledger."""

    invoice_id: str
    event: str
    from_state: Optional[str] = None
    to_state: Optional[str] = None
    ladder_index: Optional[int] = None
    reasoning: Optional[str] = None
    scored_at: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    prev_hash: Optional[str] = None
    entry_hash: Optional[str] = None


# ── In-memory ledger (replaced by event-sourcing DB in production) ─────────────

_ledger: list[DecisionTraceEntry] = []


def _compute_hash(entry: DecisionTraceEntry, prev_hash: Optional[str]) -> str:
    """SHA-256 of (entry fields minus hash) + prev_hash — forms the chain."""
    d = asdict(entry)
    d.pop("entry_hash", None)
    d["prev_hash"] = prev_hash
    return stable_hash(d)


def append_decision_trace(
    invoice_id: str,
    event: str,
    *,
    from_state: Optional[str] = None,
    to_state: Optional[str] = None,
    ladder_index: Optional[int] = None,
    reasoning: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> DecisionTraceEntry:
    """Append one entry to the ledger and return it."""
    prev_hash = _ledger[-1].entry_hash if _ledger else None
    entry = DecisionTraceEntry(
        invoice_id=invoice_id,
        event=event,
        from_state=from_state,
        to_state=to_state,
        ladder_index=ladder_index,
        reasoning=reasoning,
        scored_at=utc_now().isoformat(),
        metadata=metadata or {},
        prev_hash=prev_hash,
    )
    chained = DecisionTraceEntry(
        **{**asdict(entry), "entry_hash": _compute_hash(entry, prev_hash)}
    )
    _ledger.append(chained)
    return chained


def get_trace(invoice_id: str) -> list[DecisionTraceEntry]:
    """Return all entries for a given invoice, in order."""
    return [e for e in _ledger if e.invoice_id == invoice_id]


def get_full_trace() -> list[DecisionTraceEntry]:
    """Return the entire ledger (for export / debugging)."""
    return list(_ledger)


def clear_trace() -> None:
    """Reset the in-memory ledger (testing only)."""
    _ledger.clear()


def persist_to_file(path: Path) -> None:
    """Write the full ledger to a JSON-lines file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for entry in _ledger:
            f.write(json.dumps(asdict(entry), default=str) + "\n")
