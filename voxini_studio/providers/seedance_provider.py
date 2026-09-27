"""Cloud (paid, opt-in) video generation via ByteDance's Seedance model on
the BytePlus ModelArk platform (the international counterpart of
ByteDance's own Volcengine Ark) - second of the three cloud providers added
per the user's "Bau alle ein" decision (2026-09-06). See kling_provider.py's
module docstring for the shared background/context.

HONESTY NOTE (Entwicklungsvertrag Regel 11): Seedance is not a standalone
API - it is a model deployed on BytePlus ModelArk / Volcengine Ark, which
layers its own request/response envelope on top. The schema below (base
URL, POST /api/v3/contents/generations/tasks, the `content` array of
{type, text|image_url, role} items, GET .../tasks/{id} polling with a
`status` field and `content.video_url` result) was NOT taken from a raw
fetch of ByteDance's own console docs (those require a logged-in session
and were not reachable in this environment) but is corroborated
consistently across multiple independent third-party integration guides
that describe themselves as mirroring the official Ark request schema
field-for-field (parameter names, `role` values like "reference_image",
the async task/status/content shape). This is believed accurate but, like
kling_provider.py, has NOT been exercised against a real BytePlus/Ark
account - no API key was available, and the project's hard constraint
forbids ever calling a real paid API during development. A local
reference-image file is sent as a base64 `data:` URI in `image_url.url`
(mirroring the OpenAI-style vision request convention that Ark's own
multimodal endpoints are documented to also follow) rather than an
externally-hosted HTTPS URL, since VOXini's character reference photos
live only on the user's disk - this specific detail is the least-verified
part of this provider and should be confirmed against a real response
before relying on it for a paid batch.
"""
from __future__ import annotations

import base64
import mimetypes
import time
from pathlib import Path
from typing import Callable, Optional

import requests

from voxini_studio.core import credentials
from voxini_studio.models.project import AspectRatio
from voxini_studio.providers.base import GenerationRequest, GenerationResult, Provider

DEFAULT_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"

# Seedance has no simple published per-second USD rate (Ark bills by
# input+output token count, itself a function of resolution/duration/fps -
# see the official formula referenced in research). This is a rough
# planning estimate for a typical 720p/5s clip, NOT a verified price.
ROUGH_USD_PER_SECOND_720P = 0.9

MODEL_MAX_REFERENCES: dict[str, int] = {
    "doubao-seedance-1-0-lite-i2v-250428": 4,
    "doubao-seedance-1-0-pro-250528": 4,
}

_ASPECT_RATIOS = {
    AspectRatio.WIDESCREEN: "16:9",
    AspectRatio.VERTICAL: "9:16",
    AspectRatio.SQUARE: "1:1",
}


class SeedanceAPIError(RuntimeError):
    """Raised for any Seedance/Ark HTTP/API failure (unreachable, HTTP
    error status, malformed response, task failure)."""


class SeedanceJobCancelled(RuntimeError):
    """Raised by wait_for_task when cancel_check signals the user
    cancelled a job while it was queued or running."""


class SeedanceProvider(Provider):
    id = "seedance"
    display_name = "Seedance (ByteDance) - kostenpflichtig (Cloud, Mehrfigur)"
    price_per_second = ROUGH_USD_PER_SECOND_720P
    max_clip_seconds = 15.0
    supported_aspect_ratios = [AspectRatio.WIDESCREEN, AspectRatio.VERTICAL, AspectRatio.SQUARE]
    requires_api_key = True

    def __init__(
        self,
        model_id: str = "doubao-seedance-1-0-lite-i2v-250428",
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        session: Optional[requests.Session] = None,
        timeout: float = 30.0,
    ):
        self.model_id = model_id
        self._explicit_api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout

    @property
    def max_simultaneous_references(self) -> int:  # type: ignore[override]
        return MODEL_MAX_REFERENCES.get(self.model_id, 4)

    # -- credentials -----------------------------------------------------

    def _resolve_api_key(self) -> str:
        key = self._explicit_api_key or credentials.get_api_key("seedance")
        if not key:
            raise SeedanceAPIError(
                "Kein Seedance/BytePlus API-Schluessel hinterlegt. Bitte in den Einstellungen eintragen."
            )
        return key

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._resolve_api_key()}",
            "Content-Type": "application/json",
        }

    # -- connection test ---------------------------------------------------

    def check_connection(self) -> dict:
        """HONESTY NOTE: no free/no-cost "account info" endpoint for
        BytePlus ModelArk/Volcengine Ark was found during research (unlike
        Runway's GET /v1/organization or Kling's GET /account/costs), so
        this deliberately does NOT make any real API call - it only
        validates that a non-empty key is configured/resolvable, to avoid
        accidentally submitting (and paying for) a real generation task
        just to "test" the connection. A real end-to-end test can only
        happen via an actual (paid) generate() call."""
        key = self._resolve_api_key()
        return {"key_present": bool(key)}

    # -- cost estimation ---------------------------------------------------

    def estimate_cost(self, duration_seconds: float, model_id: Optional[str] = None) -> float:
        """ROUGH planning estimate only - see module docstring. Ark's real
        billing formula is token-based (duration x resolution x fps /
        1024), not a flat per-second rate."""
        duration = max(1.0, duration_seconds)
        return round(ROUGH_USD_PER_SECOND_720P * duration, 4)

    # -- image encoding ----------------------------------------------------

    @staticmethod
    def _encode_image_data_uri(path: str) -> str:
        p = Path(path)
        if not p.exists():
            raise SeedanceAPIError(f"Referenzbild nicht gefunden: {p}")
        mime = mimetypes.guess_type(p.name)[0] or "image/png"
        raw = base64.b64encode(p.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{raw}"

    # -- job submission / polling -------------------------------------------

    def submit_task(
        self,
        prompt: str,
        image_paths: list[str],
        ratio: str,
        duration: int,
        model: Optional[str] = None,
    ) -> str:
        model = model or self.model_id
        max_refs = MODEL_MAX_REFERENCES.get(model, 4)
        used_paths = image_paths[:max_refs]

        content: list[dict] = [{"type": "text", "text": prompt}]
        for path in used_paths:
            content.append({
                "type": "image_url",
                "image_url": {"url": self._encode_image_data_uri(path)},
                "role": "reference_image",
            })

        body = {
            "model": model,
            "content": content,
            "ratio": ratio,
            "duration": duration,
            "resolution": "720p",
            "watermark": False,
            "generate_audio": False,
        }
        try:
            resp = self.session.post(
                f"{self.base_url}/contents/generations/tasks",
                headers=self._headers(),
                json=body,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise SeedanceAPIError(f"Job konnte nicht gesendet werden: {exc}") from exc
        if resp.status_code not in (200, 201):
            try:
                detail = resp.json()
            except ValueError:
                detail = resp.text
            raise SeedanceAPIError(f"Seedance lehnte den Job ab (HTTP {resp.status_code}): {detail}")
        data = resp.json()
        task_id = data.get("id")
        if not task_id:
            raise SeedanceAPIError(f"Keine Task-ID in Antwort: {data}")
        return task_id

    def get_task(self, task_id: str) -> dict:
        try:
            resp = self.session.get(
                f"{self.base_url}/contents/generations/tasks/{task_id}",
                headers=self._headers(),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise SeedanceAPIError(f"Statusabfrage fehlgeschlagen: {exc}") from exc
        if resp.status_code != 200:
            raise SeedanceAPIError(f"Statusabfrage fehlgeschlagen: HTTP {resp.status_code}")
        return resp.json()

    def wait_for_task(
        self,
        task_id: str,
        timeout: float = 900.0,
        poll_interval: float = 5.0,
        cancel_check: Optional[Callable[[], bool]] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> dict:
        started = clock()
        while True:
            if cancel_check is not None and cancel_check():
                raise SeedanceJobCancelled(f"Seedance-Job {task_id} wurde vom Benutzer abgebrochen.")

            task = self.get_task(task_id)
            status = task.get("status")
            if status in ("succeeded", "completed"):
                return task
            if status in ("failed", "expired"):
                raise SeedanceAPIError(f"Seedance-Job fehlgeschlagen: {task.get('error', 'unbekannter Fehler')}")

            if clock() - started > timeout:
                raise SeedanceAPIError(f"Zeitueberschreitung nach {timeout:.0f}s beim Warten auf Seedance-Job.")

            sleep(poll_interval)

    def _download_output(self, url: str, dest_path: Path) -> None:
        try:
            resp = self.session.get(url, timeout=self.timeout, stream=True)
        except requests.RequestException as exc:
            raise SeedanceAPIError(f"Download fehlgeschlagen: {exc}") from exc
        if resp.status_code != 200:
            raise SeedanceAPIError(f"Download fehlgeschlagen: HTTP {resp.status_code}")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)

    # -- Provider interface --------------------------------------------------

    def generate(self, request: GenerationRequest) -> GenerationResult:
        try:
            return self._generate(request)
        except (SeedanceAPIError, SeedanceJobCancelled) as exc:
            return GenerationResult(success=False, error_message=str(exc))
        except Exception as exc:  # pragma: no cover - defensive catch-all
            return GenerationResult(success=False, error_message=f"Unerwarteter Fehler: {exc}")

    def _generate(self, request: GenerationRequest) -> GenerationResult:
        model = request.model_id or self.model_id
        ratio = _ASPECT_RATIOS.get(request.aspect_ratio, "16:9")
        duration = max(4, min(15, round(request.scene.duration) or 5))

        task_id = self.submit_task(
            prompt=request.resolved_prompt,
            image_paths=request.character_reference_paths,
            ratio=ratio,
            duration=duration,
            model=model,
        )
        task = self.wait_for_task(task_id)
        video_url = (task.get("content") or {}).get("video_url")
        if not video_url:
            raise SeedanceAPIError("Seedance-Job erfolgreich, aber keine Ausgabe-URL erhalten.")

        dest = Path(request.dest_path)
        self._download_output(video_url, dest)

        actual_cost = self.estimate_cost(duration, model_id=model)
        return GenerationResult(success=True, file_path=str(dest), actual_cost=actual_cost)
