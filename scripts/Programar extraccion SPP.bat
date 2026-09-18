@echo off
rem Doble clic para registrar la extraccion diaria del SPP a las 18:00.
rem
rem Existe porque Windows no ejecuta archivos .ps1 con doble clic y la
rem politica por defecto (Restricted) rechaza el guion desde la consola:
rem el operador veia "running scripts is disabled on this system" y se
rem quedaba sin extraccion automatica. Este .bat llama al mismo guion
rem con -ExecutionPolicy Bypass, que solo aplica a este proceso.
rem
rem Admite los mismos argumentos:
rem   "Programar extraccion SPP.bat" -Hora 19:30
rem   "Programar extraccion SPP.bat" -Estado
rem   "Programar extraccion SPP.bat" -Probar
rem   "Programar extraccion SPP.bat" -Quitar

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Programar extraccion SPP.ps1" %*
pause
