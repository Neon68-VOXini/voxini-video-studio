"""Checks whether the machine is ready for local (ComfyUI/ROCm/Wan2.2)
generation: GPU + VRAM, ROCm install, ComfyUI reachability, and required
model files. Every OS-facing call is isolated behind a small function so
the *parsing* logic (the part that can actually go wrong) is fully unit
testable with canned command output, independent of whether this sandbox
has an AMD GPU, ROCm or ComfyUI installed - here, none of that is present,
so the live checks correctly and honestly report "not detected", which is
itself part of what's being tested.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Expected Wan2.2 TI2V 5B ComfyUI model files - filenames/dirs verified
# against the Comfy-Org/Wan_2.2_ComfyUI_Repackaged Hugging Face repo and
# the official ComfyUI Wan2.2 tutorial. Sizes below are indicative only;
# the setup wizard always re-checks the real size via an HTTP HEAD request
# before downloading anything, per the "always show size before large
# downloads" requirement - these numbers are just what's shown while that
# live check is in flight.
REQUIRED_MODELS = [
    {
        "name": "Wan2.2 TI2V 5B Diffusion Model",
        "subdir": "diffusion_models",
        "filename": "wan2.2_ti2v_5B_fp16.safetensors",
        "approx_size_gb": 10.0,
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors",
    },
    {
        "name": "Wan2.2 VAE",
        "subdir": "vae",
        "filename": "wan2.2_vae.safetensors",
        "approx_size_gb": 0.32,
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan2.2_vae.safetensors",
    },
    {
        "name": "UMT5-XXL Text Encoder (fp8)",
        "subdir": "text_encoders",
        "filename": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "approx_size_gb": 6.7,
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    },
]

MIN_VRAM_GB_RECOMMENDED = 12.0


@dataclass
class GPUInfo:
    detected: bool
    name: str = ""
    vram_bytes: int = 0
    source: str = ""  # "rocm-smi" | "wmi" | "none"

    @property
    def vram_gb(self) -> float:
        return round(self.vram_bytes / (1024 ** 3), 1)


@dataclass
class ROCmInfo:
    installed: bool
    version: str = ""
    path: str = ""


@dataclass
class ComfyUIConnection:
    reachable: bool
    version: str = ""
    message: str = ""


@dataclass
class ModelCheck:
    name: str
    filename: str
    found: bool
    path: str = ""
    size_bytes: int = 0


@dataclass
class EnvironmentReport:
    gpu: GPUInfo
    rocm: ROCmInfo
    comfyui: ComfyUIConnection
    models: list[ModelCheck]
    free_disk_gb: float
    warnings: list[str] = field(default_factory=list)

    @property
    def ready_for_local_generation(self) -> bool:
        return (
            self.gpu.detected
            and self.rocm.installed
            and self.comfyui.reachable
            and all(m.found for m in self.models)
        )


# --- parsing (pure functions, fully unit-testable) -----------------------

def parse_rocm_smi_json(raw_json: str) -> GPUInfo:
    """Parses `rocm-smi --showproductname --showmeminfo vram --json` output."""
    data = json.loads(raw_json)
    for card_key, card in data.items():
        name = card.get("Card series") or card.get("Card Series") or card.get("GPU") or ""
        vram_raw = (
            card.get("VRAM Total Memory (B)")
            or card.get("vram_total_memory_b")
            or card.get("VRAM Total Memory")
        )
        if name or vram_raw:
            try:
                vram_bytes = int(vram_raw) if vram_raw is not None else 0
            except (TypeError, ValueError):
                vram_bytes = 0
            return GPUInfo(detected=True, name=name, vram_bytes=vram_bytes, source="rocm-smi")
    return GPUInfo(detected=False, source="rocm-smi")


def parse_wmi_video_controller_csv(raw_csv: str) -> GPUInfo:
    """Parses the CSV output of
    `Get-CimInstance Win32_VideoController | Select Name,AdapterRAM | ConvertTo-Csv -NoTypeInformation`.
    """
    lines = [ln.strip() for ln in raw_csv.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return GPUInfo(detected=False, source="wmi")
    import csv
    import io

    reader = csv.reader(io.StringIO("\n".join(lines)))
    rows = list(reader)
    header = [h.strip().strip('"') for h in rows[0]]
    try:
        name_idx = header.index("Name")
        ram_idx = header.index("AdapterRAM")
    except ValueError:
        return GPUInfo(detected=False, source="wmi")

    for row in rows[1:]:
        if len(row) <= max(name_idx, ram_idx):
            continue
        name = row[name_idx].strip().strip('"')
        ram_raw = row[ram_idx].strip().strip('"')
        if not name:
            continue
        try:
            vram_bytes = int(ram_raw)
        except ValueError:
            vram_bytes = 0
        return GPUInfo(detected=True, name=name, vram_bytes=vram_bytes, source="wmi")
    return GPUInfo(detected=False, source="wmi")


def parse_rocm_version(hipconfig_output: str) -> str:
    for line in hipconfig_output.splitlines():
        line = line.strip()
        if line and line[0].isdigit():
            return line.split()[0]
    return ""


# --- live checks (subprocess/network - best-effort, graceful fallback) ---

def detect_gpu() -> GPUInfo:
    rocm_smi = shutil.which("rocm-smi")
    if rocm_smi:
        try:
            result = subprocess.run(
                [rocm_smi, "--showproductname", "--showmeminfo", "vram", "--json"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                info = parse_rocm_smi_json(result.stdout)
                if info.detected:
                    return info
        except Exception:
            pass

    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell:
        try:
            result = subprocess.run(
                [
                    powershell, "-NoProfile", "-Command",
                    "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM | ConvertTo-Csv -NoTypeInformation",
                ],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                info = parse_wmi_video_controller_csv(result.stdout)
                if info.detected:
                    return info
        except Exception:
            pass

    return GPUInfo(detected=False, source="none")


def detect_rocm() -> ROCmInfo:
    hipconfig = shutil.which("hipconfig")
    if hipconfig:
        try:
            result = subprocess.run([hipconfig, "--version"], capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and result.stdout.strip():
                version = parse_rocm_version(result.stdout)
                return ROCmInfo(installed=True, version=version, path=hipconfig)
        except Exception:
            pass

    rocm_smi = shutil.which("rocm-smi")
    if rocm_smi:
        return ROCmInfo(installed=True, version="unbekannt", path=rocm_smi)

    return ROCmInfo(installed=False)


def check_comfyui_connection(host: str = "127.0.0.1", port: int = 8188, timeout: float = 5.0) -> ComfyUIConnection:
    try:
        import requests

        resp = requests.get(f"http://{host}:{port}/system_stats", timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            version = ""
            try:
                version = str(data.get("system", {}).get("comfyui_version", ""))
            except Exception:
                pass
            return ComfyUIConnection(reachable=True, version=version, message="Verbindung erfolgreich.")
        return ComfyUIConnection(reachable=False, message=f"HTTP {resp.status_code}")
    except Exception as exc:
        return ComfyUIConnection(reachable=False, message=str(exc))


def find_pid_listening_on_port(port: int) -> Optional[int]:
    """Windows-only: finds the PID of whatever process is currently
    LISTENING on `port` by parsing `netstat -ano`. Used to locate ComfyUI's
    actual OS process regardless of how/when it was started (auto-launched
    by VOXini earlier this run, started manually by the user, or left over
    from a previous session) - tracking a subprocess.Popen handle only
    works for a process VOXini itself just spawned, which is not always the
    case here. Best-effort: returns None on any parsing/tooling failure."""
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    suffix = f":{port}"
    for line in result.stdout.splitlines():
        if "LISTENING" not in line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        local_addr = parts[1]
        if not local_addr.endswith(suffix):
            continue
        try:
            return int(parts[-1])
        except ValueError:
            continue
    return None


def kill_process_tree(pid: int) -> bool:
    """Force-kills a process and its whole child tree via Windows'
    `taskkill /T /F`. Used to fully terminate ComfyUI (see
    find_pid_listening_on_port) before relaunching it - see
    Project.comfyui_restart_every_n_scenes / GenerationPanel's
    _GenerationWorker._restart_comfyui() for why a full process restart is
    needed instead of relying on POST /free alone (ROCm/PyTorch caching
    allocator can leave VRAM fragmented across a long-running process even
    after every model is explicitly unloaded)."""
    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0
    except Exception:
        return False


def check_models(models_dir: str | Path) -> list[ModelCheck]:
    base = Path(models_dir) if models_dir else None
    checks = []
    for spec in REQUIRED_MODELS:
        path = base / spec["subdir"] / spec["filename"] if base else None
        found = bool(path and path.exists())
        size = path.stat().st_size if found else 0
        checks.append(
            ModelCheck(
                name=spec["name"], filename=spec["filename"], found=found,
                path=str(path) if path else "", size_bytes=size,
            )
        )
    return checks


def free_disk_space_gb(path: str | Path) -> float:
    try:
        usage = shutil.disk_usage(str(path) if path else ".")
        return round(usage.free / (1024 ** 3), 1)
    except Exception:
        return 0.0


def detect_gpu_and_rocm_via_torch(comfyui_install_dir: str | Path) -> tuple[GPUInfo, ROCmInfo]:
    """Fragt die tatsaechliche PyTorch/ROCm-Installation direkt in der
    isolierten comfyui_env\\venv312-Umgebung ab (torch.cuda.*), statt sich
    wie detect_gpu()/detect_rocm() auf System-PATH-Werkzeuge (hipconfig/
    rocm-smi) oder WMI zu verlassen.

    Root-Cause (Live-Test 31.08.2026, RX 7600 XT): INSTALL_LOCAL_AI.bat
    installiert ROCm ausschliesslich als PyTorch-Wheel in eine eigene, vom
    System komplett isolierte Umgebung (comfyui_env\\venv312) - es gibt
    dabei bewusst KEINE systemweiten hipconfig/rocm-smi-Kommandozeilen-
    werkzeuge (das waere ein vollstaendiges ROCm-SDK, das dieses Skript
    nicht installiert). detect_rocm() fand deshalb IMMER "nicht
    installiert", obwohl ROCm im echten Betrieb lief (siehe reales
    ComfyUI-Startprotokoll: "pytorch version: 2.9.1+rocm7.2.1", "AMD arch:
    gfx1102", "Device: cuda:0 AMD Radeon RX 7600 XT"). Zusaetzlich lieferte
    die WMI-basierte VRAM-Erkennung (Win32_VideoController.AdapterRAM)
    faelschlich nur 4.0 GB statt der echten 16 GB - ein bekannter
    Windows-Bug: AdapterRAM ist ein 32-Bit-Feld und wird bei modernen GPUs
    mit mehr als ca. 4 GB VRAM falsch gemeldet. Diese Funktion fragt
    stattdessen die Wahrheit direkt aus der Python-Umgebung ab, die
    ComfyUI tatsaechlich verwendet, und ist damit die verlaesslichere
    Quelle, wann immer diese Umgebung existiert."""
    if not comfyui_install_dir:
        return GPUInfo(detected=False, source="none"), ROCmInfo(installed=False)
    venv_python = Path(comfyui_install_dir).parent / "venv312" / "Scripts" / "python.exe"
    if not venv_python.exists():
        return GPUInfo(detected=False, source="none"), ROCmInfo(installed=False)
    probe = (
        "import json\n"
        "try:\n"
        "    import torch\n"
        "    avail = torch.cuda.is_available()\n"
        "    out = {'available': avail}\n"
        "    if avail:\n"
        "        props = torch.cuda.get_device_properties(0)\n"
        "        out['name'] = torch.cuda.get_device_name(0)\n"
        "        out['vram_bytes'] = int(props.total_memory)\n"
        "        out['hip_version'] = getattr(torch.version, 'hip', None) or ''\n"
        "    print(json.dumps(out))\n"
        "except Exception as exc:\n"
        "    print(json.dumps({'available': False, 'error': str(exc)}))\n"
    )
    try:
        result = subprocess.run(
            [str(venv_python), "-c", probe],
            capture_output=True, text=True, timeout=30,
        )
        out_lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
        data = json.loads(out_lines[-1]) if out_lines else {}
    except Exception:
        data = {}
    if data.get("available"):
        gpu = GPUInfo(
            detected=True, name=str(data.get("name", "")),
            vram_bytes=int(data.get("vram_bytes", 0) or 0), source="torch",
        )
        rocm = ROCmInfo(
            installed=True, version=str(data.get("hip_version") or "unbekannt"),
            path=str(venv_python),
        )
        return gpu, rocm
    return GPUInfo(detected=False, source="none"), ROCmInfo(installed=False)


def check_environment(
    comfyui_host: str = "127.0.0.1",
    comfyui_port: int = 8188,
    models_dir: str = "",
    comfyui_install_dir: str = "",
) -> EnvironmentReport:
    gpu, rocm = detect_gpu_and_rocm_via_torch(comfyui_install_dir)
    if not gpu.detected:
        gpu = detect_gpu()
    if not rocm.installed:
        rocm = detect_rocm()
    comfyui = check_comfyui_connection(comfyui_host, comfyui_port)
    models = check_models(models_dir)
    disk = free_disk_space_gb(models_dir or ".")

    warnings: list[str] = []
    if not gpu.detected:
        warnings.append("Keine AMD-GPU erkannt. Lokale Generierung wird nicht funktionieren.")
    elif gpu.vram_bytes and gpu.vram_gb < MIN_VRAM_GB_RECOMMENDED:
        warnings.append(
            f"Nur {gpu.vram_gb} GB VRAM erkannt - Wan2.2 5B empfiehlt mind. {MIN_VRAM_GB_RECOMMENDED} GB."
        )
    if not rocm.installed:
        warnings.append("ROCm wurde nicht gefunden. Bitte über INSTALL_LOCAL_AI.bat einrichten.")
    if not comfyui.reachable:
        warnings.append(f"ComfyUI unter {comfyui_host}:{comfyui_port} nicht erreichbar.")
    missing = [m.name for m in models if not m.found]
    if missing:
        warnings.append("Fehlende Modelle: " + ", ".join(missing))
    if disk and disk < 20.0:
        warnings.append(f"Nur noch {disk} GB freier Speicher - für Modelle werden ca. 17 GB benötigt.")

    return EnvironmentReport(
        gpu=gpu, rocm=rocm, comfyui=comfyui, models=models, free_disk_gb=disk, warnings=warnings
    )
