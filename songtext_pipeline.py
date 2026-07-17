#!/usr/bin/env python3
"""Steuer-Skript für die Songtexte-Pipeline.

Orchestriert die 5 Schritte aus dem Architektur-Dokument "workflow für
songexte.txt" (Abschnitt "ZIELARCHITEKTUR"): scannen, Anbieter abfragen,
Anbieter nachholen, bewerten, .lrc schreiben. Jeder Schritt hat sein eigenes
Flag -- KEIN Sammel-Flag mehr (Nutzer-Feedback: "kein Mensch braucht im Flag
den Begriff 'phase'"). Frühere Versionen kannten `--phase LISTE`; das ist
mit diesem Umbau ersatzlos entfallen (siehe ROADMAP.md).

Verwendung:
    python3 songtext_pipeline.py PFAD [--recursive]
        Kein Schritt-Flag angegeben -> kompletter Normal-Durchlauf: scan,
        abfragen, nachholen, bewerten, schreiben (in dieser Reihenfolge).
    python3 songtext_pipeline.py PFAD --abfragen --bewerten --schreiben
        Nur die angegebenen Schritte.
    python3 songtext_pipeline.py --nachholen
        Nur der Nachhol-Modus, über die GANZE Bibliothek (kein PFAD nötig).
    python3 songtext_pipeline.py PFAD --nachholen
        Nachhol-Modus NUR für die Songs unter PFAD (seit diesem Umbau
        möglich -- vorher wurde --nachholen bei gesetztem PFAD komplett
        übersprungen, siehe ROADMAP.md).

Jeder Schritt ist einzeln UND in beliebiger Kombination aufrufbar, jeweils
auf PFAD eingegrenzt, wenn PFAD gesetzt ist (sonst: ganze Bibliothek).
--scan/--schreiben brauchen zwingend eine echte Audiodatei, also PFAD.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import cache_store
import evaluate_lyrics
import fetch_providers
import lyrics_core
import scan_songs
import write_lrc


def build_file_song_map(
    root: Path, recursive: bool, conn: sqlite3.Connection
) -> list[tuple[Path, str, str]]:
    """Ordnet Audiodateien unter root ihren "songs"-Einträgen in der Cache-DB zu.

    Liest Künstler/Titel-Tags je Datei (lyrics_core._read_audio_tags) und
    sucht per cache_store.normalize_key den passenden (artist_key, titel_key)
    in der Tabelle "songs" -- Titel dabei über _clean_query_title bereinigt,
    genau wie beim Anlegen der songs-Zeile (siehe CACHE_DESIGN.md,
    "Normalisierung"). Dateien ohne lesbare Tags oder ohne passenden
    DB-Eintrag tauchen einfach nicht in der Rückgabe auf -- kein Fehler
    (siehe Design-Dokument, Abschnitt 3, Randfall b). Es gibt bewusst KEINE
    dauerhafte Pfad-Speicherung in der DB -- diese Zuordnung wird bei jedem
    Lauf frisch berechnet.
    """
    mapping: list[tuple[Path, str, str]] = []
    for audio_path in scan_songs._iter_audio_files(root, recursive):
        artist, title, _genre = lyrics_core._read_audio_tags(audio_path)
        if not artist and not title:
            continue
        clean_title = lyrics_core._clean_query_title(title) if title else title
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


def _scope_from_root(
    root: Path | None, recursive: bool, conn: sqlite3.Connection
) -> set[tuple[str, str]] | None:
    """Berechnet den Scope (Menge von (artist_key, titel_key)) für root, oder
    None ohne PFAD (= keine Eingrenzung, ganze Cache-DB -- bewusste "alles
    nachziehen"-Absicht, siehe fetch_providers.fetch_all-Docstring).

    Wird an mehreren Stellen in main() jeweils FRISCH aufgerufen, nie einmal
    vorab wiederverwendet: läuft --scan im selben Aufruf VOR einem anderen
    Schritt (Standardfall), stehen frisch gescannte Songs erst danach in der
    "songs"-Tabelle -- eine vorher berechnete Zuordnung sähe sie noch nicht
    (siehe ROADMAP.md, realer Bug: Datei-Zuordnung vor dem Scan zu klein).
    """
    if root is None:
        return None
    mapping = build_file_song_map(root, recursive, conn)
    return {(artist_key, titel_key) for _, artist_key, titel_key in mapping}


def fetch_providers_normal(
    conn: sqlite3.Connection, scope: set[tuple[str, str]] | None = None
) -> None:
    """--abfragen: Normal-Modus von fetch_providers -- fragt Songs in "songs"
    bei allen 4 Anbietern ab (siehe fetch_providers.fetch_all).

    scope wird unverändert durchgereicht: ist er gesetzt (PFAD-Lauf, siehe
    main()), werden NUR die Songs des aktuellen Umfangs abgefragt, nicht die
    komplette, historisch gewachsene Cache-DB (siehe fetch_all-Docstring,
    "Behebt einen echten Bug").

    Songs mit Skip-Genre (Hörbuch/Hörspiel/... ) werden dabei übersprungen --
    die Anzahl wird separat sichtbar gemacht, nicht nur stillschweigend
    gezählt."""
    queried, skipped = fetch_providers.fetch_all(conn, scope=scope)
    print(f"abfragen: {queried} Song(s) abgefragt.")
    if skipped:
        print(
            f"  {skipped} Song(s) wegen Genre übersprungen "
            "(Hörbuch/Hörspiel/Instrumental/...)."
        )


def fetch_providers_nachhol(
    conn: sqlite3.Connection, scope: set[tuple[str, str]] | None = None
) -> None:
    """--nachholen: Nachhol-Modus von fetch_providers -- fragt gezielt nur
    (Song, Provider)-Kombinationen mit status 'nichts'/'fehlschlag' erneut ab
    (siehe fetch_providers.retry_missing).

    scope wie bei --abfragen: None (kein PFAD) = ganze Cache-DB, sonst nur
    die Songs des aktuellen Laufs. Seit diesem Umbau möglich -- vorher wurde
    --nachholen bei gesetztem PFAD komplett übersprungen, weil retry_missing
    keinen Scope kannte (siehe ROADMAP.md)."""
    print("nachholen:")
    fetch_providers.retry_missing(conn, scope=scope)


def evaluate_lyrics_normal(
    conn: sqlite3.Connection,
    scope: set[tuple[str, str]] | None = None,
    file_song_map: dict[tuple[str, str], Path] | None = None,
) -> None:
    """--bewerten: bewertet Songs (Konsens/Whisper), siehe evaluate_lyrics.evaluate_all.

    scope wie bei --abfragen (None ohne PFAD = ganze DB, sonst nur die Songs
    des aktuellen Laufs). file_song_map erlaubt Whisper bei Cache-Miss live
    zu transkribieren -- ohne Eintrag fällt der Song auf Konsens/Dauer-
    Heuristik zurück."""
    counts = evaluate_lyrics.evaluate_all(
        conn, scope=scope, file_song_map=file_song_map
    )
    if not counts:
        return
    print(
        f"bewerten: {counts['konsens']} Konsens, "
        f"{counts['whisper-akzeptiert']} Whisper akzeptiert, "
        f"{counts['abgelehnt']} abgelehnt, {counts['kein-provider']} ohne Provider."
    )


def write_lrc_normal(
    conn: sqlite3.Connection, file_song_map: list[tuple[Path, str, str]]
) -> None:
    """--schreiben: schreibt/löscht .lrc-Dateien je nach --bewerten-Entscheidung
    (wird intern erneut berechnet, siehe write_lrc.write_all -- kein
    Ablageort in der DB nötig)."""
    counts = write_lrc.write_all(conn, file_song_map)
    print(
        f"schreiben: {counts['updated']} geschrieben, "
        f"{counts['skipped']} übersprungen, {counts['not_found']} nicht gefunden."
    )


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
            "Weggelassen = ganze Bibliothek (nur sinnvoll zusammen mit "
            "--abfragen/--nachholen/--bewerten, die keine echte Datei "
            "brauchen)."
        ),
    )
    parser.add_argument(
        "--recursive",
        "-r",
        action="store_true",
        help="Unterordner von PFAD mit einbeziehen",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Tags lesen, Song in der Datenbank anlegen. Braucht PFAD.",
    )
    parser.add_argument(
        "--abfragen",
        action="store_true",
        help=(
            "Anbieter (lrclib, musixmatch, netease, genius) live abfragen. "
            "Mit PFAD: nur Songs aus PFAD. Ohne PFAD: die ganze Bibliothek."
        ),
    )
    parser.add_argument(
        "--nachholen",
        action="store_true",
        help=(
            "Nur die Anbieter nochmal fragen, bei denen bisher nichts "
            "gefunden wurde oder die fehlgeschlagen sind. Mit PFAD: nur "
            "Songs aus PFAD. Ohne PFAD: die ganze Bibliothek."
        ),
    )
    parser.add_argument(
        "--bewerten",
        action="store_true",
        help=(
            "Entscheiden: Konsens der Anbieter, sonst Whisper-Check, sonst "
            "Dauer-Heuristik. Mit PFAD: nur Songs aus PFAD. Ohne PFAD: die "
            "ganze Bibliothek."
        ),
    )
    parser.add_argument(
        "--schreiben",
        action="store_true",
        help=(
            ".lrc-Datei schreiben oder löschen, je nach Entscheidung aus "
            "--bewerten. Braucht PFAD."
        ),
    )
    args = parser.parse_args()

    # Kein einziges Schritt-Flag gesetzt -> kompletter Normal-Durchlauf (alter
    # Standard ohne --phase). Mindestens ein Flag gesetzt -> NUR die
    # angegebenen Schritte, in derselben festen Reihenfolge wie immer
    # (scan -> abfragen -> nachholen -> bewerten -> schreiben).
    any_step_selected = any(
        [args.scan, args.abfragen, args.nachholen, args.bewerten, args.schreiben]
    )
    run_scan = args.scan or not any_step_selected
    run_abfragen = args.abfragen or not any_step_selected
    run_nachholen = args.nachholen or not any_step_selected
    run_bewerten = args.bewerten or not any_step_selected
    run_schreiben = args.schreiben or not any_step_selected

    # Die Cache-Connection wird von jedem Schritt gebraucht (alle lesen/
    # schreiben in der Cache-DB) -- deshalb immer geöffnet, unabhängig von
    # PFAD. --scan/--schreiben brauchen zusätzlich eine echte Audiodatei;
    # fehlt PFAD, ist das kein Fehler -- der jeweilige Schritt meldet das nur
    # und tut nichts (siehe Design-Dokument, Abschnitt 3, Randfall b).
    conn = cache_store.open_cache(_default_db_path())
    root: Path | None = Path(args.path).resolve() if args.path else None

    if root is not None and (run_scan or run_bewerten or run_schreiben):
        file_song_map = build_file_song_map(root, args.recursive, conn)
        print(
            f"Datei-Zuordnung: {len(file_song_map)} Datei(en) einem Song in "
            "der DB zugeordnet."
        )

    try:
        if run_scan:
            if root is None:
                print("scan: kein PFAD angegeben, nichts zu scannen.")
            else:
                count = scan_songs.scan(root, args.recursive, conn)
                print(f"scan: {count} Song(s) gescannt/aktualisiert.")

        if run_abfragen:
            scope = _scope_from_root(root, args.recursive, conn)
            fetch_providers_normal(conn, scope=scope)

        if run_nachholen:
            scope = _scope_from_root(root, args.recursive, conn)
            fetch_providers_nachhol(conn, scope=scope)

        if run_bewerten:
            scope = _scope_from_root(root, args.recursive, conn)
            file_map: dict[tuple[str, str], Path] = {}
            if root is not None:
                mapping = build_file_song_map(root, args.recursive, conn)
                file_map = {(a, t): p for p, a, t in mapping}
            evaluate_lyrics_normal(conn, scope=scope, file_song_map=file_map)

        if run_schreiben:
            if root is None:
                print("schreiben: kein PFAD angegeben, nichts zu schreiben.")
            else:
                mapping = build_file_song_map(root, args.recursive, conn)
                write_lrc_normal(conn, mapping)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
