# slot-hunt-bot 🩺⏰

Automatically watch a large Israeli HMO for an
**earlier appointment** than the one you already hold — for any family member — and grab
it the moment it appears. You drive it through a **web chat UI** in your browser; when a
slot is found the bot sends you a **Gmail alert**. Runs as a local FastAPI server (or on
any hosting that supports Python).

> ⚠️ **Personal / educational project.** This was built as a learning exercise and a
> portfolio piece — to explore reverse-engineering, async scheduling, HTML parsing and
> clean service architecture. It is intended for personal use against one's **own**
> account, **not** as a production service, and is **not affiliated with or endorsed by
> the provider**. Use responsibly and in line with the provider's terms of use.

> 🤖 **Built with [Claude Code](https://claude.com/claude-code)** (Anthropic's agentic
> coding tool) in a **structured, plan-driven workflow**: the design, the live
> reverse-engineering findings, architectural decisions and test results were tracked
> and iterated in **[PLAN.md](PLAN.md)** as the project evolved. See
> [How it was built](#how-it-was-built).

---

## Table of contents
- [Goals](#goals)
- [Features](#features)
- [How it works](#how-it-works)
- [Booking policy](#booking-policy)
- [Project structure](#project-structure)
- [Quick start (local)](#quick-start-local)
- [Going live](#going-live)
- [Using it (web chat commands)](#using-it-web-chat-commands)
- [Status](#status)
- [How it was built](#how-it-was-built)
- [Roadmap](#roadmap)
- [Security & privacy](#security--privacy)
- [Disclaimer](#disclaimer)

---

## Goals

Appointments for popular doctors are often weeks out, but earlier slots free up
constantly as others cancel — you just can't sit and refresh the site all day. This bot:

1. **Monitors continuously** for earlier slots matching your constraints (doctor type,
   city/area, weekdays, hours), per family member.
2. **Grabs them automatically** when there's no risk, or **asks first** when there is.
3. **Is controlled from your browser** in natural language — no config files to edit for
   day-to-day use; requests are dynamic and change with your needs and urgency.
4. **Emails you** when a slot is found, so you don't need to leave the tab open.

## Features

- 🔎 **Earlier-slot detection** across paginated search results (all doctors, not just
  page 1).
- 🗣️ **Dynamic, free-text requests via web chat** — e.g. *"רופא משפחה לשם-בן-משפחה
  ברעננה בבוקר, דחוף"*. No fixed preferences file.
- 👨‍👩‍👧‍👦 **Multi-patient & multi-account** — any family member defined in `config.yaml`.
  Requests route to the right person's session automatically.
- 🤖 **Smart booking policy** — auto-book when safe, human-confirm when replacing an
  existing appointment.
- 📧 **Gmail alerts** (push) — receive an email the moment a slot is found or booked.
- 🧪 **Dry-run by default** — books nothing until you explicitly enable it.
- ⏱️ **Polite throttling** so polling never looks abusive.
- 🔄 **Built-in scheduler** — APScheduler runs a scan every 30 minutes inside the server
  process; no external cron needed.

## How it works

```
   You (web chat UI in browser, free text)
            │
            ▼
   ┌────────┴──────────────────  FastAPI server  ──────────────────────────┐
   │  POST /api/chat → parse free text → create/cancel Watch              │
   │  Background scheduler (every 30 min):                                │
   │     for each active Watch:                                            │
   │        authenticate → search the provider's appointment API →        │
   │        does a slot match your constraints AND beat your current one? │
   │        → (book it)                                                   │
   │     apply booking policy → show result in chat + send Gmail alert    │
   └────────────────────────────────────────────────────────────────────────
```

**Authentication: the bot logs in by itself** with stored credentials whenever needed, so
short provider sessions don't matter. (A pasted session cookie also works as a fallback.)
The design and engineering approach are described in **[PLAN.md](PLAN.md)**;
provider-specific operational details are intentionally kept out of this public repo.

## Booking policy

| Situation | Action |
|-----------|--------|
| **No existing appointment** | Auto-book the first matching slot, then notify via chat + Gmail ✅ |
| **You already have an appointment** | **Never** auto-replace. Show an **urgent message** in chat + Gmail with the earlier option and wait for your confirmation ⚡ |

Rationale: replacing = cancel + rebook. If the cancel succeeds but the rebook fails you'd
be left with nothing, so replacement is always human-approved.

## Project structure

```
src/appointment_bot/
  provider_client.py   # the provider appointment API: session reuse, search (paginated), book
  html_parse.py      # parse the windows-1255 HTML fragments the provider returns
  request_parse.py   # free-text Hebrew → Watch (rule-based; optional Claude upgrade)
  notifier.py        # outbound Gmail alerts via smtplib (push-only; input is via chat)
  webapp.py          # FastAPI server: chat UI + /api/chat + APScheduler
  state.py           # active watches + chat log, persisted across runs
  models.py          # Diary / Slot / Watch
  config.py          # non-secret config + secrets from env + specialty codes
  main.py            # CLI entry point for manual / cron runs (optional)
  static/            # chat UI frontend (served by FastAPI)
tests/               # unit tests, verified against REAL captured API responses
  fixtures/          # real (de-identified) API responses
config.yaml          # dry_run / auto_book / accounts (non-secret)
PLAN.md              # design + reverse-engineering notes
captures/            # raw HAR captures (git-ignored — contains cookies)
```

## Quick start (local)

```bash
pip install -r requirements.txt

# run the tests (no secrets needed — they run against captured fixtures)
PYTHONPATH=src python -m pytest tests/ -q

# start the web server (chat UI on http://localhost:8000)
PYTHONPATH=src uvicorn appointment_bot.webapp:app --reload
```

## Going live

### 1. Store your provider credentials (auto-login)
The bot **logs in by itself** — the provider's password login needs no CAPTCHA and no OTP
(verified). So you just store the credentials once per account as environment variables
(uppercase the account name from `config.yaml`, e.g. `ADI`):

| Variable | Value |
|----------|-------|
| `APPT_ID_ADI` | ID number (ת"ז) |
| `APPT_USERCODE_ADI` | user code (קוד משתמש) |
| `APPT_PASSWORD_ADI` | password |

Locally you can put these in a git-ignored `.env` (or `secrets.env`) file — the bot loads
it automatically. A pasted cookie (`APPT_COOKIES_ADI`) still works as a fallback.

> Because the bot re-authenticates itself, the short (~20-min) provider session lifetime no
> longer matters — see PLAN.md for the full investigation.

### 2. Set up Gmail alerts

Create a dedicated Gmail account for the bot (or use an existing one), then generate an
**App Password** (requires 2-Step Verification enabled):  
Google Account → Security → 2-Step Verification → App passwords → create one named
`slot-hunt-bot`.

Set three environment variables:

| Variable | Value |
|----------|-------|
| `BOT_EMAIL` | the Gmail address the bot sends from |
| `BOT_EMAIL_APP_PASSWORD` | the 16-character App Password |
| `MY_EMAIL` | your personal address that receives alerts |

### 3. Configure & flip to live
1. In `config.yaml`, list your `accounts` (one per patient) with each `patient` Hebrew
   name so requests route correctly.
2. Keep `dry_run: true` for the first runs and verify in the server logs that the bot
   finds slots and reports the correct one it *would* book.
3. Only then set `dry_run: false` (and `auto_book: true`).

## Using it (web chat commands)

Open `http://localhost:8000` and type a message:

| You send | The bot does |
|----------|--------------|
| `רופא משפחה ל<שם בן משפחה> ברעננה בבוקר, דחוף` | opens a watch for that family member, family doctor, Raanana, mornings, high priority |
| `תור ל<שם בן משפחה> בהרצליה או כפר סבא, ימים ראשון שלישי, אחרי 16:00` | opens a watch with those constraints |
| `רשימה` | lists active watches |
| `בטל` | cancels all active watches |

When a matching earlier slot is found, the result appears in the chat **and** an email
alert is sent to `MY_EMAIL` (either an auto-book confirmation or a request to approve a
replacement).

## Status

**Core proven against the live provider; one integration detail remains before full
unattended use.**

| Component | State |
|-----------|-------|
| **Automated self-login** | ✅ verified live |
| Provider API client (search + paginate + slots + book) | ✅ built; search/slots verified live |
| HTML parser | ✅ built & verified against real (de-identified) responses |
| Free-text Hebrew request parsing | ✅ built (first specialty confirmed) |
| Web chat UI (FastAPI + static HTML) | ✅ built & smoke-tested |
| Gmail alerts (smtplib + App Password) | ✅ built |
| Orchestration + booking policy + dry-run | ✅ built & smoke-tested |
| Scheduler (interval scan + keepalive) | ✅ built |
| Server-side context after programmatic login | ⏳ a transient provider-side error sometimes occurs right after auto-login; under investigation |
| Specialty catalog beyond the first | ⏳ to be expanded |

Session lifetime is short by design, but that's mooted because the bot re-logs-in itself.

## Deployment

Run it 24/7 in the cloud (no local terminal) — see **[DEPLOY.md](DEPLOY.md)**. Because the
bot auto-logs-in, hosting only needs the 3 credentials (+ optional Gmail) as secrets; no
manual cookie pasting. A `Dockerfile` is included; Render / Fly.io / Railway all work.

## Roadmap

- **v1 (now):** automated self-login, web chat control, earlier-slot detection +
  auto-book/confirm, Gmail push alerts.
- **v2:** multi-patient from a single login; month-at-a-glance availability scan (fewer
  requests); optional LLM parsing for messier requests; richer specialty catalog.

## Security & privacy

- Secrets (cookies, Gmail App Password) live **only** in environment variables — never
  in code or config files.
- `.gitignore` blocks `captures/`, cookie files, and `.env`. HAR captures contain your
  real cookies — keep them local, never share or commit them.
- `dry_run: true` is the default; the bot books nothing until you opt in.

## Disclaimer

This tool automates **your own** the provider account to find an earlier appointment **for you
and your family**. Use it gently (30-minute polling, single account) and in accordance
with the provider's terms of use. It is a personal-use project and is not affiliated with or
endorsed by the provider.
