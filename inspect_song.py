#!/usr/bin/env python3
"""Diagnose-Werkzeug: zeigt alle Cache-Daten eines einzelnen Songs nebeneinander.

Fragt die Cache-Datenbank (cache.db) für einen Künstler/Titel
gezielt ab und schreibt Provider-Texte (Genius, Netease, Lrclib, Musixmatch)
sowie das Whisper-Transkript in eine lesbare TXT-Datei — praktisch, um bei
einem einzelnen Song nachzuvollziehen, was der Cache tatsächlich enthält,
ohne manuell mit sqlite3 auf der Kommandozeile zu hantieren.

Reiner Lesezugriff auf die Datenbank, es wird nichts verändert.

Verwendung:
    python3 inspect_song.py --artist "Nina Hagen" --title "Naturträne"
    python3 inspect_song.py --artist "Nina Hagen" --title "Naturträne" --output custom_name.txt
"""

import argparse
import re
import sys
from pathlib import Path

import cache_store

PROVIDERS = ("genius", "netease", "lrclib", "musixmatch")

_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|]')


def sanitize_filename(text: str) -> str:
    """Ersetzt Leerzeichen und dateisystem-unsichere Zeichen durch Unterstriche."""
    text = text.replace(" ", "_")
    return _UNSAFE_CHARS.sub("_", text)


def _format_provider_section(conn, song_id: int, provider: str) -> str:
    row = conn.execute(
        "SELECT status, fehlergrund, fingerabdruck FROM ergebnisse "
        "WHERE song_id=? AND quelle=?",
        (song_id, provider),
    ).fetchone()
    if row is None:
        return "(nie abgefragt)"

    status, fehlergrund, fingerabdruck = row
    if status == "treffer":
        content = None
        if fingerabdruck is not None:
            content_row = conn.execute(
                "SELECT inhalt FROM texte WHERE fingerabdruck=?", (fingerabdruck,)
            ).fetchone()
            content = content_row[0] if content_row else None
        return content if content is not None else "(kein Text vorhanden)"
    if status == "nichts":
        return "(kein Treffer)"
    # status == "fehlschlag"
    return f"(Fehlschlag: {fehlergrund})"


def _format_whisper_section(conn, song_id: int) -> str:
    row = conn.execute(
        "SELECT transkript FROM transkripte WHERE song_id=?", (song_id,)
    ).fetchone()
    if row is None or row[0] is None:
        return "(kein Transkript vorhanden)"
    return row[0]


def build_report(conn, artist: str, title: str) -> str | None:
    """Baut den TXT-Report für (artist, title) auf, oder None falls Song unbekannt."""
    artist_key = cache_store.normalize_key(artist)
    titel_key = cache_store.normalize_key(title)

    song_row = conn.execute(
        "SELECT id FROM songs WHERE artist_key=? AND titel_key=?",
        (artist_key, titel_key),
    ).fetchone()
    if song_row is None:
        return None
    song_id = song_row[0]

    lines = [f"Artist: {artist}", f"Titel: {title}", ""]
    for provider in PROVIDERS:
        lines.append(f"=== {provider.capitalize()} ===")
        lines.append(_format_provider_section(conn, song_id, provider))
        lines.append("")
    lines.append("=== Whisper ===")
    lines.append(_format_whisper_section(conn, song_id))
    lines.append("")

    return "\n".join(lines)


def _default_db_path() -> Path:
    return cache_store.default_cache_path()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--artist", required=True, help="Künstlername")
    parser.add_argument("--title", required=True, help="Songtitel")
    parser.add_argument(
        "--output",
        "-o",
        metavar="PFAD",
        help="Zielpfad der TXT-Datei (Standard: <Künstler>_<Titel>.txt im aktuellen Verzeichnis)",
    )
    args = parser.parse_args()

    conn = cache_store.open_cache(_default_db_path())

    report = build_report(conn, args.artist, args.title)
    if report is None:
        print(
            f"Song nicht in der Cache-Datenbank gefunden: "
            f"Artist={args.artist!r}, Titel={args.title!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = Path(
            f"{sanitize_filename(args.artist)}_{sanitize_filename(args.title)}.txt"
        )

    output_path.write_text(report, encoding="utf-8")
    print(f"Geschrieben: {output_path}")


if __name__ == "__main__":
    main()
