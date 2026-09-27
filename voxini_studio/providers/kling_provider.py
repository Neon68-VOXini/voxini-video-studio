"""Cloud (paid, opt-in) video generation via the Kling AI API - the first of
three new cloud providers added in response to the user's explicit "Bau
alle ein" decision (2026-09-06) to abandon further local ComfyUI
multi-character tuning in favour of integrating Kling, Seedance and Veo
3.1, all of which support genuine multi-reference-image ("multiple
characters") generation unlike the existing RunwayProvider (which only
ever sends request.character_reference_paths[0] - see its own docstring).

HONESTY NOTE (Entwicklungsvertrag Regel 11): Kling's own official API
reference (https://kling.ai/document-api/...) is a client-rendered
single-page app - every fetch attempt during development (2026-09-06)
returned only the page shell with no real documentation content, never the
actual schema. The endpoint pattern and request/response field names below
are therefore assembled from: (a) Kling's documented authentication scheme
(JWT bearer tokens built from an AccessKey/SecretKey pair, HS256, short
expiry - reported consistently across every third-party source found) and
the async submit-then-poll task pattern common to the whole class of these
video APIs, and (b) a third-party API proxy's (useapi.net) detailed,
Kling-schema-mirroring documentation of the multi-image endpoint's request
body field names (prompt, image_0..image_3, element_1..element_3,
negative_prompt, duration, aspect_ratio, model_name, mode, enable_audio)
and response/task shape. This is DIRECTIONALLY correct (matches Kling's
own field-naming conventions and the general Kling API shape reported
everywhere) but has NOT been verified against Kling's real API responses,
since no Kling API key/account was available in this development
environment and the project's hard constraint forbids ever calling a real
paid API during development anyway. Tests (tests/test_kling_provider.py)
run only against a local fake HTTP server (tests/fakes/fake_kling_server.py)
implementing this same assumed schema - they prove the provider's own
logic (auth header construction, multi-image upload wiring, polling,
error handling) is internally correct, NOT that it will work unmodified
against the real Kling API. Before spending real money, a first live call
should be made with print/log statements enabled to inspect the actual
response shape and adjust field names if Kling's real API differs.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

import requests

from voxini_studio.core import credentials
from voxini_studio.models.project import AspectRatio
from voxini_studio.providers.base import GenerationRequest, GenerationResult, Provider

DEFAULT_BASE_URL = "https://api-singapore.klingai.com"

# USD/second estimates - Kling does not publish simple per-second USD
# pricing (it bills in a points/credits system tied to duration+mode+
# model), so these are ROUGH planning estimates only, not verified
# against a real invoice. Flagged in estimate_cost()'s docstring too.
MODEL_USD_PER_SECOND: dict[str, float] = {
    "kling-v3-0": 0.7,
    "kling-v1-6": 0.5,
}

# Max combined image/element references per model (per the API surface
# researched - see module docstring honesty note).
MODEL_MAX_REFERENCES: dict[str, int] = {
    "kling-v3-0": 3,
    "kling-v1-6": 4,
}

_ASPECT_RATIOS = {
    AspectRatio.WIDESCREEN: "16:9",
    AspectRatio.VERTICAL: "9:16",
    AspectRatio.SQUARE: "1:1",
}


class KlingAPIError(RuntimeError):
    """Raised for any Kling HTTP/API failure (unreachable, HTTP error
    status, malformed response, task failure)."""


class KlingJobCancelled(RuntimeError):
    """Raised by wait_for_task when cancel_check signals the user cancelled
    a job while it was queued or running."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def build_jwt(access_key: str, secret_key: str, expire_seconds: int = 1800) -> str:
    """Builds a Kling-style HS256 JWT (header/payload/signature, base64url,
    dot-separated) from an AccessKey/SecretKey pair without requiring the
    PyJWT dependency (not otherwise used anywhere in VOXini) - HS256 is a
    simple HMAC-SHA256 over "<header>.<payload>", so stdlib hmac/hashlib
    is sufficient. `nbf` is backdated by 5s to tolerate clock skew between
    this machine and Kling's servers, matching the pattern documented for
    this auth scheme across every third-party source found."""
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"iss": access_key, "exp": now + expire_seconds, "nbf": now - 5}
    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url(signature)}"


class KlingProvider(Provider):
    id = "kling"
    display_name = "Kling AI - kostenpflichtig (Cloud, Mehrfigur)"
    price_per_second = MODEL_USD_PER_SECOND["kling-v3-0"]
    max_clip_seconds = 10.0
    supported_aspect_ratios = [AspectRatio.WIDESCREEN, AspectRatio.VERTICAL, AspectRatio.SQUARE]
    requires_api_key = True

    def __init__(
        self,
        model_id: str = "kling-v3-0",
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        session: Optional[requests.Session] = None,
        timeout: float = 30.0,
    ):
        self.model_id = model_id
        self._explicit_access_key = access_key
        self._explicit_secret_key = secret_key
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout
        self.price_per_second = MODEL_USD_PER_SECOND.get(model_id, 0.7)

    @property
    def max_simultaneous_references(self) -> int:  # type: ignore[override]
        return MODEL_MAX_REFERENCES.get(self.model_id, 3)

    # -- credentials -----------------------------------------------------
    # Stored as "<access_key>:<secret_key>" under the generic "kling"
    # credential-store slot (see core/credentials.py) - a single text
    # field is what the setup-wizard UI can realistically offer without a
    # second dedicated input widget per cloud provider.

    def _resolve_keys(self) -> tuple[str, str]:
        combined = self._explicit_access_key
        if combined and self._explicit_secret_key:
            return combined, self._explicit_secret_key
        stored = credentials.get_api_key("kling")
        if not stored or ":" not in stored:
            raise KlingAPIError(
                "Kein gueltiger Kling API-Schluessel hinterlegt (Format "
                "'AccessKey:SecretKey'). Bitte in den Einstellungen eintragen."
            )
        access_key, secret_key = stored.split(":", 1)
        return access_key, secret_key

    def _headers(self) -> dict:
        access_key, secret_key = self._resolve_keys()
        token = build_jwt(access_key, secret_key)
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # -- cost estimation ---------------------------------------------------

    def estimate_cost(self, duration_seconds: float, model_id: Optional[str] = None) -> float:
        """ROUGH planning estimate only - Kling bills via a points/credits
        system this code has not verified against a real invoice (see
        module docstring). Treat as indicative, not exact."""
        model = model_id or self.model_id
        per_sec = MODEL_USD_PER_SECOND.get(model, 0.7)
        duration = max(1.0, duration_seconds)
        return round(per_sec * duration, 4)

    # -- connection test ---------------------------------------------------

    def check_connection(self) -> dict:
        """Calls GET /account/costs (Kling's documented account/usage
        endpoint) purely to validate the API key pair without starting a
        generation. Raises KlingAPIError on any failure."""
        try:
            resp = self.session.get(
                f"{self.base_url}/account/costs", headers=self._headers(), timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise KlingAPIError(f"Kling nicht erreichbar: {exc}") from exc
        if resp.status_code == 401:
            raise KlingAPIError("Kling API-Schluessel ungueltig (HTTP 401).")
        if resp.status_code != 200:
            raise KlingAPIError(f"Kling antwortete mit HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise KlingAPIError("Ungueltige Antwort von Kling (kein JSON).") from exc

    # -- job submission / polling -------------------------------------------

    def submit_multi_image_to_video(
        self,
        prompt: str,
        image_paths: list[str],
        aspect_ratio: str,
        duration: int,
        model: Optional[str] = None,
        negative_prompt: str = "",
    ) -> str:
        model = model or self.model_id
        max_refs = MODEL_MAX_REFERENCES.get(model, 3)
        used_paths = image_paths[:max_refs]

        body: dict = {
            "model_name": model,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "duration": str(duration),
            "aspect_ratio": aspect_ratio,
            "mode": "std",
        }
        for idx, path in enumerate(used_paths):
            body[f"image_{idx}"] = self._encode_image_data_uri(path)

        try:
            resp = self.session.post(
                f"{self.base_url}/v1/videos/multi-image2video",
                headers=self._headers(),
                json=body,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise KlingAPIError(f"Job konnte nicht gesendet werden: {exc}") from exc
        if resp.status_code not in (200, 201):
            try:
                detail = resp.json()
            except ValueError:
                detail = resp.text
            raise KlingAPIError(f"Kling lehnte den Job ab (HTTP {resp.status_code}): {detail}")
        data = resp.json()
        task_id = (data.get("data") or {}).get("task_id") or data.get("task_id")
        if not task_id:
            raise KlingAPIError(f"Keine Task-ID in Antwort: {data}")
        return task_id

    @staticmethod
    def _encode_image_data_uri(path: str) -> str:
        p = Path(path)
        if not p.exists():
            raise KlingAPIError(f"Referenzbild nicht gefunden: {p}")
        raw = p.read_bytes()
        return base64.b64encode(raw).decode("ascii")

    def get_task(self, task_id: str) -> dict:
        try:
            resp = self.session.get(
                f"{self.base_url}/v1/videos/multi-image2video/{task_id}",
                headers=self._headers(),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise KlingAPIError(f"Statusabfrage fehlgeschlagen: {exc}") from exc
        if resp.status_code != 200:
            raise KlingAPIError(f"Statusabfrage fehlgeschlagen: HTTP {resp.status_code}")
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
                raise KlingJobCancelled(f"Kling-Job {task_id} wurde vom Benutzer abgebrochen.")

            task = self.get_task(task_id)
            data = task.get("data") or task
            status = data.get("task_status")
            if status == "succeed":
                return data
            if status == "failed":
                raise KlingAPIError(
                    f"Kling-Job fehlgeschlagen: {data.get('task_status_msg', 'unbekannter Fehler')}"
                )

            if clock() - started > timeout:
                raise KlingAPIError(f"Zeitueberschreitung nach {timeout:.0f}s beim Warten auf Kling-Job.")

            sleep(poll_interval)

    def _download_output(self, url: str, dest_path: Path) -> None:
        try:
            resp = self.session.get(url, timeout=self.timeout, stream=True)
        except requests.RequestException as exc:
            raise KlingAPIError(f"Download fehlgeschlagen: {exc}") from exc
        if resp.status_code != 200:
            raise KlingAPIError(f"Download fehlgeschlagen: HTTP {resp.status_code}")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)

    # -- Provider interface --------------------------------------------------

    def generate(self, request: GenerationRequest) -> GenerationResult:
        try:
            return self._generate(request)
        except (KlingAPIError, KlingJobCancelled) as exc:
            return GenerationResult(success=False, error_message=str(exc))
        except Exception as exc:  # pragma: no cover - defensive catch-all
            return GenerationResult(success=False, error_message=f"Unerwarteter Fehler: {exc}")

    def _generate(self, request: GenerationRequest) -> GenerationResult:
        model = request.model_id or self.model_id
        aspect_ratio = _ASPECT_RATIOS.get(request.aspect_ratio, "16:9")
        duration = 10 if request.scene.duration > 5 else 5  # Kling: 5 or 10s only

        if not request.character_reference_paths:
            raise KlingAPIError(
                "Kling-Provider benoetigt mindestens ein Referenzbild "
                "(reines Text-zu-Video wird von diesem VOXini-Codepfad nicht unterstuetzt)."
            )

        task_id = self.submit_multi_image_to_video(
            prompt=request.resolved_prompt,
            image_paths=request.character_reference_paths,
            aspect_ratio=aspect_ratio,
            duration=duration,
            model=model,
        )
        task = self.wait_for_task(task_id)
        works = task.get("task_result", {}).get("videos") or []
        if not works:
            raise KlingAPIError("Kling-Job erfolgreich, aber keine Ausgabe-URL erhalten.")

        dest = Path(request.dest_path)
        self._download_output(works[0]["url"], dest)

        actual_cost = self.estimate_cost(duration, model_id=model)
        return GenerationResult(success=True, file_path=str(dest), actual_cost=actual_cost)
