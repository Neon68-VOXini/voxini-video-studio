@echo off
setlocal EnableExtensions
chcp 65001 >nul
title VOXini Video Studio - Entwicklungsstart

echo ============================================================
echo  VOXini Video Studio - START_DEV.bat
echo  Legt (beim ersten Mal) eine eigene Python-Umgebung fuer die
echo  App selbst an (NICHT die ComfyUI-Umgebung - siehe
echo  INSTALL_LOCAL_AI.bat fuer diese getrennte Umgebung) und
echo  startet die Anwendung.
echo ============================================================
echo.

cd /d "%~dp0"
set "VENV_DIR=%~dp0.venv"

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

echo.
echo Starte VOXini Video Studio ...
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
