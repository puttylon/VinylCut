#!/usr/bin/env python3
"""Phase 2 (+ Nachhol-Modus als Phase 3) der Songtexte-Pipeline: Anbieter abfragen.

Zwei Modi (siehe "workflow für songexte.txt", Abschnitt ZIELARCHITEKTUR):
  - Normal-Modus (fetch_all, Phase 2): fragt für JEDEN Song in der Tabelle
    "songs" (aus Meilenstein 1/scan_songs.py befüllt) alle 4 Anbieter
    (lrclib, musixmatch, netease, genius) ab.
  - Nachhol-Modus (retry_missing, Phase 3): fragt gezielt nur (Song,
    Provider)-Kombinationen mit status IN ('nichts', 'fehlschlag') erneut ab.

Beide Modi bauen auf fetch_songtext._query_provider auf (Rate-Limit-Handling,
lrclib-Dump-Lookup, Cache-Schreiblogik -- siehe dortiger Docstring) statt
diese ausgereifte Logik zu duplizieren -- gleiches Prinzip wie scan_songs.py
(Phase 1), das fetch_songtext._read_audio_tags wiederverwendet. Der
Nachhol-Modus ruft dafür direkt fetch_songtext._retry_missing auf, die diese
Abfrage-Eingrenzung schon fertig implementiert.

Bekannte, bereits akzeptierte Einschränkung (siehe fetch_songtext.
_retry_missing-Docstring, "Bekannte Einschränkung"): die Suchanfrage wird aus
den normalisierten artist_key/titel_key gebaut, nicht aus der Original-
Schreibweise -- die Tabelle "songs" speichert nur die normalisierten
Schlüssel. Gilt hier bewusst 1:1 auch für den Normal-Modus.

Öffnet KEINE eigene Cache-Connection -- beide Funktionen bekommen die
bereits offene Connection von songtext_pipeline.main() übergeben (Stil wie
scan_songs.scan(root, recursive, conn)).
"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed

import cache_store
import fetch_songtext


def _prepare_fetch_songtext_globals(conn: sqlite3.Connection) -> None:
    """Setzt die Modul-Globals in fetch_songtext, die _query_provider/
    _retry_missing brauchen -- repliziert das Setup aus fetch_songtext.main()
    vor dem --retry-missing-Zweig (committete Version, Zeile ~2163-2178):
    eigene Cache-Connection setzen, TTL, lokalen LRCLib-Datenbank-Abzug öffnen
    (reiner Beschleuniger, degradiert still bei Fehlern -- siehe
    fetch_songtext._open_lrclib_dump_conn).

    _cache_refresh/_cache_only werden explizit auf False gesetzt (kein
    --force/--cache-only-Äquivalent in dieser Pipeline, YAGNI) statt sich auf
    die Modul-Defaults zu verlassen -- schützt vor Zustand, den ein früherer
    Aufruf im selben Prozess (z.B. ein Test) stehen gelassen hat.
    """
    fetch_songtext._cache_conn = conn
    fetch_songtext._cache_ttl_days = cache_store.DEFAULT_TTL_DAYS
    fetch_songtext._cache_refresh = False
    fetch_songtext._cache_only = False
    fetch_songtext._lrclib_dump_conn = fetch_songtext._open_lrclib_dump_conn(
        no_cache=False
    )


def fetch_all(conn: sqlite3.Connection) -> tuple[int, int]:
    """Normal-Modus (Phase 2): fragt für jeden Song in "songs" alle 4
    Anbieter gleichzeitig ab (ThreadPoolExecutor, analog zum Provider-Block
    in fetch_songtext.fetch_lrc, Zeile ~1348-1361 der committeten Version).

    Jedes Ergebnis landet als Seiteneffekt INNERHALB von _query_provider in
    der Cache-DB (Tabellen "ergebnisse"/"texte") -- die zurückgegebenen
    temporären .lrc-Pfade werden hier nur gelöscht, nicht weiterverwendet
    (Phase 2 speichert nur; Phase 4 entscheidet später anhand der DB).

    Songs mit bereits gültigem (nicht abgelaufenem) Cache-Ergebnis werden
    trotzdem angefragt -- _query_provider selbst erkennt den Cache-Treffer
    und überspringt dafür die Live-Abfrage (kein doppelter Netzwerk-Aufwand,
    siehe dortiger Docstring).

    Genre-Filter (übernommen aus dem committeten fetch_songtext.main(),
    Zeile ~2305): ein Song, dessen gespeichertes Genre eines der
    fetch_songtext._SKIP_GENRE_KEYWORDS enthält (Hörbuch, Hörspiel,
    Instrumental, Podcast, ...), wird komplett übersprungen -- keine
    einzige Anbieter-Anfrage. Spart unnötige, ratenlimitierte Live-Abfragen
    für Songs, bei denen von vornherein feststeht, dass kein Songtext zu
    erwarten ist. `genre` kann in der DB NULL sein (kein Tag vorhanden) --
    fetch_songtext._is_skip_genre erwartet einen String (ruft intern
    .lower() auf und würde bei None abstürzen), deshalb wird die Prüfung
    nur bei gesetztem genre ausgeführt.

    Gibt (Anzahl abgefragter Songs, Anzahl wegen Genre übersprungener Songs)
    zurück.
    """
    _prepare_fetch_songtext_globals(conn)
    env = fetch_songtext._load_env()
    rows = conn.execute(
        "SELECT artist_key, titel_key, genre FROM songs ORDER BY artist_key, titel_key"
    ).fetchall()

    queried = 0
    skipped = 0
    for artist_key, titel_key, genre in rows:
        if genre and fetch_songtext._is_skip_genre(genre):
            skipped += 1
            continue
        queried += 1
        query = f"{artist_key} {titel_key}".strip()
        with ThreadPoolExecutor(max_workers=len(fetch_songtext._ALL_PROVIDERS)) as pool:
            futures = [
                pool.submit(
                    fetch_songtext._query_provider,
                    query,
                    provider,
                    env,
                    artist=artist_key,
                    title=titel_key,
                )
                for provider in fetch_songtext._ALL_PROVIDERS
            ]
            for future in as_completed(futures):
                _provider, path = future.result()
                if path is not None:
                    path.unlink(missing_ok=True)

    return queried, skipped


def retry_missing(conn: sqlite3.Connection, providers: list[str] | None = None) -> None:
    """Nachhol-Modus (Phase 3): dünner Wrapper um fetch_songtext._retry_missing
    -- die dort schon fertige Logik (Eingrenzung auf status IN ('nichts',
    'fehlschlag'), Rate-Limit-Handling, Cache-Schreiblogik, siehe deren
    Docstring) wird unverändert wiederverwendet, inklusive deren eigener
    Erfolgs-/Fehlschlag-Zusammenfassung auf stdout.

    providers=None fragt alle 4 Anbieter ab (Standard für Phase 3 aus dem
    Steuer-Skript). Eine artist/title-Eingrenzung (wie bei fetch_songtext
    --retry-missing --artist/--title) bietet diese Funktion bewusst nicht an
    -- YAGNI, songtext_pipeline.py kennt für --phase 3 keinen Songs-Scope.
    """
    _prepare_fetch_songtext_globals(conn)
    fetch_songtext._retry_missing(
        providers if providers is not None else fetch_songtext._ALL_PROVIDERS,
        None,
        None,
    )
