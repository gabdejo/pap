@echo off
rem Doble clic para levantar el tablero: API FastAPI + dashboard compilado
rem en un solo origen, y abre el navegador en la vista SPP.
rem Cerrar la ventana "Tablero SPP (API)" detiene el servidor.

rem Un solo lugar para el puerto: guardia, uvicorn y navegador deben coincidir.
set "PUERTO=8000"

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo No se encontro el entorno en .venv\Scripts\python.exe
  echo.
  echo Crea el entorno desde esta carpeta:
  echo   py -3 -m venv .venv
  echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
  echo.
  echo Paso a paso completo en INSTALACION.md
  pause
  exit /b 1
)

rem .env esta git-ignorado: NO viene en el zip del repo. Sin el, la API
rem arranca "sana" (/api/health responde) y recien falla al primer dato,
rem con un 500 que el operador ve como un tablero vacio.
if not exist ".env" (
  echo Falta el archivo .env con los datos de PostgreSQL.
  echo.
  echo   copy .env.example .env
  echo.
  echo y llena PG_HOST, PG_PORT, PG_DBNAME, PG_USER y PG_PASSWORD.
  echo Paso a paso completo en INSTALACION.md
  pause
  exit /b 1
)

if not exist "web\apps\dashboards\out\index.html" (
  echo Aviso: no existe web\apps\dashboards\out - el dashboard no esta compilado.
  echo La API funcionara igual, pero la pagina no se servira. Compila con:
  echo   cd web\apps\dashboards ^&^& npm install ^&^& npm run build
  pause
)

rem Si el tablero ya responde en el puerto, no levantar otro: solo abrir el
rem navegador. Se comprueba contra /api/health y no contra netstat, para no
rem confundir cualquier otro proceso que escuche en el puerto con el tablero.
curl -s -m 2 http://127.0.0.1:%PUERTO%/api/health | findstr "ok" >nul
if %errorlevel%==0 (
  echo El tablero ya esta corriendo; abriendo el navegador...
  start "" http://127.0.0.1:%PUERTO%/spp/
  exit /b 0
)

rem cmd /k mantiene la ventana viva si uvicorn muere al arrancar (falta un
rem paquete, .env mal escrito, puerto ocupado): asi el traceback queda a la
rem vista en vez de desaparecer con la consola.
start "Tablero SPP (API)" cmd /k .venv\Scripts\python.exe -m uvicorn web.api.main:app --port %PUERTO%

rem Esperar a que la API responda de verdad en lugar de contar tres segundos:
rem en una maquina recien instalada (sin .pyc en cache, con antivirus mirando)
rem el arranque tarda mas y el navegador abria sobre un puerto todavia mudo.
echo Esperando a que la API responda...
set /a intentos=0
:esperar
curl -s -m 1 http://127.0.0.1:%PUERTO%/api/health | findstr "ok" >nul
if %errorlevel%==0 goto abrir
set /a intentos+=1
if %intentos% geq 40 (
  echo.
  echo La API no respondio en 40 segundos. Revisa la ventana "Tablero SPP (API)":
  echo ahi queda el error exacto.
  pause
  exit /b 1
)
timeout /t 1 /nobreak >nul
goto esperar

:abrir
start "" http://127.0.0.1:%PUERTO%/spp/
