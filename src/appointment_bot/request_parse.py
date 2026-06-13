"""Turn a free-text WhatsApp message into a Watch.

The user writes naturally, e.g.:
    "רופא משפחה לעדי ברעננה בבוקר, דחוף"
    "תור עיניים לנויה בהרצליה או כפר סבא, ימים ראשון שלישי, אחרי 16:00"

Default: a rule-based Hebrew parser (no external dependency, no API key).
Optional: if ANTHROPIC_API_KEY is set, parse_with_claude() can be used for messier text.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Optional

from .config import SPECIALIZATION_CODES
from .models import Watch

# Hebrew weekday name -> Python weekday() (Mon=0 .. Sun=6)
_HEB_DAYS = {
    "ראשון": 6, "שני": 0, "שלישי": 1, "רביעי": 2,
    "חמישי": 3, "שישי": 4, "שבת": 5,
}

# Specialty keywords -> code (only codes we have confirmed; extend as captured).
_SPECIALTY_KEYWORDS = {
    "משפחה": "62", "רופא משפחה": "62", "family": "62",
    # codes 31 / 61 exist but their specialty names are not yet confirmed (see PLAN).
}

# Small starter city list; SelectedCityName accepts the Hebrew name verbatim.
_KNOWN_CITIES = [
    "רעננה", "הרצליה", "כפר סבא", "הוד השרון", "תל אביב", "רמת גן",
    "גבעתיים", "פתח תקווה", "נתניה", "ירושלים", "חיפה", "ראשון לציון",
    "חולון", "בת ים", "רחובות", "מודיעין", "באר שבע", "אשדוד", "אשקלון",
]

_TIME_OF_DAY = {
    "בוקר": (6, 12), "צהריים": (12, 14), "צהוריים": (12, 14),
    "אחר הצהריים": (14, 18), "אחהצ": (14, 18), "אחה\"צ": (14, 18),
    "ערב": (16, 21), "אחרי הצהריים": (14, 18),
}

_URGENT = ["דחוף", "דחיפות", "בהקדם", "כמה שיותר מהר", "urgent", "asap"]


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s).strip()


def parse_message(
    text: str,
    *,
    watch_id: str,
    account: str,
    default_patient: str = "",
    created_at: Optional[str] = None,
) -> tuple[Optional[Watch], list[str]]:
    """Parse free text -> (Watch | None, list_of_problems).

    Returns problems (e.g. unknown specialty) so the caller can WhatsApp back a question.
    """
    t = _norm(text)
    problems: list[str] = []

    # specialty
    spec_code = None
    for kw, code in _SPECIALTY_KEYWORDS.items():
        if kw in t:
            spec_code = code
            break
    if spec_code is None:
        problems.append(
            "לא זיהיתי את סוג הרופא. כרגע מוגדר רק 'רופא משפחה'. "
            "תכתבי לי איזה מקצוע ואוסיף אותו."
        )

    # patient: "ל<name>" / "עבור <name>" / known fallback
    patient = default_patient
    m = re.search(r"(?:עבור|ל|בשביל)\s*([א-ת]{2,})", t)
    if m:
        cand = m.group(1)
        if cand not in _HEB_DAYS and cand not in _KNOWN_CITIES:
            patient = cand
    if not patient:
        problems.append("לא זיהיתי עבור מי התור (עדי / נויה / טומי).")

    # cities
    cities = [c for c in _KNOWN_CITIES if c in t]

    # weekdays
    weekdays = sorted({wd for name, wd in _HEB_DAYS.items() if name in t})

    # hours: explicit range first, then time-of-day words
    hour_from, hour_to = 0, 24
    rng = re.search(r"(\d{1,2})(?::\d{2})?\s*[-–]\s*(\d{1,2})(?::\d{2})?", t)
    after = re.search(r"אחרי\s*(\d{1,2})", t)
    before = re.search(r"לפני\s*(\d{1,2})", t)
    if rng:
        hour_from, hour_to = int(rng.group(1)), int(rng.group(2))
    else:
        for word, (a, b) in _TIME_OF_DAY.items():
            if word in t:
                hour_from, hour_to = a, b
                break
        if after:
            hour_from = int(after.group(1))
        if before:
            hour_to = int(before.group(1))

    urgent = any(w in t.lower() for w in _URGENT)

    if spec_code is None or not patient:
        return None, problems

    w = Watch(
        id=watch_id,
        account=account,
        patient=patient,
        specialization_code=spec_code,
        cities=cities or [],
        weekdays=weekdays,
        hour_from=hour_from,
        hour_to=hour_to,
        current_appointment=None,   # set later if the user mentions an existing appt
        urgent=urgent,
        raw_text=text,
        created_at=created_at,
    )
    if not cities:
        problems.append("לא ציינת עיר/אזור — אחפש בכל המקומות. אפשר להוסיף עיר.")
    return w, problems


def describe(w: Watch) -> str:
    """Human-readable summary for a confirmation WhatsApp reply."""
    days = "כל הימים" if not w.weekdays else ", ".join(
        n for n, wd in _HEB_DAYS.items() if wd in w.weekdays
    )
    cities = ", ".join(w.cities) if w.cities else "כל האזורים"
    spec = next((k for k, v in SPECIALIZATION_CODES.items()
                 if v == w.specialization_code and not k.isascii()), w.specialization_code)
    urgent = " ⚡דחוף" if w.urgent else ""
    return (f"מעקב נפתח: {spec} ל{w.patient} | {cities} | {days} | "
            f"{w.hour_from:02d}:00-{w.hour_to:02d}:00{urgent}")
