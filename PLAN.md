# Project Plan & Engineering Approach

> A personal/educational automation project: watch a healthcare provider's online portal
> for an **earlier appointment** than the one already booked, and surface it (or grab it)
> automatically. This document captures the design and the engineering approach. Operational
> specifics for any particular provider are intentionally omitted.

## Goal
Earlier appointment slots free up constantly as others cancel, but you can't refresh a
site all day. This bot watches continuously for slots matching your constraints (specialty,
area, weekday, hours) per family member, and notifies / books when one appears.

### Booking policy (a deliberate safety decision)
- **No existing appointment** → auto-book the first matching slot, then notify.
- **An existing appointment exists** → never auto-replace it; send an urgent notification
  and wait for human approval. Rationale: a replace = cancel + rebook; if the cancel
  succeeds but the rebook fails, you'd be left with nothing.

## Architecture
A single **FastAPI** service that does everything in one process:
1. Serves a **web chat UI** — the user types requests in natural language (Hebrew).
2. **API**: `POST /api/chat` (create/list/cancel a "watch"), `GET /api/state`.
3. An internal **APScheduler** job scans on an interval and keeps the session warm.
4. On a found slot → applies the booking policy, shows it in chat, and emails the user.

```
  Web chat UI ──► FastAPI ──► parse free text → Watch
                     │
                     └► scheduler (interval): for each Watch →
                          authenticate → search → match → (book) → notify (chat + email)
```

### Modules
| File | Responsibility |
|------|----------------|
| `provider_client.py` | HTTP client for the provider portal: self-login, search, book |
| `html_parse.py` | Parse the (windows-1255) HTML fragments the portal returns |
| `request_parse.py` | Free-text Hebrew → structured `Watch` (rule-based) |
| `notifier.py` | Outbound email alerts (SMTP) — push only |
| `state.py` | Active watches + chat log, persisted across runs |
| `engine.py` | Orchestration + booking policy |
| `webapp.py` | FastAPI app: chat UI + API + scheduler |
| `config.py`, `models.py` | Settings/secrets + core data types |

## Engineering approach (how this was built)
Built iteratively with **Claude Code** in a **plan-driven workflow** — each finding,
decision and dead-end was recorded and the design adjusted accordingly. Highlights:

- **Reverse-engineering from captured traffic.** Browser HAR captures were analyzed
  offline to understand the portal's request/response shapes, then a minimal HTTP client
  was written to reproduce only the needed calls (no browser automation at runtime).
- **Verify against real data.** The HTML parser is unit-tested against **real captured
  responses** (de-identified) — so parsing is proven, not assumed.
- **Honest investigation of hard constraints.** Session lifetime, auth, and anti-abuse
  behavior were measured empirically with small timed probes; conclusions (and a couple of
  wrong initial assumptions that were corrected) are documented in the working notes.
- **Be a good citizen.** Gentle polling and request throttling; the bot is for a single
  personal account, not scraping at scale.
- **Safety-first defaults.** `dry_run: true` books nothing until explicitly enabled;
  replacing an existing appointment always requires human confirmation.

## Status (high level)
- ✅ Self-authentication, search, slot parsing, scheduling, email alerts, web chat — built.
- ✅ Parser verified against real captured responses; core flow exercised live.
- ⏳ One integration detail remains around establishing the right server-side context
  after a programmatic login (the portal occasionally returns a transient error).
- ⏳ Specialty catalog beyond the first confirmed specialty — to be expanded.

## Roadmap
- Multi-patient from a single login.
- Month-at-a-glance availability scan (fewer requests).
- Optional LLM parsing for messier free-text requests.

## Security & privacy
- Credentials and any session data live only in local env / host secrets — **never** in
  the repo. Raw captures are git-ignored.
- This is a personal-use, educational project and is not affiliated with any provider.
