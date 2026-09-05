# Referenzbindung Runway/ComfyUI – Lückenanalyse gegen "Verbindliche Referenzregel v2"

Stand: 4. September 2026. Reine Analyse, keine Code-Änderung. Grundlage:
`Verbindliche_Referenzregel_VOXini_Runway_v2.md` und
`PROJEKTBEFUND_UND_UEBERGABE_AN_CLAUDE.md` aus dem ChatGPT-Arbeitspaket
"WITHOUT EVER HAVING YOU" (Zwischenstand v4, 2026-09-04).

## Was heute schon funktioniert

- `Scene.character_ids` (models/project.py) verknüpft Szenen mit Figuren.
- `Character.reference_image_paths` speichert Referenzbilder pro Figur.
- `Scene.resolved_prompt()` fügt einen Textblock "CHARACTERS: - Name: Beschreibung"
  vor den Szenenprompt.
- `generation_service.generate_scene()` sammelt die Referenzbilder aller
  zugeordneten Figuren und setzt bereits einen `reference_warning`-Hinweis,
  wenn mehr als ein Bild vorhanden wäre.
- `resolve_provider()` + `budget_check()` verhindern automatisches
  Anbieterwechseln und blocken Runway-Aufträge über dem Budgetlimit, bevor
  der Provider überhaupt aufgerufen wird.
- Verknüpfung Figur↔Szene ist bewusst manuell (Checkbox-Liste in
  `storyboard_view.py`, Zeilen ~200-280) – kein Auto-Erkennungsmechanismus.
  **Das erklärt, warum aktuell 43 von 43 Szenen leere `character_ids` haben:
  das ist kein Software-Fehler, sondern ein noch nicht durchgeführter
  manueller Arbeitsschritt.**

## Lücken gegenüber der Referenzregel v2

1. **Keine Sperre bei fehlender Pflichtreferenz.** Aktuell nur ein
   optionaler `reference_warning`-Hinweis (nicht-blockierend). Die Regel
   verlangt: fehlt einer sichtbaren Figur eine freigegebene Referenz, muss
   der Auftrag **vor** der Kostenbestätigung gesperrt werden.
2. **Nur EIN Referenzbild wird pro Auftrag tatsächlich übertragen.**
   `comfyui_provider.py:483/535` und `runway_provider.py:354` verwenden
   ausschließlich `character_reference_paths[0]`. Bei mehreren Figuren in
   einer Szene (z. B. Emma + Axel) geht jedes weitere Referenzbild verloren
   – aktuell nur als Warnung vermerkt, nicht verhindert oder kompensiert.
3. **Kein strikter Identitäts-Prompt-Block.** `resolved_prompt()` fügt nur
   Name + Freitext-Beschreibung ein, nicht den in der Referenzregel
   vorgeschriebenen Anti-Drift-Textblock ("IDENTITY REFERENCE — ABSOLUTE
   CONTINUITY REQUIREMENT ...").
4. **Kein Multi-Charakter-Trennungsblock** ("MULTI-CHARACTER SEPARATION"),
   der verhindern soll, dass Gesichtszüge/Kleidung zwischen Figuren
   vermischt werden.
5. **Kein "Anschlussbild"-Mechanismus.** Die Regel verlangt, dass bei
   direkt aufeinanderfolgenden Einstellungen das letzte Bild des
   Vorclips als Kontinuitätsreferenz mitgesendet wird. Dafür existiert
   aktuell weder ein Datenmodell-Feld noch Provider-Unterstützung.
6. **Kostenfreigabe-Dialog zu knapp.** `cost_confirm_dialog.py` zeigt nur
   Anbieter, Modell, Gesamtkosten und einen Prompt-Ausschnitt. Die Regel
   verlangt zusätzlich sichtbar: erkannte Figuren, angehängte
   Referenzbilder, Kleidungszustand, "Anschlussbild vorhanden: ja/nein".
7. **Keine automatische Gesichtskontrolle nach der Generierung.** Aktuell
   verlässt sich VOXini komplett darauf, dass Prompt-Text + (bei
   `comfyui_identity_scene_mode`) InstantID-Konditionierung das Gesicht
   der Referenz treffen - es gibt keinen Nachgenerierungs-Check, der
   verifiziert, ob das erzeugte Gesicht tatsächlich noch zur
   Referenzperson passt. Ergebnis: Identitäts-Drift über mehrere Szenen
   hinweg (Gesicht "wandert" langsam weg vom Original) würde nicht
   automatisch auffallen, sondern erst beim manuellen Ansehen. Gewünscht
   (Neon68, 2026-09-04): eine echte Gesichtserkennung/-verifikation, die
   pro generierter Szene prüft, ob das Ergebnis-Gesicht noch zur
   Referenz passt, statt sich nur auf den Prompt zu verlassen.
   Technischer Ansatz (noch nicht umgesetzt): Gesichts-Embedding-Vergleich
   (z. B. via InsightFace/ArcFace - dieselbe Bibliothek, die der bereits
   vorhandene ComfyUI_InstantID-Node ohnehin schon benötigt, also keine
   komplett neue schwere Abhängigkeit) zwischen Referenzfoto und einem
   extrahierten Frame des generierten Clips, mit einem Ähnlichkeits-
   Schwellwert. Unterhalb des Schwellwerts: Szene bekommt eine sichtbare
   Warnung (analog `quality_warning`/`reference_warning` in
   `ClipVersion`, siehe models/project.py) statt automatisch akzeptiert
   zu werden - ob das nur warnen oder die Szene aktiv auf "prüfen
   nötig" statt "fertig" setzen soll, ist noch mit Neon68 zu klären.
   Konkreter Praxisbeleg (Neon68, 2026-09-04): genau dieses Problem
   (Gesicht driftet über mehrere Szenen von der Referenz weg) trat
   bereits im Video "Willkommen bei Neon68" auf - kein theoretisches
   Risiko, sondern schon einmal real passiert.

## Vorschlag für die Umsetzungsreihenfolge (noch nicht begonnen)

1. Datenmodell erweitern: mehrere benannte Referenzbilder pro Auftrag
   statt eines einzelnen Pfads; optionales Feld für das Anschlussbild
   (letzter Frame des vorherigen akzeptierten Clips).
2. Provider (ComfyUI + Runway) so weit wie deren API es zulässt auf
   Mehrfachreferenz umstellen; wo die API nur ein Bild akzeptiert, das
   klar in der UI kennzeichnen statt still zu verwerfen.
3. Identitäts-Block-Generator: baut pro Szene automatisch den
   vorgeschriebenen Anti-Drift-Text (+ Trennungsblock bei mehreren Figuren)
   aus den Charakterdaten zusammen.
4. Sperr-Logik vor der Kostenfreigabe: fehlende Pflichtreferenz blockiert
   den Auftrag als Fehler, nicht als überspringbare Warnung.
5. Kostenfreigabe-Dialog um das vorgeschriebene Prüfprotokoll erweitern.
6. Erst danach: technische Übernahme der neuen Charakterbilder und des
   neuen Szenenplans aus dem ChatGPT-Arbeitspaket – ausdrücklich erst nach
   Freigabe der Charaktere/des Drehbuchs durch Neon68, wie im Arbeitspaket
   selbst gefordert.

Nichts aus dem ChatGPT-Arbeitspaket (Bilder, Drehbuch, Szenenplan-JSON)
wurde in dieses Projekt übernommen – das war ausdrücklich noch nicht
gewünscht.

## Verbindliche Entscheidungen (Neon68 + ChatGPT, 4. September 2026)

Diese Lückenanalyse wurde von ChatGPT/Codex gegengeprüft und bestätigt
(Quelle: neuer Szenenplan v2, 46 Szenen, 44 mit Figuren, davon 23 mit
mehreren Figuren gleichzeitig - nur die Eröffnungsszene "Regen hinter
Glas" und das Schlussbild "Leeres Glas/Schwarz" haben keine Figur). Bis
zur vollständigen Umsetzung der folgenden neun Punkte darf keine
kostenpflichtige Produktion starten:

1. Automatische Zuordnung: die `character_names` im neuen Szenenplan
   müssen beim Import automatisch den angelegten Figuren/Referenzbildern
   zugeordnet werden - keine manuelle Zuordnung für 46 Szenen einzeln.
2. Eine sichtbare Figur ohne gültiges Referenzbild sperrt den Auftrag
   vollständig (siehe Lücke 1 oben).
3. Referenzbilder dürfen nie still verworfen werden. Unterstützt ein
   Anbieter/Modell nicht alle benötigten Figuren gleichzeitig, muss
   VOXini entweder sperren oder einen ausdrücklich ausgewählten
   mehrstufigen Schlüsselbild-Workflow verwenden (erst lokal ein
   Szenenbild mit allen sichtbaren Figuren komponieren, dieses dann als
   Startbild für die Videogenerierung nutzen - Erweiterung der
   bestehenden `comfyui_identity_scene_mode`-Pipeline, siehe
   comfyui_provider.py, auf mehrere Gesichter).
4. Pro Figur wird abhängig von der Kamera-/Blickrichtung die passendste
   freigegebene Ansicht gewählt (Front/Dreiviertel/Profil links/Profil
   rechts/Ganzkörper), nicht alle Ansichten gleichzeitig übertragen.
5. Der Anti-Drift-Block (+ Trennungsblock bei mehreren Figuren) wird
   automatisch in jeden Szenenprompt eingefügt (siehe Lücke 3+4 oben).
6. Das Anschlussbild (letzter Frame des vorherigen akzeptierten Clips)
   wird als Kontinuitätsreferenz unterstützt (siehe Lücke 5 oben).
7. Die automatische Gesichtskontrolle (siehe Lücke 7 oben) setzt bei zu
   geringer Übereinstimmung den Szenenstatus auf "Prüfung nötig" - nicht
   automatisch fertig, nicht automatisch exportierbar, bis Neon68 die
   Szene ausdrücklich akzeptiert. Erfordert eine neue `SceneStatus`-
   Ausprägung (siehe models/project.py).
8. Niemals automatisch kostenpflichtig neu generieren - jede erneute
   Runway-Generierung braucht wieder Kostenanzeige + ausdrückliche
   Bestätigung (heutiger Stand bereits so, da jede Generierung nutzer-
   ausgelöst über generate_scene() läuft - hier nur explizit als
   Anforderung bestätigt, kein neuer Code nötig, aber beim
   mehrstufigen Schlüsselbild-Workflow aus Punkt 3 zu beachten).
9. Der Kostenfreigabe-Dialog zeigt Figuren, tatsächlich verwendete
   Referenzbilder, Kleidungszustand, Anschlussbild und Kosten (siehe
   Lücke 6 oben).

Der Import des neuen Szenenplans (`schema: voxini_creative_scene_plan`,
`schema_version: 2`, ausdrücklicher Hinweis im JSON: "Claude must map
this schema to VOXini data models; do not overwrite project.voxproj
without validation.") erfolgt erst nach Umsetzung und Test dieser neun
Punkte, und erst wenn Emma, Axel UND Clara vollständig mit allen
Ansichten freigegeben sind (Clara steht bei ChatGPT noch aus).

## Umsetzungsstand (Stand: 5. September 2026)

Ehrlicher Zwischenstand nach dem grünen Licht "okay dann geben wir das
jetzt richtig in die VOXini Video Studio ein" (Neon68, 5. September 2026).
Verifiziert ausschließlich per `py_compile` + eigenständigen
Logik-Simulationsskripten in der Sandbox (kein PySide6/pydantic dort
installierbar, kein Live-Test mit echter GPU/ComfyUI möglich) - ein
Live-Test auf dem PC von Neon68 steht für alles unten noch aus.

- **Punkt 1 (automatische Zuordnung) - umgesetzt.** `core/scene_plan_
  importer.py` liest `Charakterreferenzen_VOXini_v1.json` +
  `Szenenplan_VOXini_v4_FINAL_mit_Mikrotiming.json`, ordnet
  `character_names` automatisch den Katalog-Charakteren zu, validiert
  ALLE referenzierten Bilddateien vor jedem Schreibzugriff (all-or-nothing:
  bei einem Fehler wird nichts angelegt/kopiert). Gegen das echte
  v6-Datenpaket getestet: 3 Charaktere, 46 Szenen, 29 kopierte Bilder,
  0 Validierungsfehler.
- **Punkt 2 (Sperre bei fehlender Pflichtreferenz) - umgesetzt.**
  `generation_service.resolve_scene_references()` blockiert eine Szene
  hart (kein Kostenvoranschlag, kein Provider-Aufruf), sobald ein
  zugeordneter Charakter kein aufloesbares Referenzbild hat.
- **Punkt 3 (nie still verwerfen; sperren oder Mehrbild-Workflow) -
  teilweise umgesetzt.** Die Sperre greift bereits für jede Szene, die
  gleichzeitig mehr als ein Referenzbild bräuchte (z. B. 2 Charaktere,
  oder 1 Charakter + Anschlussbild) - das deckt "sperren" ab. Der
  alternative "mehrstufige Schlüsselbild-Workflow" (Punkt 3b) ist NICHT
  umgesetzt - siehe Task #637, ausdrücklich als größeres Folgestück mit
  Live-GPU-Test zurückgestellt. Praktische Folge: von den 46 Szenen im
  v6-Plan sind 26 Mehrfigurenszenen aktuell gesperrt, bis #637 fertig ist.
- **Punkt 4 (kamerarichtungsabhängige Ansicht) - umgesetzt.** `Scene.
  character_view_roles` übernimmt `preferred_view_role` direkt aus dem
  Szenenplan (dort bereits von ChatGPT pro Szene berechnet); `Character.
  resolve_reference_image()` nutzt es als Fallback-Priorität.
- **Punkt 5 (automatischer Anti-Drift-Block) - umgesetzt.** `Scene.
  resolved_prompt()` fügt automatisch Identitäts-Sperr- und
  Mehrfigur-Trennungstext ein; importierte Szenen verwenden stattdessen
  den vom Szenenplan mitgelieferten, freigegebenen `reference_lock_
  prompt_prefix` (kein doppelter/abweichender Text).
- **Punkt 6 (Anschlussbild) - umgesetzt, bewusst MANUELL.** `core/
  continuity.py` extrahiert automatisch das letzte Frame jedes akzeptierten
  Clips (ffmpeg), verknüpft es aber nur nach explizitem Klick von Neon68
  im Storyboard-Tab mit der nächsten Szene - siehe Entscheidung vom
  5. September 2026 unten. Automatische Verknüpfung hätte sofort
  Szenen mit Charakter+Anschlussbild gesperrt (Punkt 3), ohne dass Neon68
  danach gefragt hat.
- **Punkt 7 (automatische Gesichtskontrolle) - umgesetzt, Live-Test auf
  echter Hardware steht noch aus (Task #638, direkte Reaktion auf das
  "Willkommen bei Neon68"-Identitäts-Drift-Problem).** `core/
  face_verification.py` vergleicht nach jeder erfolgreichen Generierung
  automatisch das erste und letzte Frame des Clips (ffmpeg-Extraktion)
  gegen das tatsächlich verwendete Charakter-Referenzbild - per InsightFace
  (Modell `buffalo_l`, Neon68-Entscheidung) im eigenen isolierten
  `.venv-faceverify`-Environment (gleiches Muster wie `.venv-lyrics`),
  damit die Haupt-App weiterhin frei von ML-Abhängigkeiten bleibt. Gilt
  nur für Szenen mit genau einem Charakter ohne gleichzeitiges
  Anschlussbild (Punkt 3 blockiert die Kombination ohnehin schon).
  Standardmäßig AUS (`Project.face_verification_enabled = False`, Toggle +
  Schwellenwert + Environment-Einrichtung im Setup-Assistenten); ist es an,
  aber das Environment noch nicht eingerichtet, wird trotzdem ganz normal
  generiert und nur ein ehrlicher Hinweis vermerkt (`ClipVersion.
  face_warning`) - nie ein stiller Download mitten in einem
  Batch-/Overnight-Lauf. Fällt die Ähnlichkeit unter den Schwellenwert
  (Startwert 0,40, ausdrücklich nicht empirisch kalibriert - keine
  GPU/kein Modell in der Entwicklungsumgebung verfügbar) oder wird gar
  kein Gesicht erkannt, setzt `generate_scene()` `SceneStatus.
  NEEDS_REVIEW` statt `DONE`; `ffmpeg_assembly.ready_scenes()` schließt
  solche Szenen automatisch vom Export aus, bis Neon68 sie im
  Storyboard-Tab entweder manuell per "Trotzdem akzeptieren" (bewusste,
  protokollierte Entscheidung) freigibt oder die Szene neu generiert.
  Verifiziert in der Sandbox ausschließlich per `py_compile` und
  eigenständigen Logik-Simulationsskripten (26 Prüfpunkte, u. a.
  Anwendbarkeitsregel, Nicht-Blockieren bei fehlendem Environment,
  Export-Ausschluss) - kein echter InsightFace-Lauf, keine echte
  Gesichtserkennung, kein Live-Test der UI möglich ohne PySide6/GPU in
  dieser Umgebung.
- **Punkt 8 (nie automatisch neu generieren) - bereits erfüllt,** unverändert
  bestätigt: jede Generierung bleibt nutzerausgelöst über generate_scene().
- **Punkt 9 (erweitertes Kostenfreigabe-Protokoll) - umgesetzt.**
  `CostConfirmDialog` zeigt pro Szene Charaktere, tatsächlich verwendete
  Referenzbilder, Kleidungszustand und "Anschlussbild vorhanden: ja/nein"
  an; gesperrte Szenen erscheinen als "GESPERRT" und zählen nicht in die
  Gesamtkosten; sind ALLE ausgewählten Szenen gesperrt, ist der
  Bestätigen-Button deaktiviert.

**Entscheidung Neon68, 5. September 2026 (Anschlussbild-Modus):** auf
Nachfrage ausdrücklich "Nur manuell anhängen" gewählt - VOXini extrahiert
jedes letzte Frame automatisch, verknüpft es aber erst nach explizitem
Klick pro Szene, damit keine Szene überraschend durch ein automatisch
gesetztes Anschlussbild gesperrt wird.

**Entscheidung Neon68, 5. September 2026 (Gesichtskontrolle, Task #638):**
auf Nachfrage bestätigt: Modell `buffalo_l` (genauer, größerer Download,
statt des kleineren `buffalo_sc`), Prüfung von erstem UND letztem Frame
des Clips, und standardmäßig AUS (manuell im Setup-Assistenten aktivieren
und das Environment einrichten) - passend zur bisherigen Linie dieses
Projekts, neue dependency-schwere Funktionen nicht ungefragt für
bestehende Projekte einzuschalten.

**Damit noch offen, bevor der eigentliche Import (Task #640) sinnvoll
ist:** Task #637 (Mehrbild-Workflow) ist der eigentliche Flaschenhals -
ohne ihn bleiben 26 der 46 v6-Szenen gesperrt. Ein Import des v6-Plans
JETZT wäre technisch möglich (Punkt 1 fertig), würde aber sofort mehr als
die Hälfte der Szenen als "gesperrt" anzeigen. Empfehlung: #637 vor #640
angehen, oder Neon68 entscheidet bewusst, trotzdem schon zu importieren
und die gesperrten Szenen später freizuschalten.
