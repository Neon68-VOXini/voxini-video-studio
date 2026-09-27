@echo off
setlocal EnableExtensions
chcp 65001 >nul
title VOXini Video Studio - Entwicklungsstart

echo ============================================================
echo  VOXini Video Studio - START_DEV.bat
echo  Legt (beim ersten Mal) eine eigene Python-Umgebung fuer die
echo  App selbst an (NICHT die ComfyUI-Umgebung - siehe
echo  INSTALL_LOCAL_AI.bat fuer diese getrennte Umgebung) und
echo  startet die Anwendung DIREKT AUS DEM QUELLCODE in diesem
echo  Ordner - also exakt derselbe Code, aus dem auch die
echo  gepackte VOXini Video Studio.exe gebaut wird (siehe
echo  BUILD_WINDOWS.bat). Ideal, wenn Aenderungen am Quellcode
echo  sofort ohne Neu-Build sichtbar sein sollen.
echo ============================================================
echo.

cd /d "%~dp0"
set "VENV_DIR=%~dp0.venv"

REM -- Sicherheitscheck: laeuft die gepackte EXE noch parallel? -------
REM Zwei gleichzeitig offene Instanzen (EXE + diese Dev-Version) auf
REM demselben Projekt fuehren dazu, dass die zuletzt speichernde
REM Instanz den Stand der anderen stillschweigend ueberschreibt -
REM das genaue Muster, das am 05.09.2026 einen manuellen
REM Charakter-/Szenen-Import wieder verworfen hat. Deshalb hier immer
REM warnen statt das Risiko unausgesprochen zu lassen.
tasklist /fi "imagename eq VOXini Video Studio.exe" 2>nul | find /i "VOXini Video Studio.exe" >nul
if not errorlevel 1 (
    echo WARNUNG: "VOXini Video Studio.exe" laeuft aktuell noch ^(siehe
    echo Task-Manager^). Wenn du jetzt zusaetzlich diese Dev-Version
    echo startest und in BEIDEN am selben Projekt speicherst, ueberschreibt
    echo die zuletzt speichernde Instanz die Aenderungen der anderen ohne
    echo Warnung. Bitte die EXE zuerst vollstaendig schliessen, wenn du am
    echo Quellcode weiterarbeiten willst.
    echo.
    choice /c JN /m "Trotzdem fortfahren"
    if errorlevel 2 exit /b 1
)

where py >nul 2>nul
if errorlevel 1 (
    set "PYCMD=python"
) else (
    set "PYCMD=py"
)

if exist "%VENV_DIR%\Scripts\python.exe" (
    echo Verwende bestehende Umgebung: %VENV_DIR%
) else (
    echo Lege neue Python-Umgebung an: %VENV_DIR%
    %PYCMD% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo FEHLER: Konnte keine Python-Umgebung anlegen.
        echo Bitte stelle sicher, dass Python 3.11 oder neuer installiert ist:
        echo   https://www.python.org/downloads/
        pause
        exit /b 1
    )
)

set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

echo.
echo Installiere/aktualisiere Abhaengigkeiten ...
"%VENV_PY%" -m pip install --upgrade pip
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo FEHLER: Installation der Abhaengigkeiten fehlgeschlagen.
    pause
    exit /b 1
)

REM -- Versionsnummer live aus dem Quellcode lesen (nie hartkodiert,
REM    damit diese Anzeige nie veralten kann) -------------------------
set "APP_VERSION=unbekannt"
for /f "usebackq delims=" %%v in (`"%VENV_PY%" -c "from voxini_studio import __version__; print(__version__)" 2^>nul`) do set "APP_VERSION=%%v"
title VOXini Video Studio - Entwicklungsstart (v%APP_VERSION%)

echo.
echo Starte VOXini Video Studio aus dem Quellcode - Version %APP_VERSION% ...
echo ============================================================
"%VENV_PY%" -m voxini_studio.main
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
    echo.
    echo Die Anwendung wurde mit Fehlercode %EXITCODE% beendet.
    echo Ein Fehlerprotokoll findest du unter:
    echo   %%APPDATA%%\VOXiniVideoStudio\logs\error.log
    pause
)

exit /b %EXITCODE%
