# VOXini Video Studio 1.0

Hybride Windows-Anwendung zum Bau eines zeitlich synchronisierten, charakterkonsistenten Musikvideos aus Song, SRT und einem vollständigen Regie-Prompt. Generiert Clips lokal und kostenlos über ComfyUI/AMD-ROCm/Wan2.2 TI2V 5B oder optional über die kostenpflichtige Runway-Cloud-API — Anbieterwahl pro Projekt und pro Szene — und setzt sie mit FFmpeg zu einem fertigen MP4 zusammen.

Für die vollständige Bedienungsanleitung siehe **`Bedienungsanleitung.md`**. Für den finalen Windows-Testdurchlauf siehe **`Windows_Testprotokoll.md`**. Für den aktuellen Entwicklungsstand siehe **`DEVELOPMENT_STATUS.md`**.

## Schnellstart

```
START_DEV.bat
```

Für die kostenlose lokale KI-Generierung (separate Umgebung, siehe Bedienungsanleitung Abschnitt 2.3):

```
INSTALL_LOCAL_AI.bat
```

Für eine einzelne, eigenständige `.exe` (inkl. eingebettetem ffmpeg/ffprobe, direkt in diesem Ordner, keine Portable-ZIP):

```
BUILD_WINDOWS.bat
```

## Anbieter

| Anbieter | Kosten | Status |
|---|---|---|
| Lokal (ComfyUI/Wan2.2 TI2V 5B) | kostenlos | vollständig implementiert |
| Runway Cloud (gen4.5/gen4_turbo/veo3/veo3.1/seedance2) | kostenpflichtig, mit Kosten-Bestätigung + Budgetgate | vollständig implementiert |
| Mock (Testmodus) | kostenlos | prozedurale Platzhalterclips, kein Stockmaterial |

VOXini wechselt niemals automatisch zu einem kostenpflichtigen Anbieter; jeder Runway-Auftrag erfordert eine ausdrückliche Bestätigung mit Kostenanzeige.

## Projektstruktur

```
voxini_studio/
  models/          Datenmodelle (Project, Character, Scene, ClipVersion) - reines Python, kein Qt
  core/            Geschäftslogik: Projektverwaltung, Audioanalyse, SRT-/Prompt-Parser, Szenenplanung,
                    Generierungs-/Budget-Service, Autosave/Backups, Fehlerprotokoll, Modell-Downloader,
                    FFmpeg-Zusammenschnitt
  providers/       Provider-Schnittstelle + Mock-, ComfyUI- und Runway-Provider
  ui/              PySide6-Oberfläche inkl. zentralem VOXini/Neon68-Farbthema (theme.py) und SVG-Icons
tests/             Headless-Tests (pytest, 185 Tests, u. a. gegen lokale Fake-HTTP-Server für
                    ComfyUI/Runway - keine echten Netzwerkaufrufe, keine Kosten)
```

## Projektdateiformat

Jedes VOXini-Projekt ist ein eigener Ordner mit `project.voxproj` (JSON) plus einem eigenen `media/`-Unterordner, in den Audio, SRT, Prompt-Text und Charakter-Referenzbilder beim Import hineinkopiert werden. Ein Projektordner ist damit vollständig eigenständig und lässt sich verschieben/teilen.

## Tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```
