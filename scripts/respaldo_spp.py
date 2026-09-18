# scripts/respaldo_spp.py
# ---------------------------------------------------------------
# Moves the database between machines: pg_dump out, pg_restore in.
#
# The project's other data script, migrate_spp_history.py, reads the
# OLD standalone monitor's database - it exists to import history that
# already lived somewhere else. On a second machine that database does
# not exist, and re-scraping cannot rebuild what only lives here:
# hand corrections (fact_prices.source = 'manual'), the Bloomberg
# registry, the manual series and the benchmark compositions. So the
# way to replicate this project is to carry the database itself.
#
# Usage:
#   # On the machine that HAS the data
#   python scripts/respaldo_spp.py --exportar
#
#   # On the new machine, after creating .env and the empty database
#   python scripts/respaldo_spp.py --importar "<archivo>.dump" --crear
#
# Connection comes from .env (the same PG_* the rest of the project
# uses); the password travels to pg_dump/pg_restore through the
# environment, never on the command line.
# ---------------------------------------------------------------

import argparse
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

# La consola de Windows suele ser cp1252 y PostgreSQL responde en
# espanol: sin esto, imprimir un error con acentos revienta con
# UnicodeEncodeError y tapa el mensaje que hacia falta leer.
for _flujo in (sys.stdout, sys.stderr):
    if hasattr(_flujo, "reconfigure"):
        _flujo.reconfigure(errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.shared.config_pg import get_db_config
from src.shared.paths import DATA_DIR

# Tables that only exist here: named so the summary can prove the dump
# carried the SPP work, not just the upstream pipelines.
TABLAS_SPP = ("fact_prices", "series_registry", "dim_entity",
              "bloomberg_serie", "bloomberg_dato",
              "serie_manual", "serie_manual_dato", "benchmark", "benchmark_composicion")


def _herramienta(nombre: str) -> str:
    """
    Locates pg_dump/pg_restore: PATH first, then the standard Windows
    install (PostgreSQL's installer does not add bin/ to PATH).
    """
    from shutil import which
    ruta = which(nombre)
    if ruta:
        return ruta
    candidatos = sorted(Path("C:/Program Files/PostgreSQL").glob(f"*/bin/{nombre}.exe"),
                        reverse=True)
    if candidatos:
        return str(candidatos[0])
    raise RuntimeError(
        f"No se encontro {nombre}. Viene con PostgreSQL; si esta instalado, "
        "agrega su carpeta bin al PATH (p. ej. "
        "C:\\Program Files\\PostgreSQL\\18\\bin).")


def _entorno(cfg: dict) -> dict:
    entorno = dict(os.environ)
    entorno["PGPASSWORD"] = cfg["password"] or ""
    return entorno


def _correr(cmd: list, cfg: dict) -> None:
    r = subprocess.run(cmd, env=_entorno(cfg), capture_output=True, text=True)
    salida = (r.stderr or "").strip()
    if r.returncode != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} fallo ({r.returncode}): "
                           f"{salida[-800:]}")
    if salida:
        # pg_restore warns about owners/ACLs that do not exist on the
        # target; harmless with --no-owner, but worth showing.
        print(salida[-800:])


def exportar(salida: Path | None) -> Path:
    cfg = get_db_config()
    destino = salida or (DATA_DIR / "respaldo" /
                         f"pap_{dt.datetime.now():%Y%m%d_%H%M}.dump")
    destino.parent.mkdir(parents=True, exist_ok=True)
    print(f"Exportando {cfg['dbname']}@{cfg['host']}:{cfg['port']} -> {destino}")
    _correr([_herramienta("pg_dump"),
             "-h", str(cfg["host"]), "-p", str(cfg["port"]),
             "-U", str(cfg["user"]), "-d", str(cfg["dbname"]),
             "--format=custom", "--no-owner", "--no-privileges",
             "--file", str(destino)], cfg)
    mb = destino.stat().st_size / 1e6
    print(f"Listo: {destino} ({mb:,.1f} MB)")
    print("\nLlevalo a la otra maquina junto con el zip del repo y restaura con:")
    print(f'  python scripts/respaldo_spp.py --importar "{destino.name}" --crear')
    return destino


def importar(archivo: Path, crear: bool) -> None:
    cfg = get_db_config()
    if not archivo.exists():
        raise RuntimeError(f"No existe el archivo {archivo}")

    if crear:
        # Connect to the maintenance database to create the target one.
        import psycopg
        with psycopg.connect(host=cfg["host"], port=cfg["port"],
                             dbname="postgres", user=cfg["user"],
                             password=cfg["password"], autocommit=True) as conn:
            existe = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (cfg["dbname"],)).fetchone()
            if existe:
                print(f"La base {cfg['dbname']} ya existe; se restaura encima.")
            else:
                conn.execute(f'CREATE DATABASE "{cfg["dbname"]}"')
                print(f"Base {cfg['dbname']} creada.")

    print(f"Restaurando {archivo} -> {cfg['dbname']}@{cfg['host']}:{cfg['port']}")
    _correr([_herramienta("pg_restore"),
             "-h", str(cfg["host"]), "-p", str(cfg["port"]),
             "-U", str(cfg["user"]), "-d", str(cfg["dbname"]),
             "--no-owner", "--no-privileges", "--clean", "--if-exists",
             str(archivo)], cfg)
    _resumen()


def _resumen() -> None:
    """What actually landed - a restore that 'worked' but brought no
    rows is the failure this prints to catch."""
    from src.db.connection import get_connection
    print("\nContenido restaurado:")
    with get_connection() as conn:
        for tabla in TABLAS_SPP:
            try:
                n = conn.execute(f"SELECT count(*) AS n FROM {tabla}").fetchone()["n"]
                print(f"  {tabla:<24} {n:>9,}")
            except Exception as exc:
                print(f"  {tabla:<24} (no existe: {str(exc)[:60]})")
    print("\nSiguiente paso: 'Tablero SPP.bat' para abrir el tablero.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Respaldo y restauracion de la base del tablero SPP.")
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--exportar", action="store_true",
                       help="Vuelca la base de este .env a un archivo .dump")
    grupo.add_argument("--importar", type=Path, metavar="ARCHIVO",
                       help="Restaura un .dump en la base de este .env")
    parser.add_argument("--salida", type=Path, default=None,
                        help="Ruta del .dump al exportar (por defecto "
                             "DATA_DIR/respaldo/pap_<fecha>.dump)")
    parser.add_argument("--crear", action="store_true",
                        help="Al importar, crea la base si no existe.")
    args = parser.parse_args()

    try:
        if args.exportar:
            exportar(args.salida)
        else:
            importar(args.importar, args.crear)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
