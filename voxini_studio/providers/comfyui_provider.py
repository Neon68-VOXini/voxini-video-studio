"""Local, free video generation via a locally running ComfyUI instance with
the Wan2.2 TI2V 5B model. Talks to ComfyUI's HTTP API (default
127.0.0.1:8188) - it never touches the network beyond that local socket, so
running this provider (including the automated tests in
tests/test_comfyui_provider.py, which spin up a real local fake HTTP
server) never makes a real internet call and never costs anything.

Workflow graphs are loaded from voxini_studio/providers/workflows/*.json -
these are the official Comfy-Org Wan2.2 5B TI2V templates (fetched from
github.com/Comfy-Org/workflow_templates), converted to ComfyUI's API
("prompt") submission format, with {{TOKEN}} placeholders filled in per
request. Keeping them as editable JSON files (not hard-coded Python dicts)
matches the requirement that the workflows stay user-editable.
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
    display_name = "Lokal - ComfyUI / Wan2.2 5B (kostenlos)"
    price_per_second = 0.0
    max_clip_seconds = 8.0
    """Wan2.2 5B is generated in one pass up to ~121 frames (~5s at 24fps);
    8s gives headroom while keeping single-job VRAM/time reasonable on a
    16GB card. Longer scenes are simply split into multiple clips upstream
    by the scene planner, same as any other provider's max_clip_seconds."""
    supported_aspect_ratios = [AspectRatio.WIDESCREEN, AspectRatio.VERTICAL, AspectRatio.SQUARE]
    requires_api_key = False

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
        identity_checkpoint: str = "sd_xl_base_1.0.safetensors",
        negative_prompt: Optional[str] = None,
        identity_negative_prompt: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.resolution = resolution if resolution in ("480p", "720p") else "720p"
        self.upscale_to_1080p = upscale_to_1080p
        self.session = session or requests.Session()
        self.workflows_dir = Path(workflows_dir) if workflows_dir else WORKFLOWS_DIR
        self.timeout = timeout
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
        filename = "wan22_image_to_video.json" if image_mode else "wan22_text_to_video.json"
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
        (_generate) decides whether/how to fall back."""
        width, height = _SDXL_RESOLUTIONS.get(
            request.aspect_ratio, _SDXL_RESOLUTIONS[AspectRatio.WIDESCREEN]
        )
        face_filename = self.upload_image(request.character_reference_paths[0])
        template = self.load_identity_template()
        workflow = self._fill_template(
            template,
            prompt=request.resolved_prompt,
            negative_prompt=self.identity_negative_prompt,
            width=width,
            height=height,
            seed=uuid.uuid4().int % (2 ** 32),
            filename_prefix=f"voxini_identity_{request.scene.id}",
            image_filename=face_filename,
            checkpoint_name=self.identity_checkpoint,
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
        frames = max(9, round(duration * DEFAULT_FPS) // 4 * 4 + 1)  # Wan latent length ~4n+1

        image_filename = None
        if request.character_reference_paths:
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
