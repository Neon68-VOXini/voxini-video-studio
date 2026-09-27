"""Cloud (paid, opt-in) video generation via Google's Veo 3.1 model through
the direct Gemini API - third of the three cloud providers added per the
user's "Bau alle ever" ("Bau alle ein") decision (2026-09-06). See
kling_provider.py's module docstring for the shared background/context.

Verified directly against Google's own official documentation
(https://ai.google.dev/gemini-api/docs/veo, fetched and read in full
2026-09-06 - unlike Kling and Seedance, this page IS server-rendered and
returned real content, so the schema below is high-confidence, not a
third-party reconstruction):

- Base URL: https://generativelanguage.googleapis.com/v1beta
- Auth: header "x-goog-api-key: <key>" on every call (submit, poll, download)
- Submit: POST /models/{model}:predictLongRunning with body
  {"instances": [{"prompt": ..., "referenceImages": [{"image":
  {"inlineData": {"mimeType": ..., "data": <base64>}}, "referenceType":
  "asset"}, ...]}], "parameters": {"aspectRatio": ..., "resolution": ...,
  "durationSeconds": ..., "personGeneration": "allow_adult"}}
  ("referenceImages" - "Ingredients to Video" - accepts up to 3 images,
  Veo 3.1/3.1 Fast only; durationSeconds must be "8" when reference images
  are used; personGeneration must be "allow_adult" for this mode).
- Submit response: {"name": "<operation name>"}
- Poll: GET /{operation_name} (already includes its own path segment) ->
  {"done": bool, "response": {"generateVideoResponse": {"generatedSamples":
  [{"video": {"uri": <signed download URL>}}]}}}
- Download: GET <uri> with the same x-goog-api-key header.

Per the project's hard constraint, this is still never exercised against
the real Gemini API during development - tests (tests/test_veo_provider.py)
run only against a local fake HTTP server (tests/fakes/fake_veo_server.py)
implementing this documented schema, making zero real (paid) API calls.
Only ONE genuinely unverified assumption remains: whether a real Gemini
API key with Veo access billed for this session behaves exactly as
documented (rate limits, regional personGeneration restrictions, etc.) -
that can only be confirmed with a real key, which was not available here.
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

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# No official simple per-second USD figure was present on the fetched docs
# page (it only links to a separate pricing page) - this is a widely
# reported ballpark for veo-3.1-generate-preview at 720p with audio, NOT
# taken from Google's own pricing table (not fetched this session).
ROUGH_USD_PER_SECOND: dict[str, float] = {
    "veo-3.1-generate-preview": 0.40,
    "veo-3.1-fast-generate-preview": 0.15,
}

_ASPECT_RATIOS = {
    AspectRatio.WIDESCREEN: "16:9",
    AspectRatio.VERTICAL: "9:16",
    AspectRatio.SQUARE: "16:9",  # Veo has no native 1:1 - falls back to 16:9
}

MAX_REFERENCE_IMAGES = 3


class VeoAPIError(RuntimeError):
    """Raised for any Veo/Gemini API HTTP/API failure (unreachable, HTTP
    error status, malformed response, task failure)."""


class VeoJobCancelled(RuntimeError):
    """Raised by wait_for_operation when cancel_check signals the user
    cancelled a job while it was queued or running. Note: the Gemini API
    has no documented cancel-operation endpoint, so this only stops VOXini
    from continuing to poll/wait - it does not stop billing for a job
    already submitted."""


class VeoProvider(Provider):
    id = "veo"
    display_name = "Google Veo 3.1 - kostenpflichtig (Cloud, Mehrfigur)"
    price_per_second = ROUGH_USD_PER_SECOND["veo-3.1-generate-preview"]
    max_clip_seconds = 8.0
    supported_aspect_ratios = [AspectRatio.WIDESCREEN, AspectRatio.VERTICAL]
    requires_api_key = True
    max_simultaneous_references = MAX_REFERENCE_IMAGES

    def __init__(
        self,
        model_id: str = "veo-3.1-generate-preview",
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
        self.price_per_second = ROUGH_USD_PER_SECOND.get(model_id, 0.40)

    # -- credentials -----------------------------------------------------

    def _resolve_api_key(self) -> str:
        key = self._explicit_api_key or credentials.get_api_key("veo")
        if not key:
            raise VeoAPIError(
                "Kein Google Gemini/Veo API-Schluessel hinterlegt. Bitte in den Einstellungen eintragen."
            )
        return key

    def _headers(self) -> dict:
        return {"x-goog-api-key": self._resolve_api_key(), "Content-Type": "application/json"}

    # -- connection test ---------------------------------------------------

    def check_connection(self) -> dict:
        """Calls GET /models/{model} (Gemini API's model-metadata lookup)
        purely to validate the API key without starting a generation - a
        read-only, documented, no-cost call. Raises VeoAPIError on any
        failure. NOTE: unlike Runway's/Kling's dedicated account-info
        endpoints, this doesn't return billing/credit information, only
        confirms the key can authenticate."""
        try:
            resp = self.session.get(
                f"{self.base_url}/models/{self.model_id}", headers=self._headers(), timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise VeoAPIError(f"Veo/Gemini nicht erreichbar: {exc}") from exc
        if resp.status_code == 401 or resp.status_code == 403:
            raise VeoAPIError(f"Veo/Gemini API-Schluessel ungueltig (HTTP {resp.status_code}).")
        if resp.status_code != 200:
            raise VeoAPIError(f"Veo/Gemini antwortete mit HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise VeoAPIError("Ungueltige Antwort von Veo/Gemini (kein JSON).") from exc

    # -- cost estimation ---------------------------------------------------

    def estimate_cost(self, duration_seconds: float, model_id: Optional[str] = None) -> float:
        """ROUGH planning estimate only - see module docstring."""
        model = model_id or self.model_id
        per_sec = ROUGH_USD_PER_SECOND.get(model, 0.40)
        duration = max(4.0, min(8.0, duration_seconds))
        return round(per_sec * duration, 4)

    # -- image encoding ----------------------------------------------------

    @staticmethod
    def _encode_reference_image(path: str) -> dict:
        p = Path(path)
        if not p.exists():
            raise VeoAPIError(f"Referenzbild nicht gefunden: {p}")
        mime = mimetypes.guess_type(p.name)[0] or "image/png"
        data = base64.b64encode(p.read_bytes()).decode("ascii")
        return {
            "image": {"inlineData": {"mimeType": mime, "data": data}},
            "referenceType": "asset",
        }

    # -- job submission / polling -------------------------------------------

    def submit(
        self,
        prompt: str,
        image_paths: list[str],
        aspect_ratio: str,
        model: Optional[str] = None,
    ) -> str:
        model = model or self.model_id
        instance: dict = {"prompt": prompt}
        parameters: dict = {"aspectRatio": aspect_ratio, "resolution": "720p"}

        used_paths = image_paths[:MAX_REFERENCE_IMAGES]
        if used_paths:
            instance["referenceImages"] = [self._encode_reference_image(p) for p in used_paths]
            # Per Google's own parameter table: durationSeconds must be "8"
            # and personGeneration must be "allow_adult" whenever
            # referenceImages are used.
            parameters["durationSeconds"] = "8"
            parameters["personGeneration"] = "allow_adult"
        else:
            parameters["durationSeconds"] = "8"
            parameters["personGeneration"] = "allow_all"

        body = {"instances": [instance], "parameters": parameters}
        try:
            resp = self.session.post(
                f"{self.base_url}/models/{model}:predictLongRunning",
                headers=self._headers(),
                json=body,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise VeoAPIError(f"Job konnte nicht gesendet werden: {exc}") from exc
        if resp.status_code != 200:
            try:
                detail = resp.json()
            except ValueError:
                detail = resp.text
            raise VeoAPIError(f"Veo lehnte den Job ab (HTTP {resp.status_code}): {detail}")
        data = resp.json()
        operation_name = data.get("name")
        if not operation_name:
            raise VeoAPIError(f"Keine Operation-ID in Antwort: {data}")
        return operation_name

    def get_operation(self, operation_name: str) -> dict:
        try:
            resp = self.session.get(
                f"{self.base_url}/{operation_name}", headers=self._headers(), timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise VeoAPIError(f"Statusabfrage fehlgeschlagen: {exc}") from exc
        if resp.status_code != 200:
            raise VeoAPIError(f"Statusabfrage fehlgeschlagen: HTTP {resp.status_code}")
        return resp.json()

    def wait_for_operation(
        self,
        operation_name: str,
        timeout: float = 900.0,
        poll_interval: float = 10.0,
        cancel_check: Optional[Callable[[], bool]] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> dict:
        started = clock()
        while True:
            if cancel_check is not None and cancel_check():
                raise VeoJobCancelled(f"Veo-Job {operation_name} wurde vom Benutzer abgebrochen.")

            op = self.get_operation(operation_name)
            if op.get("done"):
                if "error" in op:
                    raise VeoAPIError(f"Veo-Job fehlgeschlagen: {op['error']}")
                return op

            if clock() - started > timeout:
                raise VeoAPIError(f"Zeitueberschreitung nach {timeout:.0f}s beim Warten auf Veo-Job.")

            sleep(poll_interval)

    def _download_output(self, uri: str, dest_path: Path) -> None:
        try:
            resp = self.session.get(uri, headers=self._headers(), timeout=self.timeout, stream=True)
        except requests.RequestException as exc:
            raise VeoAPIError(f"Download fehlgeschlagen: {exc}") from exc
        if resp.status_code != 200:
            raise VeoAPIError(f"Download fehlgeschlagen: HTTP {resp.status_code}")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)

    # -- Provider interface --------------------------------------------------

    def generate(self, request: GenerationRequest) -> GenerationResult:
        try:
            return self._generate(request)
        except (VeoAPIError, VeoJobCancelled) as exc:
            return GenerationResult(success=False, error_message=str(exc))
        except Exception as exc:  # pragma: no cover - defensive catch-all
            return GenerationResult(success=False, error_message=f"Unerwarteter Fehler: {exc}")

    def _generate(self, request: GenerationRequest) -> GenerationResult:
        model = request.model_id or self.model_id
        aspect_ratio = _ASPECT_RATIOS.get(request.aspect_ratio, "16:9")

        operation_name = self.submit(
            prompt=request.resolved_prompt,
            image_paths=request.character_reference_paths,
            aspect_ratio=aspect_ratio,
            model=model,
        )
        op = self.wait_for_operation(operation_name)
        try:
            samples = op["response"]["generateVideoResponse"]["generatedSamples"]
            video_uri = samples[0]["video"]["uri"]
        except (KeyError, IndexError, TypeError) as exc:
            raise VeoAPIError(f"Veo-Job erfolgreich, aber keine Ausgabe-URL erhalten: {op}") from exc

        dest = Path(request.dest_path)
        self._download_output(video_uri, dest)

        actual_cost = self.estimate_cost(8.0, model_id=model)
        return GenerationResult(success=True, file_path=str(dest), actual_cost=actual_cost)
