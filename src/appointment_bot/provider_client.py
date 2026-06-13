"""HTTP client for the provider's /Zimunet/ appointment API.

Flow (reverse-engineered from captures/, see PLAN.md):
  1. UserIdentity            -> bootstrap session identity
  2. SearchDiaries           -> diaries (doctor calendars) by specialization + city
  3. GetMonthlyAvailableVisit-> which days have availability (cheap month scan)
  4. GetDailyAvailableVisit  -> slots for a given day
  5. CanCreateVisit          -> precondition check
  6. Create/{slotGuid}       -> BOOK (a plain GET; gated by dry_run)

Responses are JSON `{"data": <html-or-dict>, "errorType": int}`. Hebrew payloads are
windows-1255 on the wire, so we decode bytes defensively (utf-8 then cp1255).
"""
from __future__ import annotations

import json as _json
import logging
import time
from datetime import date
from typing import Optional

import httpx

from . import html_parse
from .models import Diary, Slot

log = logging.getLogger("provider")


class SessionExpired(RuntimeError):
    """Raised when the saved cookies are no longer authenticated."""


class ProviderClient:
    LOGIN_PATH = "/onlineweb/general/login.aspx"

    def __init__(
        self,
        cookies: str = "",
        base_url: str = "__PROVIDER_BASE_URL__",
        dry_run: bool = True,
        timeout: float = 30.0,
        throttle: float = 0.4,   # polite delay (s) between scan requests
    ):
        self.base_url = base_url.rstrip("/")
        self.dry_run = dry_run
        self.throttle = throttle
        self._cookie_domain = httpx.URL(self.base_url).host
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "*/*",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Referer": f"{self.base_url}/Zimunet/Diary",
        }
        self._client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
        )
        # seed cookies from the raw "Cookie:" header string the user exported
        for part in cookies.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                self._client.cookies.set(k, v, domain=self._cookie_domain)

    # -- low level ---------------------------------------------------------

    def _decode(self, resp: httpx.Response) -> str:
        raw = resp.content
        for enc in ("utf-8", "cp1255"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("cp1255", errors="replace")

    def _json(self, resp: httpx.Response) -> dict:
        text = self._decode(resp)
        try:
            return _json.loads(text)
        except _json.JSONDecodeError:
            # A login redirect returns HTML, not JSON -> session is dead.
            if "login" in str(resp.url).lower() or "<html" in text.lower():
                raise SessionExpired("got HTML instead of JSON — cookies expired")
            raise

    def _check_alive(self, resp: httpx.Response) -> None:
        # the provider bounces unauthenticated callers to the WebForms login.
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("location", "")
            if "login" in loc.lower():
                raise SessionExpired(f"redirected to login: {loc}")
        if resp.status_code == 401:
            raise SessionExpired("HTTP 401 — cookies are not authenticated")

    def _extract_data(self, payload: dict):
        """Return payload['data'].

        Auth failures are detected by bootstrap()/HTTP 401, not here. So here we only
        raise SessionExpired on an explicit redirect to *login*; any other server
        errorType / error-redirect is a functional issue (no availability, bad context)
        — log it and return empty rather than crash or cry "session expired".
        """
        err = payload.get("errorType", 0)
        data = payload.get("data")
        if isinstance(data, dict):
            redirect = (data.get("redirectTo") or data.get("redirectUrl") or "")
            if data.get("isRedirect") and "login" in redirect.lower():
                raise SessionExpired(f"redirected to login: {redirect}")
            if data.get("isRedirect"):
                log.info("server redirect (errorType=%s) -> %s", err, redirect)
                return ""
            return data
        if err not in (0, None):
            log.info("server errorType=%s (treating as no data)", err)
            return ""
        return data or ""

    # -- flow steps --------------------------------------------------------

    def login(self, user_id: str, user_code: str, password: str) -> bool:
        """Authenticate from scratch with ID + user code + password (no cookie needed).

        Verified: the provider's login.aspx accepts a plain WebForms POST — no CAPTCHA and no
        OTP for the password path. On success `.ONLINEAUTH` is set and we land on the
        member home page. This lets the bot re-authenticate itself, so the ~20-min
        session cap stops mattering.
        """
        from bs4 import BeautifulSoup

        r = self._client.get(self.LOGIN_PATH, follow_redirects=True)
        soup = BeautifulSoup(self._decode(r), "lxml")

        def val(name: str) -> str:
            el = soup.find("input", {"name": name})
            return el.get("value", "") if el else ""

        form = {
            "__EVENTTARGET": "ctl00$cphBody$_loginView$btnSend",  # the "כניסה" button
            "__EVENTARGUMENT": "",
            "__LASTFOCUS": "",
            "__VIEWSTATE": val("__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": val("__VIEWSTATEGENERATOR"),
            "__VIEWSTATEENCRYPTED": "",
            "__PREVIOUSPAGE": val("__PREVIOUSPAGE"),
            "__EVENTVALIDATION": val("__EVENTVALIDATION"),
            "ctl00$cphBody$_loginView$tbUserId": user_id,
            "ctl00$cphBody$_loginView$tbUserName": user_code,
            "ctl00$cphBody$_loginView$tbPassword": password,
            "ctl00$cphBody$_loginView$tbCaptchaLogin": "",
        }
        for el in soup.find_all("input", {"type": "hidden"}):
            if "LBD_VCID" in (el.get("name") or ""):
                form[el.get("name")] = el.get("value", "")

        self._client.post(
            self.LOGIN_PATH, data=form,
            headers={"Referer": f"{self.base_url}{self.LOGIN_PATH}"},
            follow_redirects=True,
        )
        ok = ".ONLINEAUTH" in self._client.cookies
        log.info("login(%s) -> %s", user_id, ok)
        return ok

    TAMUZ_PATH = "/OnlineWeb/Services/Tamuz/TamuzTransfer.aspx"

    def enter_tamuz(self, person_index: int = 0) -> bool:
        """Enter the appointments (Tamuz/Zimunet) module after login, selecting a family
        member, so SearchDiaries has the right server-side context.

        Replicates the browser's TamuzTransfer postback:
        __EVENTTARGET = ...FamilySliderControl21$rptPersonList$ctl{NN}$lnkChild1
        (ctl00 = account owner; ctl01/ctl02 = other family members → also how multi-patient
        selection works). Verified against x.har as the exact entry the browser uses.
        """
        from bs4 import BeautifulSoup
        r = self._client.get(self.TAMUZ_PATH, follow_redirects=True)
        soup = BeautifulSoup(self._decode(r), "lxml")

        def val(name: str) -> str:
            el = soup.find("input", {"name": name})
            return el.get("value", "") if el else ""

        slider = "ctl00$ctl00$cphTopMenuRight$FamilySliderControl21"
        target = f"{slider}$rptPersonList$ctl{person_index:02d}$lnkChild1"
        post = {
            "ctl00$ctl00$ScriptManager1": f"{slider}$upFamilySlider|{target}",
            "__EVENTTARGET": target,
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": val("__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": val("__VIEWSTATEGENERATOR"),
            "__VIEWSTATEENCRYPTED": "",
            "__EVENTVALIDATION": val("__EVENTVALIDATION"),
            f"{slider}$au": val(f"{slider}$au"),
            f"{slider}$cu": val(f"{slider}$cu"),
            "__ASYNCPOST": "true",
        }
        resp = self._client.post(
            self.TAMUZ_PATH, data=post, follow_redirects=True,
            headers={"Referer": f"{self.base_url}{self.TAMUZ_PATH}",
                     "X-Requested-With": "XMLHttpRequest", "X-MicrosoftAjax": "Delta=true"},
        )
        log.info("enter_tamuz(person=%d) -> %s", person_index, resp.status_code)
        return resp.status_code == 200

    def keepalive(self) -> bool:
        """Keep the session alive. Returns True if still authenticated.

        Strategy: call UserIdentity (bootstrap) rather than SyncSession.aspx.
        SyncSession is in the OnlineWeb IIS application; the appointment API lives
        under /Zimunet/, which may be a separate application with its own session
        state. UserIdentity is the one endpoint we know re-validates auth for both
        contexts, and its sliding Forms-Auth window covers the /Zimunet/ calls too.
        """
        return self.bootstrap()

    def bootstrap(self) -> bool:
        """POST UserIdentity. Returns True when the session answers 'OK'."""
        resp = self._client.post(
            "/onlineweb/api/ApplicationAuth/UserIdentity",
            headers={"Content-Type": "application/json; charset=utf-8"},
            content=b"",  # captured request sent an empty body
        )
        self._check_alive(resp)
        text = self._decode(resp).strip().strip('"')
        log.info("UserIdentity -> %s (%s)", text, resp.status_code)
        return resp.status_code == 200 and text.upper() == "OK"

    MAX_PAGES = 10  # safety cap on pagination

    def search_diaries(self, specialization_code: str, city: str) -> list[Diary]:
        """POST SearchDiaries, then walk all result pages (/Diary/Paging).

        city is Hebrew; httpx url-encodes it as UTF-8. Results are paginated server-side
        and Paging relies on the session's last search, so we page right after searching.
        """
        data = {
            "SelectedGroupCode": specialization_code,
            "SelectedSpecializationCode": specialization_code,
            "SelectedDoctorName": "",
            "IsSearchDiariesByDistricts": "true",
            "SelectedCityName": city,
        }
        resp = self._client.post(
            "/Zimunet/Diary/SearchDiaries",
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
            },
        )
        self._check_alive(resp)
        payload = self._json(resp)
        html = self._extract_data(payload)
        if not isinstance(html, str):
            html = ""

        diaries: dict[str, Diary] = {d.diary_id: d for d in html_parse.parse_diaries(html)}
        max_page = min(html_parse.parse_max_page(html), self.MAX_PAGES)
        for page in range(2, max_page + 1):
            try:
                page_html = self._fetch_page(page)
            except Exception as e:
                log.warning("SearchDiaries paging stopped at page %d: %s", page, e)
                break
            for d in html_parse.parse_diaries(page_html):
                diaries.setdefault(d.diary_id, d)
        log.info("search %s/%s -> %d diaries across %d page(s)",
                 specialization_code, city, len(diaries), max_page)
        return list(diaries.values())

    def _fetch_page(self, page_number: int) -> str:
        if self.throttle:
            time.sleep(self.throttle)
        resp = self._client.get(
            "/Zimunet/Diary/Paging", params={"pageNumber": page_number}
        )
        self._check_alive(resp)
        payload = self._json(resp)
        return payload.get("data", "") or ""

    def open_diary(self, diary_id: str, is_update: bool = False) -> None:
        """Open a diary's availability page first — the browser GETs this before asking
        for daily slots, and it sets the server-side visit context."""
        if self.throttle:
            time.sleep(self.throttle)
        try:
            resp = self._client.get(
                f"/Zimunet/AvailableVisit/Index/{diary_id}",
                params={"isUpdateVisit": "True" if is_update else "False"},
            )
            self._check_alive(resp)
        except SessionExpired:
            raise
        except Exception as e:
            log.warning("open_diary %s failed (continuing): %s", diary_id, e)

    def get_daily_slots(self, diary_id: str, on: date, is_update: bool = False) -> list[Slot]:
        if self.throttle:
            time.sleep(self.throttle)
        resp = self._client.get(
            "/Zimunet/AvailableVisit/GetDailyAvailableVisit",
            params={
                "id": diary_id,
                "professionType": "Professional",
                "day": f"{on.day:02d}",
                "month": f"{on.month:02d}",
                "year": str(on.year),
                "isUpdateVisit": "True" if is_update else "False",
            },
        )
        self._check_alive(resp)
        payload = self._json(resp)
        data = self._extract_data(payload)
        html = data.get("dailyAvailableVisits", "") if isinstance(data, dict) else (data or "")
        return html_parse.parse_slots(html, on_date=on)

    def get_monthly_availability(self, diary_id: str, year: int, month: int) -> dict:
        """Raw monthly availability payload (which days are open). Shape TBD; returned as-is."""
        resp = self._client.get(
            "/Zimunet/AvailableVisit/GetMonthlyAvailableVisit",
            params={"id": diary_id, "professionType": "Professional",
                    "month": f"{month:02d}", "year": str(year), "isUpdateVisit": "False"},
        )
        self._check_alive(resp)
        return self._json(resp)

    def can_create_visit(self, slot_id: str) -> bool:
        try:
            resp = self._client.get(f"/Zimunet/Visit/CanCreateVisit/{slot_id}")
            self._check_alive(resp)
            payload = self._json(resp)
            return payload.get("errorType", 0) == 0
        except Exception as e:  # non-fatal: don't block booking on a precheck quirk
            log.warning("CanCreateVisit failed (continuing): %s", e)
            return True

    def book_slot(self, slot: Slot) -> bool:
        """Commit a booking. NO-OP when dry_run is True.

        ⚠️ Hitting Create/{guid} actually reserves the appointment.
        """
        if self.dry_run:
            log.warning("[DRY RUN] would book slot %s at %s (%s)",
                        slot.slot_id, slot.start, slot.book_url)
            return False

        self.can_create_visit(slot.slot_id)
        resp = self._client.get(
            f"/Zimunet/AvailableVisit/Create/{slot.slot_id}",
            params={"selectedZoharVisitType": slot.visit_type},
        )
        self._check_alive(resp)
        payload = self._json(resp)
        ok = html_parse.is_booking_success(payload)
        log.info("book_slot %s -> success=%s payload=%s", slot.slot_id, ok, payload)
        return ok

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ProviderClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
