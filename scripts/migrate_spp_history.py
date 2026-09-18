# scripts/migrate_spp_history.py
# ---------------------------------------------------------------
# One-time migration from the standalone SPP monitor's PostgreSQL
# (wide tables in schema `spp`) into this project's dimensional
# model. The monitor's database is read-only source and stays intact
# as the backup.
#
#   spp.valor_cuota_diario  48 wide columns ({clave}_f{n}[ _cuotas |
#                           _fondo ]) -> melted into fact_prices via
#                           series_registry (source 'sbs').
#   spp.benchmark_diario    bench_f{n} -> fact_prices (source
#                           'benchmark').
#   spp.bloomberg_serie /   copied into the ported tables; serie_id is
#   spp.bloomberg_dato      remapped through the logical key
#                           (ticker_clave, campo, intervalo) so re-runs
#                           are safe whatever the destination holds.
#
# Idempotent: every insert is ON CONFLICT DO NOTHING, so re-running
# after a partial migration only fills what is missing.
#
# Source credentials come from the CLI; the password may also come
# from the SPP_SRC_PASSWORD environment variable so it does not land
# in the shell history. Destination is the project's own PG (.env).
#
# Usage:
#   python scripts/migrate_spp_history.py --dry-run
#   set SPP_SRC_PASSWORD=... && python scripts/migrate_spp_history.py
#   python scripts/migrate_spp_history.py --skip-bloomberg
# ---------------------------------------------------------------

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
from psycopg.rows import dict_row

from src.shared.logging import setup_logging

import logging
logger = logging.getLogger(__name__)

RE_COL = re.compile(r"^(?P<clave>[a-z0-9_]+?)_f(?P<fondo>\d+)(?P<sufijo>_cuotas|_fondo)?$")
RE_BENCH = re.compile(r"^bench_f(?P<fondo>\d+)$")

# wide-column suffix -> series_registry field
SUFIJO_FIELD = {None: "PX_LAST", "_cuotas": "CUOTAS", "_fondo": "FONDO_SOLES"}

BATCH_LOG_EVERY = 200


def src_connection(args) -> psycopg.Connection:
    password = args.src_password or os.getenv("SPP_SRC_PASSWORD", "")
    try:
        return psycopg.connect(
            host=args.src_host, port=args.src_port, dbname=args.src_dbname,
            user=args.src_user, password=password, row_factory=dict_row,
        )
    except psycopg.OperationalError as exc:
        # This script imports from the OLD standalone monitor's database.
        # On any machine that never ran the monitor - the second computer,
        # in particular - that database does not exist, and the traceback
        # says nothing about what the operator should do instead.
        raise SystemExit(
            f"No se pudo abrir la base del monitor "
            f"'{args.src_dbname}'@{args.src_host}:{args.src_port}: "
            f"{str(exc).strip()[:200]}\n"
            "Este script solo sirve para importar desde el monitor viejo. "
            "Si estas instalando en otra maquina, restaura un respaldo:\n"
            "  python scripts/respaldo_spp.py --importar <archivo>.dump --crear"
        ) from exc


def migrate_book(src, dest, schema: str, series_map, dry_run: bool) -> tuple[int, int]:
    """Melts spp.valor_cuota_diario into fact_prices. Returns (read, loaded)."""
    from src.pipelines.prices.sbs.valor_cuota.afps import SOURCE_SBS, procode
    from src.pipelines.prices.sbs.valor_cuota.loader import upsert_fact

    cur = src.execute(f'SELECT * FROM "{schema}"."valor_cuota_diario" ORDER BY fecha')
    leidas = cargadas = 0
    sin_serie: set[str] = set()
    for fila in cur:
        fecha = fila.pop("fecha")
        fila.pop("fuente", None)
        fila.pop("actualizado_en", None)
        for col, valor in fila.items():
            if valor is None:
                continue
            m = RE_COL.match(col)
            if not m:
                continue
            leidas += 1
            try:
                code = procode(m.group("clave"), int(m.group("fondo")))
            except ValueError:
                sin_serie.add(col)
                continue
            field = SUFIJO_FIELD[m.group("sufijo")]
            serie = series_map.get((code, field))
            if serie is None:
                sin_serie.add(col)
                continue
            if dry_run:
                cargadas += 1
                continue
            if upsert_fact(dest, serie["series_id"], fecha, float(valor),
                           SOURCE_SBS):
                cargadas += 1
    if sin_serie:
        logger.warning("Columnas del libro sin serie destino (revisar afps.yaml): %s",
                       ", ".join(sorted(sin_serie)))
    return leidas, cargadas


def migrate_bench(src, dest, schema: str, series_map, dry_run: bool) -> tuple[int, int]:
    from src.pipelines.prices.sbs.valor_cuota.afps import SOURCE_BENCH, procode_bench
    from src.pipelines.prices.sbs.valor_cuota.loader import upsert_fact

    try:
        cur = src.execute(f'SELECT * FROM "{schema}"."benchmark_diario" ORDER BY fecha')
    except psycopg.errors.UndefinedTable:
        logger.info("benchmark_diario no existe en el origen; se omite.")
        src.rollback()
        return 0, 0
    leidas = cargadas = 0
    for fila in cur:
        fecha = fila.pop("fecha")
        for col, valor in fila.items():
            m = RE_BENCH.match(col)
            if not m or valor is None:
                continue
            leidas += 1
            serie = series_map.get((procode_bench(int(m.group("fondo"))), "PX_LAST"))
            if serie is None:
                continue
            if dry_run:
                cargadas += 1
                continue
            if upsert_fact(dest, serie["series_id"], fecha, float(valor),
                           SOURCE_BENCH):
                cargadas += 1
    return leidas, cargadas


def migrate_bloomberg(src, dest, schema: str, dry_run: bool) -> tuple[int, int]:
    """
    Copies bloomberg_serie + bloomberg_dato, remapping serie_id through the
    LOGICAL key (ticker_clave, campo, intervalo).

    Preserving the source ids would silently diverge the moment the
    destination already holds one of these series under a different id
    (registered from the dashboard, or a prior partial run): the serie
    insert would be skipped by ON CONFLICT while the dato rows carried the
    source's id - a foreign-key abort at best, points attached to the wrong
    ticker at worst. Letting the identity assign ids and mapping per series
    makes re-runs safe against any destination state.
    """
    try:
        series = src.execute(
            f'SELECT * FROM "{schema}"."bloomberg_serie" ORDER BY serie_id').fetchall()
    except psycopg.errors.UndefinedTable:
        logger.info("bloomberg_serie no existe en el origen; se omite.")
        src.rollback()
        return 0, 0

    n_series = n_datos = 0
    id_map: dict[int, int] = {}
    for s in series:
        if dry_run:
            n_series += 1
            continue
        r = dest.execute(
            """
            INSERT INTO bloomberg_serie (
                ticker, ticker_clave, campo, intervalo, descripcion,
                moneda, fecha_inicio, activa, ultima_fecha, ultimo_intento,
                ultimo_resultado, ultimo_error, puntos, creada_en
            ) VALUES (%(ticker)s, %(ticker_clave)s, %(campo)s,
                      %(intervalo)s, %(descripcion)s, %(moneda)s,
                      %(fecha_inicio)s, %(activa)s, %(ultima_fecha)s,
                      %(ultimo_intento)s, %(ultimo_resultado)s,
                      %(ultimo_error)s, %(puntos)s, %(creada_en)s)
            ON CONFLICT (ticker_clave, campo, intervalo) DO NOTHING
            """,
            s,
        )
        if r.rowcount > 0:
            n_series += 1
        fila = dest.execute(
            """
            SELECT serie_id FROM bloomberg_serie
            WHERE ticker_clave = %(ticker_clave)s
              AND campo = %(campo)s AND intervalo = %(intervalo)s
            """,
            s,
        ).fetchone()
        id_map[s["serie_id"]] = fila["serie_id"]

    cur = src.execute(
        f'SELECT serie_id, fecha, valor FROM "{schema}"."bloomberg_dato"')
    sin_serie = 0
    for d in cur:
        if dry_run:
            n_datos += 1
            continue
        dest_id = id_map.get(d["serie_id"])
        if dest_id is None:
            sin_serie += 1
            continue
        r = dest.execute(
            """
            INSERT INTO bloomberg_dato (serie_id, fecha, valor)
            VALUES (%s, %s, %s)
            ON CONFLICT (serie_id, fecha) DO NOTHING
            """,
            (dest_id, d["fecha"], d["valor"]),
        )
        if r.rowcount > 0:
            n_datos += 1
    if sin_serie:
        logger.warning(f"bloomberg_dato: {sin_serie} punto(s) huerfanos en el "
                       "origen (serie_id sin fila en bloomberg_serie); omitidos.")
    return n_series, n_datos


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate the SPP monitor's history into this project's DB.")
    parser.add_argument("--src-host", default="localhost")
    parser.add_argument("--src-port", default="5432")
    parser.add_argument("--src-dbname", default="spp")
    parser.add_argument("--src-user", default="postgres")
    parser.add_argument("--src-password", default=None,
                        help="Prefer the SPP_SRC_PASSWORD env var.")
    parser.add_argument("--src-schema", default="spp")
    parser.add_argument("--skip-bloomberg", action="store_true")
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
        registradas = afps.register_series(dest)
        series_map = afps.series_map(dest)
        if args.dry_run and registradas:
            logger.info(f"registro: {registradas} serie(s) se registrarian.")

        leidas, cargadas = migrate_book(src, dest, args.src_schema,
                                        series_map, args.dry_run)
        logger.info(f"valor_cuota_diario: {leidas:,} celdas leidas, "
                    f"{cargadas:,} {'a cargar' if args.dry_run else 'cargadas'}.")

        b_leidas, b_cargadas = migrate_bench(src, dest, args.src_schema,
                                             series_map, args.dry_run)
        logger.info(f"benchmark_diario: {b_leidas:,} celdas leidas, "
                    f"{b_cargadas:,} {'a cargar' if args.dry_run else 'cargadas'}.")

        if not args.skip_bloomberg:
            n_s, n_d = migrate_bloomberg(src, dest, args.src_schema, args.dry_run)
            logger.info(f"bloomberg: {n_s:,} series, {n_d:,} datos "
                        f"{'a copiar' if args.dry_run else 'copiados'}.")

        # Verification: destination totals for the migrated sources.
        if not args.dry_run:
            tot = dest.execute(
                """
                SELECT sr.source, COUNT(*) AS filas
                FROM fact_prices fp
                JOIN series_registry sr ON sr.series_id = fp.series_id
                JOIN dim_entity e ON e.entity_id = sr.entity_id
                WHERE e.procode LIKE 'SPP\\_%'
                GROUP BY sr.source
                """
            ).fetchall()
            for t in tot:
                logger.info(f"fact_prices [{t['source']}]: {t['filas']:,} filas totales.")

        if args.dry_run:
            # get_connection commits on a clean exit, so "write nothing"
            # has to be said explicitly before leaving the block.
            dest.rollback()
            logger.info("dry-run: no se escribio nada en el destino.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
