"""FastAPI web app: a simple chat UI for the bot + an internal scheduler.

Endpoints:
  GET  /              -> the chat web page
  POST /api/chat      -> {message} : parse a free-text request, create/cancel/list watch
  GET  /api/state     -> active watches + recent bot messages (for the chat to render)

A background APScheduler job runs the watching engine every 30 minutes and e-mails the
user when a slot is found. Day-to-day input is via the chat, never e-mail.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, engine, request_parse
from .notifier import EmailNotifier
from .state import State

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("webapp")

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="slot-hunt-bot")
settings = config.load()
state = State.load(settings.state_path)
notifier = EmailNotifier(settings.bot_email, settings.bot_email_password, settings.my_email)


def _bot_say(text: str) -> None:
    """Record a bot message in the chat log and (for alerts) e-mail it."""
    state.chat_log.append({"role": "bot", "text": text,
                           "at": datetime.now().isoformat(timespec="seconds")})
    state.save()


def _alert(text: str) -> None:
    """Engine callback: show in chat AND push by e-mail (found-a-slot path)."""
    _bot_say(text)
    notifier.send(text)


_scan_lock = threading.Lock()


def _run_scan_async() -> None:
    """Run a scan pass in a background thread (so the chat responds instantly)."""
    def job():
        if not _scan_lock.acquire(blocking=False):
            return  # a scan is already running
        try:
            engine.run_watches(state, settings, _alert)
        except Exception as e:
            log.exception("async scan failed: %s", e)
        finally:
            _scan_lock.release()
    threading.Thread(target=job, daemon=True).start()


class ChatIn(BaseModel):
    message: str


@app.post("/api/chat")
def chat(inp: ChatIn) -> dict:
    body = (inp.message or "").strip()
    state.chat_log.append({"role": "user", "text": body,
                           "at": datetime.now().isoformat(timespec="seconds")})
    low = body.lower()
    default_account = next(iter(settings.accounts), "")

    if low in ("רשימה", "list", "status", "מה אתה מחפש"):
        if not state.watches:
            _bot_say("אין כרגע מעקבים פעילים.")
        else:
            lines = [f"{i+1}. {request_parse.describe(w)}" for i, w in enumerate(state.watches)]
            _bot_say("מעקבים פעילים:\n" + "\n".join(lines))
        return _state_dict()

    if low.startswith(("בטל", "cancel", "עצור", "stop")):
        n = len(state.watches)
        state.watches.clear()
        _bot_say(f"בוטלו {n} מעקבים." if n else "אין מעקבים לבטל.")
        return _state_dict()

    watch_id = datetime.now().strftime("%Y%m%d%H%M%S")
    w, problems = request_parse.parse_message(
        body, watch_id=watch_id, account=default_account,
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    if w is None:
        _bot_say("לא הצלחתי להבין:\n- " + "\n- ".join(problems))
        return _state_dict()

    routed = settings.account_for_patient(w.patient)
    if routed:
        w.account = routed
    elif len(settings.accounts) > 1:
        problems.append(f"לא ידוע לאיזה חשבון שייך '{w.patient}' — משתמש ב'{w.account}'.")
    state.add_watch(w)
    reply = request_parse.describe(w)
    if problems:
        reply += "\n(" + " ".join(problems) + ")"
    reply += "\nמתחיל לחפש עכשיו…"
    _bot_say(reply)
    _run_scan_async()   # start scanning immediately instead of waiting for the timer
    return _state_dict()


def _state_dict() -> dict:
    return {
        "watches": [request_parse.describe(w) for w in state.watches],
        "messages": state.chat_log[-50:],
        "dry_run": settings.dry_run,
    }


@app.get("/api/state")
def get_state() -> dict:
    return _state_dict()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


def _scan_job() -> None:
    log.info("scheduled scan starting")
    try:
        engine.run_watches(state, settings, _alert)
    except Exception as e:
        log.exception("scan job failed: %s", e)


def _keepalive_job() -> None:
    """Ping SyncSession for every account that has active watches, to keep the the provider
    session from idling out between 30-min scans."""
    from .provider_client import ProviderClient
    accounts = {w.account for w in state.watches}
    for acc in accounts:
        try:
            cookies = settings.cookies_for(acc)
        except Exception:
            continue
        with ProviderClient(cookies, base_url=settings.base_url, dry_run=True, throttle=0) as c:
            alive = c.keepalive()
        log.info("keepalive %s -> %s", acc, "alive" if alive else "DEAD")
        if not alive:
            _alert(f"🔑 פג תוקף הכניסה לחשבון '{acc}'. היכנסי שוב למערכת ועדכני התחברות.")


@app.on_event("startup")
def _start_scheduler() -> None:
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        sched = BackgroundScheduler(timezone="UTC")
        sched.add_job(_scan_job, "interval", minutes=settings.scan_interval_min,
                      id="scan", next_run_time=datetime.now())
        sched.add_job(_keepalive_job, "interval", minutes=settings.keepalive_min,
                      id="keepalive")
        sched.start()
        log.info("scheduler started: scan every %d min, keepalive every %d min",
                 settings.scan_interval_min, settings.keepalive_min)
    except Exception as e:
        log.warning("scheduler not started (%s) — API still works", e)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
