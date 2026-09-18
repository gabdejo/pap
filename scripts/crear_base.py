# scripts/crear_base.py
# ---------------------------------------------------------------
# Crea la base de datos que el .env declara, y le aplica el esquema.
#
# Es el paso que va entre "PostgreSQL instalado" y "abrir el tablero".
# Se puede hacer a mano desde pgAdmin o con createdb, pero eso obliga
# a saber donde quedaron las herramientas y a repetir el nombre y las
# credenciales que el .env ya tiene: aqui se leen de ahi.
#
#   scripts\Crear base.bat        (doble clic)
#   .venv\Scripts\python.exe scripts\crear_base.py
#
# Si la base ya existe no la toca: solo se asegura de que el esquema
# este al dia, que es inofensivo y deja el tablero listo para abrir.
# NUNCA borra nada.
# ---------------------------------------------------------------

import sys
from pathlib import Path

# La consola de Windows suele ser cp1252 y PostgreSQL responde en
# espanol: sin esto, imprimir un error con acentos revienta con
# UnicodeEncodeError y tapa el mensaje que hacia falta leer.
for _flujo in (sys.stdout, sys.stderr):
    if hasattr(_flujo, "reconfigure"):
        _flujo.reconfigure(errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    import psycopg

    from src.shared.config_pg import get_db_config

    try:
        cfg = get_db_config()
    except Exception as exc:
        print(f"ERROR: no se pudo leer la configuracion: {exc}")
        print("Revisa que exista el archivo .env con PG_HOST, PG_PORT, "
              "PG_DBNAME, PG_USER y PG_PASSWORD.")
        return 1

    destino = cfg["dbname"]
    print(f"Servidor : {cfg['host']}:{cfg['port']} (usuario {cfg['user']})")
    print(f"Base     : {destino}")
    print()

    # La conexion va a 'postgres', la base de mantenimiento: no se puede
    # crear una base estando conectado a ella.
    try:
        admin = psycopg.connect(host=cfg["host"], port=cfg["port"],
                                dbname="postgres", user=cfg["user"],
                                password=cfg["password"], autocommit=True)
    except Exception as exc:
        print(f"ERROR: no se pudo conectar a PostgreSQL: "
              f"{str(exc).splitlines()[0]}")
        print()
        print("Revisa que el servicio este corriendo y que PG_USER y "
              "PG_PASSWORD del .env sean correctos.")
        return 1

    with admin:
        existe = admin.execute("SELECT 1 FROM pg_database WHERE datname = %s",
                               (destino,)).fetchone()
        if existe:
            print(f"La base '{destino}' ya existe; no se toca.")
        else:
            # El nombre viene del .env, no de una entrada libre, pero se
            # cita igual: un nombre con mayusculas o guiones lo exige.
            admin.execute(f'CREATE DATABASE "{destino}"')
            print(f"Base '{destino}' creada.")

    print()
    print("Aplicando el esquema...")
    from src.db.bootstrap import create_schema
    from src.db.connection import get_connection
    with get_connection() as conn:
        create_schema(conn)
        tablas = conn.execute(
            "SELECT count(*) AS n FROM information_schema.tables "
            "WHERE table_schema = 'public'").fetchone()["n"]
    print(f"Listo: {tablas} tablas.")
    print()
    print("Siguiente paso: abre el tablero con 'Tablero SPP.bat'.")
    print("Al arrancar registra las series y ya puedes cargar el historico.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
