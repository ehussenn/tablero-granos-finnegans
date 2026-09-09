# Instala la tarea programada del cruce ARCA (CPE + Liquidaciones) para correr
# TODOS LOS DIAS a las 09:00 (pedido del usuario 09/09/2026), hasta la fecha actual.
# Uso: powershell -ExecutionPolicy Bypass -File install_arca_schedule.ps1

$ErrorActionPreference = "Stop"
$TaskName = "ArcaDailyRefresh"
$RepoRoot = "C:\Users\Public\Documents\Granos\tablero-granos-finnegans"
$PyExe    = (Get-Command py.exe -ErrorAction SilentlyContinue).Source
if (-not $PyExe) { $PyExe = (Get-Command python.exe).Source }
$ScriptPath = Join-Path $RepoRoot "scripts\arca_daily_refresh.py"

# Borrar tarea anterior si existe
schtasks /Delete /TN $TaskName /F 2>$null

# Diaria 09:00 — la sesion de ARCA vive en el perfil persistente; si un dia pide
# captcha, esa corrida falla y hay que correrla a mano con --visible.
$Action  = New-ScheduledTaskAction -Execute $PyExe -Argument "`"$ScriptPath`"" -WorkingDirectory $RepoRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At 9:00am
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 40)
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description "Refresh diario 09:00 del cruce ARCA vs Finnegans (hasta la fecha actual)"

Write-Host ""
Write-Host "[OK] Tarea '$TaskName' instalada: corre todos los dias a las 09:00."
Write-Host "    Log: $RepoRoot\data\arca\_logs\daily_refresh.log"
Write-Host "    Probar ahora: schtasks /Run /TN $TaskName"
Write-Host "    Ver estado:   schtasks /Query /TN $TaskName /V /FO LIST"
Write-Host "    Desinstalar:  schtasks /Delete /TN $TaskName /F"
