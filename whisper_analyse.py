#!/usr/bin/env python3
"""Analysiert ob und warum Whisper für jeden Track gelaufen ist (oder nicht).

Liest alle .fetch_songtext.json-Dateien rekursiv — alle Programmversionen,
kein Versionsfilter. Gibt eine Übersicht nach Kategorie aus.

Verwendung:
    python3 whisper_analyse.py /Volumes/music/musik/
"""

import argparse
import json
from pathlib import Path

CACHE_FILENAME = ".fetch_songtext.json"


# ── Legacy-Kompatibilität (identisch mit lrc_analyse.py) ─────────────────────

def _method(entry: dict) -> str:
    method = entry.get("method")
    if method:
        if method == "konsens" and entry.get("no_vocal"):
            return "konsens-kein-vokal"
        return method
    # Legacy pre-v1.5.0
    if entry.get("consensus") and entry.get("no_vocal"):
        return "konsens-kein-vokal"
    if entry.get("consensus"):
        return "konsens"
    if entry.get("fallback"):
        return "konsens-kein-vokal"
    model = entry.get("model")
    if model == "small":
        return "whisper-small"
    if model == "base":
        return "whisper-base"
    if entry.get("score") is not None:
        return "whisper-base"
    return "heuristik"


def _reject_reason(entry: dict) -> str:
    reason = entry.get("reason")
    if reason:
        return reason
    # Legacy pre-v1.5.0
    if entry.get("providers", 0) == 0:
        return "kein-provider"
    words = entry.get("words") or 0
    score = entry.get("score")
    if score is None:
        return "kein-whisper"
    if words == 0 and score == 0.0:
        return "kein-vokal"
    return "unter-schwelle"


# ── Kategorisierung ───────────────────────────────────────────────────────────

def _categorise(entry: dict) -> str:
    """Gibt eine von 8 Kategorien zurück."""
    r = entry.get("r")
    if r == "skip":
        return "genre-skip"
    if r == "ok":
        m = _method(entry)
        if m == "konsens":
            return "konsens"
        if m == "konsens-kein-vokal":
            return "konsens-kein-vokal"
        if m == "whisper-small":
            return "whisper-small-ok"
        if m == "heuristik":
            return "heuristik"
        return "whisper-base-ok"   # whisper-base oder unbekanntes ok
    if r == "nf":
        return _reject_reason(entry)  # kein-provider / kein-vokal / unter-schwelle / kein-whisper
    return "unbekannt"


# ── Auswertung ────────────────────────────────────────────────────────────────

def analyse(root: Path) -> None:
    cache_files = sorted(root.rglob(CACHE_FILENAME))
    if not cache_files:
        print(f"Keine Cache-Dateien gefunden in: {root}")
        return

    counts: dict[str, int] = {}
    albums: set[Path] = set()
    total = 0

    for cf in cache_files:
        albums.add(cf.parent)
        try:
            data = json.loads(cf.read_text(encoding="utf-8"))
        except Exception:
            continue
        for entry in data.values():
            if not isinstance(entry, dict):
                continue
            cat = _categorise(entry)
            counts[cat] = counts.get(cat, 0) + 1
            total += 1

    if total == 0:
        print("Keine Einträge gefunden.")
        return

    def pct(n: int) -> str:
        return f"{n / total:.1%}"

    def row(label: str, key: str, indent: int = 2) -> None:
        n = counts.get(key, 0)
        if n:
            print(f"{'':>{indent}}{label:<36}{n:>6}  {pct(n)}")

    whisper_ran = sum(
        counts.get(k, 0)
        for k in ("whisper-base-ok", "whisper-small-ok", "kein-vokal", "unter-schwelle", "kein-whisper")
    )
    no_whisper = total - whisper_ran

    print(f"\n=== WHISPER-ANALYSE {root} ===")
    print(f"Alben: {len(albums)}   Einträge gesamt: {total}\n")

    print(f"{'OHNE WHISPER':<38}{no_whisper:>6}  {pct(no_whisper)}")
    row("Provider-Konsens",             "konsens")
    row("Konsens (kein Vokal erkannt)", "konsens-kein-vokal")
    row("Genre übersprungen",           "genre-skip")
    row("Kein Provider gefunden",       "kein-provider")
    row("Heuristik (kein Whisper)",     "heuristik")
    row("--no-whisper: Dauer passt nicht", "dauer-abweichung")

    print()
    print(f"{'MIT WHISPER':<38}{whisper_ran:>6}  {pct(whisper_ran)}")
    row("Akzeptiert via Whisper base",  "whisper-base-ok")
    row("Akzeptiert via Whisper small", "whisper-small-ok")
    row("Abgelehnt: kein Vokal erkannt","kein-vokal")
    row("Abgelehnt: unter Schwelle",    "unter-schwelle")
    row("Abgelehnt: kein Whisper verf.","kein-whisper")

    if counts.get("unbekannt"):
        print()
        row("Unbekannte Einträge", "unbekannt", indent=0)

    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".", help="Wurzelverzeichnis (Standard: .)")
    args = parser.parse_args()
    analyse(Path(args.path).expanduser().resolve())


if __name__ == "__main__":
    main()
