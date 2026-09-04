# VOXini Video Studio 1.0 — Windows-Testprotokoll

Dieses Protokoll ist für den finalen Test auf einem echten Windows-PC gedacht. Es kann nicht aus der Entwicklungsumgebung heraus (Linux-Sandbox, kein AMD-GPU-Zugriff) vollständig durchgeführt werden — die folgenden Schritte sind daher der letzte offene Teil vor Fertigstellung 1.0 und müssen einmal manuell auf deinem Windows-Rechner abgehakt werden.

Trage bei jedem Schritt **OK** oder **Fehler + Beschreibung** ein.

## A. Grundinstallation

| # | Schritt | Ergebnis |
|---|---|---|
| A1 | `START_DEV.bat` doppelklicken (frischer Ordner, noch keine `.venv`) | eigene Python-Umgebung wird angelegt, Pakete installieren sich, App startet |
| A2 | App-Fenster erscheint, Farbthema „VOXini“ (Pink/Cyan/Violett) ist aktiv | ☐ |
| A3 | `START_DEV.bat` ein zweites Mal starten | startet deutlich schneller (Umgebung wird wiederverwendet) |
| A4 | In `.venv\Scripts\activate` aktivieren, dann `pytest` im Projektordner ausführen | Ziel: 185 bestanden, 0 fehlgeschlagen, 0 Fehler, 0 unerwartet übersprungen (siehe `DEVELOPMENT_STATUS.md` für die Korrekturen, die genau darauf abzielen) |

## B. Projekt- und UI-Grundfunktionen

| # | Schritt | Ergebnis |
|---|---|---|
| B1 | Reiter „Projekt“ → „Neues Projekt...“ | Projektordner + Name lassen sich wählen |
| B2 | Song (WAV/MP3), SRT-Datei, Prompt-Text importieren | alle drei erscheinen als importiert |
| B3 | Mindestens einen Charakter mit Referenzbild anlegen | Charakter erscheint in der Liste |
| B4 | „Storyboard aus Audio + SRT + Prompt generieren“ | Szenenliste erscheint, zeitlich sinnvoll |
| B5 | Storyboard-Reiter: eine Szene löschen | Szene verschwindet, Rest bleibt konsistent |
| B6 | Fenster schließen und Programm neu starten, Projekt erneut öffnen | alle Daten sind vorhanden |

## C. Lokale KI (ComfyUI/Wan2.2, kostenlos)

| # | Schritt | Ergebnis |
|---|---|---|
| C1 | `INSTALL_LOCAL_AI.bat` ausführen | Python-3.12-Umgebung, ComfyUI, PyTorch/ROCm werden installiert |
| C2 | Ergebnis von C1 bei RX 7600 XT dokumentieren | GPU wird erkannt: ☐ ja ☐ nein (falls nein: `torch-directml`-Alternative aus der Bedienungsanleitung, Abschnitt 8, testen und Ergebnis notieren) |
| C3 | `comfyui_env\START_COMFYUI.bat` starten | ComfyUI-Serverfenster bleibt offen, keine Fehlermeldung |
| C4 | In der App: Einrichtungsassistent → „Lokal“ → „Jetzt prüfen“ | ComfyUI-Verbindung wird als „verbunden“ angezeigt |
| C5 | Mindestens die Basismodell-Dateien über den Assistenten herunterladen | Größen-/Zielordner-Bestätigung erscheint vor jedem Download, Fortschritt sichtbar, Datei danach als „vorhanden“ markiert |
| C6 | Eine einzelne, kurze Szene lokal generieren lassen (Anbieter „Lokal“) | Clip wird erzeugt und im Storyboard als fertig markiert |
| C7 | Erzeugten Clip anschauen | Bild entspricht ungefähr Prompt/Charakter, keine Abstürze |
| C8 | Im Einrichtungsassistenten „ComfyUI automatisch neu starten alle:“ auf z.B. 2 setzen, Projekt mit mindestens 5 lokalen Szenen generieren lassen | Nach jeweils 2 abgeschlossenen lokalen Szenen erscheint die Statusmeldung „ComfyUI wird zur Speicherbereinigung neu gestartet ...“, das ComfyUI-Konsolenfenster schließt sich und ein neues öffnet sich, danach läuft die Generierung automatisch weiter (keine manuelle Aktion nötig) |
| C9 | Echter Overnight-Batch: „ComfyUI automatisch neu starten alle:“ aktiv lassen, einen langen Lauf (≥15-20 Szenen, unbeaufsichtigt über Nacht) starten | Alle Neustarts laufen ohne Nutzereingriff durch, VRAM-Auslastung bleibt über die gesamte Laufzeit stabil (Task-Manager stichprobenartig prüfen), am Ende sind alle Szenen fertig oder sauber als fehlgeschlagen markiert - kein hängender/stiller Absturz. **Dieser Schritt schließt Task #590 endgültig ab und ist bislang noch nicht durchgeführt worden.** |
| C10 | Einrichtungsassistent → „Jetzt prüfen“ VOR der ComfyUI-Installation, auf einer GPU mit tatsächlich >4 GB VRAM ausführen | Falls der gemeldete VRAM-Wert unter 12 GB liegt, erscheint der abgeschwächte Hinweistext („Windows-Systemabfrage vor der Installation - dieser Wert ist bekanntermaßen ungenau...“) statt einer unqualifizierten Warnung; nach abgeschlossener Installation liefert „Jetzt prüfen“ den echten (torch-basierten) VRAM-Wert |
| C11 | Lokale Szene ohne installiertes/erreichbares ffmpeg generieren (z.B. `PATH` temporär ohne ffmpeg-Ordner) | Clip wird trotzdem erzeugt (in nativer Wan2.2-Auflösung statt 1080p), in der Generierungs-Tabelle erscheint ein Warn-Symbol mit Tooltip „1080p-Upscaling übersprungen: ffmpeg wurde nicht gefunden...“ |
| C12 | Qualitäts-Einstellungen im Einrichtungsassistenten testen: Auflösung auf 480p stellen, 1080p-Hochskalieren ausschalten, eigenen Negativ-Prompt eintragen, Szene generieren | Generierte Szene läuft mit 480p-Auflösung, ohne Hochskalierung, sichtbar andere Bildwirkung durch den eigenen Negativ-Prompt (grober Stichprobentest reicht) |
| C13 | `comfyui_install_dir` im Projekt absichtlich auf einen Ordner ohne funktionierende `START_COMFYUI.bat` (z.B. leerer Test-Ordner mit falschem Pfad-Parent) setzen, Auto-Neustart auslösen | Statusmeldung „ComfyUI-Neustart fehlgeschlagen: START_COMFYUI.bat wurde sofort beendet (Exit-Code ...)" erscheint zügig (nicht erst nach 180s) |
| C14 | `INSTALL_LOCAL_AI.bat` auf einem PC mit **NVIDIA**-Grafikkarte ausführen | Schritt [4/7] erkennt die Karte korrekt als „NVIDIA-GPU erkannt“, Schritt [5/7] installiert CUDA-PyTorch-Wheels (kein ROCm-Download-Versuch), Skript läuft ohne Fehler durch. **Dieser Schritt wurde bislang nur als Code/Logik geprüft, nie auf echter NVIDIA-Hardware ausgeführt.** |
| C15 | Nach C14: In der App „Jetzt prüfen“ ausführen | GPU wird korrekt erkannt (Name + VRAM), Warnungen (falls VRAM knapp) nennen „CUDA“, nicht „ROCm“ |
| C16 | `INSTALL_LOCAL_AI.bat` auf einem PC ohne AMD/NVIDIA-Grafikkarte (z.B. nur integrierte Intel-Grafik) ausführen | Schritt [4/7] meldet „Keine unterstützte GPU (AMD/NVIDIA) eindeutig erkannt“, Skript fragt vor der CPU-only-Installation nach Bestätigung, bricht bei „N“/Enter sauber ab |
| C17 | Regressionstest: `INSTALL_LOCAL_AI.bat` erneut auf der bestehenden **AMD**-Testmaschine (RX 7600 XT) ausführen | Verhält sich identisch zu vorher: Schritt [4/7] erkennt „AMD-GPU“, Schritt [5/7] installiert exakt dieselben ROCm-Wheel-URLs wie vor dieser Änderung - keine Regression für bestehende AMD-Nutzer |

## D. Runway-Cloud (kostenpflichtig, nur mit eigenem Konto/Guthaben)

| # | Schritt | Ergebnis |
|---|---|---|
| D1 | Einrichtungsassistent → „Runway“ → API-Schlüssel eingeben, „Schlüssel speichern“ | Bestätigung „sicher gespeichert“ |
| D2 | „Verbindung testen (kostenlos)“ | Guthaben/Kontostatus wird angezeigt, **keine Kosten** entstehen |
| D3 | Budgetlimit auf einen niedrigen Betrag setzen | Wert wird übernommen |
| D4 | Eine Szene testweise auf Anbieter „Runway“ umstellen und generieren | Kostenbestätigungsdialog mit korrektem Betrag erscheint **vor** jedem Auftrag |
| D5 | Bestätigungsdialog mit „Abbrechen“ verlassen | **kein** Auftrag wird an Runway geschickt |
| D6 | Budgetlimit absichtlich sehr niedrig setzen und Generierung erneut versuchen | Auftrag wird blockiert, verständliche deutsche Fehlermeldung, **kein** API-Aufruf |

*(Schritte D4–D6 nur mit echtem, bezahltem Runway-Guthaben sinnvoll durchführbar — bei Bedarf mit einem sehr kurzen, günstigen Clip testen.)*

## E. Export

| # | Schritt | Ergebnis |
|---|---|---|
| E1 | Export-Reiter, 16:9, weiche Untertitel, „Exportieren...“ | MP4 wird erzeugt, Original-Song als Tonspur, Untertitel im Player umschaltbar |
| E2 | Export in 9:16 | korrektes Hochformat |
| E3 | Export mit eingebrannten Untertiteln | Text ist fest im Bild sichtbar |

## F. Autosave, Wiederherstellung, Backups, Fehlerprotokoll

| # | Schritt | Ergebnis |
|---|---|---|
| F1 | Programm während laufender Arbeit über den Task-Manager hart beenden (nicht regulär schließen) | — |
| F2 | Projekt erneut öffnen | Abfrage „automatische Sicherung übernehmen?“ erscheint |
| F3 | „Wartung“ → „Backups“ | mehrere Zeitpunkte gelistet, „Wiederherstellen“ funktioniert |
| F4 | „Wartung“ → „Fehlerprotokoll“ | zeigt Einträge, „Protokollordner öffnen“ öffnet den Explorer-Ordner |

## G. Windows-Build (.exe)

| # | Schritt | Ergebnis |
|---|---|---|
| G1 | `BUILD_WINDOWS.bat` ausführen | Build läuft ohne Fehler durch, Ergebnis ist EINE Datei `VOXini Video Studio.exe` direkt in diesem Ordner (kein `dist`-Unterordner, keine Portable-ZIP) |
| G2 | `VOXini Video Studio.exe` doppelklicken (ohne installierte Entwicklungsumgebung, z. B. auf einem zweiten Rechner oder nach Löschen von `.venv`) | App startet eigenständig, echtes Hauptfenster „VOXini Video Studio“ mit allen 5 Reitern erscheint, **kein** „Unhandled exception in script“-Dialog, Icons/Farbthema korrekt sichtbar |
| G3 | In der gebauten `.exe`: neues Projekt anlegen, Audio+SRT+Prompt importieren, Charakter mit Referenzbild anlegen, Storyboard erzeugen, Mock-Clip generieren, MP4 exportieren | alles funktioniert identisch zur Entwicklungsversion — insbesondere **ohne** separat installiertes FFmpeg, da ffmpeg/ffprobe in der `.exe` eingebettet sind |

---

**Hinweis:** Alle Schritte bis einschließlich der automatisierten Testsuite (`pytest`, 185 Tests, siehe `DEVELOPMENT_STATUS.md` für die Korrekturen nach dem ersten echten Windows-Testlauf) und ein vollständiger Mock-Provider-Durchlauf mit dem echten Song „WITHOUT EVER HAVING YOU“ wurden in der Entwicklungsumgebung ausgeführt und sind dort grün. Ein erneuter `pytest`-Lauf direkt auf Windows nach diesen Korrekturen steht noch aus (Abschnitt A). Die Abschnitte C, D und G dieses Protokolls erfordern echte Windows-Hardware (AMD-GPU bzw. ein fertiges Windows-System) bzw. ein echtes, bezahltes Runway-Konto und konnten daher nicht in der Linux-Entwicklungsumgebung durchgeführt werden.
