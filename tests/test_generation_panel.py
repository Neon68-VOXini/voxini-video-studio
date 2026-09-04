"""Tests for the ComfyUI-Auto-Neustart ("restart every N scenes") feature in
_GenerationWorker (see voxini_studio/ui/generation_panel.py), added for
Project.comfyui_restart_every_n_scenes / task #590.

Two things are tested separately, matching the two independent pieces of
logic in the implementation:

- _restart_comfyui() itself: the actual kill/relaunch/reconnect mechanics
  (mocked subprocess + environment_check calls, no real ComfyUI needed).
- the scene-counting trigger logic in _GenerationWorker.run(): which scenes
  cause a restart to be attempted, and how a failed restart affects the
  rest of the batch. Here _restart_comfyui itself is stubbed out so this
  half is independent of the mechanics above.

Real end-to-end verification (does a real ComfyUI process on the target
AMD/ROCm machine actually come back up cleanly after N scenes on an
overnight run) is out of scope for this file by nature - see
Windows_Testprotokoll.md."""
from __future__ import annotations

import shutil
import tempfile

import pytest

pytest.importorskip("PySide6")

from voxini_studio.core.environment_check import ComfyUIConnection  # noqa: E402
from voxini_studio.core.project_manager import ProjectManager  # noqa: E402
from voxini_studio.models.project import ClipVersion, Scene, SceneStatus  # noqa: E402
from voxini_studio.ui import generation_panel  # noqa: E402
from voxini_studio.ui.generation_panel import _GenerationWorker  # noqa: E402


@pytest.fixture
def project_dir():
    d = tempfile.mkdtemp(prefix="voxini_restart_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def pm(project_dir):
    manager = ProjectManager()
    manager.create_new(project_dir, "Test")
    return manager


class _FakeProvider:
    """Stand-in for a real provider - only .id is read by
    _GenerationWorker._scene_provider_id() when a worker-wide provider is
    set (i.e. not using per-scene resolve_provider())."""

    def __init__(self, provider_id: str) -> None:
        self.id = provider_id


def _make_scenes(n: int) -> list[Scene]:
    return [
        Scene(order=i, label=f"S{i}", start_seconds=float(i), end_seconds=float(i + 1))
        for i in range(n)
    ]


def _stub_generate_scene_ok(monkeypatch):
    """Makes generate_scene() a no-op that always succeeds, so run() never
    touches ffmpeg/providers - only the restart-trigger logic under test."""
    def fake_generate_scene(pm_, scene, provider, model_id=""):
        scene.status = SceneStatus.DONE
        return ClipVersion(accepted=True)

    monkeypatch.setattr(generation_panel, "generate_scene", fake_generate_scene)


# -- _restart_comfyui() mechanics --------------------------------------------


def test_restart_comfyui_skips_when_no_install_dir(pm):
    worker = _GenerationWorker(pm, [], None)
    messages = []
    worker.statusMessage.connect(messages.append)

    pm.project.comfyui_install_dir = ""
    assert worker._restart_comfyui(pm.project) is True
    assert any("Installationspfad" in m for m in messages)


def test_restart_comfyui_skips_when_bat_missing(pm, tmp_path):
    worker = _GenerationWorker(pm, [], None)
    messages = []
    worker.statusMessage.connect(messages.append)

    # comfyui_install_dir's PARENT is where START_COMFYUI.bat is expected
    # (see _restart_comfyui) - point it at an empty tmp dir so the .bat
    # genuinely doesn't exist.
    install_dir = tmp_path / "ComfyUI"
    install_dir.mkdir()
    pm.project.comfyui_install_dir = str(install_dir)

    assert worker._restart_comfyui(pm.project) is True
    assert any("START_COMFYUI.bat" in m for m in messages)


class _FakeProcess:
    """Stand-in for subprocess.Popen's return value - only .poll() is read
    by _restart_comfyui (see Kandidat 3, task #577). None = still running,
    matching the real Popen.poll() contract."""

    def __init__(self, exit_code=None):
        self._exit_code = exit_code

    def poll(self):
        return self._exit_code


def test_restart_comfyui_success_path(monkeypatch, pm, tmp_path):
    install_dir = tmp_path / "ComfyUI"
    install_dir.mkdir()
    (tmp_path / "START_COMFYUI.bat").write_text("echo test")
    pm.project.comfyui_install_dir = str(install_dir)
    pm.project.comfyui_port = 8188

    popen_calls = {"n": 0}
    monkeypatch.setattr(
        generation_panel.subprocess, "Popen",
        lambda *a, **k: (popen_calls.__setitem__("n", popen_calls["n"] + 1), _FakeProcess())[1],
    )
    monkeypatch.setattr(generation_panel, "find_pid_listening_on_port", lambda port: 1234)
    kill_calls = {"n": 0}
    monkeypatch.setattr(generation_panel, "kill_process_tree", lambda pid: kill_calls.__setitem__("n", kill_calls["n"] + 1) or True)
    monkeypatch.setattr(generation_panel.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        generation_panel,
        "check_comfyui_connection",
        lambda host, port, timeout: ComfyUIConnection(reachable=True, message="ok"),
    )

    worker = _GenerationWorker(pm, [], None)
    messages = []
    worker.statusMessage.connect(messages.append)

    assert worker._restart_comfyui(pm.project) is True
    assert kill_calls["n"] == 1
    assert popen_calls["n"] == 1
    assert any("neu gestartet" in m for m in messages)


def test_restart_comfyui_popen_failure_returns_false(monkeypatch, pm, tmp_path):
    install_dir = tmp_path / "ComfyUI"
    install_dir.mkdir()
    (tmp_path / "START_COMFYUI.bat").write_text("echo test")
    pm.project.comfyui_install_dir = str(install_dir)

    monkeypatch.setattr(generation_panel, "find_pid_listening_on_port", lambda port: None)

    def raise_popen(*a, **k):
        raise OSError("boom")

    monkeypatch.setattr(generation_panel.subprocess, "Popen", raise_popen)

    worker = _GenerationWorker(pm, [], None)
    messages = []
    worker.statusMessage.connect(messages.append)

    assert worker._restart_comfyui(pm.project) is False
    assert any("fehlgeschlagen" in m for m in messages)


def test_restart_comfyui_timeout_returns_false(monkeypatch, pm, tmp_path):
    install_dir = tmp_path / "ComfyUI"
    install_dir.mkdir()
    (tmp_path / "START_COMFYUI.bat").write_text("echo test")
    pm.project.comfyui_install_dir = str(install_dir)

    monkeypatch.setattr(generation_panel, "find_pid_listening_on_port", lambda port: None)
    monkeypatch.setattr(generation_panel.subprocess, "Popen", lambda *a, **k: _FakeProcess(exit_code=None))
    monkeypatch.setattr(generation_panel.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        generation_panel,
        "check_comfyui_connection",
        lambda host, port, timeout: ComfyUIConnection(reachable=False, message="not up"),
    )

    # fast-forward the 180s deadline in a single loop iteration instead of
    # burning real wall-clock time / spinning on a no-op sleep()
    counter = {"t": 0.0}

    def fake_monotonic():
        counter["t"] += 100.0
        return counter["t"]

    monkeypatch.setattr(generation_panel.time, "monotonic", fake_monotonic)

    worker = _GenerationWorker(pm, [], None)
    messages = []
    worker.statusMessage.connect(messages.append)

    assert worker._restart_comfyui(pm.project) is False
    assert any("nicht erreichbar" in m for m in messages)


def test_restart_comfyui_bat_exits_immediately_returns_false_fast(monkeypatch, pm, tmp_path):
    """Kandidat 3 (task #577): if START_COMFYUI.bat itself exits right away
    (e.g. a broken venv path), _restart_comfyui should report that
    specifically instead of only ever timing out after the full 180s."""
    install_dir = tmp_path / "ComfyUI"
    install_dir.mkdir()
    (tmp_path / "START_COMFYUI.bat").write_text("echo test")
    pm.project.comfyui_install_dir = str(install_dir)

    monkeypatch.setattr(generation_panel, "find_pid_listening_on_port", lambda port: None)
    monkeypatch.setattr(generation_panel.subprocess, "Popen", lambda *a, **k: _FakeProcess(exit_code=1))
    monkeypatch.setattr(generation_panel.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        generation_panel,
        "check_comfyui_connection",
        lambda host, port, timeout: ComfyUIConnection(reachable=False, message="not up"),
    )
    # real time.monotonic() is fine here - the .bat's exit code is detected
    # on the very first loop iteration, so no actual waiting/sleeping ever
    # happens (time.sleep is mocked above only as a defensive no-op).

    worker = _GenerationWorker(pm, [], None)
    messages = []
    worker.statusMessage.connect(messages.append)

    assert worker._restart_comfyui(pm.project) is False
    assert any("Exit-Code 1" in m for m in messages)


# -- scene-counting trigger logic in run() -----------------------------------


def test_run_triggers_restart_at_configured_interval(monkeypatch, pm):
    _stub_generate_scene_ok(monkeypatch)
    pm.project.comfyui_restart_every_n_scenes = 2
    scenes = _make_scenes(5)
    pm.project.scenes.extend(scenes)

    worker = _GenerationWorker(pm, scenes, _FakeProvider("comfyui"))
    restart_calls = []
    worker._restart_comfyui = lambda proj: (restart_calls.append(1) or True)

    worker.run()

    # 5 scenes, restart every 2, last scene never triggers a restart even
    # if the count would reach the threshold there -> restarts after scene
    # index 1 and index 3 only (2 total), not after index 4 (the last one).
    assert len(restart_calls) == 2


def test_run_never_restarts_when_disabled(monkeypatch, pm):
    _stub_generate_scene_ok(monkeypatch)
    pm.project.comfyui_restart_every_n_scenes = 0
    scenes = _make_scenes(6)
    pm.project.scenes.extend(scenes)

    worker = _GenerationWorker(pm, scenes, _FakeProvider("comfyui"))
    restart_calls = []
    worker._restart_comfyui = lambda proj: (restart_calls.append(1) or True)

    worker.run()

    assert restart_calls == []


def test_run_ignores_non_comfyui_scenes_for_the_counter(monkeypatch, pm):
    _stub_generate_scene_ok(monkeypatch)
    pm.project.comfyui_restart_every_n_scenes = 2
    scenes = _make_scenes(4)
    pm.project.scenes.extend(scenes)

    # worker-wide provider is Runway, not ComfyUI - _scene_provider_id()
    # returns "runway" for every scene, so the ComfyUI-only counter must
    # never advance and no restart is ever attempted.
    worker = _GenerationWorker(pm, scenes, _FakeProvider("runway"))
    restart_calls = []
    worker._restart_comfyui = lambda proj: (restart_calls.append(1) or True)

    worker.run()

    assert restart_calls == []


def test_run_stops_remaining_scenes_when_restart_fails(monkeypatch, pm):
    _stub_generate_scene_ok(monkeypatch)
    pm.project.comfyui_restart_every_n_scenes = 2
    scenes = _make_scenes(6)
    pm.project.scenes.extend(scenes)

    worker = _GenerationWorker(pm, scenes, _FakeProvider("comfyui"))
    restart_calls = []
    worker._restart_comfyui = lambda proj: (restart_calls.append(1) or False)  # fails immediately

    results = {}

    def capture(n_ok, n_fail, n_budget_blocked, n_skipped):
        results.update(n_ok=n_ok, n_fail=n_fail, n_budget_blocked=n_budget_blocked, n_skipped=n_skipped)

    worker.allFinished.connect(capture)
    worker.run()

    # restart triggers after scene index 1 (the 2nd scene, 0-indexed) and
    # immediately fails -> scenes 0 and 1 completed normally, scenes 2-5
    # (4 remaining) never run.
    assert len(restart_calls) == 1
    assert results["n_ok"] == 2
    assert results["n_skipped"] == 4
