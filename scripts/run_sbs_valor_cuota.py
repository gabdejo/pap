# scripts/run_sbs_valor_cuota.py
# ---------------------------------------------------------------
# CLI entry point for the SPP valor cuota feed.
#
# Not part of scripts/run_prices.py on purpose: the other SBS feeds
# read files already on disk, while this one opens a VISIBLE Chrome
# window through the Imperva WAF - it must never launch as a side
# effect of "run all SBS". Schedule it as a Windows task with an
# interactive logon (scripts/schedule_sbs_valor_cuota.ps1): the WAF
# blocks headless browsers and locked sessions.
#
# Usage:
#   # Daily scrape (last 7 business days, three metrics)
#   python scripts/run_sbs_valor_cuota.py
#
#   # Restatement: re-scrape and replace figures already loaded
#   python scripts/run_sbs_valor_cuota.py --force
#
#   # Load a hand-downloaded SBS monthly XLS (valor cuota since 1993)
#   python scripts/run_sbs_valor_cuota.py --history-file "valores_cuota.xls"
#
#   # Download the XLS from the SBS index and load it
#   python scripts/run_sbs_valor_cuota.py --download-history
#
#   # Unattended daily run (what the Windows task executes): same as
#   # the default but leaves a trail in data/spp/scrape.log/.json and
#   # never raises - the exit code carries the result
#   python scripts/run_sbs_valor_cuota.py --scheduled
#
#   # Only (re)register the AFP series universe
#   python scripts/run_sbs_valor_cuota.py --register-only
# ---------------------------------------------------------------

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.shared.logging import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SPP valor cuota feed (SBS).",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--date", type=date.fromisoformat, default=None,
                        help="Run date YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--history-file", type=Path, default=None,
                        help="SBS monthly XLS to load instead of scraping.")
    parser.add_argument("--download-history", action="store_true",
                        help="Download the monthly XLS from the SBS and load it.")
    parser.add_argument("--force", action="store_true",
                        help="Restatement: replace figures already in fact_prices.")
    parser.add_argument("--scheduled", action="store_true",
                        help="Unattended daily run with a written trail; never raises.")
    parser.add_argument("--register-only", action="store_true",
                        help="Only register the series universe and exit.")
    args = parser.parse_args()

    setup_logging("run_sbs_valor_cuota")

    from src.configs.machine_config import machine_id, scraper_enabled
    from src.pipelines.prices.sbs.valor_cuota.run import (
        ensure_registered, run_daily, run_history, run_scheduled)

    if args.register_only:
        ensure_registered()
        return 0

    if args.history_file:
        # Loading a local file needs no browser and no machine gate.
        run_history(file=args.history_file, force=args.force)
        return 0

    if args.scheduled:
        # BEFORE the machine gate on purpose: run_scheduled carries the
        # gate itself, so a machine whose configuration is missing leaves
        # the reason in data/spp/scrape.log instead of dying with a
        # traceback the scheduled task reports as a bare exit code.
        return 0 if run_scheduled(force=args.force)["ok"] else 1

    # The scraping paths need the machine gate. scraper_enabled() rather
    # than assert_scraper(): the latter also demands a chromedriver path,
    # which this Playwright-driven scraper does not use.
    if not scraper_enabled():
        raise RuntimeError(
            f"The scraper is not enabled on this machine ({machine_id()}). "
            "Set SCRAPER_ENABLED=true in the project's .env.")

    if args.download_history:
        run_history(download=True, force=args.force)
        return 0

    result = run_daily(run_date=args.date, force=args.force)
    return 0 if result.get("dates") else 1


if __name__ == "__main__":
    raise SystemExit(main())
