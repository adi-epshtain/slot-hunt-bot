"""Configuration & secrets.

Secrets come from environment variables (GitHub Actions Secrets locally / in CI).
Non-secret tunables live in config.yaml. Dynamic per-request preferences do NOT live
here — they arrive as free-text WhatsApp messages and become Watches at runtime.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Known the provider specialization codes (SelectedSpecializationCode in SearchDiaries).
# Confirmed from capture: 62 = family doctor. Others are placeholders — fill in by
# capturing a search for each type (read SelectedSpecializationCode from the request).
SPECIALIZATION_CODES: dict[str, str] = {
    "family": "62",        # רופא משפחה  (CONFIRMED)
    "רופא משפחה": "62",
    # TODO(capture): derma/eyes/ortho/... add real codes once captured.
}


@dataclass
class AccountCfg:
    name: str
    cookies_env: str           # env var name holding this account's session cookies
    patient: str = ""          # Hebrew patient name this session is scoped to (v1)
    person_index: int = 0      # FamilySlider index (0=owner, 1/2=other family members)


@dataclass
class Settings:
    dry_run: bool = True       # ⚠️ never books while True
    auto_book: bool = True     # auto-book when no existing appointment
    poll_days_ahead: int = 30  # how many days forward to scan for earlier slots
    scan_interval_min: int = 30
    keepalive_min: int = 8      # ping SyncSession this often to keep the session alive
    accounts: dict[str, AccountCfg] = field(default_factory=dict)
    base_url: str = "__PROVIDER_BASE_URL__"
    state_path: str = "state/watches.json"

    # secrets (env) — e-mail is OUTPUT only (push alerts); input is via the web chat
    bot_email: str = ""
    bot_email_password: str = ""     # Gmail App Password
    my_email: str = ""               # where alerts are sent

    def account_for_patient(self, patient: str) -> str | None:
        """Map a (Hebrew) patient name to the account whose session is scoped to them."""
        p = patient.strip()
        for acc in self.accounts.values():
            if acc.patient and acc.patient.strip() == p:
                return acc.name
        # fall back to a case-insensitive match on the account name itself
        for name in self.accounts:
            if name.strip().lower() == p.lower():
                return name
        return None

    def cookies_for(self, account: str) -> str:
        acc = self.accounts.get(account)
        if acc is None:
            raise KeyError(f"unknown account '{account}'")
        return os.environ.get(acc.cookies_env, "")

    def creds_for(self, account: str):
        """Return (id, user_code, password) for auto-login, or None if not configured.

        Env: APPT_ID_<ACCOUNT>, APPT_USERCODE_<ACCOUNT>, APPT_PASSWORD_<ACCOUNT>
        (uppercased account name).
        """
        up = account.upper()
        uid = os.environ.get(f"APPT_ID_{up}", "")
        code = os.environ.get(f"APPT_USERCODE_{up}", "")
        pw = os.environ.get(f"APPT_PASSWORD_{up}", "")
        if uid and code and pw:
            return (uid, code, pw)
        return None


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from local gitignored env files into os.environ (if present),
    so local runs pick up secrets without exporting them by hand. Real values never get
    committed (these files are in .gitignore)."""
    for path in (".env", "secrets.env", "captures/_creds.env"):
        f = Path(path)
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _real(v):
    """Ignore the committed placeholder base_url."""
    return v if v and v != "__PROVIDER_BASE_URL__" else None


def load(config_path: str = "config.yaml") -> Settings:
    _load_dotenv()
    p = Path(config_path)
    data: dict = {}
    if p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    accounts: dict[str, AccountCfg] = {}
    for acc in data.get("accounts", []) or []:
        accounts[acc["name"]] = AccountCfg(
            name=acc["name"],
            cookies_env=acc.get("cookies_secret", f"APPT_COOKIES_{acc['name'].upper()}"),
            patient=acc.get("patient", ""),
            person_index=int(acc.get("person_index", 0)),
        )

    s = Settings(
        dry_run=bool(data.get("dry_run", True)),
        auto_book=bool(data.get("auto_book", True)),
        poll_days_ahead=int(data.get("poll_days_ahead", 30)),
        accounts=accounts,
        base_url=(os.environ.get("PROVIDER_BASE_URL")
                  or _real(data.get("base_url"))
                  or "__PROVIDER_BASE_URL__"),
        state_path=data.get("state_path", "state/watches.json"),
        scan_interval_min=int(data.get("scan_interval_min", 30)),
        keepalive_min=int(data.get("keepalive_min", 8)),
        bot_email=os.environ.get("BOT_EMAIL", ""),
        bot_email_password=os.environ.get("BOT_EMAIL_APP_PASSWORD", ""),
        my_email=os.environ.get("MY_EMAIL", ""),
    )
    return s


def specialization_code(name: str) -> str | None:
    return SPECIALIZATION_CODES.get(name.strip().lower()) or SPECIALIZATION_CODES.get(name.strip())
