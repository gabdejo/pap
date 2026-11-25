# scripts/diagnose_bloomberg_dividends.py
# ---------------------------------------------------------------
# Terminal spike for the Bloomberg dividend pipeline. Workstation
# only (needs blpapi + a logged-in Terminal). Writes nothing.
#
# Per ticker it answers three questions:
#   1. Does src.vendors.bloomberg.fetch_reference_request survive a
#      BULK field (EQY_DVD_ADJUST_FACT), or does it fall back to raw
#      messages? Either way the factor table is printed.
#   2. Do the four adjustment flags, all False, return raw prices?
#   3. Does raw price + factors reproduce Bloomberg's fully adjusted
#      PX_LAST? This is the go/no-go for "store raw, derive adjusted".
#
# Factor convention (verified on the Terminal):
#   operator 1 = divide, 2 = multiply, 3 = add
#   flag     1 = prices only, 3 = prices and volume
# A factor dated D applies to every price strictly before D, events
# applied oldest first (order only matters when an 'add' is present).
#
# Usage:
#   python scripts/diagnose_bloomberg_dividends.py "AAPL US Equity" "SPY US Equity"
#   python scripts/diagnose_bloomberg_dividends.py "BAP US Equity" --start 2015-01-01
#   python scripts/diagnose_bloomberg_dividends.py --self-check     # no Terminal needed
# ---------------------------------------------------------------

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIELD = "EQY_DVD_ADJUST_FACT"
FACTOR_COLS = ["date", "factor", "operator", "flag"]
# The bulk only returns the action types named by this override, and its
# default is CAPITAL_CHANGE alone - so cash dividends need their own calls.
FILTER_OVERRIDE = "CORPORATE_ACTIONS_FILTER"
ACTION_FILTERS = ["CAPITAL_CHANGE", "ABNORMAL_CASH", "NORMAL_CASH"]
RAW = {"adjustmentFollowDPDF": False, "adjustmentNormal": False,
       "adjustmentAbnormal": False, "adjustmentSplit": False}
ADJUSTED = {"adjustmentFollowDPDF": False, "adjustmentNormal": True,
            "adjustmentAbnormal": True, "adjustmentSplit": True}


def _apply_factors(prices: pd.Series, factors: pd.DataFrame) -> pd.Series:
    """prices: indexed by date. Returns the back-adjusted series."""
    out = prices.astype(float).copy()
    for ev in factors.sort_values("date").itertuples():
        before = out.index < ev.date
        if ev.operator == 1:
            out[before] /= ev.factor
        elif ev.operator == 2:
            out[before] *= ev.factor
        elif ev.operator == 3:
            out[before] += ev.factor
        else:
            raise ValueError(f"unknown operator {ev.operator} on {ev.date}")
    return out


def _self_check() -> None:
    d = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"])
    prices = pd.Series([100.0, 100.0, 50.0, 49.0], index=d)
    factors = pd.DataFrame(
        [[d[2], 2.0, 1, 3],      # 2:1 split on the 3rd: divide earlier prices by 2
         [d[3], 0.98, 2, 1]],    # dividend on the 4th: multiply earlier prices by 0.98
        columns=FACTOR_COLS)
    got = _apply_factors(prices, factors)
    assert [round(x, 6) for x in got] == [49.0, 49.0, 49.0, 49.0], list(got)
    print("self-check ok")


def _fetch_bulk(ticker: str, action_filter: str) -> pd.DataFrame:
    from src.vendors.bloomberg import data_request, fetch_reference_request

    result = fetch_reference_request(data_request(
        securities=[ticker], fields=[FIELD],
        options={"overrides": [{"fieldId": FILTER_OVERRIDE, "value": action_filter}]}))
    parsed = isinstance(result, pd.DataFrame)

    if parsed:
        # The vendor only yields a DataFrame for 0 rows (just an 'id' column)
        # or exactly 1 row (the cell holds that row's dict).
        cell = result[FIELD].iloc[0] if FIELD in result.columns else []
        rows = [cell] if isinstance(cell, dict) else list(cell)
    else:
        rows = []
        for msg in result or []:
            for sec in msg.get("securityData", []):
                if sec.get("fieldExceptions"):
                    print(f"  fieldExceptions: {sec['fieldExceptions']}")
                rows.extend(sec.get("fieldData", {}).get(FIELD, []))

    df = pd.DataFrame(rows)
    print(f"  {action_filter:<15} {len(df):>4} rows | vendor returned "
          f"{'DataFrame' if parsed else 'raw messages (bulk not parsed by secdata_list_to_df)'}")
    if df.empty:
        return pd.DataFrame(columns=FACTOR_COLS + ["filter"]).astype({"date": "datetime64[ns]"})
    assert df.shape[1] == 4, f"expected 4 bulk columns, got {list(df.columns)}"
    df.columns = FACTOR_COLS      # positional: date, factor, operator type, flag
    df["date"] = pd.to_datetime(df["date"])
    return df.astype({"factor": float, "operator": int, "flag": int}).assign(filter=action_filter)


def _fetch_factors(ticker: str) -> pd.DataFrame:
    """One bulk call per CORPORATE_ACTIONS_FILTER value, concatenated."""
    df = pd.concat([_fetch_bulk(ticker, f) for f in ACTION_FILTERS], ignore_index=True)
    dupes = df.duplicated(subset=FACTOR_COLS, keep=False)
    if dupes.any():
        # Same event under two filters would be applied twice: the filters overlap.
        print(f"  WARNING: {dupes.sum()} rows repeat across filters; keeping one of each")
        df = df.drop_duplicates(subset=FACTOR_COLS)
    return df


def _fetch_prices(ticker: str, start: str, end: str, options: dict) -> pd.Series:
    from src.vendors.bloomberg import data_request, fetch_historical_request

    df = fetch_historical_request(data_request(
        securities=[ticker], fields=["PX_LAST"],
        start_date=start, end_date=end, options=options))
    if not isinstance(df, pd.DataFrame) or df.empty:
        raise RuntimeError(f"no PX_LAST history for {ticker}: {df!r}")
    return pd.Series(df["PX_LAST"].astype(float).values, index=pd.to_datetime(df["date"]))


def diagnose(ticker: str, start: str, end: str, tol: float) -> bool:
    print(f"\n=== {ticker} | {start} to {end} ===")
    factors = _fetch_factors(ticker)
    print(f"  {len(factors)} factors | by operator: "
          f"{factors['operator'].value_counts().to_dict()} | by flag: "
          f"{factors['flag'].value_counts().to_dict()}")
    print(factors.groupby(["filter", "operator", "flag"]).size().to_string())
    if not factors.empty:
        print(factors.sort_values("date").tail(8).to_string(index=False))

    raw = _fetch_prices(ticker, start, end, RAW)
    adj = _fetch_prices(ticker, start, end, ADJUSTED)
    print(f"  raw vs adjusted differ on {(raw != adj.reindex(raw.index)).sum()} of {len(raw)} days "
          f"(0 with factors in the window means the flags are being ignored)")

    in_window = factors[(factors["date"] > raw.index.min()) & (factors["date"] <= raw.index.max())]
    calc = _apply_factors(raw, in_window)
    cmp = pd.DataFrame({"raw": raw, "bbg_adj": adj, "calc_adj": calc}).dropna()
    cmp["rel_diff"] = (cmp["calc_adj"] / cmp["bbg_adj"] - 1).abs()
    worst = cmp["rel_diff"].max()
    ok = worst <= tol
    print(f"  reconciliation: max |calc/bbg - 1| = {worst:.2e}  ->  {'OK' if ok else 'MISMATCH'}")
    if not ok:
        print(cmp.sort_values("rel_diff", ascending=False).head(10).to_string())
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Spike: EQY_DVD_ADJUST_FACT vs adjusted PX_LAST.")
    parser.add_argument("tickers", nargs="*", help='parsekyables, e.g. "AAPL US Equity"')
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--tol", type=float, default=1e-3,
                        help="max relative diff tolerated (Bloomberg rounds adjusted prices)")
    parser.add_argument("--self-check", action="store_true", dest="self_check")
    args = parser.parse_args()

    _self_check()
    if args.self_check:
        return
    if not args.tickers:
        parser.error("give at least one ticker")

    results = {t: diagnose(t, args.start, args.end, args.tol) for t in args.tickers}
    print("\n" + "\n".join(f"{'OK      ' if ok else 'MISMATCH'}  {t}" for t, ok in results.items()))
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
