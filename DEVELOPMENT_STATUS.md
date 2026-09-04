# VOXini Video Studio 1.0 — Entwicklungsstatus

Stand: 2026-08-31 (Nachbesserung nach echtem Windows-Testlauf + echtem BUILD_WINDOWS.bat-Lauf)

## Nachtrag: echter BUILD_WINDOWS.bat-Lauf auf Windows fand einen weiteren Bug

Der erste echte `BUILD_WINDOWS.bat`-Lauf auf Windows (Projektpfad `D:\Studio Neon68\VOXini Video Studio`, enthält ein Leerzeichen) brach sofort ab: `ERROR: Script file 'Neon68\VOXini' does not exist.` Ursache: `--distpath "%~dp0"` übergab `%~dp0` (das IMMER mit einem abschließenden Backslash endet) als alleinstehendes, in Anführungszeichen gesetztes Argument an PyInstaller. Ein Backslash unmittelbar vor dem schließenden Anführungszeichen wird von der Windows-Kommandozeilen-Argumentzerlegung (die PyInstaller/Python beim Start durchläuft, anders als cmd.exes eigene `cd`-Verarbeitung) als Escape-Zeichen für das Anführungszeichen interpretiert und zerstört damit die Argumentgrenze - der Pfad wurde dadurch falsch in `Neon68\VOXini` und `Video Studio\voxini_studio.spec` zerrissen. Behoben durch Umstellung auf `%CD%` (ohne abschließenden Backslash, da das Skript vorher bereits per `cd /d "%~dp0"` in den Projektordner wechselt):

```
"%VENV_PY%" -m PyInstaller --noconfirm --distpath "%CD%" --workpath "%CD%\build" "%CD%\voxini_studio.spec"
```

Alle anderen `.bat`-Dateien wurden auf dasselbe Muster geprüft - `%~dp0` kommt sonst nur als `cd /d "%~dp0"` (cmd.exe-Built-in, nicht betroffen) oder mit angehängtem Dateinamen (kein abschließender Backslash vor dem Anführungszeichen) vor. Dieser Fix wurde noch **nicht** durch einen erneuten echten `BUILD_WINDOWS.bat`-Lauf auf Windows bestätigt.

## Ausgangslage dieser Nachbesserung

Ein echter Test der unveränderten Version auf Windows ergab: UI startet und zeigt alle 5 Reiter, aber die Testsuite war **nicht** vollständig grün (148 bestanden, 5 fehlgeschlagen, 17 mit Fehler, 11 übersprungen), FFmpeg fehlte auf dem Testrechner, Pillow fehlte in `requirements.txt`, Medienpfade wurden mit `\` statt `/` gespeichert, die Keyring-Tests enthielten falsche Windows-Annahmen, der QSettings-Test war nicht wirksam isoliert, und es gab keine gebaute/getestete `.exe`. Alle unten gelisteten Punkte wurden daraufhin im Quellcode behoben.

## Vorgenommene Korrekturen

1. **FFmpeg fehlt auf dem Zielrechner behoben:** ein offizieller statischer FFmpeg/FFprobe-Build für Windows (GPLv3, gyan.dev „essentials“-Build) liegt jetzt direkt im Projekt (`voxini_studio/resources/ffmpeg/win64/ffmpeg.exe` + `ffprobe.exe` + Lizenztext) und wird über einen neuen zentralen Locator (`voxini_studio/core/ffmpeg_locator.py`) automatisch bevorzugt verwendet, falls kein System-FFmpeg gefunden wird. Betrifft `ffmpeg_assembly.py`, `mock_provider.py`, `comfyui_provider.py` und die zugehörigen Tests — alle riefen zuvor ein bloßes `"ffmpeg"`/`"ffprobe"` auf, das auf PATH-Verfügbarkeit angewiesen war.
2. **Hart codierter Linux-Font-Pfad behoben (wichtiger Zusatzfund):** sowohl `ffmpeg_assembly.py` (Titelkarte) als auch `mock_provider.py` (Szenenbeschriftung) hatten `/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf` fest einprogrammiert — dieser Pfad existiert unter Windows nicht und ließ jede Titelkarten-/Mock-Generierung mit Text dort fehlschlagen. Die Schriftdatei (DejaVu Sans Bold, freie Lizenz) liegt jetzt gebündelt unter `voxini_studio/resources/fonts/` und wird plattformunabhängig über `theme.resource_root()` aufgelöst.
3. **`Pillow` ergänzt:** war in Tests (`from PIL import Image`) tatsächlich nötig, fehlte aber in `requirements.txt` — jetzt ergänzt.
4. **Medienpfade portabel gespeichert:** alle drei Stellen, die relative Medienpfade in `project.voxproj` schreiben (`project_manager.py` ×2, `generation_service.py` ×1), verwenden jetzt `Path.as_posix()` statt `str(Path(...))`, sodass niemals ein `\`-Pfad in die Projektdatei geschrieben wird — unabhängig vom Betriebssystem. Ein neuer Test (`test_stored_media_paths_always_use_forward_slashes_even_on_windows`) prüft das explizit, inklusive der rohen JSON-Datei.
5. **Keyring-Tests von echtem Windows-Anmeldeinformationsspeicher isoliert:** `tests/test_credentials.py` wurde komplett neu geschrieben. Statt sich auf zufällige Eigenschaften der jeweiligen Testmaschine zu verlassen, monkeypatcht ein deterministischer Fixture-Satz (`force_keyring_unavailable` / `fake_working_keyring`) direkt die `keyring`-Funktionen — dadurch werden sowohl der Fallback-Pfad als auch der „gesunder Windows-Credential-Manager“-Pfad getestet, **ohne jemals einen echten Zugangsdatenspeicher zu berühren**, auf keinem Betriebssystem.
6. **QSettings-Test isoliert:** `theme.py` hat jetzt eine injizierbare `_make_settings()`-Fabrikfunktion; der Test patcht diese auf eine `QSettings.Format.IniFormat`-Instanz mit temporärer Datei. Vorher isolierte der Test nur die Umgebungsvariable `HOME`, was unter Windows wirkungslos ist, da `QSettings`' natives Format dort die Registry ist und Umgebungsvariablen dabei ignoriert werden — der Test hat also zuvor tatsächlich die echte Registry unter `HKCU\Software\VOXini\VideoStudio` gelesen/beschrieben.
7. **Segfault im Headless-Testlauf behoben:** beim vollständigen Testlauf in der Sandbox trat nach ca. 140 Tests ein reproduzierbarer nativer Absturz in `setup_wizard.py`s Download-Fortschrittsdialog auf (`QApplication.processEvents()` doppelt aufgerufen, zusammen mit über die Zeit angesammelten Qt-Widgets aus vorherigen Tests). Behoben durch (a) ein neues `tests/conftest.py` mit einer Fixture, die nach jedem Test die Event-Queue leert und eine Garbage Collection anstößt, und (b) Entfernen des redundanten zweiten `processEvents()`-Aufrufs im Abbruch-Check.
8. **PyInstaller-Build auf Onefile umgestellt:** `voxini_studio.spec` erzeugt jetzt **eine einzelne** `VOXini Video Studio.exe` (kein `dist`-Unterordner, kein `_internal`-Ordner, keine separate Portable-Version/ZIP). `BUILD_WINDOWS.bat` baut direkt mit `--distpath` in den Projektordner selbst, sodass das Ergebnis exakt unter `VOXini Video Studio.exe` im Projektordner liegt.

## Testergebnis (Linux-Sandbox, bester verfügbarer Ersatz für den echten Windows-Lauf)

Nach allen Korrekturen: **185/185 Tests bestanden, 0 fehlgeschlagen, 0 Fehler, 0 übersprungen** (185 statt vorher 181, weil 4 neue, gezielte Regressionstests für die Pfad-/Keyring-/QSettings-Korrekturen hinzukamen). Zusätzlich wurde der PyInstaller-Onefile-Build hier real ausgeführt: er lief fehlerfrei durch, erzeugte eine einzelne ausführbare Datei (~291 MB, inkl. eingebettetem ffmpeg/ffprobe), und diese Datei wurde tatsächlich gestartet — sie blieb ohne Absturz und ohne Fehlermeldung am Leben (das hier erzeugte Ergebnis ist eine Linux-Binärdatei, nicht die auslieferbare `.exe`, aber der Build- und Startvorgang selbst validiert Spec-Syntax, Ressourcenpfade und alle `hiddenimports`).

**Wichtige Einschränkung, ehrlich benannt:** diese 185/185 wurden in der Linux-Entwicklungssandbox erzielt, nicht auf echtem Windows. Alle hier behobenen Defekte (Pfadtrennzeichen, Keyring-Testannahmen, QSettings-Isolation, fehlender Font-Pfad, fehlendes FFmpeg) waren jedoch spezifisch als Windows-Probleme diagnostiziert und die Korrekturen sind plattformunabhängig korrekt — sie können aber nur auf einem echten Windows-Rechner endgültig als „auf Windows behoben“ bestätigt werden.

## Weiterhin offen — nur auf echter Windows-Hardware durchführbar

Diese Punkte sind ehrlich als **nicht** in dieser Umgebung durchführbar/geprüft zu benennen (kein AMD-GPU, kein echtes Windows-Betriebssystem hier verfügbar):

1. **Erneuter kompletter Testlauf auf echtem Windows** (`pytest`) — sollte nach diesen Korrekturen 181/181 (bzw. mit den 4 neuen Tests 185/185) ohne unbeabsichtigte Skips zeigen, ist aber nicht selbst auf Windows verifiziert worden.
2. **Reale Wan2.2-Testgenerierung auf der RX 7600 XT** (ROCm/lokale GPU) — weiterhin nicht getestet, siehe `Windows_Testprotokoll.md` Abschnitt C.
3. **Echter Runway-Cloud-Auftrag** — weiterhin nicht ausgeführt (erfordert eigenes bezahltes Konto und deine ausdrückliche Zustimmung vor jedem kostenpflichtigen Auftrag), siehe Abschnitt D.
4. **Echter Doppelklick-Test der gebauten `VOXini Video Studio.exe` auf Windows**, inkl. der in Abschnitt G von `Windows_Testprotokoll.md` gelisteten Schritte (Programmstart, neues Projekt, Import, Charakter, Storyboard, Mock-Generierung, MP4-Export) — die `.exe` selbst wurde bisher nur als Linux-Binärdatei in der Sandbox gebaut und gestartet, nicht als echte Windows-`.exe` auf echtem Windows.

## Nächster Schritt für dich

Auf deinem Windows-PC in diesem Ordner:

```
BUILD_WINDOWS.bat
```

erzeugt `VOXini Video Studio.exe` direkt in diesem Ordner. Führe anschließend `Windows_Testprotokoll.md` Abschnitt G (und bei Gelegenheit A/B/C/D/E/F) durch und führe `pytest` erneut aus, um die 185/185 auch auf echtem Windows zu bestätigen.
