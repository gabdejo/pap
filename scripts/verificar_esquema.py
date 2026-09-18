# scripts/verificar_esquema.py
# ---------------------------------------------------------------
# Compara la base real contra el esquema del repo y dice que le
# falta. Con --reparar, se lo agrega.
#
# El problema que resuelve: todo el esquema se escribe con
# CREATE TABLE IF NOT EXISTS, que NO altera una tabla que ya existe.
# Una columna o una restriccion agregada despues nunca llega a una
# base creada antes, y el sintoma aparece lejos de la causa - el
# clasico es
#     there is no unique or exclusion constraint matching the
#     ON CONFLICT specification
# al registrar series o cargar el historico.
#
# Como sabe que deberia haber: crea una base temporal, le aplica el
# esquema del repo y la usa de patron. Asi no depende de una lista
# escrita a mano que se queda vieja.
#
#   scripts\Verificar esquema.bat          (solo mira)
#   .venv\Scripts\python.exe scripts\verificar_esquema.py --reparar
#
# Sin --reparar no escribe nada. Con --reparar agrega SOLO lo que
# falta: restricciones y columnas nuevas. Nunca borra una tabla, una
# columna ni una fila.
# ---------------------------------------------------------------

import argparse
import os
import sys
from pathlib import Path

for _flujo in (sys.stdout, sys.stderr):
    if hasattr(_flujo, "reconfigure"):
        _flujo.reconfigure(errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _constraints(conn) -> dict:
    """{(tabla, definicion): nombre} de las UNIQUE y PRIMARY KEY."""
    filas = conn.execute("""
        SELECT c.conrelid::regclass::text AS tabla,
               c.conname                  AS nombre,
               pg_get_constraintdef(c.oid) AS definicion
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE c.contype IN ('u', 'p') AND n.nspname = 'public'
    """).fetchall()
    return {(f["tabla"], f["definicion"]): f["nombre"] for f in filas}


def _columnas(conn) -> dict:
    """{(tabla, columna): tipo} de todo el esquema public."""
    filas = conn.execute("""
        SELECT table_name AS tabla, column_name AS columna,
               data_type AS tipo, is_nullable AS acepta_nulos
        FROM information_schema.columns
        WHERE table_schema = 'public'
    """).fetchall()
    return {(f["tabla"], f["columna"]): (f["tipo"], f["acepta_nulos"])
            for f in filas}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compara la base contra el esquema del repo.")
    parser.add_argument("--reparar", action="store_true",
                        help="Agrega lo que falte (no borra nada).")
    args = parser.parse_args()

    import psycopg
    from psycopg.rows import dict_row

    from src.db.bootstrap import create_schema
    from src.db.connection import get_connection
    from src.shared.config_pg import get_db_config

    cfg = get_db_config()
    referencia = f"pap_patron_{os.getpid()}"
    print(f"Base revisada : {cfg['dbname']}@{cfg['host']}:{cfg['port']}")
    print(f"Patron        : el esquema de src/db/schema")
    print()

    admin = psycopg.connect(host=cfg["host"], port=cfg["port"], dbname="postgres",
                            user=cfg["user"], password=cfg["password"],
                            autocommit=True)
    try:
        with admin:
            admin.execute(f'CREATE DATABASE "{referencia}"')
            try:
                # El patron: una base limpia con el esquema del repo.
                patron = psycopg.connect(host=cfg["host"], port=cfg["port"],
                                         dbname=referencia, user=cfg["user"],
                                         password=cfg["password"],
                                         row_factory=dict_row)
                with patron:
                    create_schema(patron)
                    patron.commit()
                    esperadas = _constraints(patron)
                    cols_esperadas = _columnas(patron)

                with get_connection() as real:
                    actuales = _constraints(real)
                    cols_actuales = _columnas(real)
                    tablas_reales = {t for (t, _) in cols_actuales}

                    faltan = {k: v for k, v in esperadas.items()
                              if k not in actuales and k[0] in tablas_reales}
                    faltan_cols = {k: v for k, v in cols_esperadas.items()
                                   if k not in cols_actuales and k[0] in tablas_reales}
                    sin_tabla = {t for (t, _) in cols_esperadas} - tablas_reales

                    if sin_tabla:
                        print(f"Tablas que no existen todavia ({len(sin_tabla)}): "
                              "se crean solas al abrir el tablero.")
                        for t in sorted(sin_tabla):
                            print(f"   - {t}")
                        print()

                    if not faltan and not faltan_cols:
                        print("La base coincide con el esquema del repo. "
                              "No falta ninguna restriccion ni columna.")
                        return 0

                    if faltan:
                        print(f"RESTRICCIONES QUE FALTAN ({len(faltan)}):")
                        for (tabla, definicion), nombre in sorted(faltan.items()):
                            print(f"   {tabla}: {definicion}")
                        print()
                    if faltan_cols:
                        print(f"COLUMNAS QUE FALTAN ({len(faltan_cols)}):")
                        for (tabla, col), (tipo, _) in sorted(faltan_cols.items()):
                            print(f"   {tabla}.{col}  ({tipo})")
                        print()

                    if not args.reparar:
                        print("Para agregarlas:")
                        print("   .venv\\Scripts\\python.exe scripts\\verificar_esquema.py --reparar")
                        return 1

                    print("Reparando...")
                    hechas = fallidas = 0
                    # Las columnas primero: una restriccion puede necesitarlas.
                    for (tabla, col), (tipo, acepta) in sorted(faltan_cols.items()):
                        sql = f'ALTER TABLE {tabla} ADD COLUMN IF NOT EXISTS "{col}" {tipo}'
                        try:
                            real.execute(sql)
                            print(f"   + {tabla}.{col}")
                            hechas += 1
                        except Exception as exc:
                            print(f"   ! {tabla}.{col}: {str(exc).splitlines()[0][:100]}")
                            real.rollback()
                            fallidas += 1
                    for (tabla, definicion), nombre in sorted(faltan.items()):
                        sql = f'ALTER TABLE {tabla} ADD CONSTRAINT "{nombre}" {definicion}'
                        try:
                            real.execute(sql)
                            print(f"   + {tabla}: {definicion}")
                            hechas += 1
                        except Exception as exc:
                            motivo = str(exc).splitlines()[0][:150]
                            print(f"   ! {tabla}: {motivo}")
                            if "duplicate key" in motivo or "could not create" in motivo:
                                print("     (hay filas repetidas: hay que decidir cual "
                                      "conservar antes de poder crearla)")
                            real.rollback()
                            fallidas += 1
                    print()
                    print(f"Agregadas: {hechas}. Con problemas: {fallidas}.")
                    if hechas:
                        print("Cierra y vuelve a abrir el tablero.")
                    return 0 if not fallidas else 1
            finally:
                admin.execute(f'DROP DATABASE IF EXISTS "{referencia}" WITH (FORCE)')
    except Exception as exc:
        print(f"ERROR: {str(exc).splitlines()[0][:200]}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
