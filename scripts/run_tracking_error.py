# scripts/run_tracking_error.py
# ---------------------------------------------------------------
# CLI entry point for the ex-ante tracking error (Postgres only).
#
# Usage:
#   python scripts/run_tracking_error.py --portfolio FONDO2 --benchmark SPP_BENCH_F2 --date 2026-11-20
#   python scripts/run_tracking_error.py ... --currency USD
#   python scripts/run_tracking_error.py ... --halflife-days 126
#   python scripts/run_tracking_error.py ... --window-days 504 --top 30
#
# Prints the tracking error, the same number with the short-history
# names at zero volatility, coverage by tier, the largest contributors
# and every name that is not on full history. Nothing is stored.
# Inputs runbook: docs/TRACKING_ERROR_INPUTS.md.
# ---------------------------------------------------------------

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipelines.analytics.tracking_error.run import WINDOW_DAYS, run
from src.shared.logging import setup_logging

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ex-ante tracking error of a fund against a benchmark.")
    parser.add_argument("--portfolio", required=True, help="fund procode (dim_portfolio)")
    parser.add_argument("--benchmark", required=True, help="benchmark procode (dim_portfolio)")
    parser.add_argument("--date", required=True, type=date.fromisoformat,
                        help="as-of date of the weights (YYYY-MM-DD)")
    parser.add_argument("--window-days", type=int, default=WINDOW_DAYS, dest="window_days",
                        help=f"daily observations in the covariance (default {WINDOW_DAYS})")
    parser.add_argument("--halflife-days", type=float, default=None, dest="halflife_days",
                        help="EWMA half-life; default equal weights")
    parser.add_argument("--currency", default="PEN", help="reporting currency (default PEN)")
    parser.add_argument("--top", type=int, default=15, help="contributors to list (default 15)")
    args = parser.parse_args()

    setup_logging("run_tracking_error")

    res = run(args.portfolio, args.benchmark, args.date,
              window_days=args.window_days, halflife_days=args.halflife_days,
              currency=args.currency)
    detail = res["detail"]

    print(f"\n{res['portfolio']} vs {res['benchmark']} | {res['as_of']} | "
          f"{res['currency']} | {res['n_days']} daily returns")
    print(f"Tracking error (annualised)       {res['te']:.4%}")
    print(f"  short-history names at zero vol {res['te_zero_vol']:.4%}")
    print(f"Weight sums: portfolio {res['w_port_sum']:.4f}, benchmark {res['w_bench_sum']:.4f}")

    print("\nCoverage by tier")
    tiers = detail.groupby("tier").agg(
        assets=("active", "size"), w_port=("w_port", "sum"),
        abs_active=("active", lambda s: s.abs().sum()),
        contribution=("contribution", "sum"))
    print(tiers.to_string(float_format=lambda v: f"{v:.4f}"))

    cols = ["name", "w_port", "w_bench", "active", "tier", "n_obs", "beta", "contribution"]
    fmt = lambda v: f"{v:.4f}"
    top = detail.reindex(detail["contribution"].abs().sort_values(ascending=False).index)
    print(f"\nTop {args.top} contributors")
    print(top.head(args.top)[cols].to_string(index=False, float_format=fmt))

    partial = detail[(detail["tier"] != "full") & (detail["active"] != 0)]
    if not partial.empty:
        print(f"\nNot on full history ({len(partial)})")
        print(partial.sort_values("active", key=abs, ascending=False)[cols]
              .to_string(index=False, float_format=fmt))

    if res["stale"]:
        names = detail.loc[res["stale"]]
        print(f"\nStale prices, over half the daily returns are zero "
              f"({len(names)}, |active| {names['active'].abs().sum():.4f}):")
        print("  " + ", ".join(names["name"]))
    if res["fx_missing"]:
        print(f"\nno FX series for: {', '.join(res['fx_missing'])} "
              f"(exposure treated as having no currency risk)")
    print("\nFX sources: " + ", ".join(f"{c}={p}" for c, p in sorted(res["fx_sources"].items())))


if __name__ == "__main__":
    main()
