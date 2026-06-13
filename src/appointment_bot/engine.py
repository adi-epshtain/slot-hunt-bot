"""The watching engine: scan the provider for earlier matching slots and apply the booking
policy. Reused by the web app's scheduler and by any CLI/cron entry point.

A "notifier" here is any object with `.send(text)` (EmailNotifier, or a no-op).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from . import config
from .provider_client import ProviderClient, SessionExpired
from .models import Slot, Watch
from .state import State

log = logging.getLogger("engine")

MAX_DIARIES_PER_WATCH = 8   # gentle cap; logged so coverage is never silently dropped


def scan_watch(client: ProviderClient, w: Watch, days_ahead: int) -> list[Slot]:
    """Matching slots earlier than the current appointment (if any), earliest first."""
    today = date.today()
    last_day = today + timedelta(days=days_ahead)
    if w.current_appointment is not None:
        last_day = min(last_day, w.current_appointment.date())

    cities = w.cities or [""]   # "" => search without a city filter
    diaries = []
    for city in cities:
        diaries.extend(client.search_diaries(w.specialization_code, city))
    uniq = {d.diary_id: d for d in diaries}
    diary_list = list(uniq.values())
    if len(diary_list) > MAX_DIARIES_PER_WATCH:
        log.info("watch %s: capping %d diaries to %d (coverage note)",
                 w.id, len(diary_list), MAX_DIARIES_PER_WATCH)
        diary_list = diary_list[:MAX_DIARIES_PER_WATCH]

    found: list[Slot] = []
    for diary in diary_list:
        client.open_diary(diary.diary_id)   # set server-side context before slots
        d = today + timedelta(days=1)
        while d <= last_day:
            for slot in client.get_daily_slots(diary.diary_id, d):
                if w.matches_time(slot):
                    found.append(slot)
            d += timedelta(days=1)
    found.sort(key=lambda s: (s.when() or datetime.max))
    return found


def process_watch(st: State, w: Watch, settings: config.Settings, notify: Callable[[str], None]) -> None:
    cookies = settings.cookies_for(w.account)
    creds = settings.creds_for(w.account)

    with ProviderClient(cookies, base_url=settings.base_url, dry_run=settings.dry_run) as client:
        try:
            # Preferred: auto-login with stored credentials (fresh session each scan,
            # so the ~20-min session cap never bites). Falls back to a pasted cookie.
            if creds:
                if not client.login(*creds):
                    raise SessionExpired("auto-login failed (check credentials)")
                # enter the appointments module so SearchDiaries has context
                acc = settings.accounts.get(w.account)
                client.enter_tamuz(acc.person_index if acc else 0)
            elif cookies:
                if not client.bootstrap():
                    raise SessionExpired("UserIdentity did not return OK")
            else:
                notify(f"⚠️ לחשבון '{w.account}' חסרים פרטי התחברות (או cookies).")
                return
            slots = scan_watch(client, w, settings.poll_days_ahead)
        except SessionExpired as e:
            log.warning("watch %s: auth failed: %s", w.id, e)
            notify(f"🔑 בעיית התחברות לחשבון '{w.account}': {e}")
            return

        fresh = [s for s in slots if s.slot_id not in w.notified_slot_ids]
        if not fresh:
            log.info("watch %s: no new matching slots", w.id)
            notify(f"🔍 סיימתי לחפש ל{w.patient} — לא נמצאו תורים מתאימים כרגע. אנסה שוב בעוד {settings.scan_interval_min} דקות.")
            return

        best = fresh[0]
        details = _slot_details(w, best)

        if not w.has_existing_appointment:
            if settings.auto_book and not settings.dry_run:
                if client.book_slot(best):
                    notify(f"✅ נקבע תור!\n{details}")
                    st.remove_watch(w.id)
                    return
                notify(f"⚠️ ניסיתי לקבוע אך נכשל, אנסה שוב:\n{details}")
            else:
                mode = "מצב בדיקה" if settings.dry_run else "קביעה אוטומטית כבויה"
                notify(f"מצאתי תור מתאים ({mode} — לא נקבע):\n{details}")
        else:
            cur = w.current_appointment.strftime("%d.%m.%Y %H:%M")
            notify(f"⚡ נמצא תור מוקדם יותר (הנוכחי שלך: {cur}).\n{details}\n"
                   f"להחליף? צריך אישור — לא מחליף תור קיים לבד.")
        w.notified_slot_ids.append(best.slot_id)


def _slot_details(w: Watch, slot: Slot) -> str:
    """Full, human-readable appointment details for the alert / e-mail."""
    when = slot.when()
    heb_days = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]
    day_name = heb_days[when.weekday()] if when else ""
    when_str = when.strftime("%d.%m.%Y בשעה %H:%M") if when else str(slot.start)
    spec = next((k for k, v in config.SPECIALIZATION_CODES.items()
                 if v == w.specialization_code and not k.isascii()), w.specialization_code)
    lines = [
        f"👤 מטופל/ת: {w.patient}",
        f"🩺 סוג: {spec}",
        f"📅 מתי: יום {day_name}, {when_str}",
    ]
    if w.cities:
        lines.append(f"📍 אזור: {', '.join(w.cities)}")
    if slot.doctor_name:
        lines.append(f"👨‍⚕️ רופא/ה: {slot.doctor_name}")
    return "\n".join(lines)


def run_watches(st: State, settings: config.Settings, notify: Callable[[str], None]) -> None:
    """One scan pass over all active watches."""
    log.info("scan pass: dry_run=%s auto_book=%s watches=%d",
             settings.dry_run, settings.auto_book, len(st.watches))
    for w in list(st.watches):
        try:
            process_watch(st, w, settings, notify)
        except Exception as e:  # never let one watch kill the whole pass
            log.exception("watch %s failed: %s", w.id, e)
    st.last_inbound_check = datetime.now().isoformat(timespec="seconds")
    st.save()
