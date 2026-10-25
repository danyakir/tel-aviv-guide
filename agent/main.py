"""CLI entry point for the Tel Aviv Weekend Agent.

Usage:
    python -m agent.main --send-now    # scrape + email this weekend's guide immediately
    python -m agent.main --schedule    # keep running, send every Wednesday 20:00 Israel time
    python -m agent.main --preview     # print the guide to stdout
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta

from pytz import timezone

from .email import EmailSender
from .report import build_message, build_sample_message, build_plain_text
from .scraper import fetch_weekend_events

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("tel-aviv-agent")
ISRAEL = timezone("Asia/Jerusalem")


def _upcoming_weekend(now: datetime | None = None) -> tuple[datetime, datetime, datetime, datetime]:
    """Return (Thu evening, Fri, Sat, Sun) of the coming weekend relative to now (Israel time)."""
    if now is None:
        now = datetime.now(ISRAEL)
    days_until_thu = (3 - now.weekday()) % 7
    if days_until_thu == 0 and now.hour >= 20:
        days_until_thu = 7
    thu = (now + timedelta(days=days_until_thu)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    fri = thu + timedelta(days=1)
    sat = thu + timedelta(days=2)
    sun = thu + timedelta(days=3)
    return thu, fri, sat, sun


def run_once(send_now: bool = False) -> str:
    now = datetime.now(ISRAEL)
    thu, fri, sat, sun = _upcoming_weekend(now)
    log.info("Fetching events for Thu %s – Sat %s", thu.date(), sat.date())

    events = fetch_weekend_events(thu, fri, sat, sun)
    log.info("Found %d events after filtering", len(events))

    if events:
        html_body = build_message(events, thu, fri, sat, sun)
        text_body = build_plain_text(events, thu, fri, sat, sun)
    else:
        log.info("No live events returned")
        html_body = build_message([], thu, fri, sat, sun)
        text_body = build_plain_text([], thu, fri, sat, sun)

    subject = f"Weekend in Tel Aviv — {thu:%d %b}–{sat:%d %b %Y}"

    if send_now:
        sender = EmailSender()
        if not sender.is_configured():
            raise RuntimeError('Email configuration is missing; no guide was sent.')
        if not events:
            raise RuntimeError('No matching events were returned; no empty guide was sent.')
        sender.send(subject, html_body, text_body)

    return text_body


def main() -> None:
    parser = argparse.ArgumentParser(description="Tel Aviv Weekend Agent")
    parser.add_argument("--send-now", action="store_true", help="Send this weekend's guide immediately")
    parser.add_argument("--schedule", action="store_true", help="Run Wednesday 20:00 Israel scheduler")
    parser.add_argument("--preview", action="store_true", help="Print the guide to stdout without sending")
    args = parser.parse_args()

    if args.schedule:
        from .scheduler import start_scheduler
        start_scheduler()
        return

    if not args.send_now and not args.preview:
        parser.print_help()
        return

    body = run_once(send_now=args.send_now)

    if args.preview or args.send_now:
        print("\n===== WEEKEND GUIDE =====\n")
        print(body)
        print("\n=========================\n")


if __name__ == "__main__":
    main()
