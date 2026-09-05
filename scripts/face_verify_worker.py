"""Worker-Skript fuer die automatische Gesichtskontrolle nach der Generierung
(Task #638 - siehe docs/Referenzbindung_Luecken_und_Plan.md, Punkt 7).

Laeuft ausschliesslich im isolierten ".venv-faceverify"-Environment (siehe
voxini_studio/core/face_verify_env.py), NICHT im Haupt-Environment (".venv")
- dort sind insightface/onnxruntime bewusst nicht installiert, um die
schlanke Haupt-App nicht mit ML-Abhaengigkeiten zu belasten.

Kommuniziert mit dem aufrufenden Prozess (voxini_studio/core/
face_verification.py, laeuft im Haupt-Environment) nach dem gleichen Muster
wie scripts/lyrics_align_worker.py:

  PROGRESS <0-100> <Text>   - Fortschritt, reine Diagnoseausgabe
  ERROR <Text>              - Fehlermeldung, danach Exit-Code != 0
  (alle anderen Zeilen)     - reine Diagnose-/Logausgabe, wird ignoriert

Exit-Code 0    = Erfolg, das Ergebnis liegt als JSON am --output-json-Pfad.
Exit-Code != 0 = Fehler (Modell laden fehlgeschlagen, Datei nicht lesbar,
                 o.ae.), siehe letzte ERROR-Zeile auf stdout.

WICHTIG - Ablehnung ist kein Fehler: "kein Gesicht im Referenzbild oder im
zu pruefenden Frame gefunden" ist ein normales, erwartbares Ergebnis (kein
Exit-Code != 0), das im JSON als detected=false festgehalten wird - der
aufrufende Code core/face_verification.py entscheidet dann, wie er das
bewertet (siehe dortige Dokumentation), nicht dieser Worker.

Das InsightFace-Modellpaket "buffalo_l" (Neon68-Entscheidung, 2026-09-05,
siehe requirements-faceverify.txt) wird von insightface selbst beim
allerersten Lauf automatisch heruntergeladen und danach lokal
zwischengespeichert (~/.insightface/models/) - kein eigener Downloadcode
hier noetig.

Aufruf:
  python face_verify_worker.py --reference <Bild> --frame <Frame1>
      [--frame <Frame2> ...] --output-json <Zielpfad>
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any


def _report(percent: int, message: str) -> None:
    print(f"PROGRESS {percent} {message}", flush=True)


def _error(message: str) -> None:
    print(f"ERROR {message}", flush=True)


def _biggest_face(faces: list) -> Any:
    """Waehlt bei mehreren erkannten Gesichtern in einem Bild das groesste
    (per Bounding-Box-Flaeche) - das ist praktisch immer das eigentliche
    Portraet-/Identitaetsgesicht, waehrend kleinere Treffer meist
    Hintergrundpersonen oder Fehlerkennungen sind."""
    def _area(face) -> float:
        x1, y1, x2, y2 = face.bbox
        return max(0.0, x2 - x1) * max(0.0, y2 - y1)
    return max(faces, key=_area)


def _cosine_similarity(a, b) -> float:
    import numpy as np
    a = a / (np.linalg.norm(a) + 1e-8)
    b = b / (np.linalg.norm(b) + 1e-8)
    return float(np.dot(a, b))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gesichtskontrolle: Referenzbild gegen ein oder mehrere generierte Frames (isoliertes Environment)"
    )
    parser.add_argument("--reference", required=True, help="Pfad zum Charakter-Referenzbild")
    parser.add_argument(
        "--frame", action="append", required=True, dest="frames",
        help="Pfad zu einem zu pruefenden Frame - kann mehrfach angegeben werden",
    )
    parser.add_argument("--output-json", required=True, help="Zielpfad fuer das JSON-Ergebnis")
    args = parser.parse_args()

    try:
        _report(5, "InsightFace-Modell wird geladen (buffalo_l)")
        import cv2
        from insightface.app import FaceAnalysis

        app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        app.prepare(ctx_id=-1, det_size=(640, 640))
    except Exception as exc:
        _error(f"InsightFace-Modell konnte nicht geladen werden: {exc}\n{traceback.format_exc()}")
        return 1

    result: dict[str, Any] = {
        "reference": {"path": args.reference, "detected": False},
        "frames": [],
    }

    try:
        _report(20, "Referenzbild wird analysiert")
        ref_img = cv2.imread(args.reference)
        if ref_img is None:
            _error(f"Referenzbild konnte nicht gelesen werden: {args.reference}")
            return 1
        ref_faces = app.get(ref_img)
        if not ref_faces:
            result["reference"]["detected"] = False
            # Kein Gesicht in der Referenz gefunden - kein Fehler, aber jeder
            # Frame-Vergleich unten kann dann keine Aehnlichkeit berechnen.
        else:
            ref_face = _biggest_face(ref_faces)
            ref_embedding = ref_face.normed_embedding
            result["reference"]["detected"] = True

        total = len(args.frames)
        for idx, frame_path in enumerate(args.frames):
            progress = 20 + int(70 * (idx + 1) / max(1, total))
            _report(progress, f"Frame {idx + 1}/{total} wird geprueft: {Path(frame_path).name}")
            entry: dict[str, Any] = {"path": frame_path, "detected": False, "similarity": None}
            frame_img = cv2.imread(frame_path)
            if frame_img is None:
                entry["error"] = "Frame-Datei konnte nicht gelesen werden."
                result["frames"].append(entry)
                continue
            frame_faces = app.get(frame_img)
            if not frame_faces:
                result["frames"].append(entry)
                continue
            frame_face = _biggest_face(frame_faces)
            entry["detected"] = True
            if result["reference"]["detected"]:
                entry["similarity"] = _cosine_similarity(ref_embedding, frame_face.normed_embedding)
            result["frames"].append(entry)

        Path(args.output_json).write_text(json.dumps(result, indent=2), encoding="utf-8")
        _report(100, "Gesichtskontrolle abgeschlossen")
        return 0
    except Exception as exc:
        _error(f"Gesichtskontrolle fehlgeschlagen: {exc}\n{traceback.format_exc()}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
