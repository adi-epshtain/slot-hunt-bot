"""Core data structures shared across the bot."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Optional


@dataclass
class Diary:
    """A bookable doctor/clinic calendar returned by SearchDiaries."""

    diary_id: str               # GUID used by GetDailyAvailableVisit?id=...
    doctor_name: str = ""
    clinic_name: str = ""
    gender: str = ""            # "1"=male / "2"=female (the provider's diary gender filter)
    raw_title: str = ""

    @property
    def index_url(self) -> str:
        return f"/Zimunet/AvailableVisit/Index/{self.diary_id}?isUpdateVisit=False"


@dataclass
class Slot:
    """A single available appointment time."""

    slot_id: str                # GUID; booking = GET Create/{slot_id}
    start: time                 # appointment time of day
    visit_type: str = "Clinic_1"  # selectedZoharVisitType
    doctor_license: str = ""
    doctor_gender: str = ""
    doctor_name: str = ""
    on_date: Optional[date] = None

    @property
    def book_url(self) -> str:
        return (
            f"/Zimunet/AvailableVisit/Create/{self.slot_id}"
            f"?selectedZoharVisitType={self.visit_type}"
        )

    def when(self) -> Optional[datetime]:
        if self.on_date is None:
            return None
        return datetime.combine(self.on_date, self.start)


@dataclass
class Watch:
    """A standing request: find an earlier slot matching these constraints.

    Created dynamically from a free-text WhatsApp message; lives only while active.
    """

    id: str                                  # stable id (for state/dedupe)
    account: str                             # which the provider login/cookies to use
    patient: str                             # for whom (Adi/Noya/Tomi/...)
    specialization_code: str                 # e.g. "62" = family doctor
    cities: list[str] = field(default_factory=list)
    weekdays: list[int] = field(default_factory=list)   # 0=Mon..6=Sun; [] = any
    hour_from: int = 0
    hour_to: int = 24
    current_appointment: Optional[datetime] = None  # None = no existing appt
    urgent: bool = False
    raw_text: str = ""                       # original user message
    created_at: Optional[str] = None
    notified_slot_ids: list[str] = field(default_factory=list)  # dedupe alerts

    def matches_time(self, slot: Slot) -> bool:
        if slot.on_date is None:
            return False
        if self.weekdays and slot.on_date.weekday() not in self.weekdays:
            return False
        if not (self.hour_from <= slot.start.hour < self.hour_to):
            return False
        # Only earlier than the current appointment (the whole point of the bot).
        if self.current_appointment is not None:
            when = slot.when()
            if when is None or when >= self.current_appointment:
                return False
        return True

    @property
    def has_existing_appointment(self) -> bool:
        return self.current_appointment is not None
