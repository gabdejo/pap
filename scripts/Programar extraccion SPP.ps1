# Registra, consulta o quita la extraccion automatica diaria del SPP,
# apuntando al pipeline de ESTE repo (scripts/run_sbs_valor_cuota.py).
#
# LA FORMA COMODA es el .bat hermano (doble clic, sin pelear con la
# politica de ejecucion de PowerShell, que por defecto rechaza este
# guion en una maquina recien instalada):
#
#   scripts\"Programar extraccion SPP.bat"              -> registra a las 18:00
#   scripts\"Programar extraccion SPP.bat" -Hora 19:30  -> a otra hora
#   scripts\"Programar extraccion SPP.bat" -Estado      -> que hay registrado hoy
#   scripts\"Programar extraccion SPP.bat" -Probar      -> la corre ahora
#   scripts\"Programar extraccion SPP.bat" -Quitar      -> la elimina
#
# Desde una consola de PowerShell, el equivalente directo es:
#   powershell -ExecutionPolicy Bypass -File ".\scripts\Programar extraccion SPP.ps1"
#
# USA EL MISMO NOMBRE DE TAREA que el monitor standalone a proposito:
# registrarla aqui ES el corte - reemplaza la tarea vieja (-Force), y asi
# nunca hay dos scrapers compitiendo por la misma pagina de la SBS.
# El nombre tiene un gemelo en web/api/services/spp_tarea.py
# (TAREA_WINDOWS): /api/spp/programado consulta la tarea por ese nombre,
# asi que un cambio aqui sin su gemelo deja al tablero reportando la
# automatizacion como muerta mientras el scrape sigue corriendo.
#
# No requiere permisos de administrador: la tarea corre en la sesion del
# usuario (-LogonType Interactive), que es justo lo que hace falta para que
# el Chrome visible que exige el WAF exista de verdad.

[CmdletBinding()]
param(
  [string]$Hora = "18:00",
  [switch]$Estado,
  [switch]$Probar,
  [switch]$Quitar
)

$ErrorActionPreference = "Stop"
$Raiz   = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)
$Python = Join-Path $Raiz ".venv\Scripts\python.exe"
$Script = Join-Path $Raiz "scripts\run_sbs_valor_cuota.py"
$Tarea  = "Profuturo - Valor cuota SPP"

function Mostrar-Estado {
  $t = Get-ScheduledTask -TaskName $Tarea -ErrorAction SilentlyContinue
  if (-not $t) { Write-Output "No hay ninguna tarea registrada con el nombre '$Tarea'."; return }
  $i = $t | Get-ScheduledTaskInfo
  $disparo = ($t.Triggers | ForEach-Object { $_.StartBoundary }) -join ", "
  Write-Output "Tarea      : $($t.TaskName)"
  Write-Output "Accion     : $($t.Actions[0].Execute) $($t.Actions[0].Arguments)"
  Write-Output "Estado     : $($t.State)"
  Write-Output "Programada : $disparo (hora local)"
  Write-Output "Proxima    : $($i.NextRunTime)"
  Write-Output "Ultima     : $($i.LastRunTime)  resultado $($i.LastTaskResult)"
  Write-Output "Omitidas   : $($i.NumberOfMissedRuns)"
}

if ($Estado) { Mostrar-Estado; exit 0 }

if ($Quitar) {
  if (Get-ScheduledTask -TaskName $Tarea -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $Tarea -Confirm:$false
    Write-Output "Tarea '$Tarea' eliminada. La extraccion ya no corre sola."
  } else {
    Write-Output "No habia nada que quitar."
  }
  exit 0
}

if ($Probar) {
  Write-Output "Corriendo la extraccion ahora (se abrira Chrome)..."
  & $Python $Script --programado
  Write-Output "Codigo de salida: $LASTEXITCODE"
  exit $LASTEXITCODE
}

if (-not (Test-Path $Python)) { throw "No se encontro el entorno en $Python" }
if (-not (Test-Path $Script)) { throw "No se encontro $Script" }
if ($Hora -notmatch '^([01]?\d|2[0-3]):[0-5]\d$') { throw "Hora invalida: $Hora. Usa HH:mm." }

# El WAF de la SBS exige Chrome REAL (el Chromium de Playwright no pasa).
# Avisar aqui evita descubrirlo recien a las 18:00, en una corrida sin nadie
# delante.
$chrome = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) {
  Write-Warning ("Google Chrome no aparece instalado. La extraccion lo necesita " +
                 "(el WAF de la SBS rechaza headless y Chromium). Instalalo antes " +
                 "de confiar en la corrida diaria.")
}

$accion = New-ScheduledTaskAction -Execute $Python `
            -Argument "`"$Script`" --programado" -WorkingDirectory $Raiz

$disparador = New-ScheduledTaskTrigger -Daily -At $Hora

# StartWhenAvailable recupera la corrida si a esa hora la maquina estaba
# apagada. El tope de 30 minutos solo existe para que un Chrome colgado por
# el WAF no quede corriendo indefinidamente.
$opciones = New-ScheduledTaskSettingsSet `
              -StartWhenAvailable `
              -DontStopIfGoingOnBatteries `
              -AllowStartIfOnBatteries `
              -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
              -MultipleInstances IgnoreNew

# El principal se identifica por SID, no por "DOMINIO\usuario": en una
# laptop unida a Entra/Azure AD $env:USERDOMAIN es "AzureAD" y
# Register-ScheduledTask aborta con "No mapping between account names and
# security IDs was done", dejando la tarea sin registrar. El SID siempre
# resuelve, y el Programador muestra el nombre igual.
$quien = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$principal = New-ScheduledTaskPrincipal -UserId $quien `
               -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $Tarea -Action $accion -Trigger $disparador `
  -Settings $opciones -Principal $principal -Force `
  -Description ("Extraccion diaria de valor cuota del SPP desde la SBS (pipeline pap). " +
                "Inserta lo que falte; nunca sobreescribe. " +
                "Rastro en data\spp\extraccion.log.") | Out-Null

Write-Output "Registrada: '$Tarea', todos los dias a las $Hora, apuntando a este repo."
Write-Output ""
Mostrar-Estado
Write-Output ""
Write-Output "Importante: la SBS exige un Chrome visible, asi que la sesion de Windows"
Write-Output "debe estar iniciada a esa hora. Con el equipo apagado la corrida se"
Write-Output "pospone y se recupera al volver (StartWhenAvailable)."
Write-Output ""
Write-Output "Si es la PRIMERA vez en esta maquina: corre ahora"
Write-Output "  scripts\`"Programar extraccion SPP.bat`" -Probar"
Write-Output "con el operador delante. El perfil de Chrome nace vacio y el reto del"
Write-Output "WAF hay que resolverlo una vez a mano; recien despues la corrida de las"
Write-Output "$Hora puede trabajar sola."
