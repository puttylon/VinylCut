#!/usr/bin/env python3
"""Findet gecachte 'nf'-Einträge und löscht sie für einen Neuprüflauf.

Sucht in allen .fetch_songtext.json-Dateien nach Tracks die:
  - als 'nf' (nicht gefunden) gecacht sind
  - mindestens --min-providers Provider-Treffer hatten
  - und einen Whisper-Score ≥ --min-score erreichten
  - optional: nur einen bestimmten Ablehnungsgrund (--reason)

Mit --apply werden ihre Cache-Einträge gelöscht,
sodass ein normaler Lauf (ohne --force) sie erneut prüft.

Verwendung:
    python3 lrc_recheck.py /Volumes/musik/                                # Vorschau (≥3 Provider)
    python3 lrc_recheck.py /Volumes/musik/ --apply                        # Cache-Einträge löschen
    python3 lrc_recheck.py /Volumes/musik/ --min-providers 1 --min-score 0.0  # alle mit ≥1 Provider

Für v1.5.2-Fixes (robustere Konsens- und VAD-Logik):
    # VAD false positives (kein-vokal trotz Gesang — V1/V2/V3-Fix)
    python3 lrc_recheck.py /Volumes/musik/ --reason kein-vokal --min-providers 1 --min-score 0.0

    # Konsens-Ausreißer (Genius-Label-Pollution — C1/C3-Fix)
    python3 lrc_recheck.py /Volumes/musik/ --reason unter-schwelle --min-providers 3 --min-score 0.0
"""

import argparse
import json
from pathlib import Path

from library import reject_reason_from_cache_entry as _reject_reason

CACHE_FILENAME = ".fetch_songtext.json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("path", help="Wurzelverzeichnis zum Durchsuchen")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Cache-Einträge tatsächlich löschen (Standard: nur anzeigen)",
    )
    parser.add_argument(
        "--min-providers",
        type=int,
        default=3,
        metavar="N",
        help="Mindestanzahl Provider-Treffer (Standard: 3)",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.20,
        metavar="F",
        help="Mindest-Whisper-Score (Standard: 0.20, für kein-vokal: 0.0)",
    )
    parser.add_argument(
        "--reason",
        metavar="GRUND",
        help="Nur Einträge mit diesem Ablehnungsgrund: kein-provider, kein-vokal, unter-schwelle, kein-whisper",
    )
    args = parser.parse_args()

    root = Path(args.path).resolve()
    candidates: list[tuple[Path, str, dict]] = []

    for cache_file in sorted(root.rglob(CACHE_FILENAME)):
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        for track, entry in data.items():
            if not isinstance(entry, dict):
                continue
            if entry.get("r") != "nf":
                continue
            if entry.get("providers", 0) < args.min_providers:
                continue
            if (entry.get("score") or 0.0) < args.min_score:
                continue
            if args.reason and _reject_reason(entry) != args.reason:
                continue
            candidates.append((cache_file, track, entry))

    if not candidates:
        print("Keine Kandidaten gefunden.")
        return

    print(
        f"{'VORSCHAU' if not args.apply else 'LÖSCHE'} — {len(candidates)} Kandidaten:\n"
    )
    by_file: dict[Path, list[str]] = {}
    for cache_file, track, entry in candidates:
        rel = cache_file.parent.relative_to(root)
        score = entry.get("score")
        score_str = f"{score:.0%}" if score is not None else "?"
        reason_str = _reject_reason(entry)
        print(
            f"  {rel}/{track}  providers={entry.get('providers')}  score={score_str}  reason={reason_str}"
        )
        by_file.setdefault(cache_file, []).append(track)

    if not args.apply:
        print(
            f"\nMit --apply werden diese {len(candidates)} Einträge aus dem Cache entfernt."
        )
        return

    removed = 0
    for cache_file, tracks in by_file.items():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            for track in tracks:
                if track in data:
                    del data[track]
                    removed += 1
            cache_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            print(f"  Fehler bei {cache_file}: {e}")

    print(f"\n{removed} Einträge gelöscht. Nächster Lauf verarbeitet diese Tracks neu.")


if __name__ == "__main__":
    main()
