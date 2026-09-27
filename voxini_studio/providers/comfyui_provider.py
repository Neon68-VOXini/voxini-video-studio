"""Local, free video generation via a locally running ComfyUI instance,
with a choice of local model (see local_model / _LOCAL_MODELS below):
Wan2.2 TI2V 5B (default) or LTX-2.5 22B. Talks to ComfyUI's HTTP API
(default 127.0.0.1:8188) - it never touches the network beyond that local
socket, so running this provider (including the automated tests in
tests/test_comfyui_provider.py, which spin up a real local fake HTTP
server) never makes a real internet call and never costs anything.

Workflow graphs are loaded from voxini_studio/providers/workflows/*.json -
the official Comfy-Org templates for each supported local model (fetched
from github.com/Comfy-Org/workflow_templates), converted to ComfyUI's API
("prompt") submission format, with {{TOKEN}} placeholders filled in per
request. Keeping them as editable JSON files (not hard-coded Python dicts)
matches the requirement that the workflows stay user-editable.

local_model="wan22" uses wan22_text_to_video.json / wan22_image_to_video.json.
local_model="ltx25" uses ltx25_text_to_video.json / ltx25_image_to_video.json
(see those files' own _comment for the LTX-2.5-specific fixes applied and
their live-test status). LTX-2.5 needs its own gated Hugging Face model
files (huggingface.co/Lightricks/LTX-2.5) and, in practice on this
project's dev machine, a larger Windows pagefile to memory-map the 22B
safetensors weights - so it stays strictly opt-in (default "wan22") per
the codebase's "off by default for anything with extra prerequisites"
convention (see Project.comfyui_identity_scene_mode for the same pattern).
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

import requests

from voxini_studio.models.project import AspectRatio
from voxini_studio.providers.base import GenerationRequest, GenerationResult, Provider

WORKFLOWS_DIR = Path(__file__).parent / "workflows"

# (width, height) per aspect ratio and target resolution tier - all multiples
# of 16 as required by the Wan2.2 VAE, close to true 480p/720p pixel counts.
# Reused as-is for local_model="ltx25" (multiples of 16 satisfy LTX-2.5's own
# VAE constraints too, per the live-tested workflow) - no LTX-2.5-specific
# resolution tuning has been done yet; revisit if LTX-2.5 output quality
# suggests different target resolutions are needed.
_RESOLUTIONS: dict[tuple[AspectRatio, str], tuple[int, int]] = {
    (AspectRatio.WIDESCREEN, "480p"): (832, 480),
    (AspectRatio.WIDESCREEN, "720p"): (1280, 704),
    (AspectRatio.VERTICAL, "480p"): (480, 832),
    (AspectRatio.VERTICAL, "720p"): (704, 1280),
    (AspectRatio.SQUARE, "480p"): (480, 480),
    (AspectRatio.SQUARE, "720p"): (704, 704),
}

DEFAULT_FPS = 24
DEFAULT_NEGATIVE_PROMPT = (
    "low quality, worst quality, blurry, distorted, extra limbs, watermark, "
    "text, subtitles, jpeg artifacts, deformed hands, static frame"
)

# Per-local_model metadata: display name and max single-clip length. LTX-2.5's
# max_clip_seconds is deliberately kept identical to Wan2.2's for now - only
# one short text-to-video clip has ever been live-tested against a real
# ComfyUI instance (2026-09-27), so there is no verified basis yet for a
# longer limit. Revisit once more LTX-2.5 clips of varying length have
# actually been generated and reviewed.
_LOCAL_MODELS: dict[str, dict] = {
    "wan22": {
        "display_name": "Lokal - ComfyUI / Wan2.2 5B (kostenlos)",
        "max_clip_seconds": 8.0,
    },
    "ltx25": {
        "display_name": "Lokal - ComfyUI / LTX-2.5 22B (kostenlos)",
        "max_clip_seconds": 8.0,
    },
}
DEFAULT_LOCAL_MODEL = "wan22"

# Stage-1 resolutions for the identity-scene pipeline (SDXL + InstantID, see
# sdxl_instantid_scene.json) - deliberately separate from _RESOLUTIONS above:
# SDXL was trained on ~1024x1024-area buckets, not Wan2.2's 480p/720p video
# resolutions, so reusing _RESOLUTIONS here would push SDXL well outside its
# trained resolution range (composition/anatomy degrades badly). These are
# the standard documented SDXL aspect-ratio buckets, kept close to each
# target AspectRatio while staying near ~1024x1024 total pixels.
_SDXL_RESOLUTIONS: dict[AspectRatio, tuple[int, int]] = {
    AspectRatio.WIDESCREEN: (1216, 704),
    AspectRatio.VERTICAL: (704, 1216),
    AspectRatio.SQUARE: (1024, 1024),
}
DEFAULT_IDENTITY_NEGATIVE_PROMPT = (
    "deformed, blurry, bad anatomy, disfigured, poorly drawn face, extra "
    "limbs, mutated hands, watermark, text, low quality, cartoon, cgi, "
    "3d render, illustration, painting, drawing, doll, plastic skin"
)


# Matches any leftover, unresolved {{TOKEN}} placeholder after _fill_template
# has run all of its known substitutions - see _fill_template's use of this.
_UNRESOLVED_TOKEN_RE = re.compile(r"\{\{[A-Z_]+\}\}")


class ComfyUIAPIError(RuntimeError):
    """Raised for any ComfyUI HTTP/API failure (unreachable, HTTP error
    status, malformed response, node/validation errors, job failure)."""


class ComfyUIJobCancelled(RuntimeError):
    """Raised by wait_for_job when the caller's cancel_check signals that
    the user cancelled the job while it was queued or running."""


class ComfyUIProvider(Provider):
    id = "comfyui"
    price_per_second = 0.0
    supported_aspect_ratios = [AspectRatio.WIDESCREEN, AspectRatio.VERTICAL, AspectRatio.SQUARE]
    requires_api_key = False

    @property
    def display_name(self) -> str:
        """Overrides Provider.display_name (plain class attribute on other
        providers) - here it depends on self.local_model, so it must be a
        property, same rationale as max_simultaneous_references below."""
        return _LOCAL_MODELS.get(self.local_model, _LOCAL_MODELS[DEFAULT_LOCAL_MODEL])["display_name"]

    @property
    def max_clip_seconds(self) -> float:
        """Overrides Provider.max_clip_seconds - see _LOCAL_MODELS above.
        Wan2.2 5B is generated in one pass up to ~121 frames (~5s at 24fps);
        8s gives headroom while keeping single-job VRAM/time reasonable on a
        16GB card. Longer scenes are simply split into multiple clips
        upstream by the scene planner, same as any other provider's
        max_clip_seconds."""
        return _LOCAL_MODELS.get(self.local_model, _LOCAL_MODELS[DEFAULT_LOCAL_MODEL])["max_clip_seconds"]

    _MAX_MULTI_CHARACTER_FACES = 3
    """Task #637 (Mehrfigur-Workflow): upper bound on how many character
    faces _build_multi_character_instantid_workflow() will composite into
    one stage-1 image. Not a hard technical ceiling (the builder itself
    works for any N>=2) - chosen because splitting the canvas into more
    than 3 vertical strips leaves too little width per character for
    InstantID's face-region conditioning to stay coherent, and because no
    scene in the current 46-scene storyboard needs more than 3 simultaneous
    characters (see docs/Referenzbindung_Luecken_und_Plan.md)."""

    _KPS_FACE_HEIGHT_FRACTION = 0.42
    """Fraction of the target canvas HEIGHT that a synthesized image_kps
    face is scaled to (see _build_character_kps_image) - a normal
    medium-shot proportion for 2-3 people standing together in one frame,
    as opposed to the near-full-frame size the reference photo would
    imply if used unscaled (see _build_character_kps_image's docstring for
    the exact defect this fixes, found live in task #637's first real
    test, 2026-09-06)."""
    _KPS_FACE_TOP_MARGIN_FRACTION = 0.06
    """Top margin (fraction of canvas height) above the synthesized face in
    _build_character_kps_image - keeps the face out of the very top edge,
    roughly where a head would sit in a normal medium shot."""
    _KPS_CANVAS_BACKGROUND = (32, 32, 32)
    """Flat background colour for the synthetic image_kps canvas - never
    seen by the user (image_kps only feeds ControlNet pose conditioning,
    not the final visible pixels), just needs enough contrast against the
    pasted face photo for InsightFace's own face detector to find it."""

    @property
    def max_simultaneous_references(self) -> int:
        """Overrides Provider.max_simultaneous_references (see its
        docstring in base.py). ComfyUIProvider can combine up to
        _MAX_MULTI_CHARACTER_FACES character faces into ONE stage-1 SDXL+
        InstantID image (_generate_multi_character_identity_scene_image
        below) - but ONLY while identity_scene_mode is on. Plain
        image-to-video (identity_scene_mode=False) still only ever accepts
        a single start_image, exactly like every other provider, so it must
        keep reporting 1 in that case. A property (not a plain class
        attribute like the base class default) because this genuinely
        depends on this instance's configuration, not just which provider
        class is in use."""
        return self._MAX_MULTI_CHARACTER_FACES if self.identity_scene_mode else 1

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8188,
        resolution: str = "720p",
        upscale_to_1080p: bool = True,
        session: Optional[requests.Session] = None,
        workflows_dir: Optional[Path] = None,
        timeout: float = 15.0,
        identity_scene_mode: bool = False,
        identity_checkpoint: str = "SDXL\\sd_xl_base_1.0.safetensors",
        negative_prompt: Optional[str] = None,
        identity_negative_prompt: Optional[str] = None,
        local_model: str = DEFAULT_LOCAL_MODEL,
    ):
        self.host = host
        self.port = port
        self.resolution = resolution if resolution in ("480p", "720p") else "720p"
        self.upscale_to_1080p = upscale_to_1080p
        self.session = session or requests.Session()
        self.workflows_dir = Path(workflows_dir) if workflows_dir else WORKFLOWS_DIR
        self.timeout = timeout
        self.local_model = local_model if local_model in _LOCAL_MODELS else DEFAULT_LOCAL_MODEL
        """Which local model's workflow templates/frame-math to use - see
        _LOCAL_MODELS and the module docstring. An unknown value silently
        falls back to DEFAULT_LOCAL_MODEL rather than raising, matching how
        self.resolution above handles an invalid value - a stale/typo'd
        Project setting should degrade to the safe default, not crash
        generation."""
        self.identity_scene_mode = identity_scene_mode
        """When True, _generate() runs the two-step identity-scene pipeline
        (see sdxl_instantid_scene.json) instead of plain image-to-video,
        whenever the scene has a character reference photo. See
        Project.comfyui_identity_scene_mode for the full rationale."""
        self.identity_checkpoint = identity_checkpoint
        # Empty/None -> fall back to the module-level defaults, same pattern
        # as identity_checkpoint above. Exposed as constructor params (rather
        # than only the DEFAULT_*_PROMPT module constants) so
        # registry.build_provider() can pass through the per-project
        # Project.comfyui_negative_prompt / comfyui_identity_negative_prompt
        # overrides - see Project for the rationale (Kandidat 9, task #577).
        self.negative_prompt = negative_prompt or DEFAULT_NEGATIVE_PROMPT
        self.identity_negative_prompt = identity_negative_prompt or DEFAULT_IDENTITY_NEGATIVE_PROMPT

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    # -- connection / status -------------------------------------------

    def check_connection(self) -> dict:
        """Lightweight connectivity check (no job submitted). Raises
        ComfyUIAPIError with a user-facing message on any failure."""
        try:
            resp = self.session.get(f"{self.base_url}/system_stats", timeout=self.timeout)
        except requests.RequestException as exc:
            raise ComfyUIAPIError(
                f"ComfyUI unter {self.base_url} nicht erreichbar: {exc}"
            ) from exc
        if resp.status_code != 200:
            raise ComfyUIAPIError(f"ComfyUI antwortete mit HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise ComfyUIAPIError("Ungueltige Antwort von ComfyUI (kein JSON).") from exc

    # -- workflow templates ----------------------------------------------

    def _load_template_json(self, path: Path) -> dict:
        """Shared load+parse for load_workflow_template/load_identity_template.
        These templates are user-editable JSON files (see module docstring),
        so a hand-edit that breaks the JSON syntax is a realistic failure
        mode, not just a hypothetical one - without this, it would previously
        surface as an unguarded json.JSONDecodeError with no ComfyUI/VOXini
        context, instead of the same clear German ComfyUIAPIError every other
        failure in this class produces."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as exc:
            raise ComfyUIAPIError(
                f"Workflow-Vorlage ist fehlerhaft (ungueltiges JSON) in {path}: {exc}"
            ) from exc

    def load_workflow_template(self, image_mode: bool) -> dict:
        prefix = "ltx25" if self.local_model == "ltx25" else "wan22"
        filename = f"{prefix}_image_to_video.json" if image_mode else f"{prefix}_text_to_video.json"
        path = self.workflows_dir / filename
        if not path.exists():
            raise ComfyUIAPIError(f"Workflow-Vorlage fehlt: {path}")
        return self._load_template_json(path)

    def load_identity_template(self) -> dict:
        """Stage-1 template for the identity-scene pipeline (SDXL +
        InstantID) - see sdxl_instantid_scene.json and
        Project.comfyui_identity_scene_mode."""
        path = self.workflows_dir / "sdxl_instantid_scene.json"
        if not path.exists():
            raise ComfyUIAPIError(f"Workflow-Vorlage fehlt: {path}")
        return self._load_template_json(path)

    def _fill_template(
        self,
        template: dict,
        *,
        prompt: str,
        negative_prompt: str,
        width: int,
        height: int,
        frames: int = 0,
        fps: int = 0,
        seed: int,
        filename_prefix: str,
        image_filename: Optional[str] = None,
        checkpoint_name: Optional[str] = None,
        image_kps_filename: Optional[str] = None,
    ) -> dict:
        raw = json.dumps(template)
        substitutions = {
            "{{PROMPT}}": prompt,
            "{{NEGATIVE_PROMPT}}": negative_prompt,
            "{{FILENAME_PREFIX}}": filename_prefix,
        }
        for token, value in substitutions.items():
            raw = raw.replace(token, json.dumps(value)[1:-1])  # escape safely, keep quotes from source
        # numeric tokens are embedded unquoted in the template (e.g. "seed": "{{SEED}}"
        # -> "seed": 12345), so replace the quoted-token form with a raw number.
        raw = raw.replace('"{{SEED}}"', str(int(seed)))
        raw = raw.replace('"{{WIDTH}}"', str(int(width)))
        raw = raw.replace('"{{HEIGHT}}"', str(int(height)))
        raw = raw.replace('"{{FRAMES}}"', str(int(frames)))
        raw = raw.replace('"{{FPS}}"', str(int(fps)))
        if image_filename is not None:
            raw = raw.replace("{{IMAGE_FILENAME}}", json.dumps(image_filename)[1:-1])
        if checkpoint_name is not None:
            raw = raw.replace("{{CHECKPOINT_NAME}}", json.dumps(checkpoint_name)[1:-1])
        if image_kps_filename is not None:
            raw = raw.replace("{{IMAGE_KPS_FILENAME}}", json.dumps(image_kps_filename)[1:-1])
        # These templates are user-editable JSON files (see module docstring).
        # A leftover, unresolved {{TOKEN}} - e.g. from a typo introduced while
        # hand-editing a template, or a token this method simply doesn't know
        # about - would otherwise be submitted to ComfyUI verbatim as a
        # literal string value, which ComfyUI would either reject with a
        # confusing node-validation error or, worse, silently accept and
        # generate a wrong/garbage result. Catching it here instead gives a
        # clear, specific German error pointing at the exact token name.
        leftover = _UNRESOLVED_TOKEN_RE.findall(raw)
        if leftover:
            raise ComfyUIAPIError(
                "Workflow-Vorlage enthaelt nicht aufgeloeste Platzhalter: "
                + ", ".join(sorted(set(leftover)))
            )
        workflow = json.loads(raw)
        workflow.pop("_comment", None)
        return workflow

    # -- job lifecycle -----------------------------------------------------

    def upload_image(self, image_path: str | Path) -> str:
        """Uploads a local image to ComfyUI's input folder so it can be used
        as start_image for image-to-video. Returns the server-side filename
        to reference in the workflow."""
        path = Path(image_path)
        if not path.exists():
            raise ComfyUIAPIError(f"Referenzbild nicht gefunden: {path}")
        try:
            with open(path, "rb") as f:
                resp = self.session.post(
                    f"{self.base_url}/upload/image",
                    files={"image": (path.name, f, "application/octet-stream")},
                    data={"overwrite": "true"},
                    timeout=self.timeout,
                )
        except requests.RequestException as exc:
            raise ComfyUIAPIError(f"Bild-Upload fehlgeschlagen: {exc}") from exc
        if resp.status_code != 200:
            raise ComfyUIAPIError(f"Bild-Upload fehlgeschlagen: HTTP {resp.status_code}")
        data = resp.json()
        return data.get("name", path.name)

    def submit_job(self, workflow: dict, client_id: Optional[str] = None) -> str:
        client_id = client_id or uuid.uuid4().hex
        try:
            resp = self.session.post(
                f"{self.base_url}/prompt",
                json={"prompt": workflow, "client_id": client_id},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ComfyUIAPIError(f"Job konnte nicht gesendet werden: {exc}") from exc
        if resp.status_code != 200:
            try:
                detail = resp.json()
            except ValueError:
                detail = resp.text
            raise ComfyUIAPIError(f"ComfyUI lehnte den Job ab (HTTP {resp.status_code}): {detail}")
        data = resp.json()
        node_errors = data.get("node_errors")
        if node_errors:
            raise ComfyUIAPIError(f"Workflow-Fehler: {node_errors}")
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ComfyUIAPIError(f"Keine prompt_id in Antwort: {data}")
        return prompt_id

    def free_memory(self) -> None:
        """Calls ComfyUI's POST /free endpoint (unload_models + free_memory)
        after a job finishes. Root-cause fix for the VRAM/performance
        degradation seen across long batches (e.g. a 40+ scene project):
        without this, each job's model weights/activations stay resident in
        VRAM, so later scenes in the same batch get progressively slower and
        can eventually fail with an out-of-memory error, even though every
        single job would work fine on its own. Best-effort only - a failure
        here (e.g. ComfyUI too old to have /free) must never mask the actual
        generation result, so exceptions are swallowed."""
        try:
            self.session.post(
                f"{self.base_url}/free",
                json={"unload_models": True, "free_memory": True},
                timeout=self.timeout,
            )
        except requests.RequestException:
            pass

    def get_queue(self) -> dict:
        """Not currently called anywhere in the app (kept as a documented,
        properly error-wrapped mirror of ComfyUI's GET /queue endpoint,
        alongside get_history/get_history, for future queue-status/diagnostic
        UI - see Kandidat 2, task #577). Same ComfyUIAPIError wrapping as
        every other network call in this class, instead of leaking a raw
        requests exception."""
        try:
            resp = self.session.get(f"{self.base_url}/queue", timeout=self.timeout)
        except requests.RequestException as exc:
            raise ComfyUIAPIError(f"Warteschlangen-Abfrage fehlgeschlagen: {exc}") from exc
        if resp.status_code != 200:
            raise ComfyUIAPIError(f"Warteschlangen-Abfrage fehlgeschlagen: HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise ComfyUIAPIError("Ungueltige Antwort von ComfyUI (kein JSON) bei /queue.") from exc

    def get_history(self, prompt_id: str) -> Optional[dict]:
        """Polled repeatedly (every poll_interval, for up to `timeout`
        seconds) by wait_for_job() - previously used requests' raw
        raise_for_status()/resp.json(), so a single transient hiccup (a
        connection reset, ComfyUI answering with a non-200 mid-restart, a
        truncated response) surfaced as a bare requests/ValueError exception
        instead of the same clear German ComfyUIAPIError every other
        failure in this class produces (see Kandidat 2, task #577)."""
        try:
            resp = self.session.get(f"{self.base_url}/history/{prompt_id}", timeout=self.timeout)
        except requests.RequestException as exc:
            raise ComfyUIAPIError(f"Verlaufs-Abfrage fehlgeschlagen: {exc}") from exc
        if resp.status_code != 200:
            raise ComfyUIAPIError(f"Verlaufs-Abfrage fehlgeschlagen: HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ComfyUIAPIError("Ungueltige Antwort von ComfyUI (kein JSON) bei /history.") from exc
        return data.get(prompt_id)

    def cancel_job(self, prompt_id: str) -> None:
        """Cancels a job whether it's still queued or already running."""
        try:
            self.session.post(
                f"{self.base_url}/queue", json={"delete": [prompt_id]}, timeout=self.timeout
            )
        except requests.RequestException:
            pass
        try:
            self.session.post(f"{self.base_url}/interrupt", timeout=self.timeout)
        except requests.RequestException:
            pass

    def wait_for_job(
        self,
        prompt_id: str,
        timeout: float = 10800.0,
        poll_interval: float = 2.0,
        cancel_check: Optional[Callable[[], bool]] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> dict:
        """Polls /history until the job completes, fails, or times out.
        `cancel_check` (optional) is polled each loop - if it returns True
        the job is cancelled server-side and ComfyUIJobCancelled is raised,
        so the UI's Abbrechen button has a real effect mid-job.

        The 3-hour default is intentionally generous: on GPUs without
        official ROCm support (e.g. an unlisted AMD card running an
        unofficial-but-working ROCm build), a single Wan2.2 sampling step
        can take several minutes rather than seconds, so a single ~20-step
        job can legitimately run for over an hour. A shorter timeout here
        would silently interrupt (POST /interrupt - visible in the ComfyUI
        console as "Global interrupt") a job that was still working
        correctly, purely because it was running on slower-than-expected
        hardware - not because anything was actually stuck."""
        started = clock()
        while True:
            if cancel_check is not None and cancel_check():
                self.cancel_job(prompt_id)
                raise ComfyUIJobCancelled(f"Job {prompt_id} wurde vom Benutzer abgebrochen.")

            entry = self.get_history(prompt_id)
            if entry is not None:
                status = entry.get("status", {})
                if status.get("completed") is True or status.get("status_str") == "success":
                    return entry
                if status.get("status_str") == "error":
                    messages = status.get("messages", [])
                    raise ComfyUIAPIError(f"ComfyUI-Job fehlgeschlagen: {messages}")

            if clock() - started > timeout:
                self.cancel_job(prompt_id)
                raise ComfyUIAPIError(f"Zeitueberschreitung nach {timeout:.0f}s beim Warten auf ComfyUI-Job.")

            sleep(poll_interval)

    def _extract_output_file(self, history_entry: dict) -> tuple[str, str, str]:
        """Finds the generated video's (filename, subfolder, type) inside a
        completed /history entry. ComfyUI's exact output key name for a
        SaveVideo node varies by version ('images'/'videos'/'gifs'), so this
        scans every output list for the first item that has a 'filename'."""
        outputs = history_entry.get("outputs", {})
        for _node_id, node_output in outputs.items():
            if not isinstance(node_output, dict):
                continue
            for _key, items in node_output.items():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if isinstance(item, dict) and "filename" in item:
                        return item["filename"], item.get("subfolder", ""), item.get("type", "output")
        raise ComfyUIAPIError("Kein Ausgabe-Video in der ComfyUI-Historie gefunden.")

    def _download_output(self, filename: str, subfolder: str, type_: str, dest_path: Path) -> None:
        params = {"filename": filename, "subfolder": subfolder, "type": type_}
        try:
            resp = self.session.get(f"{self.base_url}/view", params=params, timeout=self.timeout, stream=True)
        except requests.RequestException as exc:
            raise ComfyUIAPIError(f"Download fehlgeschlagen: {exc}") from exc
        if resp.status_code != 200:
            raise ComfyUIAPIError(f"Download fehlgeschlagen: HTTP {resp.status_code}")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)

    def _upscale_local(self, src_path: Path, dest_path: Path, target_height: int = 1080) -> bool:
        """Upscales the freshly generated clip to 1080p locally with ffmpeg
        (lanczos scaling), matching the '480p/720p mit lokalem
        1080p-Upscaling' requirement. Runs entirely offline/local - no API
        cost. Falls back to leaving the file at native resolution if ffmpeg
        is unavailable - returns False in that case (True on a real
        upscale) so the caller (_generate) can surface this as
        GenerationResult.warning instead of the clip silently staying at a
        lower resolution than requested with zero visible trace."""
        import shutil
        import subprocess

        from voxini_studio.core import ffmpeg_locator

        ffmpeg_bin = ffmpeg_locator.ffmpeg_path()
        if ffmpeg_bin == "ffmpeg" and not shutil.which("ffmpeg"):
            # no bundled binary resolved AND nothing on PATH either
            if src_path != dest_path:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src_path, dest_path)
            return False

        scale_filter = f"scale=-2:{target_height}:flags=lanczos"
        cmd = [
            ffmpeg_bin, "-y", "-i", str(src_path),
            "-vf", scale_filter,
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-an",
            str(dest_path), "-loglevel", "error",
        ]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            raise ComfyUIAPIError(f"Lokales Upscaling fehlgeschlagen: {result.stderr.decode()[-500:]}")
        return True

    # -- identity-scene pipeline (stage 1) ------------------------------

    def _generate_identity_scene_image(self, request: GenerationRequest, dest: Path) -> Path:
        """Stage 1 of the identity-scene pipeline (see
        Project.comfyui_identity_scene_mode / sdxl_instantid_scene.json):
        SDXL + InstantID composes a brand-new scene image from the scene's
        prompt, using request.character_reference_paths[0] ONLY for facial
        identity - unlike plain image-to-video, the reference photo's own
        background/composition/pose is not reused. The returned image is
        meant to be fed into the existing Wan2.2 image-to-video path
        (stage 2) as its start_image, in place of the raw reference photo.
        Raises ComfyUIAPIError like any other job here - the caller
        (_generate) decides whether/how to fall back.

        Also builds and uploads a synthetic image_kps (see
        _build_character_kps_image's docstring for the oversized-face defect
        this fixes, found live in task #637's first real batch test,
        2026-09-06 - confirmed to affect this single-character path too, not
        only the newer multi-character one) using the FULL canvas as this
        one character's "column"."""
        width, height = _SDXL_RESOLUTIONS.get(
            request.aspect_ratio, _SDXL_RESOLUTIONS[AspectRatio.WIDESCREEN]
        )
        face_filename = self.upload_image(request.character_reference_paths[0])
        kps_path = dest.with_name(dest.stem + "_identity_kps.png")
        self._build_character_kps_image(
            face_photo_path=request.character_reference_paths[0],
            canvas_width=width,
            canvas_height=height,
            column_x=0,
            column_width=width,
            dest=kps_path,
        )
        kps_filename = self.upload_image(kps_path)
        template = self.load_identity_template()
        workflow = self._fill_template(
            template,
            prompt=request.identity_stage_prompt or request.resolved_prompt,
            negative_prompt=self.identity_negative_prompt,
            width=width,
            height=height,
            seed=uuid.uuid4().int % (2 ** 32),
            filename_prefix=f"voxini_identity_{request.scene.id}",
            image_filename=face_filename,
            checkpoint_name=self.identity_checkpoint,
            image_kps_filename=kps_filename,
        )
        prompt_id = self.submit_job(workflow)
        history = self.wait_for_job(prompt_id)
        filename, subfolder, type_ = self._extract_output_file(history)

        identity_image_path = dest.with_name(dest.stem + "_identity_scene.png")
        self._download_output(filename, subfolder, type_, identity_image_path)

        # Release SDXL/InstantID's VRAM before stage 2 loads the Wan2.2
        # model - without this the two models' weights would both stay
        # resident at once, defeating the whole point of doing this as two
        # sequential passes on modest consumer hardware. See free_memory().
        self.free_memory()

        return identity_image_path

    # -- identity-scene pipeline (stage 1, multi-character) -------------

    def _multi_character_columns(self, n: int, width: int) -> list[tuple[int, int]]:
        """Task #637: single shared column layout (list of (x, column_width)
        pairs, left to right) used both by _build_multi_character_instantid_
        workflow()'s per-character region masks AND by
        _build_character_kps_image()'s synthetic pose images below -
        keeping both derived from the exact same split is what makes a
        character's synthesized face position actually agree with where
        their region mask lets the diffusion output draw them. The last
        column absorbs any remainder from integer division so the columns
        always cover the full canvas width with no gap."""
        column_width = width // n
        columns = []
        for i in range(n):
            w = column_width if i < n - 1 else (width - column_width * (n - 1))
            columns.append((i * column_width, w))
        return columns

    def _build_character_kps_image(
        self,
        face_photo_path: str,
        canvas_width: int,
        canvas_height: int,
        column_x: int,
        column_width: int,
        dest: Path,
    ) -> Path:
        """Task #637 fix: synthesizes a small 'pose reference' image for ONE
        character's image_kps input, so ApplyInstantID places that
        character's face at a normal, medium-shot size within their column
        instead of filling the whole canvas.

        Root cause this works around (confirmed by reading the installed
        comfyui_instantid/InstantID.py directly, after task #637's first
        live test on 2026-09-06 produced a single oversized, cropped face
        filling most of a 3-character scene): ApplyInstantID's own code
        says outright "if no keypoints image is provided, use the image
        itself" - i.e. when image_kps is omitted (VOXini's original V1
        simplification, since it has no per-scene pose-reference asset), it
        runs face detection on the reference PHOTO itself and draws the
        resulting keypoints onto a canvas the size of THAT PHOTO. VOXini's
        reference photos are tight face/headshot crops, so the face fills
        almost that entire crop - and ComfyUI's ControlNet hint handling
        then resizes that keypoint image up to the FULL target generation
        resolution, which is what commands InstantID's ControlNet to draw a
        face filling almost the entire scene, regardless of the region
        mask (the mask only restricts which pixels the DIFFUSION touches,
        it does not rescale the ControlNet hint itself).

        The fix: build a synthetic canvas at the ACTUAL target resolution
        with the reference face resized down to a normal proportion
        (_KPS_FACE_HEIGHT_FRACTION of the canvas height) and pasted at this
        character's own column position (from _multi_character_columns),
        then pass THAT as image_kps instead of leaving it empty.
        ApplyInstantID's face detection then runs on our synthetic image,
        so the extracted keypoints already have a realistic size/position
        and are not later stretched to fill the frame.

        Purely a flat-colour canvas otherwise (_KPS_CANVAS_BACKGROUND) -
        never seen by the user, since image_kps only feeds ControlNet pose
        conditioning, not the final visible pixels. Requires Pillow
        (already a hard dependency - see requirements.txt).

        UNTESTED against a real ComfyUI instance as of this fix's initial
        implementation (written in response to the task #637 live-test
        finding above, not yet re-verified live) - see the honesty note in
        _generate_multi_character_identity_scene_image.
        """
        from PIL import Image

        with Image.open(face_photo_path) as src:
            face = src.convert("RGB")
            target_height = max(1, round(canvas_height * self._KPS_FACE_HEIGHT_FRACTION))
            target_width = max(1, round(face.width * (target_height / face.height)))
            max_width = max(1, round(column_width * 0.9))
            if target_width > max_width:
                target_width = max_width
                target_height = max(1, round(face.height * (target_width / face.width)))
            face = face.resize((target_width, target_height), Image.LANCZOS)

        canvas = Image.new("RGB", (canvas_width, canvas_height), self._KPS_CANVAS_BACKGROUND)
        paste_x = column_x + max(0, (column_width - face.width) // 2)
        paste_y = max(0, round(canvas_height * self._KPS_FACE_TOP_MARGIN_FRACTION))
        canvas.paste(face, (paste_x, paste_y))

        dest.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(dest, "PNG")
        return dest

    def _build_multi_character_instantid_workflow(
        self,
        *,
        face_filenames: list[str],
        image_kps_filenames: list[str],
        character_prompts: list[str],
        scene_description: str,
        negative_prompt: str,
        width: int,
        height: int,
        seed: int,
        filename_prefix: str,
        checkpoint_name: str,
    ) -> dict:
        """Task #637 (Mehrfigur-Workflow): dynamically builds a ComfyUI
        API-format workflow with N chained ApplyInstantID nodes (N =
        len(face_filenames), 2 or 3 - see _MAX_MULTI_CHARACTER_FACES), one
        per character, each conditioned by that character's own face photo
        and gated by that character's own vertical-strip region mask, so
        all N faces end up composited into ONE stage-1 SDXL image instead of
        the single-face _generate_identity_scene_image() above.

        Modelled directly on the officially bundled example workflow
        comfyui_env/ComfyUI/custom_nodes/comfyui_instantid/examples/
        InstantID_multi_id.json (chained ApplyInstantID -> chained
        ConditioningCombine for both positive and negative, one shared
        KSampler/VAEDecode/SaveImage) with one deliberate V1 simplification
        agreed with Neon68 (see docs/Referenzbindung_Luecken_und_Plan.md,
        Punkt 3):
          - built directly in Python (unlike every other workflow in this
            provider, which are {{TOKEN}}-filled static JSON files) because
            the node COUNT itself varies with the character count, which a
            static template can't express.

        image_kps IS now provided per character (image_kps_filenames,
        1:1 with face_filenames) - NOT omitted as originally planned. The
        official example uses a real shared pose photo (mirrored for the
        second character via ImageFlip+/MaskFlip+) for precise face/
        keypoint placement; VOXini has no per-scene pose-reference asset in
        its data model, so each character's image_kps is instead a small
        synthetic canvas built by _build_character_kps_image() with that
        character's face resized to a normal proportion and placed at their
        column position - see that method's docstring for the exact
        oversized-face defect this fixes (found live in task #637's first
        real batch test, 2026-09-06). Character separation therefore comes
        from BOTH the per-character region mask AND the per-character
        image_kps now, not the mask alone.

        Each character's region mask is built the direct way (own SolidMask
        pair + MaskComposite per character) rather than the official
        example's build-once-and-flip trick, since that trick only works
        for exactly 2, symmetric characters - this needs to generalise to
        3. All characters share one InstantIDModelLoader/
        InstantIDFaceAnalysis/ControlNetLoader/negative CLIPTextEncode
        (matching the official example, which also shares negative/insight-
        face/controlnet/instantid across both ApplyInstantID nodes) and one
        constant ApplyInstantID weight (0.8) - the official example uses
        slightly different weights per character (0.8/0.9), but a single
        shared value keeps this generalising cleanly to 3 characters without
        an arbitrary per-slot table.

        Each character's positive (text) conditioning also passes through a
        ConditioningSetMask using that SAME column mask before the
        ConditioningCombine chain (added 2026-09-06 after a live test showed
        the mask alone - only wired into ApplyInstantID's own "mask" input -
        gates InstantID's face identity but not the character/scene text
        conditioning, so N characters' full-canvas text prompts were
        competing unrestricted across the whole image and produced a
        collaged, quadrant-tiled composition; see the fix's comment at the
        ConditioningSetMask node below for the full explanation). Negative
        conditioning stays unmasked/shared, since it's identical text for
        every character anyway.

        Honesty note: the region-mask + image_kps combination (before this
        ConditioningSetMask fix) WAS live-tested on 2026-09-06 and confirmed
        to fix the original oversized/cropped-face defect, but also revealed
        this separate text-conditioning-bleed defect. This
        ConditioningSetMask fix itself is UNTESTED against a real ComfyUI
        instance as of this edit - treat any claim that the collage/blur
        defect is now resolved as unverified until a fresh live test
        confirms it.
        """
        n = len(face_filenames)
        if n != len(character_prompts) or n != len(image_kps_filenames):
            raise ComfyUIAPIError(
                f"Interner Fehler: {n} Referenzbilder, {len(character_prompts)} "
                f"Charakter-Prompt-Bloecke, {len(image_kps_filenames)} image_kps-Bilder - "
                "Mehrfigur-Workflow abgebrochen."
            )
        if not (2 <= n <= self._MAX_MULTI_CHARACTER_FACES):
            raise ComfyUIAPIError(
                f"Mehrfigur-Workflow unterstuetzt 2 bis {self._MAX_MULTI_CHARACTER_FACES} "
                f"gleichzeitige Charaktere, diese Szene hat {n}."
            )

        workflow: dict = {}
        next_id = [1]

        def nid() -> str:
            s = str(next_id[0])
            next_id[0] += 1
            return s

        checkpoint = nid()
        workflow[checkpoint] = {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": checkpoint_name},
        }
        instantid_loader = nid()
        workflow[instantid_loader] = {
            "class_type": "InstantIDModelLoader",
            "inputs": {"instantid_file": "ip-adapter.bin"},
        }
        face_analysis = nid()
        workflow[face_analysis] = {
            "class_type": "InstantIDFaceAnalysis",
            "inputs": {"provider": "CPU"},
        }
        controlnet = nid()
        workflow[controlnet] = {
            "class_type": "ControlNetLoader",
            "inputs": {"control_net_name": "instantid\\diffusion_pytorch_model.safetensors"},
        }
        negative_encode = nid()
        workflow[negative_encode] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative_prompt, "clip": [checkpoint, 1]},
        }
        latent = nid()
        workflow[latent] = {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        }

        columns = self._multi_character_columns(n, width)
        prev_model_ref = [checkpoint, 0]
        positive_conditionings: list[list] = []
        negative_conditionings: list[list] = []

        for i in range(n):
            column_x, column_w = columns[i]

            load_image = nid()
            workflow[load_image] = {"class_type": "LoadImage", "inputs": {"image": face_filenames[i]}}

            load_kps = nid()
            workflow[load_kps] = {"class_type": "LoadImage", "inputs": {"image": image_kps_filenames[i]}}

            positive_encode = nid()
            char_prompt = character_prompts[i].strip().rstrip(".")
            scene_text = scene_description.strip()
            prompt_text = f"{char_prompt}. {scene_text}" if scene_text else char_prompt
            workflow[positive_encode] = {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": prompt_text, "clip": [checkpoint, 1]},
            }

            base_mask = nid()
            workflow[base_mask] = {
                "class_type": "SolidMask",
                "inputs": {"value": 0.0, "width": width, "height": height},
            }
            fill_mask = nid()
            workflow[fill_mask] = {
                "class_type": "SolidMask",
                "inputs": {"value": 1.0, "width": column_w, "height": height},
            }
            composite_mask = nid()
            workflow[composite_mask] = {
                "class_type": "MaskComposite",
                "inputs": {
                    "destination": [base_mask, 0],
                    "source": [fill_mask, 0],
                    "x": column_x,
                    "y": 0,
                    "operation": "add",
                },
            }

            apply_instantid = nid()
            workflow[apply_instantid] = {
                "class_type": "ApplyInstantID",
                "inputs": {
                    "weight": 0.8,
                    "start_at": 0,
                    "end_at": 1,
                    "instantid": [instantid_loader, 0],
                    "insightface": [face_analysis, 0],
                    "control_net": [controlnet, 0],
                    "image": [load_image, 0],
                    "image_kps": [load_kps, 0],
                    "model": prev_model_ref,
                    "positive": [positive_encode, 0],
                    "negative": [negative_encode, 0],
                    "mask": [composite_mask, 0],
                },
            }
            prev_model_ref = [apply_instantid, 0]

            # Fix (2026-09-06, live-test finding after the image_kps fix):
            # ApplyInstantID's own "mask" input above only gates its InstantID
            # face-identity cross-attention - it does NOT restrict this
            # character's TEXT conditioning (character_prompt + shared
            # scene_description) to their column. Without this
            # ConditioningSetMask, the plain ConditioningCombine below merges
            # all N characters' full-canvas text conditioning into one
            # unrestricted signal for the shared KSampler, so SDXL sees N
            # competing whole-image scene descriptions at once - confirmed
            # live: this produced a collaged/quadrant-tiled image (each
            # character's prompt rendering its own mini-scene in a different
            # part of the canvas) with only the InstantID-masked face staying
            # sharp/correct. Restricting the TEXT conditioning to the same
            # column mask the identity already uses keeps both aligned, so
            # each character's prompt only shapes their own region.
            conditioning_mask = nid()
            workflow[conditioning_mask] = {
                "class_type": "ConditioningSetMask",
                "inputs": {
                    "conditioning": [apply_instantid, 1],
                    "mask": [composite_mask, 0],
                    "strength": 1.0,
                    "set_cond_area": "default",
                },
            }
            positive_conditionings.append([conditioning_mask, 0])
            negative_conditionings.append([apply_instantid, 2])

        # chain-combine every character's own POSITIVE/NEGATIVE conditioning
        # output into one shared pair for the sampler, same pattern as the
        # official example's two ConditioningCombine nodes (generalised here
        # to N-1 combines for N characters instead of a fixed 2). NEGATIVE is
        # NOT region-masked - it's the same shared negative_prompt text for
        # every character, so restricting it per-column would only make the
        # negative guidance weaker everywhere for no benefit.
        combined_positive = positive_conditionings[0]
        combined_negative = negative_conditionings[0]
        for i in range(1, n):
            combine_pos = nid()
            workflow[combine_pos] = {
                "class_type": "ConditioningCombine",
                "inputs": {"conditioning_1": combined_positive, "conditioning_2": positive_conditionings[i]},
            }
            combined_positive = [combine_pos, 0]

            combine_neg = nid()
            workflow[combine_neg] = {
                "class_type": "ConditioningCombine",
                "inputs": {"conditioning_1": combined_negative, "conditioning_2": negative_conditionings[i]},
            }
            combined_negative = [combine_neg, 0]

        sampler = nid()
        workflow[sampler] = {
            "class_type": "KSampler",
            "inputs": {
                "seed": int(seed),
                "steps": 30,
                "cfg": 4.5,
                "sampler_name": "ddpm",
                "scheduler": "karras",
                "denoise": 1,
                "model": prev_model_ref,
                "positive": combined_positive,
                "negative": combined_negative,
                "latent_image": [latent, 0],
            },
        }
        vae_decode = nid()
        workflow[vae_decode] = {
            "class_type": "VAEDecode",
            "inputs": {"samples": [sampler, 0], "vae": [checkpoint, 2]},
        }
        save_image = nid()
        workflow[save_image] = {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": filename_prefix, "images": [vae_decode, 0]},
        }

        return workflow

    def _generate_multi_character_identity_scene_image(self, request: GenerationRequest, dest: Path) -> Path:
        """Task #637 multi-character counterpart to
        _generate_identity_scene_image() above: composes ONE SDXL+InstantID
        scene image containing ALL of the scene's characters at once, each
        conditioned by their own face photo within their own masked region
        (see _build_multi_character_instantid_workflow), instead of the
        single whole-canvas face conditioning the plain method uses. The
        returned image feeds into the existing, unchanged Wan2.2
        image-to-video path (stage 2) exactly like the single-character
        case - _generate() below is the only caller and only reaches this
        method when there is more than one reference image to place.

        Only ever called with request.identity_character_prompts /
        request.identity_scene_description already populated 1:1 with
        request.character_reference_paths - generation_service.
        generate_scene() only populates those when the resolved provider's
        max_simultaneous_references (see the property above) actually
        allowed this many references for this scene in the first place, so
        this method can assume that invariant rather than re-deriving it.

        Also builds and uploads one synthetic image_kps per character (see
        _build_character_kps_image), using each character's own column from
        _multi_character_columns() - the SAME columns the region masks use
        in _build_multi_character_instantid_workflow, so a character's
        synthesized face position always agrees with where their mask lets
        the output actually draw them.

        Honesty note (Entwicklungsvertrag Regel 11): this code path's FIRST
        live test (task #637, 2026-09-06, a 3-character scene) ran without
        errors but produced a badly oversized/cropped face - root-caused
        afterwards to the missing image_kps (see _build_character_kps_image's
        docstring) and fixed by adding it. A SECOND live test that same day,
        after that fix, confirmed the oversized/cropped-face defect was gone
        (the InstantID-masked face was normal-sized and sharp) but revealed a
        second, separate defect: the two other characters' regions came out
        blurred/collaged rather than as a coherent shared scene - root-caused
        to the character text conditioning not being region-masked (see the
        ConditioningSetMask fix in _build_multi_character_instantid_workflow
        above). That fix itself has NOT yet been re-verified against a real
        ComfyUI instance - treat any claim that this now "works correctly"
        as unverified until a fresh live test confirms it.
        """
        width, height = _SDXL_RESOLUTIONS.get(
            request.aspect_ratio, _SDXL_RESOLUTIONS[AspectRatio.WIDESCREEN]
        )
        face_filenames = [self.upload_image(p) for p in request.character_reference_paths]
        character_prompts = request.identity_character_prompts
        if len(character_prompts) != len(face_filenames):
            raise ComfyUIAPIError(
                "Interner Fehler: Mehrfigur-Szene ohne passende identity_character_prompts "
                "- generation_service.generate_scene() haette diese Referenz-Kombination "
                "bereits sperren muessen."
            )

        columns = self._multi_character_columns(len(face_filenames), width)
        image_kps_filenames: list[str] = []
        for i, face_path in enumerate(request.character_reference_paths):
            column_x, column_w = columns[i]
            kps_path = dest.with_name(dest.stem + f"_identity_kps_{i}.png")
            self._build_character_kps_image(
                face_photo_path=face_path,
                canvas_width=width,
                canvas_height=height,
                column_x=column_x,
                column_width=column_w,
                dest=kps_path,
            )
            image_kps_filenames.append(self.upload_image(kps_path))

        workflow = self._build_multi_character_instantid_workflow(
            face_filenames=face_filenames,
            image_kps_filenames=image_kps_filenames,
            character_prompts=character_prompts,
            scene_description=request.identity_scene_description,
            negative_prompt=self.identity_negative_prompt,
            width=width,
            height=height,
            seed=uuid.uuid4().int % (2 ** 32),
            filename_prefix=f"voxini_identity_multi_{request.scene.id}",
            checkpoint_name=self.identity_checkpoint,
        )
        prompt_id = self.submit_job(workflow)
        history = self.wait_for_job(prompt_id)
        filename, subfolder, type_ = self._extract_output_file(history)

        identity_image_path = dest.with_name(dest.stem + "_identity_scene.png")
        self._download_output(filename, subfolder, type_, identity_image_path)

        # Same rationale as the single-character path - release SDXL/
        # InstantID's VRAM before stage 2 loads the Wan2.2 model.
        self.free_memory()

        return identity_image_path

    # -- Provider interface --------------------------------------------

    def generate(self, request: GenerationRequest) -> GenerationResult:
        try:
            return self._generate(request)
        except (ComfyUIAPIError, ComfyUIJobCancelled) as exc:
            return GenerationResult(success=False, error_message=str(exc))
        except Exception as exc:  # pragma: no cover - defensive catch-all
            return GenerationResult(success=False, error_message=f"Unerwarteter Fehler: {exc}")
        finally:
            # ALWAYS free VRAM/RAM after a job, success or failure, so a
            # long batch (many scenes generated back-to-back) doesn't
            # accumulate resident model state across jobs. See free_memory().
            self.free_memory()

    def _generate(self, request: GenerationRequest) -> GenerationResult:
        width, height = _RESOLUTIONS.get(
            (request.aspect_ratio, self.resolution), _RESOLUTIONS[(AspectRatio.WIDESCREEN, "720p")]
        )
        duration = max(0.5, min(request.scene.duration, self.max_clip_seconds))
        if self.local_model == "ltx25":
            # LTX-2.5's workflow computes the real frame count itself, inside
            # the graph (node 378, ComfyMathExpression "a * b + 1"), from a
            # duration-in-seconds primitive - so {{FRAMES}} here must be the
            # raw duration in seconds, NOT a pre-computed frame count. See
            # ltx25_text_to_video.json's _comment.
            frames = max(1, round(duration))
        else:
            frames = max(9, round(duration * DEFAULT_FPS) // 4 * 4 + 1)  # Wan latent length ~4n+1

        image_filename = None
        if request.character_reference_paths:
            if len(request.character_reference_paths) > 1:
                # Task #637 - a scene with more than one simultaneous
                # character reference only ever reaches this provider when
                # resolve_scene_references() already confirmed
                # max_simultaneous_references allowed it (see base.py /
                # generation_service.py) - which for THIS provider only
                # ever happens while identity_scene_mode is on (see the
                # property above). The check below is therefore defensive,
                # not the normal way this gets caught: it protects against
                # a future caller bypassing that choke point, or an
                # in-flight job whose identity_scene_mode was toggled off
                # between the check and this call.
                if not self.identity_scene_mode:
                    raise ComfyUIAPIError(
                        "Diese Szene hat mehrere gleichzeitige Charakter-Referenzbilder, aber die "
                        "Identitaets-Szenen-Pipeline ist nicht aktiviert - das einfache "
                        "Bild-zu-Video-Verfahren kann nur ein Referenzbild gleichzeitig verwenden. "
                        "Bitte 'Identitaets-Szenen-Pipeline' in den Projekteinstellungen aktivieren."
                    )
                start_image_path = str(
                    self._generate_multi_character_identity_scene_image(request, Path(request.dest_path))
                )
            else:
                start_image_path = request.character_reference_paths[0]
                if self.identity_scene_mode:
                    # two-step pipeline: replace the raw reference photo with a
                    # freshly composed scene image that only reuses the face
                    # (see _generate_identity_scene_image / Project.
                    # comfyui_identity_scene_mode). If stage 1 fails, we
                    # deliberately do NOT silently fall back to the plain
                    # reference photo - that would silently reintroduce the
                    # exact "whole scene locked to the reference photo" problem
                    # this mode exists to fix, without the user ever knowing
                    # why. Let the ComfyUIAPIError propagate to generate()'s
                    # existing error handling instead.
                    start_image_path = str(
                        self._generate_identity_scene_image(request, Path(request.dest_path))
                    )
            image_filename = self.upload_image(start_image_path)

        template = self.load_workflow_template(image_mode=image_filename is not None)
        workflow = self._fill_template(
            template,
            prompt=request.resolved_prompt,
            negative_prompt=self.negative_prompt,
            width=width,
            height=height,
            frames=frames,
            fps=DEFAULT_FPS,
            seed=uuid.uuid4().int % (2 ** 32),
            filename_prefix=f"voxini_{request.scene.id}",
            image_filename=image_filename,
        )

        prompt_id = self.submit_job(workflow)
        history = self.wait_for_job(prompt_id)
        filename, subfolder, type_ = self._extract_output_file(history)

        dest = Path(request.dest_path)
        warning = ""
        if self.upscale_to_1080p:
            raw_path = dest.with_name(dest.stem + "_raw" + dest.suffix)
            self._download_output(filename, subfolder, type_, raw_path)
            upscaled = self._upscale_local(raw_path, dest, target_height=1080)
            if not upscaled:
                warning = (
                    "1080p-Upscaling übersprungen: ffmpeg wurde nicht gefunden. Der Clip liegt "
                    f"in der nativen Wan2.2-Auflösung ({self.resolution}) vor."
                )
            try:
                raw_path.unlink(missing_ok=True)
            except Exception:
                pass
        else:
            self._download_output(filename, subfolder, type_, dest)

        return GenerationResult(success=True, file_path=str(dest), actual_cost=0.0, warning=warning)
