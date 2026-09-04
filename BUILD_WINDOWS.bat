@echo off
setlocal EnableExtensions
chcp 65001 >nul
title VOXini Video Studio - Windows-Build erstellen

echo ============================================================
echo  VOXini Video Studio - BUILD_WINDOWS.bat
echo  Erstellt EINE einzelne, eigenstaendige "VOXini Video Studio.exe"
echo  direkt in diesem Ordner (PyInstaller-Onefile-Build, kein
echo  separater dist-Ordner, keine separate Portable-Version/ZIP).
echo  ComfyUI/ROCm/Wan2.2 bleiben eine getrennte, separat installierte
echo  Umgebung, siehe INSTALL_LOCAL_AI.bat.
echo ============================================================
echo.

cd /d "%~dp0"
set "VENV_DIR=%~dp0.venv"
set "OUTPUT_EXE=%~dp0VOXini Video Studio.exe"

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
echo Entferne alten Build-Zwischenordner (build\) ...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "%OUTPUT_EXE%" del /f /q "%OUTPUT_EXE%"

echo.
echo Baue mit PyInstaller (voxini_studio.spec, Onefile-Modus) ...
echo Das kann - da eine einzelne, vollstaendig eingebettete EXE
echo entsteht (inkl. Python, Qt und einem gebuendelten ffmpeg/ffprobe) -
echo einige Minuten dauern.
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
echo  Einzelne startbare Datei liegt direkt hier:
echo    %OUTPUT_EXE%
echo.
echo  Es gibt keine separate "Portable"-Version und keine ZIP-Datei -
echo  diese eine .exe ist bereits die vollstaendige Anwendung
echo  (Python, Qt, Icons, Workflow-Vorlagen und ein gebuendeltes
echo  ffmpeg/ffprobe sind direkt eingebettet).
echo.
echo  WICHTIG: ComfyUI/ROCm/Wan2.2 sind NICHT in dieser EXE
echo  enthalten und muessen separat ueber INSTALL_LOCAL_AI.bat
echo  eingerichtet werden. Modelldateien werden nie in die EXE
echo  eingebaut, sondern beim ersten Start ueber den
echo  Einrichtungsassistenten an einen frei waehlbaren Ort
echo  heruntergeladen.
echo ============================================================
pause
exit /b 0
