"""CLI / cron entry point: run one scan pass over the active watches.

Day-to-day use is via the web app (chat UI). This entry point exists for optional
GitHub Actions cron use or manual runs. Input (new watches) comes from the web chat,
not from here.
"""
from __future__ import annotations

import logging

from . import config, engine
from .notifier import EmailNotifier
from .state import State

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def run() -> None:
    settings = config.load()
    st = State.load(settings.state_path)
    notifier = EmailNotifier(settings.bot_email, settings.bot_email_password, settings.my_email)
    engine.run_watches(st, settings, notifier.send)


if __name__ == "__main__":
    run()
