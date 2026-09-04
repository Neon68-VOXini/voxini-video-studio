"""Vergleicht die im Code hinterlegte Versionsnummer (voxini_studio.
__version__) mit dem Git-Tag-Namen bei einem Release-Build - verhindert,
dass versehentlich ein Tag gepusht wird, dessen Nummer nicht mit dem
tatsaechlich im Code gesetzten __version__-Wert uebereinstimmt (siehe
.github/workflows/release.yml). Ohne Tag-Argument prueft es nur, dass
__version__ ueberhaupt gueltig importierbar ist (fuer workflow_dispatch-
Testlaeufe ohne echten Tag)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voxini_studio import __version__


def main() -> int:
    if len(sys.argv) > 1:
        tag = sys.argv[1]
        tag_version = tag[1:] if tag.lower().startswith("v") else tag
        if tag_version != __version__:
            print(
                f"FEHLER: Tag '{tag}' (Version {tag_version}) stimmt nicht mit "
                f"voxini_studio.__version__ = '{__version__}' überein."
            )
            return 1
    print(f"OK: Version {__version__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
