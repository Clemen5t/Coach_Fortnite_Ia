@echo off
setlocal
set "COACH_OLLAMA=ollama"
where ollama >nul 2>&1
if errorlevel 1 (
  if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
    set "COACH_OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
  ) else (
    echo Installe Ollama depuis https://ollama.com/download/windows
    echo Ouvre ensuite Ollama, puis relance ce fichier.
    pause
    exit /b 1
  )
)
echo Telechargement initial du modele local Gemma 3 4B, plusieurs Go.
echo Aucun compte ni cle API ne sont necessaires pour ce modele local.
"%COACH_OLLAMA%" pull gemma3:4b
if errorlevel 1 (
  echo Echec. Verifie Internet et ouvre Ollama depuis le menu Demarrer.
) else (
  echo Modele installe. Ouvre maintenant Lancer.bat.
)
pause
endlocal
