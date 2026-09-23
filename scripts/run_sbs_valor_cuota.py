# scripts/run_sbs_valor_cuota.py
# ---------------------------------------------------------------
# CLI entry point for the SPP valor cuota feed.
#
# Not part of scripts/run_prices.py on purpose: the other SBS feeds
# read files already on disk, while this one opens a VISIBLE Chrome
# window through the Imperva WAF - it must never launch as a side
# effect of "run all SBS". Schedule it as a Windows task with an
# interactive logon (the WAF blocks headless and locked sessions).
#
# Usage:
#   # Daily scrape (last 7 business days, three metrics)
#   python scripts/run_sbs_valor_cuota.py
#
#   # Re-scrape overwriting already-loaded figures
#   python scripts/run_sbs_valor_cuota.py --refresh
#
#   # Load a hand-downloaded SBS monthly XLS (valor cuota since 1993)
#   python scripts/run_sbs_valor_cuota.py --historico "valores_cuota.xls"
#
#   # Download the XLS from the SBS index and load it
#   python scripts/run_sbs_valor_cuota.py --historico-descargar
#
#   # Unattended daily run (what the Windows task executes): same as
#   # the default but leaves a trail in data/spp/extraccion.log/.json
#   # and never raises - the exit code carries the result
#   python scripts/run_sbs_valor_cuota.py --programado
#
#   # Only (re)register the AFP/benchmark series universe
#   python scripts/run_sbs_valor_cuota.py --solo-registro
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
    parser.add_argument("--historico", type=Path, default=None,
                        help="SBS monthly XLS to load instead of scraping.")
    parser.add_argument("--historico-descargar", action="store_true",
                        dest="historico_descargar",
                        help="Download the monthly XLS from the SBS and load it.")
    parser.add_argument("--refresh", action="store_true",
                        help="Overwrite figures already in fact_prices.")
    parser.add_argument("--programado", action="store_true",
                        help="Unattended daily run with a written trail.")
    parser.add_argument("--solo-registro", action="store_true",
                        dest="solo_registro",
                        help="Only register the series universe and exit.")
    args = parser.parse_args()

    setup_logging("run_sbs_valor_cuota")

    from src.configs.machine_config import scraper_enabled, machine_id
    from src.pipelines.prices.sbs.valor_cuota.run import (
        correr_programado, ensure_registered, run_daily, run_historico)

    if args.solo_registro:
        ensure_registered()
        return 0

    if args.historico:
        run_historico(archivo=args.historico, refresh=args.refresh)
        return 0

    if args.programado:
        # BEFORE the machine gate on purpose: correr_programado carries
        # the gate itself, so a machine whose market_data_config.yaml is
        # missing leaves the reason in data/spp/extraccion.log instead of
        # dying with a traceback the scheduled task reports as a bare
        # exit code and the tablero cannot explain.
        # The exit code is what the Windows task records as result.
        return 0 if correr_programado(refrescar=args.refresh)["ok"] else 1

    # The remaining scraping paths need the machine gate; loading a
    # local file does not.
    if not scraper_enabled():
        raise RuntimeError(
            f"Scraper is not enabled on this machine ({machine_id()}). "
            "Set SCRAPER_ENABLED=true in the project's .env file.")

    if args.historico_descargar:
        run_historico(download=True, refresh=args.refresh)
        return 0

    res = run_daily(run_date=args.date, refresh=args.refresh)
    return 0 if res.get("fechas") else 1


if __name__ == "__main__":
    raise SystemExit(main())
