# scripts/run_bloomberg_dividends.py
# ---------------------------------------------------------------
# CLI for the Bloomberg price adjustment factors
# (src/pipelines/prices/bloomberg/dividends). Workstation only.
#
# Pulls full factor history for every equity ticker with an active
# Bloomberg price series and reconciles fact_adjustment_factors with it.
# Safe to re-run: an unchanged pull writes nothing.
#
# Usage:
#   python scripts/run_bloomberg_dividends.py
#   python scripts/run_bloomberg_dividends.py --ticker "BAP US Equity" --ticker "SOXX US Equity"
# ---------------------------------------------------------------

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.shared.logging import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Load Bloomberg price adjustment factors.")
    parser.add_argument("--date", type=date.fromisoformat, default=None,
                        help="run date (YYYY-MM-DD); only decides which factors count as late. Default today.")
    parser.add_argument("--ticker", action="append", dest="tickers", default=None,
                        help="restrict to this parsekyable; repeatable. Default: the whole universe.")
    args = parser.parse_args()

    setup_logging("run_bloomberg_dividends")

    from src.configs.machine_config import assert_bloomberg
    assert_bloomberg()

    from src.pipelines.prices.bloomberg.dividends.run import run
    result = run(run_date=args.date, tickers=args.tickers)
    print(result if result is not None else "nothing loaded")


if __name__ == "__main__":
    main()
