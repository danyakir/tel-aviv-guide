"""Wednesday 20:00 Asia/Jerusalem scheduler using APScheduler."""

from __future__ import annotations

import logging
from datetime import datetime

from pytz import timezone

from .main import run_once

logger = logging.getLogger("tel-aviv-agent")
ISRAEL = timezone("Asia/Jerusalem")


def start_scheduler() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched = BlockingScheduler(timezone=ISRAEL)

    @sched.scheduled_job(CronTrigger(day_of_week="wed", hour=20, minute=0))
    def _job():
        logger.info("Running scheduled Wednesday 20:00 Israel update")
        run_once(send_now=True)

    logger.info("Scheduler started — next run Wednesday 20:00 Asia/Jerusalem")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")


def next_run() -> datetime:
    from apscheduler.triggers.cron import CronTrigger

    trigger = CronTrigger(day_of_week="wed", hour=20, minute=0, timezone=ISRAEL)
    now = datetime.now(ISRAEL)
    return trigger.get_next_fire_time(None, now)