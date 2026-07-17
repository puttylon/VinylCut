#!/usr/bin/env python3
"""Normalisiert Dateinamen-Schlüssel in allen .fetch_songtext.json-Caches auf NFC.

Dateinamen (ä/ö/ü) können je nach Zugriffsweg (lokal geschrieben, dann über
SMB gelesen) unterschiedlich Unicode-normalisiert ankommen — NFC (ü als ein
Zeichen) vs. NFD (u + separater Akzent). Ohne Normalisierung verpasst der
Cache-Lookup (siehe lyrics_core.py) vorhandene Einträge und legt Duplikate
an: zwei Einträge für denselben Track, optisch identisch, byte-verschieden.

Dieses Skript liest jede Cache-Datei, führt Duplikate zusammen (neuerer
"ts"-Zeitstempel gewinnt — dieselbe Logik wie lyrics_core._load_cache())
und schreibt die bereinigte Version zurück. Fragt nichts neu ab, keine
Provider-/Whisper-Aufrufe — reine lokale Bereinigung.

Verwendung:
    python3 normalize_cache.py /Musik/              # Vorschau
    python3 normalize_cache.py /Musik/ --apply       # tatsächlich schreiben
"""

import argparse
import json
import subprocess
from pathlib import Path

from lyrics_core import _CACHE_FILENAME, _load_cache, _save_cache


def _find_cache_files(root: Path) -> list[Path]:
    """Findet Cache-Dateien via `find` (subprocess) statt Path.rglob().

    Über SMB-Freigaben ist `find` deutlich schneller als Path.rglob() (siehe
    Erfahrung aus whisper_sample.py — ein readdir-Aufruf pro Verzeichnis statt
    ein zusätzlicher stat-Aufruf pro Eintrag).
    """
    try:
        result = subprocess.run(
            ["find", str(root), "-name", _CACHE_FILENAME],
            capture_output=True,
            text=True,
            timeout=600,
        )
        return sorted(Path(p) for p in result.stdout.splitlines() if p)
    except Exception:
        return sorted(root.rglob(_CACHE_FILENAME))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("path", help="Wurzelverzeichnis der Bibliothek")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Änderungen tatsächlich schreiben (Standard: nur Vorschau)",
    )
    args = parser.parse_args()

    root = Path(args.path).expanduser().resolve()
    cache_files = _find_cache_files(root)
    if not cache_files:
        print(f"Keine Cache-Dateien gefunden in: {root}")
        return

    print(f"{len(cache_files)} Cache-Datei(en) gefunden — prüfe auf Duplikate...\n")

    touched = 0
    total_merged = 0
    for cf in cache_files:
        try:
            raw = json.loads(cf.read_text(encoding="utf-8"))
        except Exception:
            continue
        normalized = _load_cache(cf.parent)  # normalisiert + merged bereits
        merged = len(raw) - len(normalized)
        if merged <= 0:
            continue

        touched += 1
        total_merged += merged
        rel = cf.parent.relative_to(root)
        print(f"  {rel}: {len(raw)} → {len(normalized)} Einträge ({merged} Duplikat(e))")
        if args.apply:
            _save_cache(cf.parent, normalized)

    print()
    if touched == 0:
        print("Keine Duplikate gefunden — alles schon sauber.")
    elif args.apply:
        print(f"{touched} Datei(en) bereinigt, {total_merged} Duplikat(e) entfernt.")
    else:
        print(f"{touched} Datei(en) betroffen, {total_merged} Duplikat(e) — mit --apply schreiben.")


if __name__ == "__main__":
    main()
