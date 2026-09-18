@echo off
rem Doble clic: informe de por que el tablero no hace lo que deberia.
rem Solo mira - no escribe nada, no toca la base y no imprime contrasenas.

cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" (
  .venv\Scripts\python.exe scripts\diagnostico.py
) else (
  echo No existe el entorno .venv
  echo Corre primero el instalador: scripts\Instalar entorno.bat
)
echo.
pause
