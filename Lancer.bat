@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
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
".venv\Scripts\python.exe" coach.py
:end
pause
endlocal
