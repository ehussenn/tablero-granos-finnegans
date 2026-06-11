# Instala tarea programada Windows: refresh diario Allaria 07:45 AM (15min después de LDC)
# Uso: powershell -ExecutionPolicy Bypass -File install_allaria_schedule.ps1

$ErrorActionPreference = "Stop"
$TaskName = "AllariaDailyRefresh"
$RepoRoot = "C:\Users\Public\Documents\Granos\tablero-granos-finnegans"
$PyExe    = (Get-Command py.exe -ErrorAction SilentlyContinue).Source
if (-not $PyExe) { $PyExe = (Get-Command python.exe).Source }
$ScriptPath = Join-Path $RepoRoot "scripts\allaria_daily_refresh.py"
$LogDir = Join-Path $RepoRoot "data\allaria"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

schtasks /Delete /TN $TaskName /F 2>$null

$Action  = New-ScheduledTaskAction -Execute $PyExe -Argument "`"$ScriptPath`"" -WorkingDirectory $RepoRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At 7:45am
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description "Refresh diario Allaria Agro (Posicion Campana + Cta Cte) + push repo"

Write-Host ""
Write-Host "[OK] Tarea '$TaskName' instalada (07:45 AM diaria)"
Write-Host "    Logs: $LogDir\refresh.log"
Write-Host "    Probar: schtasks /Run /TN $TaskName"
