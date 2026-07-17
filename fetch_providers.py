#!/usr/bin/env python3
"""Anbieter-Abfrage der Songtexte-Pipeline (--abfragen + --nachholen).

Zwei Modi (siehe "workflow für songexte.txt", Abschnitt ZIELARCHITEKTUR):
  - Normal-Modus (fetch_all, --abfragen): fragt Songs aus der Tabelle "songs"
    (aus Meilenstein 1/scan_songs.py befüllt) bei allen 4 Anbietern
    (lrclib, musixmatch, netease, genius) ab -- optional eingegrenzt auf
    einen `scope` (siehe dortiger Docstring; ohne scope: JEDER Song in der
    Tabelle, also die komplette Cache-DB).
  - Nachhol-Modus (retry_missing, --nachholen): fragt gezielt nur (Song,
    Provider)-Kombinationen mit status IN ('nichts', 'fehlschlag') erneut ab
    -- optional eingegrenzt auf denselben `scope` wie der Normal-Modus (ohne
    scope: ganze Cache-DB, siehe dortiger Docstring).

Beide Modi bauen auf lyrics_core._query_provider auf (Rate-Limit-Handling,
lrclib-Dump-Lookup, Cache-Schreiblogik -- siehe dortiger Docstring) statt
diese ausgereifte Logik zu duplizieren -- gleiches Prinzip wie scan_songs.py
(Phase 1), das lyrics_core._read_audio_tags wiederverwendet. Der
Nachhol-Modus ruft dafür direkt lyrics_core._retry_missing auf, die diese
Abfrage-Eingrenzung schon fertig implementiert.

Bekannte, bereits akzeptierte Einschränkung (siehe lyrics_core.
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
from pathlib import Path

import cache_store
import lyrics_core


def _prepare_lyrics_core_globals(conn: sqlite3.Connection) -> None:
    """Setzt die Modul-Globals in lyrics_core, die _query_provider/
    _retry_missing brauchen -- repliziert das Setup, das früher
    fetch_songtext.main() vor dem --retry-missing-Zweig übernahm (siehe
    Git-Historie): eigene Cache-Connection setzen, TTL, lokalen LRCLib-
    Datenbank-Abzug öffnen (reiner Beschleuniger, degradiert still bei
    Fehlern -- siehe lyrics_core._open_lrclib_dump_conn).

    _cache_refresh/_cache_only werden explizit auf False gesetzt (kein
    --force/--cache-only-Äquivalent in dieser Pipeline, YAGNI) statt sich auf
    die Modul-Defaults zu verlassen -- schützt vor Zustand, den ein früherer
    Aufruf im selben Prozess (z.B. ein Test) stehen gelassen hat.
    """
    lyrics_core._cache_conn = conn
    lyrics_core._cache_ttl_days = cache_store.DEFAULT_TTL_DAYS
    lyrics_core._cache_refresh = False
    lyrics_core._cache_only = False
    lyrics_core._lrclib_dump_conn = lyrics_core._open_lrclib_dump_conn(no_cache=False)


def fetch_all(
    conn: sqlite3.Connection,
    scope: set[tuple[str, str]] | None = None,
    file_order: list[tuple[Path, str, str]] | None = None,
    quiet: bool = False,
) -> tuple[int, int, int]:
    """Normal-Modus (--abfragen): fragt Songs aus "songs" bei allen 4 Anbietern
    gleichzeitig ab (ThreadPoolExecutor, analog zum früheren Provider-Block
    im alten fetch_songtext.fetch_lrc, siehe Git-Historie).

    scope=None (Standard) fragt JEDEN Song in "songs" ab -- die komplette
    Cache-DB, über alle jemals gescannten Alben hinweg. Das ist nur
    beabsichtigt, wenn wirklich die ganze Bibliothek nachgezogen werden soll
    (z.B. songtext_pipeline.py --abfragen ohne PFAD). Ist scope gesetzt (eine
    Menge von (artist_key, titel_key)-Paaren, siehe songtext_pipeline.main()),
    werden NUR Songs abgefragt, deren (artist_key, titel_key) darin
    enthalten ist -- alle anderen Zeilen aus "songs" werden komplett
    übersprungen, weder in `queried` noch in `skipped` gezählt (sie wurden ja
    gar nicht betrachtet). Behebt einen echten Bug aus einem Produktions-Lauf
    (ROADMAP.md): ohne scope fragte ein PFAD-Lauf über ein einzelnes Album
    versehentlich JEDE Song-Zeile der kompletten, über Jahre gewachsenen
    Cache-DB live ab, statt nur die Songs des aktuellen Albums.

    Jedes Ergebnis landet als Seiteneffekt INNERHALB von _query_provider in
    der Cache-DB (Tabellen "ergebnisse"/"texte") -- die zurückgegebenen
    temporären .lrc-Pfade werden hier nur gelöscht, nicht weiterverwendet
    (Phase 2 speichert nur; Phase 4 entscheidet später anhand der DB).

    Pro Song werden NUR die Anbieter tatsächlich angefragt (submitted an den
    ThreadPoolExecutor), die WEDER einen gecachten status="fehlschlag" (siehe
    ROADMAP.md, Nachtrag "Phase 2 soll fehlschlag-Einträge nicht automatisch
    mit-retryen" -- das ist exklusiv die Aufgabe von retry_missing, "--nachholen")
    NOCH einen noch gültigen (nicht abgelaufenen) Treffer/Nichts-Eintrag haben
    (cache_store.get_provider() prüft TTL und Fehlschlag-Status in einem Zug).
    Vorher wurde HIER zwar der Fehlschlag-Fall schon ausgefiltert, ein noch
    gültiger Treffer/Nichts-Eintrag aber trotzdem an _query_provider
    weitergereicht -- kein doppelter Netzwerk-Aufwand (der interne
    Cache-Lookup dort griff ja), aber ein IRREFÜHRENDER Konsolen-Auftritt:
    ein reiner Wiederholungslauf über eine bereits vollständig gecachte
    Bibliothek zeigte "Frage N Song(s) bei 4 Anbietern ab ..." und pro Song
    eine Treffer-Zeile, obwohl buchstäblich keine einzige Live-Anfrage
    stattfand (live an einem echten Wiederholungslauf bestätigt, siehe
    ROADMAP.md). Songs, für die dadurch KEIN Anbieter mehr übrig bleibt,
    werden jetzt komplett übersprungen -- weder Executor-Aufruf noch
    Konsolenzeile, gezählt in der neuen dritten Rückgabe (siehe unten).

    Genre-Filter (übernommen aus dem früheren fetch_songtext.main(), siehe
    Git-Historie): ein Song, dessen gespeichertes Genre eines der
    lyrics_core._SKIP_GENRE_KEYWORDS enthält (Hörbuch, Hörspiel,
    Instrumental, Podcast, ...), wird komplett übersprungen -- keine
    einzige Anbieter-Anfrage. Spart unnötige, ratenlimitierte Live-Abfragen
    für Songs, bei denen von vornherein feststeht, dass kein Songtext zu
    erwarten ist. `genre` kann in der DB NULL sein (kein Tag vorhanden) --
    lyrics_core._is_skip_genre erwartet einen String (ruft intern
    .lower() auf und würde bei None abstürzen), deshalb wird die Prüfung
    nur bei gesetztem genre ausgeführt.

    Fortschrittsanzeige (nach dem in lyrics_core._retry_missing etablierten
    Muster, siehe dortiger Aufruf von _print_status/_tprint): ohne sie wirkt
    ein Lauf mit vielen Songs und mehrsekündigen Live-Timeouts (bis zu
    lyrics_core._PROVIDER_TIMEOUT pro Anfrage) wie ein Hänger -- reale
    Nutzer-Rückmeldung nach einem Produktions-Lauf (ROADMAP.md). Vor der
    Schleife eine Zeile mit der Gesamtzahl der tatsächlich abzufragenden
    Songs (Skip-Genre-Songs schon herausgerechnet), pro Song eine
    überschreibbare Statuszeile (_print_status) VOR der Provider-Abfrage,
    danach eine persistente Ergebniszeile (_tprint) mit Treffer-
    Zusammenfassung -- Vorbild ist die prov_str/hit_str-Logik aus dem
    früheren fetch_songtext.fetch_lrc (siehe Git-Historie). Skip-Genre-Songs
    bekommen keine eigene Zeile (stehen schon in der Abschluss-
    Zusammenfassung des Aufrufers), nur tatsächlich abgefragte.

    Gibt (Anzahl tatsächlich abgefragter Songs, Anzahl wegen Genre
    übersprungener Songs, Anzahl bereits vollständig aktueller Songs) zurück
    -- Songs außerhalb von scope zählen in keinem der drei.

    file_order: optional die (Pfad, artist_key, titel_key)-Liste aus
    songtext_pipeline.build_file_song_map (bereits in Datei-/Verzeichnis-
    Reihenfolge) -- ist sie gesetzt, wird in GENAU dieser Reihenfolge
    iteriert (dedupliziert, mehrere Dateien können auf denselben Song
    zeigen) statt alphabetisch nach Künstler/Titel, und die Konsolenzeilen
    zeigen den Dateinamen statt "artist_key / titel_key" (Nutzer-Feedback:
    die Ausgabe soll sich mit der Tracklist im Ordner decken). `scope` wird
    dabei ignoriert -- file_order deckt dieselbe Eingrenzung bereits ab.
    Ohne file_order (kein PFAD) bleibt es bei der alphabetischen
    DB-Reihenfolge + `scope`-Filter wie bisher.

    quiet=True unterdrückt die Kopfzeile ("Frage N Song(s) ab ...") und die
    persistente Treffer-Zeile pro Song -- gedacht für den kombinierten
    Datei-für-Datei-Lauf aus songtext_pipeline.py, wo ohnehin gleich danach
    --bewerten/--schreiben für denselben Song laufen und deren EINE
    Abschlusszeile (siehe write_lrc.write_all) sonst von einer fast
    identischen Zwischenzeile hier verdoppelt würde (Nutzer-Feedback: "zeig
    auf trackebene [...] pro track eine zeile", siehe ROADMAP.md). Die
    überschreibbare Statuszeile (_print_status) bleibt auch in quiet=True
    bestehen -- sie ist ohnehin transient und gibt bei einer länger
    laufenden Anfrage weiterhin Lebenszeichen.
    """
    _prepare_lyrics_core_globals(conn)
    env = lyrics_core._load_env()

    if file_order is not None:
        seen: set[tuple[str, str]] = set()
        rows: list[tuple[int, str, str, str | None, Path | None]] = []
        for path, artist_key, titel_key in file_order:
            if (artist_key, titel_key) in seen:
                continue
            seen.add((artist_key, titel_key))
            row = conn.execute(
                "SELECT id, genre FROM songs WHERE artist_key=? AND titel_key=?",
                (artist_key, titel_key),
            ).fetchone()
            if row is None:
                continue
            song_id, genre = row
            rows.append((song_id, artist_key, titel_key, genre, path))
    else:
        rows = [
            (song_id, artist_key, titel_key, genre, None)
            for song_id, artist_key, titel_key, genre in conn.execute(
                "SELECT id, artist_key, titel_key, genre FROM songs "
                "ORDER BY artist_key, titel_key"
            ).fetchall()
            if scope is None or (artist_key, titel_key) in scope
        ]

    # Erster Durchgang: pro Song die Anbieter bestimmen, die WIRKLICH
    # angefragt werden müssen (siehe Docstring oben) -- schon HIER, nicht
    # erst in der Schleife, damit "Frage N Song(s) ab" von vornherein nur
    # Songs mit echtem Anfragebedarf zählt, statt hinterher falsch zu wirken.
    to_query: list[tuple[int, str, str, Path | None, list[str]]] = []
    skipped_genre = 0
    skipped_up_to_date = 0
    for song_id, artist_key, titel_key, genre, audio_path in rows:
        if genre and lyrics_core._is_skip_genre(genre):
            skipped_genre += 1
            continue

        failed_providers = {
            quelle
            for (quelle,) in conn.execute(
                "SELECT quelle FROM ergebnisse WHERE song_id=? AND status='fehlschlag'",
                (song_id,),
            ).fetchall()
        }
        providers_to_ask = [
            p
            for p in lyrics_core._ALL_PROVIDERS
            if p not in failed_providers
            and cache_store.get_provider(
                conn, p, artist_key, titel_key, ttl_days=lyrics_core._cache_ttl_days
            )
            is None
        ]
        if not providers_to_ask:
            skipped_up_to_date += 1
            continue
        to_query.append((song_id, artist_key, titel_key, audio_path, providers_to_ask))

    total = len(to_query)
    if total and not quiet:
        print(
            f"Frage {total} Song(s) bei {len(lyrics_core._ALL_PROVIDERS)} "
            "Anbietern ab ..."
        )

    for i, (song_id, artist_key, titel_key, audio_path, providers_to_ask) in enumerate(
        to_query, start=1
    ):
        # Anzeige: Dateiname wenn vorhanden (Nutzer-Feedback -- besser
        # nachvollziehbar als der normalisierte Cache-Schlüssel), sonst
        # Fallback auf "artist_key / titel_key" (globaler Lauf ohne PFAD).
        label = (
            audio_path.name if audio_path is not None else f"{artist_key} / {titel_key}"
        )
        # "i/total: " nur bei echten Mehrfach-Laeufen (Nutzer-Feedback: bei
        # total==1 -- dem Normalfall im kombinierten Datei-fuer-Datei-Lauf
        # aus songtext_pipeline.py -- ist "1/1:" reine Redundanz ohne Info).
        counter = f"{i}/{total}: " if total > 1 else ""
        lyrics_core._print_status(f"  {counter}{label} ...")
        query = f"{artist_key} {titel_key}".strip()

        results = {}
        with ThreadPoolExecutor(max_workers=len(providers_to_ask)) as pool:
            futures = [
                pool.submit(
                    lyrics_core._query_provider,
                    query,
                    provider,
                    env,
                    artist=artist_key,
                    title=titel_key,
                )
                for provider in providers_to_ask
            ]
            for future in as_completed(futures):
                try:
                    provider, tmp_path = future.result()
                except FileNotFoundError:
                    # syncedlyrics-Binary fehlt (z.B. falsches venv aktiv) --
                    # gilt fuer JEDEN weiteren Aufruf gleichermassen, deshalb
                    # nicht nur diesen einen Provider ueberspringen, sondern
                    # den ganzen Lauf sauber abbrechen (wie im frueheren
                    # fetch_songtext.fetch_lrc, siehe Git-Historie). Bereits
                    # von anderen Providern geschriebene Temp-.lrc-Dateien
                    # zuerst aufraeumen, sonst Leak.
                    for path in results.values():
                        if path:
                            path.unlink(missing_ok=True)
                    raise
                results[provider] = tmp_path

        provider_hits = []
        for provider in lyrics_core._ALL_PROVIDERS:  # Reihenfolge beibehalten
            tmp_path = results.get(provider)
            if tmp_path is not None:
                provider_hits.append(provider)
                tmp_path.unlink(missing_ok=True)

        if not quiet:
            hit_str = ", ".join(provider_hits) if provider_hits else "—"
            lyrics_core._tprint(
                f"{lyrics_core._ts()}  {label}  "
                f"{len(provider_hits)}/{len(lyrics_core._ALL_PROVIDERS)}: {hit_str}"
            )

    return total, skipped_genre, skipped_up_to_date


def retry_missing(
    conn: sqlite3.Connection,
    providers: list[str] | None = None,
    scope: set[tuple[str, str]] | None = None,
) -> None:
    """Nachhol-Modus: dünner Wrapper um lyrics_core._retry_missing -- die
    dort schon fertige Logik (Eingrenzung auf status IN ('nichts',
    'fehlschlag'), Rate-Limit-Handling, Cache-Schreiblogik, siehe deren
    Docstring) wird unverändert wiederverwendet, inklusive deren eigener
    Erfolgs-/Fehlschlag-Zusammenfassung auf stdout.

    providers=None fragt alle 4 Anbieter ab (Standard aus dem Steuer-Skript).

    scope wie bei fetch_all (Menge von (artist_key, titel_key), siehe
    dortiger Docstring): None = keine Eingrenzung, ganze Cache-DB (Standard
    ohne PFAD). Ist scope gesetzt, wird er zu einer Liste von Song-IDs
    aufgelöst und an lyrics_core._retry_missing durchgereicht (dessen
    song_ids-Parameter) -- macht "nur fehlende Anbieter für DIESEN Ordner
    nachholen" möglich, was vorher (YAGNI-Notiz, jetzt überholt) bewusst
    nicht ging: songtext_pipeline.py übersprang den Nachhol-Modus bislang
    komplett, sobald ein PFAD gesetzt war (siehe ROADMAP.md).
    """
    _prepare_lyrics_core_globals(conn)
    song_ids: list[int] | None = None
    if scope is not None:
        song_ids = []
        for artist_key, titel_key in scope:
            row = conn.execute(
                "SELECT id FROM songs WHERE artist_key=? AND titel_key=?",
                (artist_key, titel_key),
            ).fetchone()
            if row is not None:
                song_ids.append(row[0])
    lyrics_core._retry_missing(
        providers if providers is not None else lyrics_core._ALL_PROVIDERS,
        None,
        None,
        song_ids=song_ids,
    )
