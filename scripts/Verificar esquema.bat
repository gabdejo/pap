@echo off
rem Doble clic: compara la base con el esquema del proyecto y dice que le
rem falta. Luego pregunta si lo agrega.
rem
rem Sirve para el error "there is no unique or exclusion constraint
rem matching the ON CONFLICT specification" y para cualquier columna o
rem restriccion que falte en una base creada por una version anterior.
rem
rem Nunca borra tablas, columnas ni filas.

cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo No existe el entorno .venv
  echo Corre primero scripts\"Instalar entorno.bat"
  echo.
  pause
  exit /b 1
)

.venv\Scripts\python.exe scripts\verificar_esquema.py
if %errorlevel%==0 goto fin

echo.
set /p RESP=Quieres que lo agregue ahora? (s/n):
if /i "%RESP%"=="s" (
  echo.
  .venv\Scripts\python.exe scripts\verificar_esquema.py --reparar
)

:fin
echo.
pause
