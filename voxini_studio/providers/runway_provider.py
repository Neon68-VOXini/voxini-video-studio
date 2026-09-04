"""Cloud (paid, opt-in) video generation via the official Runway Dev API.

Verified against the current official Runway API reference (Runway's own
`rw-api-reference`/`rw-integrate-video`/`rw-integrate-uploads`/
`rw-check-org-details` developer docs, fetched live during development):
base URL `https://api.dev.runwayml.com`, headers `Authorization: Bearer
<key>` + `X-Runway-Version: 2024-11-06`, async task endpoints
`POST /v1/text_to_video`, `POST /v1/image_to_video`, task polling via
`GET /v1/tasks/{id}` (statuses PENDING/RUNNING/SUCCEEDED/FAILED/THROTTLED),
cancellation via `DELETE /v1/tasks/{id}`, a 3-step presigned upload flow via
`POST /v1/uploads`, and `GET /v1/organization` for credit balance / a
connection test that does NOT generate any video (and therefore costs
nothing).

Per the project's hard constraint, no code path here is ever exercised
against the real Runway API during development - tests
(tests/test_runway_provider.py) run against a local fake HTTP server
(tests/fakes/fake_runway_server.py) that returns simulated API responses
matching this documented schema, so this suite makes zero real network
calls and can never incur cost.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Callable, Optional

import requests

from voxini_studio.core import credentials
from voxini_studio.models.project import AspectRatio
from voxini_studio.providers.base import GenerationRequest, GenerationResult, Provider

DEFAULT_BASE_URL = "https://api.dev.runwayml.com"
API_VERSION = "2024-11-06"
CREDIT_USD_VALUE = 0.01  # $ per credit, per Runway's own developer docs

# credits/sec per model, verified 2026-09 against Runway's own current Dev
# pricing page (docs.dev.runwayml.com/guides/pricing). The stale "veo3"
# (non-.1) entry that used to be here has been removed - Runway no longer
# lists it as an available model. veo3.1 credits are conservatively kept at
# the "with audio" rate (40/15) since VOXini's request body does not
# currently set generate_audio=false explicitly - showing the cheaper
# no-audio rate without also making that request would risk understating
# the real cost. (VOXini always strips clip audio and remuxes the project's
# own song at export anyway - see ffmpeg_assembly.py - so requesting
# generate_audio=false explicitly, to both save money AND make this
# estimate accurate, is a worthwhile follow-up once verified against
# Runway's exact request schema.)
#
# NOTE: Runway also offers "act_two" (audio-driven performance capture /
# real lip-sync, 5 credits/sec) - deliberately NOT listed here yet. It
# needs a driving-audio input and a different request shape than the
# plain image+text used by _generate() below; listing it in this dict
# would let the user pick it in the setup wizard's model dropdown and pay
# for a job that isn't actually wired up correctly. Add it once a proper
# act_two code path exists.
MODEL_CREDITS_PER_SEC: dict[str, float] = {
    "gen4.5": 12.0,
    "gen4_turbo": 5.0,
    "veo3.1": 40.0,
    "veo3.1_fast": 15.0,
    "seedance2": 36.0,
}

# Models that can only animate an existing image (no pure text-to-video).
MODELS_REQUIRING_IMAGE = {"gen4_turbo"}

# Models compatible with the plain text_to_video endpoint.
MODELS_SUPPORTING_TEXT_TO_VIDEO = {"gen4.5", "veo3.1", "veo3.1_fast", "seedance2"}

MODEL_ALLOWED_DURATIONS: dict[str, list[int]] = {
    "veo3.1": [4, 6, 8],
    "veo3.1_fast": [4, 6, 8],
    "seedance2": list(range(2, 16)),
}
_DEFAULT_ALLOWED_DURATIONS = list(range(2, 11))  # most models: 2-10s

_RATIOS = {
    AspectRatio.WIDESCREEN: "1280:720",
    AspectRatio.VERTICAL: "720:1280",
    AspectRatio.SQUARE: "960:960",
}


class RunwayAPIError(RuntimeError):
    """Raised for any Runway HTTP/API failure (unreachable, HTTP error
    status, malformed response, task failure)."""


class RunwayJobCancelled(RuntimeError):
    """Raised by wait_for_task when cancel_check signals the user cancelled
    a job while it was queued or running."""


class RunwayBudgetExceeded(RuntimeError):
    """Raised when a job would exceed the project's configured Runway
    budget limit - callers should catch this and surface it as a blocked
    (not failed) generation, distinct from an API error."""


def _closest_allowed_duration(model: str, requested: float) -> int:
    allowed = MODEL_ALLOWED_DURATIONS.get(model, _DEFAULT_ALLOWED_DURATIONS)
    return min(allowed, key=lambda d: abs(d - requested))


class RunwayProvider(Provider):
    id = "runway"
    display_name = "Runway - kostenpflichtig (Cloud)"
    price_per_second = MODEL_CREDITS_PER_SEC["gen4_turbo"] * CREDIT_USD_VALUE
    max_clip_seconds = 10.0
    supported_aspect_ratios = [AspectRatio.WIDESCREEN, AspectRatio.VERTICAL, AspectRatio.SQUARE]
    requires_api_key = True

    def __init__(
        self,
        model_id: str = "gen4_turbo",
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
        self.price_per_second = MODEL_CREDITS_PER_SEC.get(model_id, 12.0) * CREDIT_USD_VALUE

    # -- credentials -----------------------------------------------------

    def _resolve_api_key(self) -> Optional[str]:
        if self._explicit_api_key:
            return self._explicit_api_key
        return credentials.get_runway_api_key()

    def _headers(self, extra: Optional[dict] = None) -> dict:
        key = self._resolve_api_key()
        if not key:
            raise RunwayAPIError(
                "Kein Runway API-Schluessel hinterlegt. Bitte in den Einstellungen eintragen."
            )
        headers = {
            "Authorization": f"Bearer {key}",
            "X-Runway-Version": API_VERSION,
        }
        if extra:
            headers.update(extra)
        return headers

    # -- cost estimation ---------------------------------------------------

    def estimate_cost(self, duration_seconds: float, model_id: Optional[str] = None) -> float:
        model = model_id or self.model_id
        credits_per_sec = MODEL_CREDITS_PER_SEC.get(model, 12.0)
        duration = _closest_allowed_duration(model, max(1.0, duration_seconds))
        return round(credits_per_sec * duration * CREDIT_USD_VALUE, 4)

    # -- connection test (no generation, no cost) ---------------------------

    def check_connection(self) -> dict:
        """Calls GET /v1/organization - retrieves credit balance/tier only,
        never starts a generation, so this is safe to call at any time
        (including with a freshly-typed, unsaved API key) without any risk
        of cost. Raises RunwayAPIError with a user-facing message on any
        failure (including a missing/invalid key)."""
        try:
            resp = self.session.get(
                f"{self.base_url}/v1/organization", headers=self._headers(), timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise RunwayAPIError(f"Runway nicht erreichbar: {exc}") from exc
        if resp.status_code == 401:
            raise RunwayAPIError("Runway API-Schluessel ungueltig (HTTP 401).")
        if resp.status_code != 200:
            raise RunwayAPIError(f"Runway antwortete mit HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise RunwayAPIError("Ungueltige Antwort von Runway (kein JSON).") from exc

    def credit_balance(self) -> float:
        return float(self.check_connection().get("creditBalance", 0))

    # -- uploads -------------------------------------------------------------

    def upload_image(self, image_path: str | Path) -> str:
        """3-step presigned upload (POST /v1/uploads -> upload to presigned
        URL -> use the returned runway:// URI), per Runway's documented
        upload flow. Returns the runway:// URI valid for 24h."""
        path = Path(image_path)
        if not path.exists():
            raise RunwayAPIError(f"Referenzbild nicht gefunden: {path}")

        try:
            resp = self.session.post(
                f"{self.base_url}/v1/uploads",
                headers=self._headers({"Content-Type": "application/json"}),
                json={"filename": path.name, "type": "ephemeral"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise RunwayAPIError(f"Upload-Anfrage fehlgeschlagen: {exc}") from exc
        if resp.status_code != 200:
            raise RunwayAPIError(f"Upload-Anfrage fehlgeschlagen: HTTP {resp.status_code}")
        slot = resp.json()
        upload_url = slot.get("uploadUrl")
        fields = slot.get("fields", {})
        runway_uri = slot.get("runwayUri")
        if not upload_url or not runway_uri:
            raise RunwayAPIError(f"Unerwartete Upload-Antwort von Runway: {slot}")

        try:
            with open(path, "rb") as f:
                upload_resp = self.session.post(
                    upload_url, data=fields, files={"file": (path.name, f, "application/octet-stream")},
                    timeout=self.timeout,
                )
        except requests.RequestException as exc:
            raise RunwayAPIError(f"Datei-Upload fehlgeschlagen: {exc}") from exc
        if upload_resp.status_code not in (200, 201, 204):
            raise RunwayAPIError(f"Datei-Upload fehlgeschlagen: HTTP {upload_resp.status_code}")

        return runway_uri

    # -- job submission / polling / cancellation ------------------------------

    def submit_text_to_video(self, prompt: str, ratio: str, duration: int, model: Optional[str] = None) -> str:
        model = model or self.model_id
        if model not in MODELS_SUPPORTING_TEXT_TO_VIDEO:
            raise RunwayAPIError(
                f"Modell '{model}' unterstuetzt kein reines Text-zu-Video - "
                "bitte ein Referenzbild angeben (Image-zu-Video) oder ein anderes Modell waehlen."
            )
        return self._submit(
            "/v1/text_to_video",
            {"model": model, "promptText": prompt, "ratio": ratio, "duration": duration},
        )

    def submit_image_to_video(
        self, prompt: str, image_uri: str, ratio: str, duration: int, model: Optional[str] = None
    ) -> str:
        model = model or self.model_id
        return self._submit(
            "/v1/image_to_video",
            {
                "model": model,
                "promptImage": image_uri,
                "promptText": prompt,
                "ratio": ratio,
                "duration": duration,
            },
        )

    def _submit(self, endpoint: str, body: dict) -> str:
        try:
            resp = self.session.post(
                f"{self.base_url}{endpoint}",
                headers=self._headers({"Content-Type": "application/json"}),
                json=body,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise RunwayAPIError(f"Job konnte nicht gesendet werden: {exc}") from exc
        if resp.status_code != 200:
            try:
                detail = resp.json()
            except ValueError:
                detail = resp.text
            raise RunwayAPIError(f"Runway lehnte den Job ab (HTTP {resp.status_code}): {detail}")
        data = resp.json()
        task_id = data.get("id")
        if not task_id:
            raise RunwayAPIError(f"Keine Task-ID in Antwort: {data}")
        return task_id

    def get_task(self, task_id: str) -> dict:
        try:
            resp = self.session.get(
                f"{self.base_url}/v1/tasks/{task_id}", headers=self._headers(), timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise RunwayAPIError(f"Statusabfrage fehlgeschlagen: {exc}") from exc
        if resp.status_code != 200:
            raise RunwayAPIError(f"Statusabfrage fehlgeschlagen: HTTP {resp.status_code}")
        return resp.json()

    def cancel_task(self, task_id: str) -> None:
        try:
            self.session.delete(
                f"{self.base_url}/v1/tasks/{task_id}", headers=self._headers(), timeout=self.timeout
            )
        except requests.RequestException:
            pass

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
                self.cancel_task(task_id)
                raise RunwayJobCancelled(f"Runway-Job {task_id} wurde vom Benutzer abgebrochen.")

            task = self.get_task(task_id)
            status = task.get("status")
            if status == "SUCCEEDED":
                return task
            if status == "FAILED":
                raise RunwayAPIError(f"Runway-Job fehlgeschlagen: {task.get('failure', 'unbekannter Fehler')}")

            if clock() - started > timeout:
                self.cancel_task(task_id)
                raise RunwayAPIError(f"Zeitueberschreitung nach {timeout:.0f}s beim Warten auf Runway-Job.")

            sleep(poll_interval)

    def _download_output(self, url: str, dest_path: Path) -> None:
        try:
            resp = self.session.get(url, timeout=self.timeout, stream=True)
        except requests.RequestException as exc:
            raise RunwayAPIError(f"Download fehlgeschlagen: {exc}") from exc
        if resp.status_code != 200:
            raise RunwayAPIError(f"Download fehlgeschlagen: HTTP {resp.status_code}")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)

    # -- Provider interface --------------------------------------------------

    def generate(self, request: GenerationRequest) -> GenerationResult:
        try:
            return self._generate(request)
        except (RunwayAPIError, RunwayJobCancelled, RunwayBudgetExceeded) as exc:
            return GenerationResult(success=False, error_message=str(exc))
        except Exception as exc:  # pragma: no cover - defensive catch-all
            return GenerationResult(success=False, error_message=f"Unerwarteter Fehler: {exc}")

    def _generate(self, request: GenerationRequest) -> GenerationResult:
        model = request.model_id or self.model_id
        ratio = _RATIOS.get(request.aspect_ratio, _RATIOS[AspectRatio.WIDESCREEN])
        duration = _closest_allowed_duration(model, max(1.0, request.scene.duration))

        if request.character_reference_paths:
            image_uri = self.upload_image(request.character_reference_paths[0])
            task_id = self.submit_image_to_video(
                prompt=request.resolved_prompt, image_uri=image_uri, ratio=ratio,
                duration=duration, model=model,
            )
        else:
            if model in MODELS_REQUIRING_IMAGE:
                raise RunwayAPIError(
                    f"Modell '{model}' benoetigt zwingend ein Referenzbild "
                    "(kein reines Text-zu-Video). Bitte ein Charakter-Referenzbild hinzufuegen "
                    "oder ein anderes Modell (z. B. gen4.5) waehlen."
                )
            task_id = self.submit_text_to_video(
                prompt=request.resolved_prompt, ratio=ratio, duration=duration, model=model,
            )

        task = self.wait_for_task(task_id)
        outputs = task.get("output") or []
        if not outputs:
            raise RunwayAPIError("Runway-Job erfolgreich, aber keine Ausgabe-URL erhalten.")

        dest = Path(request.dest_path)
        self._download_output(outputs[0], dest)

        actual_cost = self.estimate_cost(duration, model_id=model)
        return GenerationResult(success=True, file_path=str(dest), actual_cost=actual_cost)
