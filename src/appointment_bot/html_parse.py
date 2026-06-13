"""Parse the HTML fragments the provider's /Zimunet/ API returns.

Responses are JSON like {"data": "<html...>", "errorType": 0}. The HTML is
windows-1255 encoded on the wire; the client decodes bytes->cp1255 before parsing,
so functions here receive proper unicode strings.

Verified offline against real captured fragments in tests/fixtures/.
"""
from __future__ import annotations

import re
from datetime import date, time
from typing import Optional

from bs4 import BeautifulSoup

from .models import Diary, Slot

_INDEX_RE = re.compile(r"/Zimunet/AvailableVisit/Index/([0-9a-fA-F-]{36})")
_PAGING_RE = re.compile(r"/Zimunet/Diary/Paging\?pageNumber=(\d+)")
_CREATE_RE = re.compile(
    r"/Zimunet/AvailableVisit/Create/([0-9a-fA-F-]{36})"
    r"(?:\?selectedZoharVisitType=([^\"'&]+))?"
)
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")


def parse_diaries(html: str) -> list[Diary]:
    """Extract bookable diaries (doctor calendars) from a SearchDiaries response."""
    soup = BeautifulSoup(html, "lxml")
    diaries: list[Diary] = []
    seen: set[str] = set()

    for a in soup.select("a[data-action-link]"):
        link = a.get("data-action-link", "")
        m = _INDEX_RE.search(link)
        if not m:
            continue
        diary_id = m.group(1)
        if diary_id in seen:
            continue
        seen.add(diary_id)

        # Walk up to the surrounding diary block to scrape doctor/clinic text.
        block = a
        for _ in range(8):
            if block.parent is None:
                break
            block = block.parent
        doctor_name, clinic_name = _diary_texts(block)

        diaries.append(
            Diary(
                diary_id=diary_id,
                doctor_name=doctor_name,
                clinic_name=clinic_name,
                raw_title=(a.get("title") or "").strip(),
            )
        )
    return diaries


def _diary_texts(block) -> tuple[str, str]:
    """Best-effort doctor + clinic names from a diary block (layout varies)."""
    doctor = ""
    clinic = ""
    for sel in ("h2", "h3", ".doctorName", ".diaryDoctorName", ".css_doctorName"):
        el = block.select_one(sel)
        if el and el.get_text(strip=True):
            doctor = el.get_text(strip=True)
            break
    el = block.select_one(".diaryClinicName, .clinicName, .mapLocationLink")
    if el:
        clinic = el.get_text(strip=True)
    return doctor, clinic


def parse_max_page(html: str) -> int:
    """Highest pageNumber in the SearchDiaries pager (1 if there is no pager)."""
    pages = [int(n) for n in _PAGING_RE.findall(html)]
    return max(pages) if pages else 1


def parse_slots(html: str, on_date: Optional[date] = None) -> list[Slot]:
    """Extract available time slots from a GetDailyAvailableVisit response."""
    soup = BeautifulSoup(html, "lxml")
    slots: list[Slot] = []

    for li in soup.select("li[data-doctor-license], li.clearfix"):
        a = li.select_one("a[data-action-link*='AvailableVisit/Create']")
        if a is None:
            continue
        m = _CREATE_RE.search(a.get("data-action-link", ""))
        if not m:
            continue
        slot_id = m.group(1)
        visit_type = m.group(2) or "Clinic_1"

        slot_time = _first_time(li)
        if slot_time is None:
            continue

        name_el = li.select_one(".doctorName")
        slots.append(
            Slot(
                slot_id=slot_id,
                start=slot_time,
                visit_type=visit_type,
                doctor_license=li.get("data-doctor-license", "") or "",
                doctor_gender=li.get("data-doctor-gender", "") or "",
                doctor_name=(name_el.get_text(strip=True) if name_el else ""),
                on_date=on_date,
            )
        )
    return slots


def _first_time(li) -> Optional[time]:
    """The slot time is the first HH:MM <span> in the <li>."""
    for span in li.find_all("span"):
        m = _TIME_RE.search(span.get_text())
        if m:
            h, mn = int(m.group(1)), int(m.group(2))
            if 0 <= h < 24 and 0 <= mn < 60:
                return time(h, mn)
    return None


def is_booking_success(payload: dict) -> bool:
    """Create/{guid} returns {'data':{'isRedirect':True,'redirectUrl':'/Zimunet/Visit/Created'}}."""
    data = payload.get("data")
    if isinstance(data, dict):
        url = (data.get("redirectUrl") or "").lower()
        return data.get("isRedirect") is True and "created" in url
    return False
