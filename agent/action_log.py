"""
Action log — append-only JSONL record of every agent action.

Every entry records:
  requested  — what the agent asked to do (raw tool inputs)
  verified   — confirmation state (True/False/None if n/a)
  executed   — what actually ran (same as requested after token validation)
  result     — the outcome (success payload or error dict)
  status     — "pending" | "success" | "failed"

The log is written to a JSONL file and also held in memory for
in-process querying (e.g., action_log.summary()).
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class ActionEntry:
    def __init__(self, action_type: str, requested: dict):
        self.action_id = uuid.uuid4().hex[:8]
        self.action_type = action_type
        self.timestamp_start = _now()
        self.timestamp_end: Optional[str] = None
        self.status = "pending"
        self.requested = requested
        self.verified: Optional[bool] = None   # True = user confirmed, False = declined, None = no gate
        self.executed: Optional[dict] = None   # populated when the real call is made
        self.result: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "timestamp_start": self.timestamp_start,
            "timestamp_end": self.timestamp_end,
            "status": self.status,
            "verified_by_user": self.verified,
            "requested": self.requested,
            "executed": self.executed,
            "result": self.result,
        }


class ActionLog:
    """
    Thread-safe-enough append-only action log.

    Usage
    -----
    entry = log.start("book_ride", {"pickup": ..., "dropoff": ...})
    ...
    log.complete(entry, result={"ride_id": "UBR-123"}, verified=True)
    # or
    log.fail(entry, error={"error": "token_invalid"})
    """

    def __init__(self, log_file: Optional[Path] = None):
        self.log_file = Path(log_file) if log_file else Path("logs/actions.jsonl")
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.entries: list[ActionEntry] = []
        self._append({"_session_start": _now(), "_pid": str(__import__("os").getpid())})

    # ── Public API ──────────────────────────────────────────────────────────

    def start(self, action_type: str, requested: dict) -> ActionEntry:
        entry = ActionEntry(action_type, requested)
        self.entries.append(entry)
        return entry

    def complete(self, entry: ActionEntry, result: dict, verified: Optional[bool] = None) -> None:
        entry.status = "success"
        entry.result = result
        entry.timestamp_end = _now()
        if verified is not None:
            entry.verified = verified
        if entry.executed is None:
            entry.executed = entry.requested
        self._append(entry.to_dict())

    def fail(self, entry: ActionEntry, error: dict) -> None:
        entry.status = "failed"
        entry.result = error
        entry.timestamp_end = _now()
        if entry.executed is None:
            entry.executed = entry.requested
        self._append(entry.to_dict())

    def summary(self) -> str:
        width = 62
        lines = [
            "─" * width,
            f"  Action Log — {len(self.entries)} action(s)",
            "─" * width,
        ]
        for e in self.entries:
            icon = "✓" if e.status == "success" else ("✗" if e.status == "failed" else "·")
            gate = ""
            if e.verified is True:
                gate = " [user-confirmed]"
            elif e.verified is False:
                gate = " [user-declined]"
            lines.append(f"  {icon} {e.action_type:<32} {e.status}{gate}")
        lines.append("─" * width)
        return "\n".join(lines)

    def as_list(self) -> list[dict]:
        return [e.to_dict() for e in self.entries]

    # ── Private ─────────────────────────────────────────────────────────────

    def _append(self, data: dict) -> None:
        with open(self.log_file, "a") as fh:
            fh.write(json.dumps(data) + "\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
