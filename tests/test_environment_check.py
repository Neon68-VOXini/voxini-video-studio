import shutil
import tempfile
from pathlib import Path

import pytest

from voxini_studio.core.environment_check import (
    REQUIRED_MODELS,
    check_comfyui_connection,
    check_environment,
    check_models,
    detect_gpu,
    detect_rocm,
    free_disk_space_gb,
    parse_nvidia_smi_csv,
    parse_rocm_smi_json,
    parse_rocm_version,
    parse_wmi_video_controller_csv,
)

ROCM_SMI_SAMPLE = """
{
    "card0": {
        "Card series": "Radeon RX 7600 XT",
        "VRAM Total Memory (B)": "17179869184"
    }
}
"""

WMI_CSV_SAMPLE = '"Name","AdapterRAM"\n"AMD Radeon RX 7600 XT","17179869184"\n'

HIPCONFIG_SAMPLE = "6.2.41133-dd7f95766\nHIP version\n"


def test_parse_rocm_smi_json_extracts_gpu_and_vram():
    info = parse_rocm_smi_json(ROCM_SMI_SAMPLE)
    assert info.detected is True
    assert "7600 XT" in info.name
    assert info.vram_bytes == 17179869184
    assert info.vram_gb == pytest.approx(16.0, abs=0.1)


def test_parse_rocm_smi_json_empty_data():
    info = parse_rocm_smi_json("{}")
    assert info.detected is False


def test_parse_wmi_csv_extracts_gpu_and_vram():
    info = parse_wmi_video_controller_csv(WMI_CSV_SAMPLE)
    assert info.detected is True
    assert "7600 XT" in info.name
    assert info.vram_bytes == 17179869184
    assert info.vendor == "amd"


def test_parse_wmi_csv_malformed_returns_not_detected():
    info = parse_wmi_video_controller_csv("not,a,valid,header\n1,2,3,4")
    assert info.detected is False


# -- Kandidat 1 (task #577): NVIDIA/vendor detection ------------------------


def test_parse_wmi_csv_nvidia_row_classified_correctly():
    csv = '"Name","AdapterRAM"\n"NVIDIA GeForce RTX 3060","4294967295"\n'
    info = parse_wmi_video_controller_csv(csv)
    assert info.detected is True
    assert info.vendor == "nvidia"


def test_parse_wmi_csv_prefers_discrete_gpu_over_integrated():
    # integrated Intel adapter listed first, discrete NVIDIA GPU second -
    # picking the first row blindly would misreport vendor "unknown" even
    # though a supported discrete GPU is present.
    csv = (
        '"Name","AdapterRAM"\n'
        '"Intel(R) UHD Graphics 770","1073741824"\n'
        '"NVIDIA GeForce RTX 4070","4294967295"\n'
    )
    info = parse_wmi_video_controller_csv(csv)
    assert info.detected is True
    assert info.vendor == "nvidia"
    assert "RTX 4070" in info.name


def test_parse_wmi_csv_unknown_vendor_still_reports_first_row():
    csv = '"Name","AdapterRAM"\n"Microsoft Basic Display Adapter","0"\n'
    info = parse_wmi_video_controller_csv(csv)
    assert info.detected is True
    assert info.vendor == "unknown"


def test_parse_rocm_smi_json_reports_amd_vendor():
    info = parse_rocm_smi_json(ROCM_SMI_SAMPLE)
    assert info.vendor == "amd"


def test_parse_nvidia_smi_csv_extracts_gpu_and_vram():
    info = parse_nvidia_smi_csv("NVIDIA GeForce RTX 4070, 12282\n")
    assert info.detected is True
    assert info.vendor == "nvidia"
    assert "RTX 4070" in info.name
    assert info.vram_bytes == 12282 * 1024 * 1024


def test_parse_nvidia_smi_csv_empty_returns_not_detected():
    info = parse_nvidia_smi_csv("")
    assert info.detected is False


def test_check_environment_nvidia_low_vram_warns_about_cuda_not_rocm(monkeypatch):
    # Simulate a detected-but-not-yet-installed NVIDIA GPU (pre-install WMI
    # path) with low VRAM - the warning text must say CUDA, not ROCm.
    import voxini_studio.core.environment_check as ec

    fake_gpu = ec.GPUInfo(detected=True, name="NVIDIA GeForce GTX 1650", vram_bytes=4 * 1024**3, source="wmi", vendor="nvidia")
    monkeypatch.setattr(ec, "detect_gpu_and_rocm_via_torch", lambda install_dir: (ec.GPUInfo(detected=False), ec.ROCmInfo(installed=False)))
    monkeypatch.setattr(ec, "detect_gpu", lambda: fake_gpu)
    monkeypatch.setattr(ec, "detect_rocm", lambda: ec.ROCmInfo(installed=False))

    report = check_environment(models_dir="")
    assert any("CUDA" in w for w in report.warnings)
    assert not any("ROCm wurde nicht gefunden" in w for w in report.warnings)


def test_parse_rocm_version():
    assert parse_rocm_version(HIPCONFIG_SAMPLE) == "6.2.41133-dd7f95766"


def test_detect_gpu_returns_not_detected_when_no_tools_present():
    # in this sandbox there is genuinely no rocm-smi/powershell - this
    # exercises the real graceful-fallback code path, not a mock
    info = detect_gpu()
    assert info.detected is False
    assert info.source == "none"


def test_detect_rocm_returns_not_installed_when_absent():
    info = detect_rocm()
    assert info.installed is False


def test_check_comfyui_connection_unreachable_by_default():
    result = check_comfyui_connection("127.0.0.1", 8188, timeout=1.0)
    assert result.reachable is False
    assert result.message


def test_check_models_all_missing_without_dir():
    checks = check_models("")
    assert len(checks) == len(REQUIRED_MODELS)
    assert all(not c.found for c in checks)


def test_check_models_detects_present_files(tmp_path):
    spec = REQUIRED_MODELS[1]  # VAE - smallest, fine to fake content for
    model_dir = tmp_path / spec["subdir"]
    model_dir.mkdir(parents=True)
    (model_dir / spec["filename"]).write_bytes(b"\x00" * 1024)

    checks = check_models(tmp_path)
    vae_check = next(c for c in checks if c.filename == spec["filename"])
    assert vae_check.found is True
    assert vae_check.size_bytes == 1024

    diffusion_check = next(c for c in checks if c.filename == REQUIRED_MODELS[0]["filename"])
    assert diffusion_check.found is False


def test_free_disk_space_gb_returns_positive_number():
    gb = free_disk_space_gb(tempfile.gettempdir())
    assert gb > 0


def test_check_environment_reports_not_ready_and_collects_warnings():
    report = check_environment(comfyui_host="127.0.0.1", comfyui_port=8188, models_dir="")
    assert report.ready_for_local_generation is False
    assert len(report.warnings) > 0
    assert any("ComfyUI" in w or "GPU" in w or "ROCm" in w for w in report.warnings)


def test_check_environment_all_models_present_still_not_ready_without_gpu(tmp_path):
    for spec in REQUIRED_MODELS:
        d = tmp_path / spec["subdir"]
        d.mkdir(parents=True, exist_ok=True)
        (d / spec["filename"]).write_bytes(b"\x00" * 10)

    report = check_environment(models_dir=str(tmp_path))
    assert all(m.found for m in report.models)
    # still not ready overall in this sandbox: no GPU/ROCm/ComfyUI
    assert report.ready_for_local_generation is False
