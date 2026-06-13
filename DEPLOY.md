# Deploying slot-hunt-bot to the cloud (run 24/7, no local terminal)

The bot is a single FastAPI service (chat UI + 30-min scheduler). Because it now
**auto-logs-in with stored credentials**, there is no manual cookie pasting — the host
just needs the secrets below. Any host that runs a Docker container or a Python web
service works. Render is the simplest free option; Fly.io / Railway also work.

## Secrets the host needs (Environment Variables)

Per account (uppercase the account name from `config.yaml`, e.g. `ADI`):

| Variable | Meaning |
|----------|---------|
| `APPT_ID_ADI` | ID number (ת"ז) |
| `APPT_USERCODE_ADI` | user code (קוד משתמש) |
| `APPT_PASSWORD_ADI` | password |

For Gmail alerts (optional but recommended):

| Variable | Meaning |
|----------|---------|
| `BOT_EMAIL` | the Gmail the bot sends from |
| `BOT_EMAIL_APP_PASSWORD` | Gmail App Password (needs 2-Step Verification) |
| `MY_EMAIL` | where alerts are sent |

> Set these in the host's dashboard (e.g. Render → Environment). Never commit them.

## Option A — Render (easiest, has a free tier)
1. Push this repo to GitHub.
2. Render → **New → Web Service** → connect the repo.
3. Render auto-detects the `Dockerfile`. (Or set Build = `pip install -r requirements.txt`,
   Start = `uvicorn appointment_bot.webapp:app --host 0.0.0.0 --port $PORT`, with
   `PYTHONPATH=src`.)
4. Add the environment variables above.
5. Deploy → open the service URL → you get the chat UI.
> Free Render web services sleep after inactivity; the internal 30-min scheduler won't
> run while asleep. For always-on scanning use a paid instance, Fly.io, or keep a cheap
> external pinger hitting the URL. (See "Always-on" below.)

## Option B — Fly.io (stays running on free allowance)
```bash
fly launch --no-deploy          # uses the Dockerfile; creates fly.toml
fly secrets set APPT_ID_ADI=... APPT_USERCODE_ADI=... APPT_PASSWORD_ADI=... \
                BOT_EMAIL=... BOT_EMAIL_APP_PASSWORD=... MY_EMAIL=...
fly volumes create data --size 1     # for /app/state persistence
fly deploy
```
Mount the volume at `/app/state` so active watches survive restarts.

## Option C — Docker anywhere
```bash
docker build -t slot-hunt-bot .
docker run -d -p 8000:8000 \
  -e APPT_ID_ADI=... -e APPT_USERCODE_ADI=... -e APPT_PASSWORD_ADI=... \
  -e BOT_EMAIL=... -e BOT_EMAIL_APP_PASSWORD=... -e MY_EMAIL=... \
  -v $(pwd)/state:/app/state \
  slot-hunt-bot
```

## Always-on note
The scheduler runs inside the web process, so the process must stay alive. On hosts that
sleep idle services, either upgrade to an always-on instance or have an uptime pinger
(e.g. UptimeRobot) hit the service URL every few minutes to keep it awake.

## Safety
- `dry_run: true` in `config.yaml` books nothing. Verify behavior first, then set it
  `false`.
- Credentials live only in the host's secret store, never in the repo.
