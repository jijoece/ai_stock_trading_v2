"""PR 14 scratch: exercises APScheduler v3's stateless trigger classes
directly, without ever starting a `BlockingScheduler`/`AsyncIOScheduler`
loop, to test the narrow "coexist" use case `MASTER_PLAN.md` row 14
describes (due-time computation only). Also demonstrates the two concrete
gaps a bare `CronTrigger` has relative to this repository's existing
`shadow/schedule.py::resolve_due_status` / `paper_books/recurring_scheduler.py
::calculate_due_slot`: no exchange-calendar (XNYS holiday/early-close)
awareness, and no catch-up-window (missed-but-recoverable vs. too-old)
concept. Prints results for the PR 14 evaluation record.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

zone = ZoneInfo("America/New_York")

# 1. Stateless usage IS possible: get_next_fire_time never touches a
#    scheduler/executor/jobstore, so calling it does not require running
#    BlockingScheduler at all.
trigger = CronTrigger(hour=9, minute=30, day_of_week="mon-fri", timezone=zone)
now = datetime(2026, 8, 28, 9, 0, tzinfo=zone)  # a Friday
next_fire = trigger.get_next_fire_time(None, now)
print("stateless next_fire (Friday 09:00 -> next 09:30 weekday slot):", next_fire)

# 2. Gap: day_of_week="mon-fri" cannot express "not a US market holiday" --
#    2026-09-07 is Labor Day (a Monday), a real NYSE closure, but CronTrigger
#    has no concept of it and fires anyway.
labor_day_probe = datetime(2026, 9, 4, 9, 31, tzinfo=zone)  # just after Friday's slot
next_after_labor_day_probe = trigger.get_next_fire_time(None, labor_day_probe)
print("next fire after 2026-09-04 09:31 (expect it to (wrongly) pick Labor Day Monday):",
      next_after_labor_day_probe)
print("is that date actually a NYSE trading day? No -- 2026-09-07 is Labor Day. "
      "CronTrigger has no exchange-calendar awareness; this would require a custom "
      "Trigger subclass consulting exchange_calendars, i.e. the same custom code this "
      "repository already has in market_calendar.py, not a reduction.")

# 3. Gap: no catch-up/too-old concept. IntervalTrigger/CronTrigger only ever
#    answer "what is the next fire time after X" -- they have no idea whether
#    a *past* due slot was already missed-but-recoverable or missed-too-old;
#    that state lives only in this repository's own persisted
#    shadow_scheduler_runs / paper_recurring_scheduler_runs tables, exactly as
#    resolve_due_status/calculate_due_slot already implement by hand.
interval = IntervalTrigger(hours=24)
print("IntervalTrigger has no persisted-state / catch-up-window API at all -- "
      "confirmed via apscheduler.triggers.interval.IntervalTrigger's public surface:",
      [name for name in dir(interval) if not name.startswith("_")])
