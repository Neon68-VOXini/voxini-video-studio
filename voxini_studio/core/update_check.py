"""Prueft ueber die GitHub Releases API, ob eine neuere Version von VOXini
Video Studio verfuegbar ist. Macht selbst NICHTS automatisch - reine
Abfrage-/Vergleichsfunktion. Download und Installation laufen erst nach
expliziter Bestaetigung des Nutzers im Update-Dialog (siehe app_update.py,
update_dialog.py) - entspricht Punkt 4 der Spezifikation: "Updates niemals
unbemerkt installieren. Neue Version anzeigen -> Aenderungen nennen ->
Nutzer bestaetigt -> Update mit Sicherung und Rueckkehrmoeglichkeit
installieren."."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import requests

from voxini_studio import __version__

GITHUB_OWNER = "Neon68-VOXini"
GITHUB_REPO = "voxini-video-studio"
_RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
_ASSET_NAME = "VOXini Video Studio.exe"


class UpdateCheckError(RuntimeError):
    pass


@dataclass
class UpdateInfo:
    current_version: str
    latest_version: str
    changelog: str
    download_url: str
    asset_size_bytes: Optional[int]

    @property
    def is_newer(self) -> bool:
        return _version_tuple(self.latest_version) > _version_tuple(self.current_version)


def _version_tuple(version: str) -> tuple[int, ...]:
    """Wandelt z.B. "1.2.10" (auch mit fuehrendem "v") in (1, 2, 10) um,
    fuer einen korrekten NUMERISCHEN statt alphabetischen Vergleich - sonst
    waere der String "1.9.0" faelschlich "groesser" als "1.10.0"."""
    cleaned = version.strip()
    if cleaned.lower().startswith("v"):
        cleaned = cleaned[1:]
    parts: list[int] = []
    for piece in cleaned.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def check_for_update(timeout: float = 10.0) -> Optional[UpdateInfo]:
    """Fuer den STILLEN Hintergrund-Check beim Programmstart: gibt None
    zurueck, wenn kein Netzwerk verfuegbar ist, kein Release existiert,
    die API einen Fehler liefert, ODER die installierte Version bereits
    aktuell/neuer ist - all das wird beim Hintergrund-Check bewusst gleich
    behandelt (kein Dialog, kein Fehler-Popup, siehe main_window.py). Fuer
    den manuellen "Nach Updates suchen"-Button wird stattdessen
    check_for_update_verbose() verwendet, das Netzwerk-/API-Fehler NICHT
    verschluckt."""
    try:
        info = check_for_update_verbose(timeout=timeout)
    except UpdateCheckError:
        return None
    return info


def check_for_update_verbose(timeout: float = 10.0) -> Optional[UpdateInfo]:
    """Wie check_for_update(), meldet Netzwerk-/API-Fehler jedoch als
    UpdateCheckError statt sie zu verschlucken. Gibt None zurueck (kein
    Fehler), wenn die Abfrage erfolgreich war, aber bereits die neueste
    Version installiert ist."""
    try:
        resp = requests.get(
            _RELEASES_API_URL,
            timeout=timeout,
            headers={"Accept": "application/vnd.github+json"},
        )
    except requests.RequestException as exc:
        raise UpdateCheckError(f"Update-Prüfung fehlgeschlagen: {exc}") from exc

    if resp.status_code == 404:
        # Noch kein Release veroeffentlicht - kein Fehler, einfach kein Update.
        return None
    if resp.status_code != 200:
        raise UpdateCheckError(f"Update-Prüfung fehlgeschlagen: HTTP {resp.status_code}")

    try:
        data = resp.json()
        tag = data["tag_name"]
        body = data.get("body") or "(keine Änderungshinweise angegeben)"
        assets = data.get("assets", [])
    except (ValueError, KeyError) as exc:
        raise UpdateCheckError(f"Unerwartete Antwort der GitHub-API: {exc}") from exc

    asset = next((a for a in assets if a.get("name") == _ASSET_NAME), None)
    if asset is None:
        asset = next((a for a in assets if str(a.get("name", "")).lower().endswith(".exe")), None)
    if asset is None:
        raise UpdateCheckError(f"Release '{tag}' enthält keine .exe-Datei zum Herunterladen.")

    info = UpdateInfo(
        current_version=__version__,
        latest_version=tag,
        changelog=body,
        download_url=asset["browser_download_url"],
        asset_size_bytes=asset.get("size"),
    )
    if not info.is_newer:
        return None
    return info
