@echo off
setlocal
cd /d "%~dp0"
echo Creation du raccourci Coach Fortnite sur le bureau...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Creer-raccourci-bureau.ps1" "%~dp0"
if errorlevel 1 (
  echo.
  echo La creation du raccourci a echoue.
  echo Envoie une capture de cette fenetre pour corriger le probleme.
  pause
  exit /b 1
)
echo.
echo Termine. Tu peux fermer cette fenetre.
timeout /t 3 >nul
endlocal
