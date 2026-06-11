# Instala una tarea programada en Windows que corre el refresh diario de LDC
# todos los dias a las 07:30 AM (30 min despues de Cargill para no chocar).
# Uso: powershell -ExecutionPolicy Bypass -File install_ldc_schedule.ps1

$ErrorActionPreference = "Stop"
$TaskName = "LdcDailyRefresh"
$RepoRoot = "C:\Users\Public\Documents\Granos\tablero-granos-finnegans"
$PyExe    = (Get-Command py.exe -ErrorAction SilentlyContinue).Source
if (-not $PyExe) { $PyExe = (Get-Command python.exe).Source }
$ScriptPath = Join-Path $RepoRoot "scripts\ldc_daily_refresh.py"
$LogDir   = Join-Path $RepoRoot "data\ldc"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Borrar tarea anterior si existe
schtasks /Delete /TN $TaskName /F 2>$null

# Crear nueva tarea: diaria 07:30
$Action  = New-ScheduledTaskAction -Execute $PyExe -Argument "`"$ScriptPath`"" -WorkingDirectory $RepoRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At 7:30am
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description "Refresh diario de data LDC (Louis Dreyfus) y push al repo tablero-granos-finnegans"

Write-Host ""
Write-Host "[OK] Tarea programada '$TaskName' instalada."
Write-Host "    Corre todos los dias a las 07:30 AM (30min despues de Cargill)"
Write-Host "    Logs en: $LogDir\refresh.log"
Write-Host ""
Write-Host "Probarla ahora mismo: schtasks /Run /TN $TaskName"
Write-Host "Ver estado:           schtasks /Query /TN $TaskName /V /FO LIST"
Write-Host "Desinstalar:          schtasks /Delete /TN $TaskName /F"
