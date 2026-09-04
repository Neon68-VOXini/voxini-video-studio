# VOXini Video Studio 1.0 — Bedienungsanleitung

Diese Anleitung beschreibt die Installation und Nutzung von VOXini Video Studio: einer Windows-Anwendung, die aus einem Song, einem Untertitel-SRT und einem Regie-Prompt automatisch ein zeitlich synchronisiertes, charakterkonsistentes Musikvideo erzeugt — lokal und kostenlos über ComfyUI/Wan2.2, optional zusätzlich über die kostenpflichtige Runway-Cloud.

---

## 1. Überblick: Zwei Wege zur Videogenerierung

VOXini Video Studio erzeugt für jede Szene einen Videoclip über einen von drei **Anbietern**, die pro Projekt und sogar **pro einzelner Szene** frei gewählt werden können:

| Anbieter | Kosten | Voraussetzung | Empfehlung |
|---|---|---|---|
| **Lokal (ComfyUI/Wan2.2)** | Kostenlos | Eigene AMD-Grafikkarte, einmalige lokale Einrichtung | Standard, für die meisten Szenen |
| **Runway (Cloud)** | Kostenpflichtig, nach Sekunden je Modell | Runway-Konto + API-Schlüssel + Guthaben | Optional, für einzelne Szenen mit höheren Ansprüchen |
| **Mock (Testmodus)** | Kostenlos | Keine | Nur zum Testen der App ohne echte Generierung (farbige Platzhalterclips) |

**Wichtig:** VOXini wechselt **niemals automatisch** zu einem kostenpflichtigen Anbieter. Runway wird nur verwendet, wenn du es explizit für ein Projekt oder eine Szene auswählst — und selbst dann fragt die App vor **jedem** kostenpflichtigen Auftrag noch einmal nach Bestätigung mit genauer Kostenanzeige.

---

## 2. Installation

### 2.1 Die App selbst starten (ohne lokale KI)

Zum reinen Ausprobieren der Oberfläche (Projekte anlegen, Storyboard, Mock-Generierung, Export) reicht:

```
START_DEV.bat
```

Das Skript legt beim ersten Start automatisch eine eigene, von allem anderen getrennte Python-Umgebung im Unterordner `.venv` an, installiert die benötigten Pakete und startet die Anwendung. Bei jedem weiteren Start wird die vorhandene Umgebung wiederverwendet — der Start dauert dann nur wenige Sekunden.

### 2.2 Fertige, eigenständige Windows-`.exe` bauen (optional)

Wenn du eine eigenständige `.exe` möchtest (z. B. um sie ohne Python-Installation weiterzugeben):

```
BUILD_WINDOWS.bat
```

Das Ergebnis ist eine **einzelne Datei direkt in diesem Ordner**:

```
VOXini Video Studio.exe
```

Es gibt bewusst keinen separaten `dist`-Ordner, keine „Portable“-Variante und keine ZIP-Datei daneben — diese eine `.exe` ist bereits die vollständige Anwendung (Python, Qt, Icons, Workflow-Vorlagen und ein eingebettetes ffmpeg/ffprobe sind direkt enthalten, siehe 2.2.1). Du kannst sie einfach kopieren/verschieben/weitergeben.

**Nicht** in dieser `.exe` enthalten sind ComfyUI, ROCm und die Wan2.2-Modelldateien — diese bleiben absichtlich getrennt (siehe 2.3), damit die App klein bleibt und die großen KI-Dateien nicht mehrfach kopiert werden müssen.

#### 2.2.1 FFmpeg ist bereits eingebaut

Anders als in einer früheren Version dieser Anleitung beschrieben, musst du FFmpeg **nicht** separat installieren: ein offizieller, statischer FFmpeg/FFprobe-Build für Windows (GPLv3, „essentials“-Build von gyan.dev) ist direkt im Projekt und in der gebauten `.exe` enthalten und wird automatisch bevorzugt verwendet, falls kein eigenes FFmpeg im System-PATH gefunden wird. Export und die kostenlose Mock-Generierung funktionieren daher auch auf einem komplett frischen Windows-Rechner ohne jede zusätzliche Installation.

### 2.3 Lokale KI-Umgebung einrichten (für kostenlose lokale Generierung)

Wenn du die kostenlose lokale Generierung über deine eigene AMD- oder NVIDIA-Grafikkarte nutzen möchtest, führe einmalig aus:

```
INSTALL_LOCAL_AI.bat
```

Dieses Skript richtet **komplett getrennt** von der App selbst ein:

1. Eine eigene Python-3.12-Umgebung im Unterordner `comfyui_env\venv312` (die ROCm- wie auch die CUDA-PyTorch-Pakete benötigen exakt Python 3.12; die App selbst läuft mit einer anderen Python-Version — beide Umgebungen berühren sich nie und sprechen später nur über eine lokale Netzwerkverbindung (`http://127.0.0.1:8188`) miteinander).
2. ComfyUI (`comfyui_env\ComfyUI`), heruntergeladen von der offiziellen ComfyUI-GitHub-Seite.
3. PyTorch mit GPU-Unterstützung — das Skript **erkennt deine Grafikkarte automatisch** (über eine Windows-Systemabfrage) und installiert passend dazu entweder AMD-ROCm-Wheels (offiziell von `repo.radeon.com`, Stand ROCm 7.2.1 / PyTorch 2.9.1) oder NVIDIA-CUDA-Wheels (offiziell von `download.pytorch.org`, CUDA 12.4). Wird keine der beiden Grafikkarten eindeutig erkannt, fragt das Skript nach, bevor es ersatzweise eine CPU-only-Installation vornimmt (ohne GPU-Beschleunigung — für Videogenerierung in der Praxis zu langsam, aber immerhin lauffähig zum Testen).
4. Ein Startskript `comfyui_env\START_COMFYUI.bat`.

**Wichtiger, ehrlicher Hinweis zur GPU-Kompatibilität (AMD):** AMDs offizielle Liste unterstützter Grafikkarten für ROCm 7.2.1 unter Windows umfasst aktuell die Architekturen **gfx1100, gfx1101, gfx1200, gfx1201** (u. a. RX 7900 XTX, RX 7700, RX 9070/9070 XT, RX 9060 XT). Die **RX 7600 XT (gfx1102)** steht **nicht** auf dieser offiziellen Liste. Die Installation kann trotzdem versucht werden — PyTorch erkennt die GPU in diesem Fall möglicherweise nicht. Prüfe im Zweifel die aktuelle Liste unter:

`https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/compatibility/compatibilityrad/windows/windows_compatibility.html`

Falls ROCm auf deiner Karte nicht funktioniert, ist `torch-directml` (Microsofts DirectML-Backend für PyTorch, läuft mit praktisch jeder aktuellen Windows-GPU, allerdings meist langsamer als natives ROCm) eine mögliche Alternative — siehe Abschnitt 8 „Fehlerbehebung“.

**Für NVIDIA-Karten:** Es genügt ein aktueller, normal installierter NVIDIA-Treiber (Game-Ready oder Studio) — ein separates „CUDA Toolkit“ ist **nicht** nötig, die installierten PyTorch-Wheels bringen die benötigten CUDA-Laufzeitbibliotheken bereits mit.

**Ehrlicher Hinweis:** Die automatische Hersteller-Erkennung und der CUDA-Installationspfad wurden vom Entwickler in dieser Umgebung nur so weit geprüft, wie ohne echte Windows-Hardware möglich ist (Skriptlogik/Verzweigungen) — ein echter Durchlauf auf einer realen NVIDIA-GPU steht noch aus. Der bestehende AMD-ROCm-Pfad ist unverändert und weiterhin der auf echter Hardware getestete Standardweg.

Die eigentlichen **Wan2.2-Modelldateien (ca. 17 GB)** werden von `INSTALL_LOCAL_AI.bat` bewusst **nicht** mit heruntergeladen. Das geschieht stattdessen im **Einrichtungsassistenten** direkt in der App (siehe 2.4), wo du Gesamtgröße, benötigten Speicherplatz und Zielordner vorher in einer einzigen Übersicht siehst und einmal bestätigst.

### 2.4 Einrichtungsassistent (in der App)

Nach dem ersten Start von VOXini Video Studio öffne über den Knopf **„Einrichtungsassistent“** oben in der Kopfzeile den Assistenten. Er hat zwei Reiter:

- **„Lokal (ComfyUI) - kostenlos“**: ComfyUI-Adresse (Host/Port, Standard `127.0.0.1:8188`), Installations- und Modellordner wählen, „Jetzt prüfen“ zeigt eine Übersicht über GPU/ROCm/ComfyUI-Verbindung/vorhandene Modelldateien. Darunter der Knopf **„Fehlende Modelle herunterladen...“**: zeigt vor dem Start EINE zusammengefasste Bestätigung mit Gesamtgröße, benötigtem und verfügbarem Speicherplatz sowie Zielordner für alle noch fehlenden Wan2.2-Dateien (keine Bestätigung mehr pro Einzeldatei). Ist zu wenig Speicherplatz frei, bleibt der „Download starten“-Knopf im Bestätigungsdialog gesperrt. Nach der Bestätigung läuft der Download aller fehlenden Dateien automatisch nacheinander durch, mit gemeinsamer, abbrechbarer Fortschrittsanzeige. Dazu weitere Einstellungen:
  - **„ComfyUI automatisch neu starten alle:“** (Zahlenfeld, 0-100, „Aus“ bei 0) — startet ComfyUI nach der eingestellten Anzahl generierter Szenen automatisch neu, um bei längeren Läufen einer VRAM-Fragmentierung unter ROCm entgegenzuwirken. **Hinweis:** Dieses Feature ist funktional vorhanden, aber vom Entwickler noch als experimentell/nicht abschließend bestätigt eingestuft — bei Overnight-Batches also mit etwas Vorsicht nutzen.
  - **„Qualität“**: Auflösung für die Wan2.2-Generierung (480p = schneller, 720p = Standard), ein Schalter für lokales 1080p-Hochskalieren mit ffmpeg nach der Generierung (an/aus), sowie ein optionales Feld für einen eigenen Negativ-Prompt (leer = eingebauter Standard-Negativ-Prompt wird verwendet). Alle drei sind pro Projekt gespeichert.
  - **„Identitäts-Szenen-Pipeline (experimentell)“**: Checkbox „Identitäts-Szenen-Pipeline verwenden“ + Feld „SDXL-Checkpoint-Dateiname:“. Aktiviert einen zweistufigen lokalen Generierungsweg (SDXL+InstantID komponiert zunächst das Szenenbild mit dem Charaktergesicht, danach animiert Wan2.2 wie gewohnt), was die Charaktertreue in Szenen mit auffälligem Referenzfoto-Hintergrund verbessern kann. Setzt voraus, dass du selbst einmalig ein SDXL-Checkpoint-Modell und den `ComfyUI_InstantID`-Custom-Node in deiner ComfyUI-Installation einrichtest — VOXini lädt das nicht automatisch herunter.
- **„Cloud (Runway) - kostenpflichtig“**: API-Schlüssel eingeben und **sicher im Windows-Anmeldeinformationsspeicher** speichern (niemals im Klartext in einer Projektdatei), „Verbindung testen (kostenlos)“ prüft nur den Kontostatus ohne einen kostenpflichtigen Auftrag auszulösen, Modellwahl und ein einstellbares **Budgetlimit** in Euro. Aktuell wählbare Modelle: `gen4.5`, `gen4_turbo` (Standard, benötigt zwingend ein Referenzbild), `veo3.1`, `veo3.1_fast`, `seedance2` (das ältere, nicht-versionierte „veo3“ wurde entfernt, da Runway es nicht mehr anbietet).

---

## 3. Ein Projekt erstellen

1. Reiter **„Projekt“** → **„Neues Projekt...“**. Es wird nur noch nach einem **Projektnamen** gefragt — kein Ordner-Picker mehr. Der Projektordner wird automatisch unter `Projekte\<Name>` direkt neben der App angelegt (existiert bereits ein nicht-leerer Ordner mit diesem Namen, warnt VOXini und du wählst einen anderen Namen). Das hält alle Projekte an einem sauberen, vorhersehbaren Ort, statt sie über beliebige, selbst gewählte Ordner zu verteilen.
2. Importiere die Quelldateien über die Knöpfe im Bereich „Quelldateien“: die Audiodatei (WAV/MP3), die Untertitel-Datei (SRT) und den vollständigen Regie-Prompt-Text. Neben jeder importierten Datei steht jetzt auch ein **„Entfernen“**-Knopf: Er nimmt nur die Zuordnung im Projekt zurück (Feld zeigt wieder „nicht gesetzt“) — die bereits kopierte Datei bleibt unangetastet im Projektordner erhalten, ein erneuter Import ist jederzeit möglich.

   **Hast du noch keine SRT-Datei?** Statt eine fertige SRT zu importieren, kannst du über **„Songtext eingeben & automatisch synchronisieren...“** (direkt unter der Untertitel-Zeile, benötigt zuvor importierte Audiodatei) den Songtext eintippen oder einfügen — VOXini trennt den Gesang automatisch vom Instrumental, erkennt den gesungenen Text per Spracherkennung und gleicht ihn zeitlich mit dem Songtext ab. Danach öffnet sich eine **Korrekturansicht** (Start-/Endzeit und Text je Zeile editierbar), bevor das Ergebnis als SRT-Datei ins Projekt übernommen wird. Beim allererstem Gebrauch richtet VOXini dafür einmalig eine eigene, isolierte Python-Umgebung ein (Download mehrerer hundert MB bis über 1 GB, je nach gewählten Modellen) — das dauert je nach Internetverbindung einige Minuten; bei jedem weiteren Gebrauch entfällt das.
3. Lege im Bereich „Charakterprofile“ deine Figuren an: Name, Beschreibung (Alter, Haare, Augen, Kleidung...) und ein oder mehrere Referenzbilder je Charakter — das sichert die visuelle Konsistenz über alle Szenen hinweg. **Hinweis:** Aktuell verwendet die Generierung pro Szene technisch nur das *erste* Referenzbild des *ersten* zugeordneten Charakters — hat eine Szene mehrere Charaktere mit Referenzbildern oder ein Charakter mehrere Referenzfotos, wird der Rest stillschweigend nicht verwendet. VOXini macht das im Reiter „Generierung“ sichtbar (siehe Abschnitt 5).
4. Knopf **„Storyboard aus Audio + SRT + Prompt generieren“** erzeugt automatisch die Szenenliste, zeitlich exakt synchronisiert zu Song und Untertiteln.

Ein Projektordner ist vollständig eigenständig (JSON-Projektdatei + eigener `media`-Unterordner mit Kopien aller importierten Dateien) und lässt sich problemlos verschieben oder sichern.

---

## 4. Storyboard und Szenenplanung

Im Reiter **„Storyboard“** siehst du alle Szenen mit Zeitbereich, zugehörigem Untertitel-Text und Prompt-Auszug. Über **„Szene löschen“** lässt sich eine einzelne Szene entfernen (Zeitbereiche der Nachbarszenen bleiben unangetastet). Die Vorschau-Zeitleiste am unteren Rand zeigt die Gesamtlänge und den Status jeder Szene farbig an.

---

## 5. Generierung

Im Reiter **„Generierung“** legst du fest, **welcher Anbieter pro Szene** verwendet wird:

- **Standard-Anbieter** (Werkzeugleiste, oben): gilt für alle Szenen, die keine eigene Ausnahme haben.
- **Anbieter-Spalte** in der Tabelle: pro Szene individuell überschreibbar (z. B. „diese eine Szene über Runway, alle anderen lokal“).
- Die Spalte **„Kostenlabel“** zeigt für jede Szene farbig, ob sie „Lokal – kostenlos“, „Runway – kostenpflichtig“ oder „Mock – Testmodus“ ist — auf einen Blick, ohne nachdenken zu müssen.

Knöpfe:

- **„Ausgewählte generieren“** — nur die markierten Szenen.
- **„Alle offenen generieren“** — alle noch nicht erfolgreich generierten Szenen.
- **„Fehlgeschlagene/abgelehnte erneut versuchen“**.

**Kostenbestätigung:** Sobald mindestens eine der zu generierenden Szenen über Runway läuft, erscheint vor dem Start der Dialog **„Generierung bestätigen“** mit der exakten Kostenübersicht (Sekunden × Preis je Sekunde je Modell, Gesamtsumme). Ohne deine ausdrückliche Bestätigung wird **kein einziger kostenpflichtiger Auftrag** losgeschickt. Zusätzlich greift automatisch das in Abschnitt 2.4 gesetzte Budgetlimit: Würde ein Auftrag das Limit überschreiten, wird er blockiert, **bevor** überhaupt eine Anfrage an Runway geschickt wird, und als fehlgeschlagen mit einer verständlichen Meldung markiert.

Der Ausgabenzähler (`Bisher ausgegeben: ... €`) läuft im Projekt mit und wird bei jedem tatsächlich erfolgreich abgerechneten Runway-Auftrag erhöht.

**ComfyUI startet jetzt bei Bedarf automatisch mit:** Ist für den Lauf lokale Generierung nötig und ComfyUI gerade nicht erreichbar, fragt VOXini vor dem Start nach: „ComfyUI läuft nicht — ComfyUI ist gerade nicht erreichbar, wird aber für diesen Lauf benötigt. Jetzt automatisch starten?“ Bei Bestätigung startet die App `START_COMFYUI.bat` selbst und wartet (mit abbrechbarer Fortschrittsanzeige, bis zu 3 Minuten), bis ComfyUI bereit ist. `comfyui_env\START_COMFYUI.bat` manuell zu starten ist damit nicht mehr zwingend nötig, funktioniert aber weiterhin genauso als Alternative.

**Warnhinweis bei mehreren Referenzbildern:** Hat eine Szene mehrere Charaktere mit Referenzbildern oder ein Charakter mehrere Referenzfotos (siehe Abschnitt 3), zeigt die Tabelle in der Status-Spalte ein gelbes Warndreieck mit Tooltip-Erklärung — als Hinweis, dass nur das erste Referenzbild tatsächlich in die Generierung eingeflossen ist.

---

## 6. Export

Im Reiter **„Export“** wählst du:

- **Titeltext** (optional): erscheint als Titelkarte am Anfang.
- **Seitenverhältnis**: 16:9 (Querformat), 9:16 (Hochformat, z. B. Reels/Shorts) oder 1:1 (quadratisch).
- **Untertitel**: Weich (eigene Untertitel-Spur, im Player ein-/ausschaltbar), Eingebrannt (fest im Bild) oder Keine.
- **Plattform-Abspannkarte** (Checkbox: „Plattform-Abspannkarte (TikTok/YouTube/Instagram/Spotify/Amazon Music/Apple Music + Website) am Ende einblenden“): blendet nach der letzten Szene automatisch eine kurze Endkarte mit echten Plattform-Icons, deinen Kanalnamen-Angaben und einem Website-Textfeld (Standard „Neon68.de“) ein. Text und Icons werden fest zusammengesetzt statt vom KI-Modell generiert, damit Schrift und Logos garantiert sauber und korrekt aussehen.
- Titelkarte und Übergänge zwischen den Szenen sind automatisch enthalten.

„Exportieren...“ öffnet den Speichern-Dialog, standardmäßig bereits im projekteigenen `export`-Unterordner vorausgewählt — so landet das fertige Video automatisch beim Rest des Projekts statt in einem beliebigen zuletzt verwendeten Ordner. Ist noch keine Audiodatei importiert, warnt VOXini vor dem Export ausdrücklich („Kein Audio importiert — das exportierte Video wäre komplett STUMM“) und lässt dich wählen, ob trotzdem ohne Ton exportiert werden soll. Als Tonspur wird ausschließlich dein Original-Song verwendet (nie eine automatisch generierte Vertonung).

Nach einem erfolgreichen Export werden die dabei angelegten Zwischendateien (der `_assembly_<Name>`-Arbeitsordner mit den einzelnen Szenen-Zuschnitten) automatisch wieder gelöscht — bei einem fehlgeschlagenen Export bleiben sie zur Fehlersuche erhalten.

---

## 7. Automatisches Speichern, Wiederherstellung, Backups

- VOXini speichert **alle 60 Sekunden automatisch** eine Sicherung im Hintergrund, ohne dass du etwas tun musst.
- Wird die App unerwartet beendet (Absturz, Stromausfall) und beim nächsten Öffnen des Projekts eine neuere automatische Sicherung gefunden, fragt VOXini beim Öffnen nach, ob diese übernommen werden soll.
- Bei jedem **manuellen** Speichern legt VOXini zusätzlich automatisch ein Backup der vorherigen Projektversion an (die letzten 10 Backups werden aufbewahrt, ältere automatisch gelöscht).
- Über den Knopf **„Wartung“** in der Kopfzeile öffnest du:
  - **Reiter „Backups“**: Liste aller vorhandenen Backups mit Zeitstempel, „Ausgewählte Version wiederherstellen“ setzt das Projekt auf diesen Stand zurück (nach Bestätigung).
  - **Reiter „Fehlerprotokoll“**: die letzten Einträge aus dem technischen Fehlerprotokoll der App (siehe Abschnitt 9) sowie „Protokollordner öffnen“, um den Ordner im Windows-Explorer zu öffnen.

---

## 7a. Updates

VOXini Video Studio prüft kurz nach jedem Start automatisch im Hintergrund, ob eine neuere Version verfügbar ist (über die öffentlichen GitHub-Releases des Projekts). Das geschieht **niemals unbemerkt**: Wird ein Update gefunden, öffnet sich ein Dialog mit der neuen Versionsnummer und den Änderungshinweisen — installiert wird erst nach deiner ausdrücklichen Bestätigung „Jetzt aktualisieren“. Wird kein Update gefunden oder ist keine Internetverbindung vorhanden, passiert beim automatischen Hintergrund-Check nichts Sichtbares.

Über den Knopf **„Nach Updates suchen“** in der Kopfzeile kannst du die Prüfung jederzeit manuell auslösen — hier bekommst du auch dann eine Rückmeldung, wenn kein Update verfügbar ist oder die Prüfung fehlschlägt (z. B. ohne Internetverbindung).

Bestätigst du ein Update, läuft es automatisch ab: Download der neuen `.exe`, die bisherige Version wird als Sicherung aufbewahrt (`VOXini Video Studio.exe.vorherige_version_backup`), die neue Version wird eingesetzt. Anschließend bittet dich die App, das Programm neu zu starten, damit die neue Version verwendet wird.

Im selben Dialog steht immer auch **„Auf vorherige Version zurückkehren“** zur Verfügung, solange eine Sicherung vorhanden ist — falls sich eine installierte Version als problematisch herausstellt, kannst du so jederzeit einen Schritt zurückgehen (wieder mit Neustart-Aufforderung).

Hinweis: Der Update-Mechanismus funktioniert nur mit der fertig gebauten `.exe`; im Python-Entwicklungsbetrieb ist „Jetzt aktualisieren“ deaktiviert.

---

## 8. Fehlerbehebung

**ComfyUI verbindet nicht:** Seit Kurzem bietet VOXini vor jedem Generierungslauf, der ComfyUI benötigt, automatisch an, es selbst zu starten (Dialog „ComfyUI läuft nicht“, siehe Abschnitt 5) — im Regelfall reicht es also, diese Rückfrage zu bestätigen. Klappt das nicht, prüfe stattdessen manuell, ob `comfyui_env\START_COMFYUI.bat` läuft (ein separates Konsolenfenster sollte offen bleiben und „Uvicorn running on http://127.0.0.1:8188“ o. ä. anzeigen) und ob Host/Port im Einrichtungsassistenten dazu passen.

**GPU wird von ROCm/PyTorch nicht erkannt (insbesondere RX 7600 XT):** Deine Grafikkarte steht möglicherweise (noch) nicht auf AMDs offizieller Kompatibilitätsliste für ROCm unter Windows (siehe 2.3). Prüfe die aktuelle Liste unter der oben genannten AMD-Adresse. Als Alternative kannst du in der Umgebung `comfyui_env\venv312` versuchsweise `torch-directml` statt der ROCm-Pakete installieren (`comfyui_env\venv312\Scripts\pip install torch-directml`) — dies läuft auf praktisch jeder aktuellen Windows-GPU, ist aber üblicherweise langsamer als natives ROCm.

**Runway-Verbindungstest schlägt fehl:** Prüfe, ob der API-Schlüssel korrekt eingegeben wurde (keine Leerzeichen) und ob dein Runway-Konto aktiv/mit Guthaben versehen ist. Der Test selbst verursacht keine Kosten.

**Generierung schlägt fehl / App reagiert nicht:** Öffne „Wartung“ → „Fehlerprotokoll“ für Details, oder direkt den Protokollordner (`%APPDATA%\VOXiniVideoStudio\logs`).

**Modell-Download bricht ab:** Der Download kann jederzeit über „Abbrechen“ im Fortschrittsdialog gestoppt werden; eine unvollständige Datei wird automatisch gelöscht, sodass ein erneuter Versuch sauber neu beginnt.

---

## 9. Fehlerprotokoll

VOXini schreibt automatisch ein technisches Fehlerprotokoll (unbehandelte Ausnahmen, Warnungen) nach:

```
%APPDATA%\VOXiniVideoStudio\logs\error.log
```

Die Datei wird automatisch rotiert (maximal 2 MB je Datei, 5 ältere Versionen werden aufbewahrt), wächst also nicht unbegrenzt. Bei Problemen ist der Inhalt dieser Datei der schnellste Weg, die Ursache einzugrenzen — entweder direkt einsehen oder bequem über „Wartung“ → „Fehlerprotokoll“ in der App.

---

## 10. Farbthema

Über die Auswahlbox oben in der Kopfzeile lässt sich das Farbthema umschalten: **VOXini** (Standard, Pink/Cyan/Violett — das offizielle Neon68-Markendesign), **Midnight** (Blau/Türkis) oder **Graphite** (dezentes Gold/Grau). Die Wahl wird gespeichert und beim nächsten Start automatisch wiederhergestellt. Bei einer frischen Installation ist immer **VOXini** voreingestellt.
