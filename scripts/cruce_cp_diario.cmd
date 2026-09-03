@echo off
rem Corrida automatica diaria del Cruce CP (tarea programada de Windows, 07:00).
rem Sin "pause": la ventana se cierra sola. Deja el log del dia en data\arca\_logs.
chcp 65001 >nul
cd /d "C:\Users\Public\Documents\Granos\tablero-granos-finnegans"
if not exist "data\arca\_logs" mkdir "data\arca\_logs"
for /f "tokens=1-3 delims=/" %%a in ("%date:~-10%") do set HOY=%%c-%%b-%%a
py scripts\actualizar_cruce_cp.py --dias 45 >> "data\arca\_logs\cruce_%HOY%.log" 2>&1
exit /b %errorlevel%
