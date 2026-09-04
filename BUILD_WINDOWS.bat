@echo off
setlocal EnableExtensions
chcp 65001 >nul
title VOXini Video Studio - Windows-Build erstellen

echo ============================================================
echo  VOXini Video Studio - BUILD_WINDOWS.bat
echo  Erstellt den Anwendungsordner "VOXini Video Studio\" direkt in
echo  diesem Ordner (PyInstaller-Onedir-Build, seit Version 1.1.0 -
echo  vorher Onefile). Der komplette Ordner (.exe + _internal\) ist
echo  die Anwendung und muss immer zusammen bleiben/verschoben werden.
echo  ComfyUI/ROCm/Wan2.2 bleiben eine getrennte, separat installierte
echo  Umgebung, siehe INSTALL_LOCAL_AI.bat.
echo ============================================================
echo.

cd /d "%~dp0"
set "VENV_DIR=%~dp0.venv"
set "OUTPUT_DIR=%~dp0VOXini Video Studio"
set "OUTPUT_EXE=%OUTPUT_DIR%\VOXini Video Studio.exe"

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
        pause
        exit /b 1
    )
)

set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

echo.
echo Installiere Build-Abhaengigkeiten ...
"%VENV_PY%" -m pip install --upgrade pip
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo FEHLER: Installation der Abhaengigkeiten fehlgeschlagen.
    pause
    exit /b 1
)

echo.
echo Entferne alten Build-Zwischenordner (build\) und alten Anwendungsordner ...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "%OUTPUT_DIR%" rmdir /s /q "%OUTPUT_DIR%"

echo.
echo Baue mit PyInstaller (voxini_studio.spec, Onedir-Modus) ...
echo Das kann - da Python, Qt und ein gebuendeltes ffmpeg/ffprobe
echo eingebettet werden - einige Minuten dauern.
echo ============================================================
"%VENV_PY%" -m PyInstaller --noconfirm --distpath "%CD%" --workpath "%CD%\build" "%CD%\voxini_studio.spec"
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
    echo.
    echo FEHLER: PyInstaller-Build fehlgeschlagen ^(Code %EXITCODE%^).
    pause
    exit /b %EXITCODE%
)

if not exist "%OUTPUT_EXE%" (
    echo.
    echo FEHLER: Build meldete Erfolg, aber "%OUTPUT_EXE%" wurde nicht gefunden.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Build erfolgreich.
echo  Startbare Anwendung liegt direkt hier:
echo    %OUTPUT_EXE%
echo.
echo  WICHTIG: Der GESAMTE Ordner "VOXini Video Studio\" (inkl. des
echo  Unterordners "_internal\") gehoert zusammen und muss immer als
echo  Einheit verschoben/kopiert/weitergegeben werden - nicht nur die
echo  .exe-Datei allein. Grund fuer den Wechsel von Onefile auf Onedir:
echo  Onefile hat sich bei jedem Start neu in einen temporaeren Ordner
echo  entpackt, wobei Antivirus-Software das Programm gelegentlich am
echo  Start hindern konnte ("Security validation failure"). Onedir
echo  entpackt sich nicht mehr bei jedem Start, das Problem entfaellt.
echo.
echo  WICHTIG: ComfyUI/ROCm/Wan2.2 sind NICHT in dieser Anwendung
echo  enthalten und muessen separat ueber INSTALL_LOCAL_AI.bat
echo  eingerichtet werden. Modelldateien werden nie eingebaut, sondern
echo  beim ersten Start ueber den Einrichtungsassistenten an einen frei
echo  waehlbaren Ort heruntergeladen.
echo ============================================================
pause
exit /b 0
