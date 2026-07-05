#!/usr/bin/env python3
"""Durchsucht rekursiv alle Unterordner nach FLAC-Dateien und lädt Songtexte neu.

Für jede FLAC wird ein neuer Songtext via Waterfall (lrclib → musixmatch → alle)
gesucht. Wird ein Ergebnis gefunden, werden die ersten Zeilen angezeigt und der
Nutzer entscheidet ob er übernehmen oder überspringen möchte.

Nutzung:
    python3 refetch_lyrics.py "/Pfad/zum/Musik-Ordner"
"""

import sys
import json
import tempfile
from pathlib import Path

from fetch_songtext import _load_env, fetch_lrc


def _preview(lrc_path: Path, lines: int = 20) -> str:
    try:
        content = lrc_path.read_text(encoding="utf-8").strip().splitlines()
        return "\n".join(f"  {line}" for line in content[:lines])
    except Exception:
        return "  (Datei nicht lesbar)"


def main():
    if len(sys.argv) < 2:
        sys.exit('Nutzung: python3 refetch_lyrics.py "/Pfad/zum/Musik-Ordner"')

    root = Path(sys.argv[1]).resolve()
    flac_files = sorted(root.rglob("*.flac"))

    if not flac_files:
        print("Keine FLAC-Dateien gefunden.")
        return

    env = _load_env()
    updated = skipped = not_found = errors = 0

    print(f"\n{len(flac_files)} FLAC-Dateien gefunden. Starte Suche...\n")

    for flac in flac_files:
        lrc_path = flac.with_suffix(".lrc")

        # Künstler aus release.json des Elternordners
        artist = ""
        try:
            with open(flac.parent / "release.json", "r", encoding="utf-8") as f:
                artist = json.load(f).get("artist", "")
        except Exception:
            pass

        title = flac.stem.split(" - ", 1)[-1] if " - " in flac.stem else flac.stem
        query = f"{artist} {title}".strip()

        print(f"── {flac.parent.name} / {flac.stem}")

        # Nur den Pfad reservieren — Datei darf noch nicht existieren,
        # damit fetch_lrc's exists()-Check korrekt funktioniert
        with tempfile.NamedTemporaryFile(suffix=".lrc", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        tmp_path.unlink()

        try:
            found = fetch_lrc(query, tmp_path, env)
        except FileNotFoundError:
            print("   ✗ syncedlyrics nicht gefunden — Abbruch.")
            tmp_path.unlink(missing_ok=True)
            errors += 1
            break

        if not found:
            print("   ✗ Kein Ergebnis gefunden.")
            tmp_path.unlink(missing_ok=True)
            not_found += 1
            continue

        print(_preview(tmp_path))

        ans = input("   [Enter] übernehmen  [s] überspringen: ").strip().lower()
        if ans == "s":
            tmp_path.unlink(missing_ok=True)
            skipped += 1
        else:
            lrc_path.write_bytes(tmp_path.read_bytes())
            tmp_path.unlink(missing_ok=True)
            print("   ✓ gespeichert.")
            updated += 1

        print()

    print(
        f"\nFertig — {updated} aktualisiert, {skipped} übersprungen, {not_found} nicht gefunden",
        end="",
    )
    if errors:
        print(f", {errors} Fehler", end="")
    print(".")


if __name__ == "__main__":
    main()
