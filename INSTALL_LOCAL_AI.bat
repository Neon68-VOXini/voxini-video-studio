@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
title VOXini Video Studio - Lokale KI-Umgebung einrichten (ComfyUI + PyTorch + Wan2.2)

echo ============================================================
echo  VOXini Video Studio - INSTALL_LOCAL_AI.bat
echo  Richtet ComfyUI + PyTorch (automatisch AMD ROCm ODER NVIDIA
echo  CUDA, je nach erkannter Grafikkarte) + Wan2.2 TI2V 5B in
echo  einer VOLLSTAENDIG GETRENNTEN Python-3.12-Umgebung ein.
echo  Diese Umgebung wird NIEMALS mit der Python-Umgebung der
echo  VOXini-App selbst vermischt - beide laufen als getrennte
echo  Prozesse und sprechen nur ueber HTTP (127.0.0.1) miteinander.
echo ============================================================
echo.
echo Dieses Skript laedt KOSTENLOSE, OFFIZIELLE Software herunter:
echo   - ComfyUI (github.com/comfyanonymous/ComfyUI)
echo   - PyTorch mit GPU-Unterstuetzung - AMD ROCm 7.2.1 (repo.radeon.com)
echo     ODER NVIDIA CUDA (download.pytorch.org), je nachdem, welche
echo     Grafikkarte gleich automatisch erkannt wird.
echo Die grossen Wan2.2-Modelldateien (ca. 17 GB) werden HIER NICHT
echo heruntergeladen - das geschieht spaeter im "Einrichtungsassistent"
echo der VOXini-App selbst, dort mit genauer Groessen-/Zielordner-
echo Anzeige und ausdruecklicher Bestaetigung je Datei.
echo.
echo WICHTIGER HINWEIS zur GPU-Kompatibilitaet (AMD):
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
echo [1/7] Suche Python 3.12 ...
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
echo   Sowohl AMDs ROCm- als auch die hier verwendeten NVIDIA-CUDA-
echo   PyTorch-Pakete fuer Windows benoetigen exakt Python 3.12
echo   (cp312-Wheels).
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
echo [2/7] Lege getrennte Python-3.12-Umgebung an: %VENV_DIR%
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
echo [3/7] Lade ComfyUI herunter (github.com/comfyanonymous/ComfyUI) ...
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

REM -- 4) Grafikkarten-Hersteller erkennen (Kandidat 1, Task #577) --------
REM Root cause of the change below: this script previously ALWAYS installed
REM AMD ROCm PyTorch wheels, regardless of the actually installed GPU - on
REM an NVIDIA machine that install would either fail outright or silently
REM produce a non-functional PyTorch (no matching GPU driver), with no
REM warning that anything was wrong. Detecting the vendor first and
REM branching to the matching wheel set (or clearly warning on neither)
REM fixes that at the root instead of leaving it to the user to notice
REM ComfyUI "worked" but never actually used the GPU.
echo [4/7] Erkenne Grafikkarten-Hersteller ...
set "GPU_VENDOR=unknown"
set "GPU_NAMES="
for /f "usebackq delims=" %%G in (`powershell -NoProfile -Command "try { (Get-CimInstance Win32_VideoController).Name -join '; ' } catch { '' }" 2^>nul`) do set "GPU_NAMES=%%G"

if "%GPU_NAMES%"=="" (
    echo   Konnte keine Grafikkarte ueber Windows abfragen ^(PowerShell/WMI
    echo   nicht verfuegbar oder fehlgeschlagen^).
    goto :gpu_vendor_done
)
echo   Erkannte Grafikkarte^(n^): %GPU_NAMES%

REM Prefer a discrete NVIDIA or AMD adapter over an integrated one (e.g.
REM Intel) that might otherwise be the only/first entry - same "prefer the
REM real GPU" logic as environment_check.py's parse_wmi_video_controller_csv,
REM kept here as an independent copy since this is a standalone .bat with no
REM access to the Python-side helper. Keep these two in sync if either
REM changes (see _guess_gpu_vendor's docstring in environment_check.py).
echo %GPU_NAMES% | findstr /i "NVIDIA GeForce Quadro RTX Tesla" >nul
if not errorlevel 1 set "GPU_VENDOR=nvidia"
if "%GPU_VENDOR%"=="unknown" (
    echo %GPU_NAMES% | findstr /i "AMD Radeon" >nul
    if not errorlevel 1 set "GPU_VENDOR=amd"
)

:gpu_vendor_done
if /I "%GPU_VENDOR%"=="nvidia" echo   ^-^> NVIDIA-GPU erkannt: installiere CUDA-PyTorch.
if /I "%GPU_VENDOR%"=="amd" echo   ^-^> AMD-GPU erkannt: installiere ROCm-PyTorch.
if /I "%GPU_VENDOR%"=="unknown" (
    echo   ^-^> Keine unterstuetzte GPU ^(AMD/NVIDIA^) eindeutig erkannt.
)
echo.

REM -- 5) PyTorch installieren (vendor-spezifisch) ------------------------
echo [5/7] Installiere PyTorch mit GPU-Unterstuetzung (kann mehrere Minuten dauern) ...
"%VENV_PY%" -m pip install --upgrade pip

if /I "%GPU_VENDOR%"=="nvidia" goto :install_cuda
if /I "%GPU_VENDOR%"=="amd" goto :install_rocm
goto :install_unknown_vendor

:install_cuda
echo   NVIDIA-GPU erkannt - installiere offizielle CUDA-PyTorch-Wheels
echo   von download.pytorch.org (CUDA 12.4). Ein aktueller NVIDIA-Treiber
echo   (Game-Ready oder Studio) muss bereits installiert sein - das
echo   separate "CUDA Toolkit" ist NICHT noetig, PyTorchs Wheel bringt
echo   die benoetigten CUDA-Laufzeitbibliotheken bereits mit.
"%VENV_PY%" -m pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
if errorlevel 1 (
    echo   WARNUNG: CUDA-PyTorch-Installation fehlgeschlagen.
    echo   Pruefe https://pytorch.org/get-started/locally/ fuer die aktuell
    echo   passenden Installationsbefehle fuer deine NVIDIA-Treiberversion.
)
goto :torch_install_done

:install_rocm
echo   AMD-GPU erkannt - installiere offizielle AMD-ROCm-Wheels.
echo   Quelle: https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/install/installrad/windows/install-pytorch.html
echo   (ROCm 7.2.1, PyTorch 2.9.1, Python 3.12 - falls diese Version nicht
echo    mehr verfuegbar ist, bitte die aktuellen Wheel-URLs von obiger
echo    offizieller AMD-Seite in dieses Skript uebernehmen.)
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
goto :torch_install_done

:install_unknown_vendor
echo.
echo   WARNUNG: Es konnte keine unterstuetzte GPU (AMD oder NVIDIA) eindeutig
echo   erkannt werden. Lokale Wan2.2-Generierung braucht in der Praxis eine
echo   GPU mit ausreichend VRAM - ohne sie ist eine CPU-only-Installation
echo   fuer Videogenerierung extrem langsam (voraussichtlich unbrauchbar,
echo   ggf. Stunden pro Szene statt Sekunden/Minuten).
echo.
set /p CPU_CONFIRM="   Trotzdem mit einer CPU-only-PyTorch-Installation fortfahren? (j/N): "
if /I not "%CPU_CONFIRM%"=="j" (
    echo.
    echo   Abgebrochen. Bitte pruefe Grafikkartentreiber/-hardware und starte
    echo   dieses Skript danach erneut - oder installiere PyTorch manuell
    echo   passend zu deiner Hardware (siehe https://pytorch.org/get-started/locally/).
    pause
    exit /b 1
)
echo   Installiere CPU-only-PyTorch (kein GPU-Backend) ...
"%VENV_PY%" -m pip install --no-cache-dir torch torchvision torchaudio
if errorlevel 1 (
    echo   WARNUNG: CPU-PyTorch-Installation fehlgeschlagen.
)
goto :torch_install_done

:torch_install_done
echo.

REM -- 6) ComfyUI-Abhaengigkeiten installieren --------------------------
echo [6/7] Installiere uebrige ComfyUI-Abhaengigkeiten ...
"%VENV_PY%" -m pip install -r "%COMFYUI_DIR%\requirements.txt"
echo.

REM -- 7) Startskript fuer ComfyUI erzeugen -----------------------------
echo [7/7] Erzeuge Startskript fuer ComfyUI ...
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
echo     ROCm-CUDA/ComfyUI-Verbindung/Modelle zu verifizieren.
echo ============================================================
pause
exit /b 0
