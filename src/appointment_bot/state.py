"""Persistence of active watches + processed-message ids.

GitHub Actions runs are stateless, so the workflow commits this JSON back to the repo
after each run (see .github/workflows/bot.yml). That gives the bot memory of which
requests are still active and which WhatsApp messages it already handled.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import Watch


def _watch_to_dict(w: Watch) -> dict:
    d = asdict(w)
    d["current_appointment"] = (
        w.current_appointment.isoformat() if w.current_appointment else None
    )
    return d


def _watch_from_dict(d: dict) -> Watch:
    ca = d.get("current_appointment")
    return Watch(
        id=d["id"],
        account=d["account"],
        patient=d["patient"],
        specialization_code=d["specialization_code"],
        cities=list(d.get("cities", [])),
        weekdays=list(d.get("weekdays", [])),
        hour_from=int(d.get("hour_from", 0)),
        hour_to=int(d.get("hour_to", 24)),
        current_appointment=datetime.fromisoformat(ca) if ca else None,
        urgent=bool(d.get("urgent", False)),
        raw_text=d.get("raw_text", ""),
        created_at=d.get("created_at"),
        notified_slot_ids=list(d.get("notified_slot_ids", [])),
    )


class State:
    def __init__(self, path: str):
        self.path = Path(path)
        self.watches: list[Watch] = []
        self.processed_message_ids: list[str] = []
        self.last_inbound_check: Optional[str] = None
        self.chat_log: list[dict] = []   # [{role, text, at}, ...] for the web chat

    @classmethod
    def load(cls, path: str) -> "State":
        st = cls(path)
        if st.path.exists():
            raw = json.loads(st.path.read_text(encoding="utf-8"))
            st.watches = [_watch_from_dict(d) for d in raw.get("watches", [])]
            st.processed_message_ids = raw.get("processed_message_ids", [])
            st.last_inbound_check = raw.get("last_inbound_check")
            st.chat_log = raw.get("chat_log", [])
        return st

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "watches": [_watch_to_dict(w) for w in self.watches],
            # keep only the most recent ids to bound file growth
            "processed_message_ids": self.processed_message_ids[-500:],
            "last_inbound_check": self.last_inbound_check,
            "chat_log": self.chat_log[-200:],
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def add_watch(self, w: Watch) -> None:
        self.watches.append(w)

    def remove_watch(self, watch_id: str) -> None:
        self.watches = [w for w in self.watches if w.id != watch_id]

    def already_processed(self, message_id: str) -> bool:
        return message_id in self.processed_message_ids

    def mark_processed(self, message_id: str) -> None:
        if message_id not in self.processed_message_ids:
            self.processed_message_ids.append(message_id)
