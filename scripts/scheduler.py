# scripts/scheduler.py
# ---------------------------------------------------------------
# Unified scheduler for all machines. Replaces scheduler_central.py
# and scheduler_scraper.py.
#
# One script. Which jobs this process runs is ASSIGNED, per job, by
# the `machine` field in config/schedules.yaml: a job registers where
# that field names this machine_id and nowhere else. The machine_config
# flags (bloomberg_enabled, scraper_enabled, fms_enabled) are
# capabilities only — "this machine can", never "this machine should" —
# so enabling one to run a pipeline by hand schedules nothing. An
# assigned job whose capability is missing stops the scheduler at start
# (JOBS below pairs each job with the capability it needs).
# Fire times come from the same YAML — no cron values here.
#
# Two kinds of job:
#   - fixed-time: the data is known to exist by the fire time
#     (prices_bloomberg after NYSE close, acquire_sbs with an operator).
#     One slot a day.
#   - polling: the data appears at a variable hour. The YAML gives a
#     cron RANGE (hour: "9-11", minute: "0,30") and the job fires at
#     every slot; _gated() decides per slot:
#       done   job_runs already holds a success with a business date
#              for this job today                       -> skip, no row
#       gate   optional `ready()` probe of the SOURCE says no
#                before `deadline`                       -> skip, no row
#                at/after `deadline`                     -> error row
#       else   run (tracked as usual)
#     Jobs return None when they found nothing to load (SPP: the SBS
#     has not published yet) so the day stays open for the next slot.
#
# Observability (both tables in src/db/schema/37, 38):
#   - every job is wrapped by _tracked(): a job_runs row is inserted
#     at start ('running') and updated at end ('success'/'error').
#     Tracking failures never block the job itself.
#   - a heartbeat job upserts scheduler_heartbeat every 5 minutes so
#     "is the scheduler alive on machine X" is a SQL query.
#
# Daily chain (see schedules.yaml for the windows):
#   acquire_sbs -> ingest_sbs (when the files are on the share) ->
#   positions_fms (when every fund has a CierreIDI) ->
#   acquire_spp (afternoon slots until the book holds t-2) ->
#   prices_bloomberg (16:30 America/New_York, DST-proof).
#
# Deployment: no admin rights, so no service. Register a user-level
# Windows Task Scheduler task per machine that launches
# scripts/bat/scheduler.bat at log on with restart-on-failure — see
# docs/SCHEDULER.md.
#
# Usage:
#   python scripts/scheduler.py
# ---------------------------------------------------------------

import functools
import logging
import sys
import threading
import time
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.configs.machine_config import (
    bloomberg_enabled,
    fms_enabled,
    machine_id,
    scraper_enabled,
    timezone,
)
from src.shared.config_loader import jobs_for_machine, load_schedules
from src.shared.logging import setup_logging

setup_logging("scheduler")
logger = logging.getLogger(__name__)

TZ = timezone()
MACHINE = machine_id()
scheduler = BlockingScheduler(timezone=TZ)

_STARTED_AT: Optional[object] = None  # set in __main__, read by heartbeat


# ---- Job-run tracking ------------------------------------------

def _record_start(job_id: str) -> Optional[int]:
    """Inserts a 'running' job_runs row. Returns its id, or None if
    the DB is unreachable — tracking must never block the job."""
    try:
        from src.db.connection import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO job_runs (job_id, machine_id) "
                "VALUES (%s, %s) RETURNING job_run_id",
                (job_id, MACHINE),
            )
            return cur.fetchone()["job_run_id"]
    except Exception as e:
        logger.warning(f"job_runs start tracking failed for {job_id}: {e}")
        return None


def _record_end(
    job_run_id: Optional[int],
    status: str,
    run_date: Optional[date] = None,
    error: Optional[str] = None,
) -> None:
    if job_run_id is None:
        return
    try:
        from src.db.connection import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE job_runs SET status = %s, run_date = %s, "
                "finished_at = now(), error = %s WHERE job_run_id = %s",
                (status, run_date, error, job_run_id),
            )
    except Exception as e:
        logger.warning(f"job_runs end tracking failed for run {job_run_id}: {e}")


def _tracked(job_id: str, fn: Callable[[], Optional[date]]) -> Callable[[], None]:
    """Wraps a job so every execution lands in job_runs. The job
    returns the business date it processed, or None when it has no
    single business date or found nothing to load yet (a polling job
    returning None keeps the day open for its next slot); exceptions
    are recorded and re-raised so APScheduler logs the failure too."""
    @functools.wraps(fn)
    def wrapper() -> None:
        job_run_id = _record_start(job_id)
        try:
            run_date = fn()
        except Exception as e:
            _record_end(job_run_id, "error", error=f"{type(e).__name__}: {e}")
            raise
        _record_end(job_run_id, "success", run_date=run_date)
    return wrapper


def job_heartbeat() -> None:
    """Upserts this machine's scheduler_heartbeat row. Untracked —
    it IS the tracking."""
    try:
        from src.db.connection import get_connection
        jobs = ",".join(sorted(j.id for j in scheduler.get_jobs() if j.id != "heartbeat"))
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO scheduler_heartbeat "
                "(machine_id, started_at, last_seen, registered_jobs) "
                "VALUES (%s, %s, now(), %s) "
                "ON CONFLICT (machine_id) DO UPDATE SET "
                "started_at = EXCLUDED.started_at, last_seen = now(), "
                "registered_jobs = EXCLUDED.registered_jobs",
                (MACHINE, _STARTED_AT, jobs),
            )
    except Exception as e:
        logger.warning(f"heartbeat upsert failed: {e}")


# ---- Slot gating (polling jobs) --------------------------------

def _done_today(job_id: str) -> bool:
    """True when job_runs already holds a success with a business date
    for this job on this machine today (machine timezone). Unreachable
    DB -> False: a repeated run is idempotent, a missed one is not."""
    try:
        from src.db.connection import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM job_runs "
                "WHERE job_id = %s AND machine_id = %s AND status = 'success' "
                "AND run_date IS NOT NULL "
                "AND (started_at AT TIME ZONE %s)::date = (now() AT TIME ZONE %s)::date "
                "LIMIT 1",
                (job_id, MACHINE, TZ, TZ),
            )
            return cur.fetchone() is not None
    except Exception as e:
        logger.warning(f"done-check failed for {job_id}: {e}")
        return False


def _gated(
    job_id: str,
    fn: Callable[[], Optional[date]],
    ready: Optional[Callable[[], bool]],
    deadline: Optional[dtime],
    tz: ZoneInfo,
) -> Callable[[], None]:
    """Per-slot decision for polling jobs (see banner). A probe that
    raises counts as not ready: the deadline turns a persistently
    unreachable source into one error row instead of one per slot."""
    tracked = _tracked(job_id, fn)

    def not_ready() -> None:
        raise RuntimeError(f"{job_id}: source not ready by deadline {deadline:%H:%M}")

    @functools.wraps(fn)
    def wrapper() -> None:
        if _done_today(job_id):
            logger.info(f"{job_id}: already done today, slot skipped")
            return
        if ready is not None:
            try:
                ok = ready()
            except Exception as e:
                logger.warning(f"{job_id}: readiness probe failed: {e}")
                ok = False
            if not ok:
                if deadline is not None and datetime.now(tz).time() >= deadline:
                    _tracked(job_id, not_ready)()  # error row, then raises
                logger.info(f"{job_id}: source not ready, slot skipped")
                return
        tracked()
    return wrapper


def _sbs_files_ready() -> bool:
    """Gate for ingest_sbs: every SBS file for the previous reporting
    day is on the network share (acquisition may run on another machine)."""
    from src.calendars.calendar_sbs import prev_reporting_day
    from src.scrapers.sbs import all_files_present
    return all_files_present(prev_reporting_day(date.today()))


def _fms_closed() -> bool:
    """Gate for positions_fms: accounting has closed the previous
    reporting day for every active FMS fund. A CierreIDI row with
    FlgHistorico = 0 exists only once the fund's day is signed off, so
    the probe cannot open on a half-written day. holdings.sql inner-joins
    the same table: before the close it returns zero rows."""
    from src.calendars.calendar_sbs import prev_reporting_day
    from src.db.connection import get_connection
    from src.vendors.fms import get_fms_connection

    run_date = prev_reporting_day(date.today())
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM dim_portfolio "
                    "WHERE source = 'fms' AND status = 'active'")
        expected = cur.fetchone()["n"]
    with get_fms_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(DISTINCT i.IdFondoPension) "
            "FROM FMS.CierreIDI ci JOIN FMS.IDI i ON i.IdIDI = ci.IdIDI "
            "WHERE i.IdSecuencialFechaIDI = ? AND ci.FlgHistorico = 0",
            int(run_date.strftime("%Y%m%d")),
        )
        closed = cur.fetchone()[0]
    logger.info(f"fms close probe {run_date}: {closed}/{expected} funds closed")
    return expected > 0 and closed >= expected


# ---- Beep helper (SBS image-pad login alert) -------------------

def _beep_until_stopped(stop_event: threading.Event) -> None:
    """Repeating beep until stop_event is set. Daemon thread — dies
    with the scheduler process. Checks the event every 100ms so it
    stops promptly when acquisition completes."""
    import winsound
    while not stop_event.is_set():
        winsound.Beep(880, 300)
        for _ in range(7):
            if stop_event.is_set():
                return
            time.sleep(0.1)


# ---- Job definitions -------------------------------------------

def job_prices_bloomberg() -> date:
    """Daily Bloomberg prices. run() itself skips NYSE holidays in
    incremental mode. Fires 16:30 America/New_York, so today's Lima
    date equals today's New York date."""
    logger.info("--- job: prices bloomberg ---")
    from src.pipelines.prices.bloomberg.run import run
    run_date = date.today()
    run(run_date=run_date)
    return run_date


def job_dim_enrichment() -> None:
    """Weekly Bloomberg dim enrichment, 'new' mode only: securities never
    asked about plus unrecognised ones past the retry window. Refreshing
    already-enriched names is on demand (run_dim_enrichment.py --refresh).
    No calendar guard — runs Monday morning regardless of exchange."""
    logger.info("--- job: dim enrichment bloomberg (new) ---")
    from src.db.bootstrap import run_dim_enrichment
    run_dim_enrichment(vendor="bloomberg", domain="security", mode="new")
    return None


def job_acquire_sbs() -> Optional[date]:
    """Semi-automated SBS acquisition for the previous reporting day
    (Monday -> Friday, day after holiday -> last open day). Beeps
    until the operator completes the image-pad login. Files land on
    the network share for ingest_sbs."""
    from src.calendars.calendar_sbs import prev_reporting_day
    from src.scrapers.sbs import acquire_day, all_files_present

    run_date = prev_reporting_day(date.today())
    logger.info(f"--- job: acquire sbs | reporting day={run_date} ---")

    if all_files_present(run_date):
        logger.info(f"All SBS files already present for {run_date}. Skipping acquisition.")
        return run_date

    stop_event = threading.Event()
    beep_thread = threading.Thread(
        target=_beep_until_stopped,
        args=(stop_event,),
        daemon=True,
        name="sbs_beep",
    )
    beep_thread.start()
    logger.info("Beep started - browser opening, image pad login required.")

    try:
        results = acquire_day(run_date=run_date)
    finally:
        stop_event.set()
        beep_thread.join(timeout=1)
        logger.info("Beep stopped.")

    failed = [name for name, ok in results.items() if not ok]
    if failed:
        raise RuntimeError(
            f"SBS acquisition failed for {failed} on {run_date}. "
            f"Re-run manually: python scripts/acquire/acquire_sbs.py --date {run_date}"
        )
    logger.info(f"SBS acquisition complete: {len(results)} files for {run_date}.")
    return run_date


def job_acquire_spp() -> Optional[date]:
    """SPP valor cuota from the public SBS portal, through a visible
    Chrome (the Imperva WAF blocks headless browsers). The SBS publishes
    t-2 reporting days during the afternoon with no fixed hour, so this
    is a polling job: a cron range in schedules.yaml, one Chrome launch
    per slot until the day is in the book. No source gate exists (the
    WAF blocks anything lighter than Chrome), so the scrape IS the probe;
    run_scheduled returns None when the day was not published yet and
    _gated() skips the remaining slots once it was. The first run on a
    fresh browser profile needs an operator to solve the WAF challenge
    once (docs/SCHEDULER.md)."""
    from src.pipelines.prices.sbs.valor_cuota.run import run_scheduled

    logger.info("--- job: acquire spp ---")
    return run_scheduled()


def job_ingest_sbs() -> date:
    """Runs every SBS price sub-pipeline (shared registry, includes
    dividendos) for the previous reporting day. Each sub-pipeline
    gates itself on the reporting calendar."""
    from src.calendars.calendar_sbs import prev_reporting_day
    from src.pipelines.prices.sbs.registry import run_sbs_pipelines

    run_date = prev_reporting_day(date.today())
    logger.info(f"--- job: ingest sbs | reporting day={run_date} ---")
    run_sbs_pipelines(run_date=run_date)
    return run_date


def job_positions_fms() -> date:
    """Daily FMS positions: holdings, cash, net receivables, forwards, then
    the derived contribution fact, sequentially, all for the previous NYSE-or-XLIM business day
    (same union as the SBS reporting calendar). Feeds are idempotent,
    so one failing does not stop the rest; any failure is re-raised
    at the end so the job lands in job_runs as 'error'."""
    from src.calendars.calendar_sbs import prev_reporting_day
    from src.pipelines.positions.fms.cash.run import run_full as run_cash
    from src.pipelines.positions.fms.forwards.run import run_full as run_forwards
    from src.pipelines.positions.fms.holdings.run import run_full as run_holdings
    from src.pipelines.positions.fms.net_receivables.run import run_full as run_net_receivables
    from src.pipelines.analytics.contribution.run import run_full as run_contribution

    run_date = prev_reporting_day(date.today())
    logger.info(f"--- job: positions fms | date={run_date} ---")

    feeds: list[tuple[str, Callable]] = [
        ("holdings", run_holdings),
        ("cash", run_cash),
        ("net_receivables", run_net_receivables),
        ("forwards", run_forwards),
        ("contribution", run_contribution),   # derived from holdings + valuation
    ]
    failures: list[str] = []
    for name, run_full in feeds:
        logger.info(f"--- fms feed: {name} ---")
        try:
            run_full(start_date=run_date, end_date=run_date)
        except Exception:
            logger.exception(f"fms feed {name} failed for {run_date}")
            failures.append(name)

    if failures:
        raise RuntimeError(f"FMS feeds failed for {run_date}: {failures}")
    return run_date


# ---- Registration ----------------------------------------------

def _register(
    job_id: str,
    fn: Callable[[], Optional[date]],
    name: str,
    cfg: dict,
    ready: Optional[Callable[[], bool]] = None,
) -> None:
    """hour/minute may be cron expressions ("9-11", "0,30"): the job then
    fires at every slot of the range and _gated() decides per slot.
    `deadline` ("HH:MM", quoted, job timezone) is when a still-closed
    gate becomes an error row; set it to the last slot of the range."""
    tz = ZoneInfo(cfg.get("timezone", TZ))
    deadline = dtime.fromisoformat(cfg["deadline"]) if "deadline" in cfg else None
    scheduler.add_job(
        _gated(job_id, fn, ready, deadline, tz),
        CronTrigger(
            day_of_week=cfg["day_of_week"],
            hour=cfg["hour"],
            minute=cfg["minute"],
            timezone=tz,
        ),
        id=job_id,
        name=name,
        misfire_grace_time=cfg.get("misfire_grace_time", 3600),
        coalesce=True,
        max_instances=1,
    )


# job_id -> (fn, display name, readiness gate, capability it needs).
# ingest_sbs needs none: any machine with the share and Postgres can run it.
JOBS: dict[str, tuple[Callable, str, Optional[Callable[[], bool]], Optional[Callable[[], bool]]]] = {
    "prices_bloomberg": (job_prices_bloomberg, "Bloomberg daily prices",   None,             bloomberg_enabled),
    "dim_enrichment":   (job_dim_enrichment,   "Bloomberg dim enrichment", None,             bloomberg_enabled),
    "acquire_sbs":      (job_acquire_sbs,      "SBS daily acquisition",    None,             scraper_enabled),
    "acquire_spp":      (job_acquire_spp,      "SPP valor cuota scrape",   None,             scraper_enabled),
    "ingest_sbs":       (job_ingest_sbs,       "SBS ingestion",            _sbs_files_ready, None),
    "positions_fms":    (job_positions_fms,    "FMS daily positions",      _fms_closed,      fms_enabled),
}


def register_jobs() -> None:
    """Registers the jobs schedules.yaml assigns to this machine. The
    YAML and JOBS must list the same ids (a typo on either side would
    otherwise drop a job without a word), and an assigned job whose
    capability flag is off fails here, not at its first fire time."""
    jobs_cfg = load_schedules()["jobs"]
    if set(jobs_cfg) != set(JOBS):
        raise KeyError(
            f"config/schedules.yaml and scheduler JOBS disagree on: "
            f"{sorted(set(jobs_cfg) ^ set(JOBS))}"
        )

    for job_id in jobs_for_machine(jobs_cfg, MACHINE):
        fn, name, ready, capable = JOBS[job_id]
        if capable is not None and not capable():
            raise RuntimeError(
                f"Job '{job_id}' is assigned to this machine ({MACHINE}) in "
                f"config/schedules.yaml but {capable.__name__} is false here."
            )
        _register(job_id, fn, name, jobs_cfg[job_id], ready=ready)

    scheduler.add_job(
        job_heartbeat,
        IntervalTrigger(minutes=5),
        id="heartbeat",
        name="Scheduler heartbeat",
        coalesce=True,
        max_instances=1,
    )


# ---- Entry point -----------------------------------------------

if __name__ == "__main__":
    from datetime import datetime, timezone as dt_timezone
    _STARTED_AT = datetime.now(dt_timezone.utc)

    register_jobs()

    registered = [j.id for j in scheduler.get_jobs()]
    logger.info("=== Scheduler started ===")
    logger.info(f"Machine:                {MACHINE}")
    logger.info(f"Timezone:               {TZ}")
    logger.info(f"bloomberg_enabled:      {bloomberg_enabled()}")
    logger.info(f"scraper_enabled:        {scraper_enabled()}")
    logger.info(f"fms_enabled:            {fms_enabled()}")
    logger.info(f"Registered jobs:        {registered}")

    if registered == ["heartbeat"]:
        logger.warning(
            f"No job in config/schedules.yaml is assigned to machine_id "
            f"'{MACHINE}' - only the heartbeat will run. Check the `machine` "
            f"fields there against this machine's MACHINE_ID / machine_id."
        )

    job_heartbeat()  # first heartbeat immediately, not after 5 min

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("=== Scheduler stopped ===")
        scheduler.shutdown()
