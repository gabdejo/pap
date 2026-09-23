@echo off
rem Doble clic: crea la base de datos que declara el .env y le aplica el
rem esquema. Si ya existe, no la toca. Nunca borra nada.

cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo No existe el entorno .venv
  echo Corre primero scripts\"Instalar entorno.bat"
  echo.
  pause
  exit /b 1
)
if not exist ".env" (
  echo Falta el archivo .env con los datos de PostgreSQL.
  echo   copy .env.example .env
  echo y llena PG_HOST, PG_PORT, PG_DBNAME, PG_USER y PG_PASSWORD.
  echo.
  pause
  exit /b 1
)

.venv\Scripts\python.exe scripts\crear_base.py
echo.
pause
