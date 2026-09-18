@echo off
rem Crea el entorno de Python del proyecto SIN INTERNET, desde carpetas de
rem wheels ya descargadas. Pensado para la maquina de la red interna, que
rem no llega a PyPI.
rem
rem Uso (doble clic, o desde consola):
rem   "Instalar entorno.bat"
rem   "Instalar entorno.bat" C:\ruta\pypro_packs C:\ruta\dependencias-faltantes
rem
rem Sin argumentos busca las carpetas junto al proyecto y en Descargas.
rem El paso a paso completo esta en INSTALACION.md

setlocal enabledelayedexpansion
cd /d "%~dp0.."
echo ============================================================
echo  Tablero SPP - instalacion del entorno de Python
echo ============================================================
echo.

rem ---- 1. Las carpetas de wheels ----------------------------------------
set "PACKS=%~1"
set "EXTRA=%~2"

if "%PACKS%"=="" (
  for %%D in (
    "%~dp0..\..\pypro_packs"
    "%~dp0..\..\pypro_packs-main"
    "%USERPROFILE%\Downloads\pypro_packs-main"
    "%USERPROFILE%\Downloads\pypro_packs"
  ) do if exist "%%~D\*.whl" set "PACKS=%%~D"
)
if "%EXTRA%"=="" (
  for %%D in (
    "%~dp0..\..\dependencias-faltantes-py310"
    "%USERPROFILE%\Downloads\dependencias-faltantes-py310"
    "%~dp0..\wheels"
  ) do if exist "%%~D\*.whl" set "EXTRA=%%~D"
)

if "%PACKS%"=="" (
  echo No se encontro la carpeta de wheels de pypro_packs.
  echo.
  echo   Descargala de https://github.com/gabdejo/pypro_packs
  echo   y pasa su ruta:  "Instalar entorno.bat" C:\ruta\pypro_packs
  echo.
  pause
  exit /b 1
)
if "%EXTRA%"=="" (
  echo No se encontro la carpeta con las dependencias que faltan.
  echo.
  echo   Descarga y descomprime:
  echo   https://github.com/RicardoSachs/pap/releases/download/deps-py310/dependencias-faltantes-py310.zip
  echo.
  echo   Sin ella la API no arranca ^(falta python-multipart^).
  echo.
  pause
  exit /b 1
)
echo Wheels base   : %PACKS%
echo Wheels extra  : %EXTRA%
echo.

rem ---- 2. El interprete --------------------------------------------------
rem Prioridad: el que se indique en PYTHON_SPP, luego el portable de
rem py_versions junto al proyecto, luego el 3.10 del sistema.
rem PY es el ejecutable y va SIEMPRE entre comillas al invocarlo: una ruta
rem con espacios ("Program Files", "Mis Documentos") sin comillas se corta
rem en el primer espacio. Los argumentos del lanzador (py -3.10) van aparte
rem en PYARGS, porque un comando con argumento no admite comillas enteras.
rem "if defined" primero: la sustitucion %VAR:"=% sobre una variable que
rem no existe no da vacio, da el texto literal, y el if de abajo se rompe.
set "PY="
if defined PYTHON_SPP set "PY=%PYTHON_SPP:"=%"
set "PYARGS="
if "%PY%"=="" (
  for %%P in (
    "%~dp0..\..\py_versions\3106\python.exe"
    "%~dp0..\..\py_versions-main\3106\python.exe"
    "%USERPROFILE%\Downloads\py_versions-main\3106\python.exe"
  ) do if exist "%%~P" set "PY=%%~P"
)
if "%PY%"=="" (
  py -3.10 --version >nul 2>&1
  if !errorlevel!==0 (set "PY=py" & set "PYARGS=-3.10")
)
if "%PY%"=="" (
  python --version >nul 2>&1
  if !errorlevel!==0 set "PY=python"
)
if "%PY%"=="" (
  echo No se encontro Python. Descarga gabdejo/py_versions ^(carpeta 3106^)
  echo y vuelve a correr esto, o define PYTHON_SPP con la ruta a python.exe
  pause
  exit /b 1
)
echo Interprete    : %PY% %PYARGS%
"%PY%" %PYARGS% --version
echo.

rem ---- 3. El entorno ----------------------------------------------------
if exist ".venv\Scripts\python.exe" (
  echo Ya existe .venv; se reutiliza.
) else (
  echo Creando .venv ...
  "%PY%" %PYARGS% -m venv .venv
  if errorlevel 1 (
    echo No se pudo crear el entorno.
    pause
    exit /b 1
  )
)

rem ---- 4. Los paquetes, sin red ------------------------------------------
echo.
echo Instalando dependencias ^(sin internet^)...
.venv\Scripts\python.exe -m pip install --quiet --no-index ^
  --find-links "%PACKS%" --find-links "%EXTRA%" ^
  -r requirements-oficina.txt
if errorlevel 1 (
  echo.
  echo La instalacion fallo. Revisa que las dos carpetas tengan los .whl
  echo y que el Python sea 3.10 de 64 bits.
  pause
  exit /b 1
)

rem ---- 5. Comprobacion ---------------------------------------------------
echo.
echo Comprobando...
.venv\Scripts\python.exe -c "import sys, fastapi, uvicorn, pandas, psycopg, openpyxl, yaml, dotenv, multipart, playwright; print(f'  Python {sys.version.split()[0]} - pandas {pandas.__version__} - fastapi {fastapi.__version__} - todo importa')"
if errorlevel 1 (
  echo.
  echo Falta alguna dependencia. Revisa el mensaje de arriba.
  pause
  exit /b 1
)

echo.
echo ============================================================
echo  Entorno listo.
echo.
echo  Siguiente paso: copia .env.example a .env y llenalo
echo  ^(PG_*, SCRAPER_ENABLED=true^). Ver INSTALACION.md
echo ============================================================
pause
