@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "SILENT=0"
if /I "%~1"=="--silent" set "SILENT=1"
set "COACH_DIR=%~dp0"
set "COACH_PY="
set "COACH_ARGS="
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys, tkinter; assert sys.version_info >= (3,11)" >nul 2>&1
  if not errorlevel 1 goto dependencies
  echo Le dossier .venv existant est incomplet ou incompatible.
  echo Renomme ce dossier .venv puis relance Lancer.bat.
  goto end
)
call :try_python python
if defined COACH_PY goto create_env
call :try_python py -3
if defined COACH_PY goto create_env
for /d %%D in ("%LocalAppData%\Programs\Python\Python*") do call :try_python "%%~fD\python.exe"
if defined COACH_PY goto create_env
for /d %%D in ("%LocalAppData%\Python\pythoncore-*") do call :try_python "%%~fD\python.exe"
if defined COACH_PY goto create_env
for /d %%D in ("%ProgramFiles%\Python*" "C:\Python*") do call :try_python "%%~fD\python.exe"
if defined COACH_PY goto create_env
echo.
echo Aucun Python 3.11 ou plus avec tkinter n'a ete detecte.
echo Installe Python pour Windows depuis :
echo https://www.python.org/downloads/windows/
echo Active tkinter et, si propose, Add python.exe to PATH.
echo Ferme ensuite cette fenetre et relance Lancer.bat.
echo Si Python est deja installe ailleurs, ajoute son dossier au PATH.
goto end

:try_python
if defined COACH_PY exit /b
"%~1" %2 -c "import sys, tkinter, venv; assert sys.version_info >= (3,11)" >nul 2>&1
if errorlevel 1 exit /b
set "COACH_PY=%~1"
set "COACH_ARGS=%2"
exit /b

:create_env
echo Creation de l'environnement avec "%COACH_PY%" %COACH_ARGS%...
"%COACH_PY%" %COACH_ARGS% -m venv .venv
if errorlevel 1 (
  echo Impossible de creer l'environnement. Verifie les droits du dossier.
  goto end
)

:dependencies
".venv\Scripts\python.exe" updater.py --recover
if errorlevel 1 (
  echo Restauration impossible. Ne supprime pas le dossier .updates.
  goto end
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Installation des dependances impossible. Verifie la connexion Internet.
  goto end
)
rem Repare les attributs/ACL principaux avant de lancer l'application.
for %%F in (coach.py pc_optimizer.py local_ai.py updater.py requirements.txt) do (
  if exist "%%F" (
    attrib -R -S -H "%%F" >nul 2>&1
    powershell.exe -NoProfile -Command "try { Unblock-File -LiteralPath '%%~fF' -ErrorAction SilentlyContinue } catch {}" >nul 2>&1
    icacls "%%F" /reset /c >nul 2>&1
  )
)

if "%SILENT%"=="0" (
rem Raccourci sans VBS et sans CMD : pythonw lance directement l'interface.
if exist "%~dp0CoachFortnite.vbs" del /q "%~dp0CoachFortnite.vbs" >nul 2>&1
powershell.exe -NoProfile -Command "$ErrorActionPreference='Stop';$app=($env:COACH_DIR).TrimEnd('\');$pyw=Join-Path $app '.venv\Scripts\pythonw.exe';$coach=Join-Path $app 'coach.py';if(!(Test-Path $pyw)){throw 'pythonw.exe introuvable'};if(!(Test-Path $coach)){throw 'coach.py introuvable'};$ico=Join-Path $app 'CoachFortnite.ico';if(!(Test-Path $ico)){Add-Type -AssemblyName System.Drawing;$bmp=[System.Drawing.Bitmap]::new(64,64);$g=[System.Drawing.Graphics]::FromImage($bmp);$g.Clear([System.Drawing.Color]::FromArgb(16,24,39));$font=[System.Drawing.Font]::new('Segoe UI',24,[System.Drawing.FontStyle]::Bold,[System.Drawing.GraphicsUnit]::Pixel);$brush=[System.Drawing.SolidBrush]::new([System.Drawing.Color]::White);$g.DrawString('CF',$font,$brush,4,15);$h=$bmp.GetHicon();$icon=[System.Drawing.Icon]::FromHandle($h);$fs=[System.IO.File]::Open($ico,[System.IO.FileMode]::Create);$icon.Save($fs);$fs.Dispose();$brush.Dispose();$font.Dispose();$g.Dispose();$bmp.Dispose()};$desk=[Environment]::GetFolderPath('Desktop');$ws=New-Object -ComObject WScript.Shell;$lnk=Join-Path $desk 'Coach Fortnite.lnk';$shortcut=$ws.CreateShortcut($lnk);$shortcut.TargetPath=$pyw;$shortcut.Arguments=([char]34)+$coach+([char]34);$shortcut.WorkingDirectory=$app;$shortcut.IconLocation=$ico+',0';$shortcut.Description='Acolyte Fortnite - IA locale';$shortcut.Save()" >nul 2>&1
if errorlevel 1 echo Attention : impossible de recreer le raccourci du Bureau.
)

".venv\Scripts\python.exe" coach.py
if errorlevel 1 (
  echo.
  echo ERREUR : Acolyte n'a pas pu demarrer. Le code erreur est %ERRORLEVEL%.
)
goto end

:end
if "%SILENT%"=="0" pause
endlocal
