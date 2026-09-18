# scripts/diagnostico.py
# ---------------------------------------------------------------
# Un solo informe de por que el tablero no hace lo que deberia.
#
# Existe porque el tablero corre en maquinas a las que no siempre se
# puede llegar: en vez de ir preguntando una cosa por vez, esto
# imprime de golpe lo que suele estar detras - version del proyecto,
# interprete, paquetes, .env, configuracion de maquina, Chrome, base
# de datos, API y ultima extraccion - y termina diciendo que explica
# el problema.
#
#   scripts\Diagnostico.bat        (doble clic)
#   .venv\Scripts\python.exe scripts\diagnostico.py
#
# No escribe nada ni toca la base: solo mira. Nunca imprime
# contrasenas - del .env solo salen los NOMBRES de las variables.
# ---------------------------------------------------------------

import os
import sys
from pathlib import Path

# La consola de Windows suele ser cp1252 y PostgreSQL responde en
# espanol: sin esto, imprimir un error con acentos revienta con
# UnicodeEncodeError y tapa el mensaje que hacia falta leer.
for _flujo in (sys.stdout, sys.stderr):
    if hasattr(_flujo, "reconfigure"):
        _flujo.reconfigure(errors="replace")

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

OK, MAL, AVISO = "  [ok]   ", "  [MAL]  ", "  [ojo]  "


def titulo(t):
    print("\n--- " + t + " " + "-" * max(0, 58 - len(t)))


def intentar(f, defecto="(no se pudo)"):
    try:
        return f()
    except Exception as exc:
        return f"{defecto}: {str(exc).splitlines()[0][:90]}"


def main() -> int:
    problemas = []
    print("=" * 66)
    print(" Tablero SPP - diagnostico")
    print("=" * 66)
    print(f"  proyecto: {RAIZ}")

    # ---- 1. Version del proyecto -------------------------------------
    titulo("Version del proyecto")
    mc = (RAIZ / "src" / "configs" / "machine_config.py").read_text(
        encoding="utf-8", errors="replace")
    nuevo = "_ENV_A_CLAVE" in mc
    print((OK if nuevo else AVISO)
          + ("el .env puede configurar la maquina (version del 2026-09-14 o posterior)"
             if nuevo else
             "version ANTIGUA: SCRAPER_ENABLED en el .env todavia NO tiene efecto"))
    if not nuevo:
        problemas.append(
            "El proyecto es anterior al 2026-09-14: en esta version SCRAPER_ENABLED "
            "en el .env no hace nada. Vuelve a descargar el zip del proyecto.")
    for nombre in ("requirements-oficina.txt", "INSTALACION.md",
                   "web/apps/dashboards/out/spp/index.html"):
        existe = (RAIZ / nombre).exists()
        print((OK if existe else AVISO) + f"{nombre}: {'esta' if existe else 'NO esta'}")
        if not existe and "out" in nombre:
            problemas.append("Falta el tablero compilado (web/apps/dashboards/out): "
                             "el navegador no vera ninguna pagina.")

    # ---- 2. Interprete -----------------------------------------------
    titulo("Python")
    print(f"{OK}{sys.version.split()[0]}  ({'64' if sys.maxsize > 2 ** 32 else '32'} bits)")
    print(f"         {sys.executable}")
    if sys.maxsize <= 2 ** 32:
        problemas.append("Python de 32 bits: los wheels del wheelhouse son de 64.")

    # ---- 3. Paquetes --------------------------------------------------
    titulo("Paquetes")
    CLAVE = {
        "fastapi": "la API", "uvicorn": "el servidor", "pandas": "los datos",
        "psycopg": "PostgreSQL", "openpyxl": "leer Excel", "yaml": "configuracion",
        "dotenv": "leer el .env",
        "multipart": "SUBIR ARCHIVOS - sin esto la API no arranca",
        "playwright": "la extraccion de la SBS",
    }
    for modulo, para in CLAVE.items():
        try:
            m = __import__(modulo)
            v = getattr(m, "__version__", "")
            print(f"{OK}{modulo:<12} {str(v):<10} {para}")
        except Exception:
            print(f"{MAL}{modulo:<12} {'AUSENTE':<10} {para}")
            problemas.append(f"Falta el paquete '{modulo}' ({para}).")

    # ---- 4. .env ------------------------------------------------------
    titulo("Archivo .env")
    env = RAIZ / ".env"
    if not env.exists():
        print(f"{MAL}no existe {env}")
        problemas.append("Falta el .env: copia .env.example y llena PG_* y "
                         "SCRAPER_ENABLED.")
    else:
        print(f"{OK}{env}")
        for linea in env.read_text(encoding="utf-8", errors="replace").splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, valor = linea.split("=", 1)
            estado = "(con valor)" if valor.strip() else "(vacio)"
            print(f"           {clave.strip():<26} {estado}")

    # ---- 5. Configuracion de maquina ----------------------------------
    titulo("Que puede hacer esta maquina")
    try:
        from src.configs.machine_config import (_LOCAL_CONFIG, bloomberg_enabled,
                                                machine_id, scraper_enabled)
        donde = str(_LOCAL_CONFIG) if _LOCAL_CONFIG.exists() else "no existe (manda el .env)"
        print(f"{OK}yaml de maquina : {donde}")
        print(f"{OK}machine_id      : {machine_id()}")
        activo = scraper_enabled()
        print((OK if activo else MAL) + f"scraper_enabled : {activo}")
        if not activo:
            problemas.append(
                "El scraper esta DESHABILITADO. Pon SCRAPER_ENABLED=true en el .env "
                "y CIERRA Y VUELVE A ABRIR el tablero: la configuracion se lee al "
                "arrancar, no en caliente.")
        print(f"{OK}bloomberg       : {bloomberg_enabled()}")
    except Exception as exc:
        print(f"{MAL}no se pudo leer la configuracion: {str(exc)[:120]}")
        problemas.append("La configuracion de maquina no se puede leer.")

    # ---- 6. Chrome -----------------------------------------------------
    titulo("Google Chrome (lo exige el WAF de la SBS)")
    rutas = [Path(os.path.expandvars(r)) / "Google" / "Chrome" / "Application" / "chrome.exe"
             for r in ("%ProgramFiles%", "%ProgramFiles(x86)%", "%LOCALAPPDATA%")]
    hallado = next((r for r in rutas if r.exists()), None)
    print((OK + str(hallado)) if hallado else (MAL + "no se encontro chrome.exe"))
    if not hallado:
        problemas.append("Google Chrome no esta instalado: la extraccion no puede correr "
                         "(el Chromium de Playwright no sirve, el WAF lo rechaza).")

    # ---- 7. Base de datos ----------------------------------------------
    titulo("Base de datos")
    try:
        from src.db.connection import get_connection
        from src.shared.config_pg import get_db_config
        cfg = get_db_config()
        print(f"{OK}destino: {cfg['dbname']}@{cfg['host']}:{cfg['port']} "
              f"(usuario {cfg['user']})")
        with get_connection() as conn:
            tablas = conn.execute(
                "SELECT count(*) AS n FROM information_schema.tables "
                "WHERE table_schema='public'").fetchone()["n"]
            print(f"{OK}conecta. Tablas: {tablas}")
            if tablas == 0:
                problemas.append("La base esta vacia: abre el tablero una vez para que "
                                 "cree el esquema.")
            for tabla, etiqueta in (("series_registry", "series registradas"),
                                    ("fact_prices", "observaciones del libro"),
                                    ("bloomberg_serie", "series Bloomberg"),
                                    ("serie_manual", "series manuales"),
                                    ("benchmark", "indices declarados (target/benchmark)"),
                                    ("benchmark_composicion", "filas de composicion")):
                n = intentar(lambda t=tabla: conn.execute(
                    f"SELECT count(*) AS n FROM {t}").fetchone()["n"], "-")
                print(f"           {etiqueta:<28} {n}")
            series = intentar(lambda: conn.execute(
                "SELECT count(*) AS n FROM series_registry").fetchone()["n"], 0)
            if isinstance(series, int) and series == 0 and tablas:
                problemas.append("No hay series registradas: el registro manual y la "
                                 "carga historica fallaran. Reinicia el tablero.")
    except Exception as exc:
        print(f"{MAL}{str(exc).splitlines()[0][:140]}")
        problemas.append("No se puede conectar a PostgreSQL. Revisa PG_* en el .env y "
                         "que el servicio este corriendo.")

    # ---- 8. API ---------------------------------------------------------
    titulo("API del tablero (puerto 8000)")
    try:
        import json
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=5) as r:
            print(f"{OK}responde: {json.load(r)}")
    except Exception:
        print(f"{AVISO}no responde (normal si el tablero no esta abierto en este momento)")

    # ---- 9. Ultima extraccion -------------------------------------------
    titulo("Extraccion automatica")
    def _ultima():
        from src.pipelines.prices.sbs.valor_cuota.run import ultima_corrida
        return ultima_corrida()
    rastro = intentar(_ultima, "")
    if isinstance(rastro, dict) and rastro.get("inicio"):
        estado = "correcta" if rastro.get("ok") else "FALLO"
        print(f"{OK}ultima corrida: {rastro.get('inicio')} - {estado}")
        if rastro.get("error"):
            print(f"           motivo: {str(rastro['error'])[:150]}")
            problemas.append(f"La ultima extraccion fallo: {str(rastro['error'])[:120]}")
    else:
        print(f"{AVISO}todavia no hay ninguna corrida registrada")

    # ---- Resumen ---------------------------------------------------------
    print()
    print("=" * 66)
    if problemas:
        print(f" {len(problemas)} cosa(s) que explican el problema:")
        for i, p in enumerate(problemas, 1):
            print(f"   {i}. {p}")
    else:
        print(" Todo lo que este diagnostico revisa esta en orden.")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
