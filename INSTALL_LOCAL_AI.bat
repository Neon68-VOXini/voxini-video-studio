@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
title VOXini Video Studio - Lokale KI-Umgebung einrichten (ComfyUI + ROCm + Wan2.2)

echo ============================================================
echo  VOXini Video Studio - INSTALL_LOCAL_AI.bat
echo  Richtet ComfyUI + AMD ROCm + Wan2.2 TI2V 5B in einer
echo  VOLLSTAENDIG GETRENNTEN Python-3.12-Umgebung ein.
echo  Diese Umgebung wird NIEMALS mit der Python-Umgebung der
echo  VOXini-App selbst vermischt - beide laufen als getrennte
echo  Prozesse und sprechen nur ueber HTTP (127.0.0.1) miteinander.
echo ============================================================
echo.
echo Dieses Skript laedt KOSTENLOSE, OFFIZIELLE Software herunter:
echo   - ComfyUI (github.com/comfyanonymous/ComfyUI)
echo   - AMD ROCm 7.2.1 fuer PyTorch unter Windows (repo.radeon.com)
echo   - PyTorch/torchvision/torchaudio mit ROCm-Unterstuetzung
echo Die grossen Wan2.2-Modelldateien (ca. 17 GB) werden HIER NICHT
echo heruntergeladen - das geschieht spaeter im "Einrichtungsassistent"
echo der VOXini-App selbst, dort mit genauer Groessen-/Zielordner-
echo Anzeige und ausdruecklicher Bestaetigung je Datei.
echo.
echo WICHTIGER HINWEIS zur GPU-Kompatibilitaet:
echo   AMDs offizielle ROCm-7.2.1-Unterstuetzung unter Windows listet
echo   aktuell folgende Architekturen: gfx1100, gfx1101, gfx1200, gfx1201
echo   (u. a. RX 7900 XTX, RX 7700, RX 9070/9070 XT, RX 9060 XT).
echo   Die RX 7600 XT (gfx1102) steht NICHT auf dieser offiziellen Liste.
echo   Die Installation unten kann trotzdem versucht werden - falls
echo   PyTorch die GPU nicht erkennt, pruefe die aktuelle Liste unter:
echo   https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/compatibility/compatibilityrad/windows/windows_compatibility.html
echo   und ziehe als Alternative "torch-directml" in Betracht (siehe
echo   deutsche Bedienungsanleitung, Abschnitt "Fehlerbehebung").
echo.
pause

set "INSTALL_ROOT=%~dp0comfyui_env"
set "VENV_DIR=%INSTALL_ROOT%\venv312"
set "COMFYUI_DIR=%INSTALL_ROOT%\ComfyUI"

echo.
echo Zielordner fuer die lokale KI-Umgebung: %INSTALL_ROOT%
echo (Kann in dieser Datei am Anfang der Zeile "set INSTALL_ROOT="
echo  geaendert werden, falls du einen anderen Ort bevorzugst.)
echo.

REM -- 1) Python 3.12 finden --------------------------------------------
echo [1/6] Suche Python 3.12 ...
where py >nul 2>nul
if errorlevel 1 goto :no_py_launcher

py -3.12 -c "import sys; print(sys.version)" >nul 2>nul
if errorlevel 1 goto :no_py312

set "PY312=py -3.12"
echo   Gefunden: Python 3.12 (ueber den Windows py-Launcher).
goto :have_python

:no_py_launcher
where python3.12 >nul 2>nul
if errorlevel 1 goto :no_py312
set "PY312=python3.12"
echo   Gefunden: python3.12 im PATH.
goto :have_python

:no_py312
echo.
echo   FEHLER: Python 3.12 wurde nicht gefunden.
echo   AMDs offizielle ROCm-PyTorch-Pakete fuer Windows benoetigen
echo   exakt Python 3.12 (cp312-Wheels).
echo.
echo   Bitte installiere Python 3.12 von:
echo     https://www.python.org/downloads/release/python-3120/
echo   ("Windows installer (64-bit)", Haken bei "Add python.exe to PATH")
echo   und fuehre dieses Skript danach erneut aus.
echo.
pause
exit /b 1

:have_python
echo.

REM -- 2) Getrennte venv anlegen -----------------------------------------
echo [2/6] Lege getrennte Python-3.12-Umgebung an: %VENV_DIR%
if exist "%VENV_DIR%\Scripts\python.exe" (
    echo   Umgebung existiert bereits, ueberspringe Neuanlage.
) else (
    mkdir "%INSTALL_ROOT%" 2>nul
    %PY312% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo   FEHLER: venv-Erstellung fehlgeschlagen.
        pause
        exit /b 1
    )
)
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
echo.

REM -- 3) ComfyUI herunterladen --------------------------------------------
echo [3/6] Lade ComfyUI herunter (github.com/comfyanonymous/ComfyUI) ...
if exist "%COMFYUI_DIR%\main.py" goto :comfyui_present

where git >nul 2>nul
if errorlevel 1 goto :comfyui_via_zip

git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git "%COMFYUI_DIR%"
if errorlevel 1 (
    echo   FEHLER: git clone fehlgeschlagen. Bitte Internetverbindung pruefen.
    pause
    exit /b 1
)
goto :comfyui_done

:comfyui_via_zip
echo   git nicht gefunden - lade ZIP-Archiv stattdessen ...
set "FETCH_PY=%TEMP%\voxini_fetch_comfyui.py"
> "%FETCH_PY%" (
    echo import urllib.request, zipfile, io, os
    echo url = "https://github.com/comfyanonymous/ComfyUI/archive/refs/heads/master.zip"
    echo data = urllib.request.urlopen(url^).read(^)
    echo z = zipfile.ZipFile(io.BytesIO(data^)^)
    echo z.extractall(r"%INSTALL_ROOT%"^)
    echo for n in os.listdir(r"%INSTALL_ROOT%"^):
    echo     if n.startswith("ComfyUI-"^):
    echo         os.rename(os.path.join(r"%INSTALL_ROOT%", n^), r"%COMFYUI_DIR%"^)
)
"%VENV_PY%" "%FETCH_PY%"
if errorlevel 1 (
    echo   FEHLER: ComfyUI-Download fehlgeschlagen. Bitte Internetverbindung pruefen.
    pause
    exit /b 1
)
del "%FETCH_PY%" >nul 2>nul
goto :comfyui_done

:comfyui_present
echo   ComfyUI ist bereits vorhanden, ueberspringe Download.

:comfyui_done
echo.

REM -- 4) ROCm-PyTorch installieren (offizielle AMD-Wheels, Stand siehe Kommentar) --
echo [4/6] Installiere PyTorch mit ROCm-Unterstuetzung (kann mehrere Minuten dauern) ...
echo   Quelle: https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/install/installrad/windows/install-pytorch.html
echo   (ROCm 7.2.1, PyTorch 2.9.1, Python 3.12 - falls diese Version nicht
echo    mehr verfuegbar ist, bitte die aktuellen Wheel-URLs von obiger
echo    offizieller AMD-Seite in dieses Skript uebernehmen.)
"%VENV_PY%" -m pip install --upgrade pip
"%VENV_PY%" -m pip install --no-cache-dir ^
    https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/rocm_sdk_core-7.2.1-py3-none-win_amd64.whl ^
    https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/rocm_sdk_devel-7.2.1-py3-none-win_amd64.whl ^
    https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/rocm_sdk_libraries_custom-7.2.1-py3-none-win_amd64.whl ^
    https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/rocm-7.2.1.tar.gz
if errorlevel 1 (
    echo   WARNUNG: ROCm-SDK-Installation fehlgeschlagen oder Version veraltet.
    echo   Bitte pruefe die aktuelle Anleitung unter obiger AMD-URL.
)
"%VENV_PY%" -m pip install --no-cache-dir ^
    https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/torch-2.9.1%%2Brocm7.2.1-cp312-cp312-win_amd64.whl ^
    https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/torchaudio-2.9.1%%2Brocm7.2.1-cp312-cp312-win_amd64.whl ^
    https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/torchvision-0.24.1%%2Brocm7.2.1-cp312-cp312-win_amd64.whl
if errorlevel 1 (
    echo   WARNUNG: PyTorch-ROCm-Installation fehlgeschlagen oder Version veraltet.
    echo   Bitte pruefe die aktuelle Anleitung unter obiger AMD-URL und passe
    echo   die Wheel-URLs in diesem Skript entsprechend an.
)
echo.

REM -- 5) ComfyUI-Abhaengigkeiten installieren --------------------------
echo [5/6] Installiere uebrige ComfyUI-Abhaengigkeiten ...
"%VENV_PY%" -m pip install -r "%COMFYUI_DIR%\requirements.txt"
echo.

REM -- 6) Startskript fuer ComfyUI erzeugen -----------------------------
echo [6/6] Erzeuge Startskript fuer ComfyUI ...
> "%INSTALL_ROOT%\START_COMFYUI.bat" (
    echo @echo off
    echo title ComfyUI ^(fuer VOXini Video Studio^)
    echo cd /d "%COMFYUI_DIR%"
    echo "%VENV_PY%" main.py --listen 127.0.0.1 --port 8188
    echo pause
)
echo   Erzeugt: %INSTALL_ROOT%\START_COMFYUI.bat
echo.

echo ============================================================
echo  Fertig. Naechste Schritte:
echo  1. %INSTALL_ROOT%\START_COMFYUI.bat ausfuehren, um ComfyUI zu starten.
echo  2. In VOXini Video Studio den "Einrichtungsassistent" oeffnen,
echo     dort den Modellordner waehlen und die Wan2.2-Modelldateien
echo     herunterladen (Groesse/Zielordner werden dort bestaetigt).
echo  3. Im Einrichtungsassistent auf "Jetzt pruefen" klicken, um GPU/
echo     ROCm/ComfyUI-Verbindung/Modelle zu verifizieren.
echo ============================================================
pause
exit /b 0
