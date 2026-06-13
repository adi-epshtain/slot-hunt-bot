"""Tests against the real captured fixtures (tests/fixtures/) and the request parser."""
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from appointment_bot import html_parse, request_parse  # noqa: E402
from appointment_bot.models import Slot, Watch  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def test_parse_diaries_real_fixture():
    html = (FIX / "search_diaries.html").read_text(encoding="utf-8")
    diaries = html_parse.parse_diaries(html)
    assert len(diaries) >= 4
    # all ids look like GUIDs and are unique
    ids = [d.diary_id for d in diaries]
    assert len(ids) == len(set(ids))
    assert all(len(i) == 36 for i in ids)


def test_parse_pagination_real_fixture():
    html = (FIX / "search_diaries.html").read_text(encoding="utf-8")
    # the captured search spans multiple pages — must detect more than 1
    assert html_parse.parse_max_page(html) >= 2


def test_parse_slots_real_fixture():
    html = (FIX / "daily_slots.html").read_text(encoding="utf-8")
    slots = html_parse.parse_slots(html, on_date=date(2026, 7, 24))
    assert len(slots) >= 10
    first = slots[0]
    assert first.start.hour == 8 and first.start.minute == 30
    assert len(first.slot_id) == 36
    assert "AvailableVisit/Create" in first.book_url


def test_booking_success_detection():
    assert html_parse.is_booking_success(
        {"data": {"isRedirect": True, "redirectUrl": "/Zimunet/Visit/Created"}})
    assert not html_parse.is_booking_success({"data": {"isRedirect": False}})
    assert not html_parse.is_booking_success({"data": "<html>error</html>"})


def test_watch_matches_time_earlier_only():
    cur = datetime(2026, 9, 15, 10, 0)
    w = Watch(id="x", account="adi", patient="עדי", specialization_code="62",
              weekdays=[], hour_from=8, hour_to=13, current_appointment=cur)
    earlier = Slot(slot_id="a" * 36, start=__import__("datetime").time(9, 0),
                   on_date=date(2026, 8, 1))
    later = Slot(slot_id="b" * 36, start=__import__("datetime").time(9, 0),
                 on_date=date(2026, 10, 1))
    out_of_hours = Slot(slot_id="c" * 36, start=__import__("datetime").time(15, 0),
                        on_date=date(2026, 8, 1))
    assert w.matches_time(earlier)
    assert not w.matches_time(later)        # not earlier than current
    assert not w.matches_time(out_of_hours)  # outside hour window


def test_request_parse_hebrew():
    w, problems = request_parse.parse_message(
        "רופא משפחה לעדי ברעננה בבוקר, דחוף",
        watch_id="t1", account="adi",
    )
    assert w is not None, problems
    assert w.specialization_code == "62"
    assert w.patient == "עדי"
    assert "רעננה" in w.cities
    assert w.urgent is True
    assert w.hour_from == 6 and w.hour_to == 12


def test_request_parse_unknown_specialty_reports_problem():
    w, problems = request_parse.parse_message(
        "תור לאורתופד לטומי", watch_id="t2", account="adi")
    assert w is None
    assert any("רופא" in p for p in problems)
