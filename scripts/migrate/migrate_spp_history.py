# scripts/migrate/migrate_spp_history.py
# ---------------------------------------------------------------
# ONE-SHOT. Imports the standalone SPP monitor's valor cuota book
# (PostgreSQL, wide table `spp.valor_cuota_diario`) into this project's
# fact_prices through the AFP series registry.
#
# Ran on 2026-09-11 against the developer's monitor database (76k
# fact rows, 1993-2026). Kept for the record and for a machine that
# still holds a monitor database; delete this file once no standalone
# monitor database exists anywhere. It is NOT part of any scheduled
# path.
#
#   spp.valor_cuota_diario   48 wide columns ({key}_f{n}[ _cuotas |
#                            _fondo ]) -> melted into fact_prices via
#                            series_registry (source 'sbs').
#
# The monitor's composite benchmark levels are not carried: the
# composite indices are redesigned in the analytics layer (pending
# doc item 5) and their levels are regenerated from definitions, not
# migrated.
#
# Idempotent: every insert is ON CONFLICT DO NOTHING, so re-running
# after a partial migration only fills what is missing. The monitor's
# database is a read-only source and stays intact as the backup.
#
# Source credentials come from the CLI; the password may also come
# from the SPP_SRC_PASSWORD environment variable so it does not land
# in the shell history. Destination is the project's own PG (.env).
#
# Usage:
#   python scripts/migrate/migrate_spp_history.py --dry-run
#   set SPP_SRC_PASSWORD=... && python scripts/migrate/migrate_spp_history.py
# ---------------------------------------------------------------

import argparse
import logging
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import psycopg
from psycopg.rows import dict_row

from src.shared.logging import setup_logging

logger = logging.getLogger(__name__)

RE_COL = re.compile(r"^(?P<key>[a-z0-9_]+?)_f(?P<fund>\d+)(?P<suffix>_cuotas|_fondo)?$")

# wide-column suffix -> series_registry field
SUFFIX_FIELD = {None: "PX_LAST", "_cuotas": "CUOTAS", "_fondo": "FONDO_SOLES"}


def src_connection(args) -> psycopg.Connection:
    password = args.src_password or os.getenv("SPP_SRC_PASSWORD", "")
    try:
        return psycopg.connect(
            host=args.src_host, port=args.src_port, dbname=args.src_dbname,
            user=args.src_user, password=password, row_factory=dict_row,
        )
    except psycopg.OperationalError as exc:
        # On a machine that never ran the standalone monitor that database
        # does not exist, and the traceback says nothing useful.
        raise SystemExit(
            f"Could not open the monitor database "
            f"'{args.src_dbname}'@{args.src_host}:{args.src_port}: "
            f"{str(exc).strip()[:200]}\n"
            "This script only imports from the old standalone monitor; there "
            "is nothing to migrate on a machine that never ran it."
        ) from exc


def migrate_book(src, dest, schema: str, series_map, dry_run: bool) -> tuple[int, int]:
    """Melts spp.valor_cuota_diario into fact_prices. Returns (read, loaded)."""
    from src.pipelines.prices.sbs.valor_cuota.afps import SOURCE_SBS, procode
    from src.pipelines.prices.sbs.valor_cuota.loader import upsert_fact

    cur = src.cursor()
    cur.execute(f'SELECT * FROM "{schema}"."valor_cuota_diario" ORDER BY fecha')
    read = loaded = 0
    unresolved: set[str] = set()
    for row in cur:
        day = row.pop("fecha")
        row.pop("fuente", None)
        row.pop("actualizado_en", None)
        for col, value in row.items():
            if value is None:
                continue
            m = RE_COL.match(col)
            if not m:
                continue
            read += 1
            try:
                code = procode(m.group("key"), int(m.group("fund")))
            except ValueError:
                unresolved.add(col)
                continue
            series = series_map.get((code, SUFFIX_FIELD[m.group("suffix")]))
            if series is None:
                unresolved.add(col)
                continue
            if dry_run:
                loaded += 1
                continue
            if upsert_fact(dest, series["series_id"], day, float(value), SOURCE_SBS):
                loaded += 1
    if unresolved:
        logger.warning(f"Book columns with no destination series (check afps.yaml): "
                       f"{', '.join(sorted(unresolved))}")
    return read, loaded


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate the standalone SPP monitor's valor cuota book into fact_prices.")
    parser.add_argument("--src-host", default="localhost")
    parser.add_argument("--src-port", default="5432")
    parser.add_argument("--src-dbname", default="spp")
    parser.add_argument("--src-user", default="postgres")
    parser.add_argument("--src-password", default=None,
                        help="Prefer the SPP_SRC_PASSWORD env var.")
    parser.add_argument("--src-schema", default="spp")
    parser.add_argument("--dry-run", action="store_true",
                        help="Count what would be migrated; write nothing.")
    args = parser.parse_args()

    setup_logging("migrate_spp_history")

    from src.db.connection import get_connection
    from src.pipelines.prices.sbs.valor_cuota import afps

    with src_connection(args) as src, get_connection() as dest:
        # Registering the series universe is a write, and --dry-run promises
        # "write nothing": the rollback below undoes it, so a dry run reports
        # what WOULD be registered instead of quietly registering it.
        registered = afps.register_series(dest)
        series_map = afps.series_map(dest)
        if args.dry_run and registered:
            logger.info(f"registry: {registered} series would be registered.")

        read, loaded = migrate_book(src, dest, args.src_schema, series_map, args.dry_run)
        logger.info(f"valor_cuota_diario: {read:,} cells read, "
                    f"{loaded:,} {'to load' if args.dry_run else 'loaded'}.")

        # Verification: destination totals for the migrated source.
        if not args.dry_run:
            cur = dest.cursor()
            cur.execute(
                """
                SELECT sr.source, COUNT(*) AS rows
                FROM fact_prices fp
                JOIN series_registry sr ON sr.series_id = fp.series_id
                JOIN dim_entity e ON e.entity_id = sr.entity_id
                WHERE e.procode LIKE 'SPP\\_%%' AND e.entity_type = 'fund'
                GROUP BY sr.source
                """
            )
            for t in cur.fetchall():
                logger.info(f"fact_prices [{t['source']}]: {t['rows']:,} rows in total.")

        if args.dry_run:
            # get_connection commits on a clean exit, so "write nothing"
            # has to be said explicitly before leaving the block.
            dest.rollback()
            logger.info("dry-run: nothing was written to the destination.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
