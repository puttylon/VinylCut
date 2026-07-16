#!/usr/bin/env python3
"""Steuer-Skript für die Songtexte-Pipeline.

Orchestriert die 5 Phasen aus dem Architektur-Dokument
"workflow für songexte.txt" (Abschnitt "ZIELARCHITEKTUR"): scannen, Anbieter
abfragen, Anbieter nachholen, bewerten, .lrc schreiben. Phase 1 (scan_songs,
Meilenstein 1) und Phase 2/3 (fetch_providers, Normal-/Nachhol-Modus,
Meilenstein 2) laufen bereits echt. Die restlichen 2 Phasen-Programme
(evaluate_lyrics, write_lrc) sind noch Platzhalter -- sie geben eine Log-Zeile
aus und tun sonst nichts. Die echte Logik kommt in den folgenden
Meilensteinen (siehe ROADMAP.md, "Songtexte-Pipeline-Umbau").

Verwendung:
    python3 songtext_pipeline.py PFAD [--recursive]
        Alle 5 Phasen nacheinander -- Phase 3 (Nachhol-Modus) wird dabei
        automatisch übersprungen, siehe unten.
    python3 songtext_pipeline.py PFAD --phase 2,4,5
        Nur die angegebenen Phasen.
    python3 songtext_pipeline.py --phase 3
        Nur der Nachhol-Modus von fetch_providers -- reine Cache-DB-Operation
        über die GANZE Bibliothek. Läuft NUR, wenn GAR KEIN PFAD angegeben
        ist: wird trotzdem ein PFAD mitgegeben, wird Phase 3 komplett
        übersprungen (nicht auf PFAD eingegrenzt) -- ist PFAD gesetzt, wird
        NUR dieser PFAD verarbeitet, siehe main().
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import cache_store
import fetch_providers
import fetch_songtext
import scan_songs

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
    for audio_path in scan_songs._iter_audio_files(root, recursive):
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


def fetch_providers_normal(
    conn: sqlite3.Connection, scope: set[tuple[str, str]] | None = None
) -> None:
    """Phase 2: Normal-Modus von fetch_providers -- fragt Songs in "songs"
    bei allen 4 Anbietern ab (siehe fetch_providers.fetch_all).

    scope wird unverändert durchgereicht: ist er gesetzt (PFAD-Lauf, siehe
    main()), werden NUR die Songs des aktuellen Umfangs abgefragt, nicht die
    komplette, historisch gewachsene Cache-DB (siehe fetch_all-Docstring,
    "Behebt einen echten Bug").

    Songs mit Skip-Genre (Hörbuch/Hörspiel/... ) werden dabei übersprungen --
    die Anzahl wird separat sichtbar gemacht, nicht nur stillschweigend
    gezählt."""
    queried, skipped = fetch_providers.fetch_all(conn, scope=scope)
    print(f"Phase 2 (fetch_providers, Normal-Modus): {queried} Song(s) abgefragt.")
    if skipped:
        print(
            f"  {skipped} Song(s) wegen Genre übersprungen "
            "(Hörbuch/Hörspiel/Instrumental/...)."
        )


def fetch_providers_nachhol(conn: sqlite3.Connection) -> None:
    """Phase 3: Nachhol-Modus von fetch_providers -- fragt gezielt nur
    (Song, Provider)-Kombinationen mit status 'nichts'/'fehlschlag' erneut ab
    (siehe fetch_providers.retry_missing), über die GANZE Cache-DB, kein
    Scope-Parameter. Wird von main() nur aufgerufen, wenn kein PFAD gegeben
    ist -- ist PFAD gesetzt, überspringt main() diese Phase komplett, bevor
    diese Funktion je erreicht wird (siehe dortiger Kommentar)."""
    print("Phase 3 (fetch_providers, Nachhol-Modus):")
    fetch_providers.retry_missing(conn)


def evaluate_lyrics() -> None:
    """Platzhalter für Phase 4 -- kommt in Meilenstein 3 (evaluate_lyrics.py)."""
    print("Phase 4 (evaluate_lyrics) würde hier laufen.")


def write_lrc() -> None:
    """Platzhalter für Phase 5 -- kommt in Meilenstein 4 (write_lrc.py)."""
    print("Phase 5 (write_lrc) würde hier laufen.")


# Phasen ohne Bedarf an der Cache-Connection als Argument (aktuell nur noch
# die beiden Platzhalter 4/5) -- Phase 1/2/3 brauchen conn und werden direkt
# in main()s Schleife behandelt, siehe dort.
_PHASE_DISPATCH = {
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
            "Nicht nötig für --phase 3 (reine Cache-DB-Operation über die "
            "ganze Bibliothek) -- ist PFAD trotzdem gesetzt, wird Phase 3 "
            "komplett übersprungen statt eingegrenzt."
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

    # Die Cache-Connection wird von jeder Phase gebraucht (alle 5 lesen/
    # schreiben in der Cache-DB) -- deshalb immer geöffnet, unabhängig von
    # PFAD. Nur Phase 1 und 4 brauchen zusätzlich eine echte Audiodatei
    # (_PHASES_NEEDING_FILE); PFAD fehlt aber, ist das kein Fehler -- die
    # Datei-Zuordnung/der Scan wird einfach nicht versucht (siehe
    # Design-Dokument, Abschnitt 3, Randfall b). Die Verbindung bleibt über
    # die gesamte Phasen-Schleife offen (statt je Phase eine eigene).
    conn = cache_store.open_cache(_default_db_path())
    # root wird immer aus PFAD aufgelöst, sobald PFAD gegeben ist (reines
    # Path.resolve(), kein Datei-I/O) -- unabhängig davon, ob Phase 1/4
    # gewählt sind. Grund: Phase 2 braucht root weiter unten in der Schleife
    # ebenfalls, um sich auf die Songs des aktuellen Laufs einzugrenzen
    # (siehe dort). Die folgende, informative "Datei-Zuordnung"-Vorab-
    # Ausgabe bleibt an ihre bisherige Bedingung (Phase 1/4 gewählt)
    # gebunden -- unverändert, eigener Zweck.
    root: Path | None = Path(args.path).resolve() if args.path else None

    # Phase 3 (Nachhol-Modus) läuft bewusst nur, wenn GAR KEIN PFAD angegeben
    # ist -- dann arbeitet sie über die ganze Bibliothek (bewusste "alle
    # Tracks aktualisieren"-Absicht). Ist PFAD gesetzt, wird NUR dieser PFAD
    # verarbeitet: Phase 3 wird komplett ausgelassen (nicht auf PFAD
    # eingegrenzt -- fetch_providers.retry_missing() kennt gar keinen Scope-
    # Parameter, siehe dortiger Docstring). Präzisierte Nutzer-Vorgabe nach
    # einem echten Testlauf (ROADMAP.md) -- ursprünglich sollte --phase 3
    # PFAD immer ignorieren, das war zu grob.
    if root is not None and 3 in phases:
        phases = [p for p in phases if p != 3]
        print(
            "Phase 3 (Nachhol-Modus) übersprungen: läuft nur ohne PFAD "
            "(arbeitet über die ganze Bibliothek)."
        )

    if not phases:
        print("Keine Phase auszuführen.")
        conn.close()
        return

    if root is not None and any(p in _PHASES_NEEDING_FILE for p in phases):
        file_song_map = build_file_song_map(root, args.recursive, conn)
        print(
            f"Datei-Zuordnung: {len(file_song_map)} Datei(en) einem Song in "
            "der DB zugeordnet."
        )

    try:
        for phase in phases:
            if phase == 1:
                if root is None:
                    print(
                        "Phase 1 (scan_songs): kein PFAD angegeben, nichts zu scannen."
                    )
                    continue
                count = scan_songs.scan(root, args.recursive, conn)
                print(f"Phase 1 (scan_songs): {count} Song(s) gescannt/aktualisiert.")
            elif phase == 2:
                # Scope MUSS hier, an dieser Stelle in der Schleife, frisch
                # berechnet werden -- nicht vor der Schleife (siehe root oben):
                # läuft Phase 1 im selben Aufruf VOR Phase 2 (Standardfall,
                # Phasen laufen sortiert aufsteigend), stehen die gerade neu
                # gescannten Songs erst JETZT in der "songs"-Tabelle. Eine
                # vorher berechnete Zuordnung sähe sie noch nicht (siehe
                # ROADMAP.md, realer Bug: "Datei-Zuordnung: 2 Datei(en)" vor
                # Phase 1, obwohl das Album 17 Songs hatte).
                #
                # Ohne PFAD (root is None) bleibt scope=None -- fetch_all
                # fragt dann bewusst die komplette Cache-DB ab (explizite
                # Nutzerabsicht "ganze Bibliothek nachziehen").
                scope: set[tuple[str, str]] | None = None
                if root is not None:
                    file_song_map = build_file_song_map(root, args.recursive, conn)
                    scope = {
                        (artist_key, titel_key)
                        for _, artist_key, titel_key in file_song_map
                    }
                fetch_providers_normal(conn, scope=scope)
            elif phase == 3:
                fetch_providers_nachhol(conn)
            else:
                _PHASE_DISPATCH[phase]()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
