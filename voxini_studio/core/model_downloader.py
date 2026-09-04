"""Downloads the Wan2.2 ComfyUI model files the setup wizard lists, with
the exact size/destination confirmation the binding spec requires before
any large download starts. Deliberately does nothing automatically - every
function here must be explicitly invoked by the UI after the user has seen
and confirmed size + destination; nothing in this module runs on import or
app startup, and no model file is ever bundled into the app itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import requests


class DownloadError(RuntimeError):
    pass


@dataclass
class ModelDownloadItem:
    """Ein einzelner Eintrag aus REQUIRED_MODELS, aufgeloest zu einem
    konkreten Zielpfad plus ermittelter Groesse - Baustein fuer die EINE
    kombinierte Bestätigung (Gesamtgröße/Speicherbedarf/Zielordner) vor
    dem vollautomatischen Download aller fehlenden Modelldateien."""
    spec: dict
    dest: Path
    already_present: bool
    size_bytes: Optional[int]  # None = HEAD-Request fehlgeschlagen, approx_size_gb als Fallback anzeigen

    @property
    def display_size_bytes(self) -> int:
        """Fuer Summenbildung: echte Groesse falls bekannt, sonst die
        dokumentierte Schätzung aus REQUIRED_MODELS (approx_size_gb)."""
        if self.size_bytes is not None:
            return self.size_bytes
        return int(self.spec.get("approx_size_gb", 0) * (1024 ** 3))


def plan_downloads(
    specs: list[dict],
    models_dir: str | Path,
    session: Optional[requests.Session] = None,
) -> list[ModelDownloadItem]:
    """Loest jede REQUIRED_MODELS-Spezifikation zu einem Zielpfad auf und
    ermittelt fuer noch fehlende Dateien per HTTP-HEAD die echte aktuelle
    Groesse (schlaegt das fehl, bleibt size_bytes None und die UI zeigt die
    approx_size_gb-Schätzung als "nicht bestätigt" an - siehe
    ModelDownloadItem.display_size_bytes). Macht selbst noch KEINEN
    Download - reine Planungsfunktion fuer die EINE kombinierte
    Bestätigung, die die UI danach anzeigt."""
    session = session or requests.Session()
    base = Path(models_dir)
    items: list[ModelDownloadItem] = []
    for spec in specs:
        dest = base / spec["subdir"] / spec["filename"]
        present = dest.exists()
        size = None if present else remote_file_size(spec["url"], session=session)
        items.append(ModelDownloadItem(spec=spec, dest=dest, already_present=present, size_bytes=size))
    return items


def remote_file_size(url: str, timeout: float = 15.0, session: Optional[requests.Session] = None) -> Optional[int]:
    """HTTP HEAD to get the real current size before showing it to the
    user for confirmation - REQUIRED_MODELS' approx_size_gb is only an
    indicative fallback for when this HEAD request itself fails (e.g. no
    network yet during initial setup-wizard review)."""
    session = session or requests.Session()
    try:
        resp = session.head(url, timeout=timeout, allow_redirects=True)
        length = resp.headers.get("Content-Length")
        return int(length) if length is not None else None
    except (requests.RequestException, ValueError):
        return None


def download_file(
    url: str,
    dest_path: str | Path,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    session: Optional[requests.Session] = None,
    timeout: float = 30.0,
    chunk_size: int = 1024 * 1024,
) -> Path:
    """Streams url to dest_path. progress_callback(bytes_done, bytes_total)
    is called after every chunk (bytes_total may be 0 if unknown).
    cancel_check() is polled between chunks so the setup wizard's Abbrechen
    button can actually stop a large in-progress download. Writes to a
    `.part` file first and only renames to the final name on success, so a
    cancelled/failed download never leaves a file that looks complete."""
    session = session or requests.Session()
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part_path = dest.with_suffix(dest.suffix + ".part")

    try:
        resp = session.get(url, stream=True, timeout=timeout)
    except requests.RequestException as exc:
        raise DownloadError(f"Download fehlgeschlagen: {exc}") from exc
    if resp.status_code != 200:
        raise DownloadError(f"Download fehlgeschlagen: HTTP {resp.status_code}")

    total = int(resp.headers.get("Content-Length", 0))
    done = 0
    try:
        with open(part_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=chunk_size):
                if cancel_check is not None and cancel_check():
                    raise DownloadError("Download vom Benutzer abgebrochen.")
                if chunk:
                    f.write(chunk)
                    done += len(chunk)
                    if progress_callback is not None:
                        progress_callback(done, total)
    except Exception:
        part_path.unlink(missing_ok=True)
        raise

    part_path.rename(dest)
    return dest


def format_size(num_bytes: Optional[int]) -> str:
    if not num_bytes:
        return "unbekannte Größe"
    gb = num_bytes / (1024 ** 3)
    if gb >= 1:
        return f"{gb:.2f} GB"
    mb = num_bytes / (1024 ** 2)
    return f"{mb:.1f} MB"
