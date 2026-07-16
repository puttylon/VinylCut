#!/usr/bin/env python3
"""Steuer-Skript für die Songtexte-Pipeline (Meilenstein 0 -- Grundgerüst).

Orchestriert die 5 Phasen aus dem Architektur-Dokument
"workflow für songexte.txt" (Abschnitt "ZIELARCHITEKTUR"): scannen, Anbieter
abfragen, Anbieter nachholen, bewerten, .lrc schreiben. In diesem Meilenstein
sind die 4 echten Phasen-Programme (scan_songs, fetch_providers,
evaluate_lyrics, write_lrc) nur Platzhalter -- sie geben eine Log-Zeile aus
und tun sonst nichts. Die echte Logik kommt in den folgenden Meilensteinen
(siehe ROADMAP.md, "Songtexte-Pipeline-Umbau").

Verwendung:
    python3 songtext_pipeline.py PFAD [--recursive]
        Alle 5 Phasen nacheinander.
    python3 songtext_pipeline.py PFAD --phase 2,4,5
        Nur die angegebenen Phasen.
    python3 songtext_pipeline.py --phase 3
        Nur der Nachhol-Modus von fetch_providers -- reine Cache-DB-Operation,
        PFAD wird komplett ignoriert (siehe Design-Dokument, Abschnitt 3).
"""

from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Iterable
from pathlib import Path

import cache_store
import fetch_songtext

_ALL_PHASES = (1, 2, 3, 4, 5)
# Phasen, die eine echte Audiodatei brauchen (siehe Design-Dokument, Abschnitt
# 3 "PFAD und Audiodateien"): Phase 1 zum Scannen, Phase 4 nur wenn Whisper
# den Song noch nie gehört hat. Alle anderen Phasen kommen ohne PFAD aus.
_PHASES_NEEDING_FILE = frozenset({1, 4})


def _parse_phase_list(spec: str) -> list[int]:
    """Parst z.B. "2,4,5" oder "3" zu einer sortierten, eindeutigen Liste.

    Wirft ValueError mit einer sprechenden Meldung bei leeren, nicht-
    numerischen oder außerhalb 1-5 liegenden Werten.
    """
    phases: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            phase = int(part)
        except ValueError:
            raise ValueError(
                f"Ungültiger Phase-Wert: {part!r} (muss eine Zahl sein)"
            ) from None
        if phase not in _ALL_PHASES:
            raise ValueError(
                f"Ungültige Phase: {phase} (gültig: "
                f"{', '.join(str(p) for p in _ALL_PHASES)})"
            )
        phases.append(phase)
    if not phases:
        raise ValueError("--phase braucht mindestens einen Wert")
    return sorted(set(phases))


def _phase_arg_type(spec: str) -> list[int]:
    """argparse-`type`-Wrapper um _parse_phase_list für eine saubere CLI-Fehlermeldung."""
    try:
        return _parse_phase_list(spec)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def _iter_audio_files(root: Path, recursive: bool) -> Iterable[Path]:
    """Liefert die Audiodateien unter root: einzelne Datei, ein Ordner (nur
    oberste Ebene) oder rekursiv via fetch_songtext._iter_audio_dfs.

    Spiegelt die Pfad-Logik aus fetch_songtext.main() (Datei/Album/rekursiv).
    """
    if root.is_file():
        if root.suffix.lower() in fetch_songtext._AUDIO_EXTENSIONS:
            return [root]
        return []
    if recursive:
        return fetch_songtext._iter_audio_dfs(root)
    return sorted(
        p
        for p in root.glob("*")
        if p.suffix.lower() in fetch_songtext._AUDIO_EXTENSIONS
    )


def build_file_song_map(
    root: Path, recursive: bool, conn: sqlite3.Connection
) -> list[tuple[Path, str, str]]:
    """Ordnet Audiodateien unter root ihren "songs"-Einträgen in der Cache-DB zu.

    Liest Künstler/Titel-Tags je Datei (fetch_songtext._read_audio_tags) und
    sucht per cache_store.normalize_key den passenden (artist_key, titel_key)
    in der Tabelle "songs" -- Titel dabei über _clean_query_title bereinigt,
    genau wie beim Anlegen der songs-Zeile in fetch_songtext (siehe
    CACHE_DESIGN.md, "Normalisierung"). Dateien ohne lesbare Tags oder ohne
    passenden DB-Eintrag tauchen einfach nicht in der Rückgabe auf -- kein
    Fehler (siehe Design-Dokument, Abschnitt 3, Randfall b). Es gibt bewusst
    KEINE dauerhafte Pfad-Speicherung in der DB -- diese Zuordnung wird bei
    jedem Lauf frisch berechnet.
    """
    mapping: list[tuple[Path, str, str]] = []
    for audio_path in _iter_audio_files(root, recursive):
        artist, title, _genre = fetch_songtext._read_audio_tags(audio_path)
        if not artist and not title:
            continue
        clean_title = fetch_songtext._clean_query_title(title) if title else title
        artist_key = cache_store.normalize_key(artist)
        titel_key = cache_store.normalize_key(clean_title)
        row = conn.execute(
            "SELECT 1 FROM songs WHERE artist_key=? AND titel_key=?",
            (artist_key, titel_key),
        ).fetchone()
        if row is None:
            continue
        mapping.append((audio_path, artist_key, titel_key))
    return mapping


def scan_songs() -> None:
    """Platzhalter für Phase 1 -- kommt in Meilenstein 1 (scan_songs.py)."""
    print("Phase 1 (scan_songs) würde hier laufen.")


def fetch_providers(mode: str) -> None:
    """Platzhalter für Phase 2/3 -- kommt in Meilenstein 2 (fetch_providers.py).

    mode: "normal" (Phase 2) oder "nachhol" (Phase 3).
    """
    phase = 2 if mode == "normal" else 3
    label = "Normal-Modus" if mode == "normal" else "Nachhol-Modus"
    print(f"Phase {phase} (fetch_providers, {label}) würde hier laufen.")


def evaluate_lyrics() -> None:
    """Platzhalter für Phase 4 -- kommt in Meilenstein 3 (evaluate_lyrics.py)."""
    print("Phase 4 (evaluate_lyrics) würde hier laufen.")


def write_lrc() -> None:
    """Platzhalter für Phase 5 -- kommt in Meilenstein 4 (write_lrc.py)."""
    print("Phase 5 (write_lrc) würde hier laufen.")


_PHASE_DISPATCH = {
    1: scan_songs,
    2: lambda: fetch_providers("normal"),
    3: lambda: fetch_providers("nachhol"),
    4: evaluate_lyrics,
    5: write_lrc,
}


def _default_db_path() -> Path:
    return Path(__file__).parent / "fetch_songtext_cache.db"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        metavar="PFAD",
        help=(
            "Audiodatei oder Ordner (mit --recursive für Unterordner). "
            "Nicht nötig für --phase 3 (reine Cache-DB-Operation)."
        ),
    )
    parser.add_argument(
        "--recursive",
        "-r",
        action="store_true",
        help="Alle Unterordner rekursiv durchsuchen",
    )
    parser.add_argument(
        "--phase",
        type=_phase_arg_type,
        default=None,
        metavar="LISTE",
        help=(
            "Kommagetrennte Phasen-Auswahl, z.B. '2,4,5' oder '3' (gültig: "
            "1-5). Ohne --phase laufen alle 5 Phasen nacheinander."
        ),
    )
    args = parser.parse_args()

    phases = args.phase if args.phase is not None else list(_ALL_PHASES)

    # PFAD fehlt, aber eine gewählte Phase bräuchte eigentlich eine Datei:
    # kein Fehler -- die Datei-Zuordnung wird einfach nicht versucht (siehe
    # Design-Dokument, Abschnitt 3, Randfall b).
    if args.path and any(p in _PHASES_NEEDING_FILE for p in phases):
        root = Path(args.path).resolve()
        conn = cache_store.open_cache(_default_db_path())
        try:
            file_song_map = build_file_song_map(root, args.recursive, conn)
        finally:
            conn.close()
        print(
            f"Datei-Zuordnung: {len(file_song_map)} Datei(en) einem Song in "
            "der DB zugeordnet."
        )

    for phase in phases:
        _PHASE_DISPATCH[phase]()


if __name__ == "__main__":
    main()
