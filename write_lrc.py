#!/usr/bin/env python3
"""Phase 5 der Songtexte-Pipeline: .lrc schreiben/löschen, JSON-Ordner-Cache pflegen.

Ruft für jeden Song evaluate_lyrics.evaluate_song() (Phase 4) direkt auf, um
die Entscheidung zu bekommen -- KEIN eigener Ablageort in der Datenbank nötig
(siehe "workflow für songexte.txt", Abschnitt 2: die Entscheidung ist ein
reiner Vergleich bereits gespeicherter Daten, deterministisch reproduzierbar
egal ob im selben Lauf oder Tage später separat aufgerufen).

Übernimmt das Schreib-/Vergleichsverhalten sowie den JSON-Ordner-Cache
(.fetch_songtext.json -- Dateiname bewusst unverändert, damit bestehende
Caches in der Bibliothek weiter funktionieren) und die Ordner-Sperre
unverändert aus dem früheren fetch_songtext.main() (siehe Git-Historie) --
gleiche Cache-Eintrag-Struktur (v/r/outcome/providers/...), damit bestehende
Werkzeuge (lrc_analyse.py, lrc_recheck.py) den Cache weiter lesen können.

Bekannter, akzeptierter Unterschied zum alten fetch_songtext.py: der explizite
Genre-Skip (Hörbuch/Hörspiel/...) mit eigenem "reason": "genre" passiert jetzt
schon in Phase 2 (siehe fetch_providers.fetch_all) -- ein Skip-Genre-Song hat
hier deshalb einfach keine Provider-Kandidaten und landet als "kein-provider".
Funktional gleichwertig (kein falscher Songtext, eine vorhandene .lrc wird
trotzdem gelöscht), nur die berichtete Ursache im Cache-Eintrag unterscheidet
sich.

Bindeglied zum JSON-Ordner-Cache (siehe ROADMAP.md, Songtexte-Pipeline-Umbau,
Nachtrag "Kein Bindeglied zwischen JSON-Cache und SQLite-Cache", live
bestätigt an einem Produktions-Lauf): lyrics_core._cache_entry_valid() prüft
nur die Skript-Version, kein TTL -- ein einmal geschriebener JSON-Eintrag
gilt sonst FÜR IMMER als aktuell, auch wenn Phase "bewerten"/"nachholen"
seitdem etwas Neues in der DB gefunden haben. lyrics_core.
_db_newer_than_json_entry() (gemeinsam mit evaluate_lyrics.py genutzt, siehe
dortiger Skip) vergleicht deshalb vor jedem Skip den JSON-Eintrags-
Zeitstempel mit dem jüngsten DB-Zeitstempel für diesen Song
(cache_store.latest_result_timestamp) -- ist die DB neuer, wird trotz
gültigem JSON-Eintrag neu bewertet.
"""

from __future__ import annotations

import sqlite3
import unicodedata
from pathlib import Path

import evaluate_lyrics
import fetch_providers
import lyrics_core


def write_all(
    conn: sqlite3.Connection,
    file_song_map: list[tuple[Path, str, str]],
    force: bool = False,
    quiet: bool = False,
    external_lock: object | None = None,
) -> dict[str, int]:
    """Phase 5: schreibt/löscht .lrc je Song aus file_song_map (siehe
    songtext_pipeline.build_file_song_map), gruppiert nach Ordner für
    Ordner-Sperre + JSON-Cache -- exakt wie main()s Datei-Schleife.

    force=True ignoriert den JSON-Cache-Skip (wie --force im alten Skript).

    Bereitet dieselben lyrics_core-Modul-Globals vor wie Phase 4 (siehe
    fetch_providers._prepare_lyrics_core_globals) -- notwendig, damit
    evaluate_lyrics.evaluate_song() bei einem eigenständigen --schreiben-Lauf
    (ohne vorheriges Phase 4 im selben Prozess) den Whisper-Transkript-Cache
    findet, statt jeden Song ohne Cache neu zu transkribieren.

    quiet=True unterdrückt NUR die Kopfzeile ("Schreibe/prüfe N Datei(en)
    ...") -- gedacht für den kombinierten Datei-für-Datei-Lauf aus
    songtext_pipeline.py, wo diese Zeile bei N=1 nichts beiträgt (Nutzer-
    Feedback: "zeig auf trackebene [...] pro track eine zeile", siehe
    ROADMAP.md). Die persistente Ergebniszeile pro Song (unten, `_tprint`)
    bleibt IMMER bestehen -- das ist die eine gewollte Zeile pro Track,
    äquivalent zur Abschlusszeile des früheren fetch_songtext.py.

    external_lock (siehe ROADMAP.md Punkt 4): songtext_pipeline.main()s
    Datei-Schleife beansprucht die Ordner-Sperre inzwischen selbst VOR dem
    ersten Schritt (scan/abfragen/bewerten), damit sie auch diese Schritte
    mitschützt, nicht mehr nur das Schreiben. Ist external_lock gesetzt,
    verwendet write_all() GENAU diese bereits gehaltene Sperre weiter (kein
    zweiter _try_claim_folder-Versuch -- ein Prozess kann sich mit flock()
    selbst aussperren, weil die Sperre an die offene Dateibeschreibung
    gebunden ist, nicht an den Prozess) und gibt sie am Ende NICHT frei --
    das bleibt Aufgabe von main()s Datei-Schleife, die sie beim nächsten
    Ordnerwechsel bzw. am Laufende löst. Ohne external_lock (Standalone-
    Aufruf, z.B. direkt in Tests oder mit einem file_song_map über mehrere
    Ordner) bleibt das bisherige Verhalten unverändert: write_all()
    beansprucht/löst die Sperre selbst, pro Ordnerwechsel innerhalb dieser
    Funktion.
    """
    fetch_providers._prepare_lyrics_core_globals(conn)
    counts = {"updated": 0, "skipped": 0, "not_found": 0, "errors": 0}
    total = len(file_song_map)
    if total and not quiet:
        print(f"Schreibe/prüfe {total} Datei(en) ...")

    current_parent: Path | None = None
    dir_cache: dict = {}
    folder_lock: "object | None" = external_lock

    for i, (audio_path, artist_key, titel_key) in enumerate(file_song_map, start=1):
        lrc_path = audio_path.with_suffix(".lrc")
        cache_key = unicodedata.normalize("NFC", audio_path.name)

        if audio_path.parent != current_parent:
            current_parent = audio_path.parent
            if external_lock is None:
                lyrics_core._release_folder(folder_lock)
                folder_lock = lyrics_core._try_claim_folder(audio_path.parent)
                if folder_lock is lyrics_core._FOLDER_BUSY:
                    lyrics_core._print_status(
                        f"  Übersprungen (andere Instanz aktiv): {audio_path.parent}"
                    )
                    continue
            dir_cache = lyrics_core._load_cache(audio_path.parent)
        elif external_lock is None and folder_lock is lyrics_core._FOLDER_BUSY:
            continue

        if not force:
            entry = dir_cache.get(cache_key)
            if lyrics_core._cache_entry_up_to_date(
                entry, lrc_path, conn, artist_key, titel_key
            ):
                counts["skipped"] += 1
                continue
            # Sig-Backfill (siehe lyrics_core._sig_backfill-Docstring): fehlt
            # nur die "sig" (reine Migration, kein echter Genre-Wechsel),
            # wird sie hier -- ohne Neubewertung -- nachgetragen und
            # dauerhaft gespeichert, statt den Song unnoetig neu abzufragen/
            # zu whispern (Nutzer-Feedback: "kein Mehrwert fuer die lrc").
            backfill_sig = lyrics_core._sig_backfill(entry, conn, artist_key, titel_key)
            if backfill_sig is not None:
                entry["sig"] = backfill_sig
                dir_cache[cache_key] = entry
                lyrics_core._save_cache(
                    audio_path.parent, dir_cache, lockfile=folder_lock
                )
                counts["skipped"] += 1
                continue

        # "i/total: " nur bei echten Mehrfach-Laeufen (siehe fetch_providers.py,
        # gleiche Begruendung: bei total==1 reine Redundanz ohne Info).
        counter = f"{i}/{total}: " if total > 1 else ""
        lyrics_core._print_status(f"  {counter}{audio_path.name} ...")

        expected_dur = evaluate_lyrics._resolve_expected_dur(audio_path)
        existing_lrc = lrc_path if lrc_path.exists() else None
        found, info_str, extras = evaluate_lyrics.evaluate_song(
            conn, artist_key, titel_key, audio_path, expected_dur, existing_lrc
        )
        new_content = extras.pop("content", None)

        if not found and extras.get("existing_best") and lrc_path.exists():
            # Bugfix (siehe ROADMAP.md): existing_best ist heute nur noch
            # True, wenn es KEINE Audiodatei fuer einen Gegenbeweis gab --
            # ein Whisper-Verdikt (kein-vokal/unter-schwelle) ist sonst immer
            # final, auch fuer eine bereits vorhandene Datei ("Pohlmann-
            # Fall": eine Datei ohne jede Konkurrenz "gewann" frueher
            # automatisch, obwohl ihr Score katastrophal niedrig war).
            extras["outcome"] = "keep"
            lyrics_core._tprint(
                f"{lyrics_core._ts()}  {audio_path.name}  {info_str}  ="
            )
            counts["skipped"] += 1
            cache_result = "ok"
        elif not found:
            had_lrc = lrc_path.exists()
            lrc_path.unlink(missing_ok=True)
            extras["outcome"] = "delete" if had_lrc else "none"
            lyrics_core._tprint(
                f"{lyrics_core._ts()}  {audio_path.name}  {info_str}  "
                f"{'–' if had_lrc else '='}"
            )
            counts["not_found"] += 1
            cache_result = "nf"
        else:
            old_content = lrc_path.read_bytes() if lrc_path.exists() else None
            if old_content == new_content:
                extras["outcome"] = "none"
                lyrics_core._tprint(
                    f"{lyrics_core._ts()}  {audio_path.name}  {info_str}  ="
                )
                counts["skipped"] += 1
            else:
                lrc_path.write_bytes(new_content)
                extras["outcome"] = "write"
                lyrics_core._tprint(
                    f"{lyrics_core._ts()}  {audio_path.name}  {info_str}  ✓"
                )
                counts["updated"] += 1
            cache_result = "ok"

        # Gemeinsame Stelle mit cut.py fuer den Cache-Eintrag (siehe
        # lyrics_core._build_cache_entry-Docstring, ROADMAP.md) -- baut u.a.
        # "ts" aus dem DB-Zeitstempel statt der Wanduhr-Zeit.
        dir_cache[cache_key] = lyrics_core._build_cache_entry(
            conn, artist_key, titel_key, cache_result, extras
        )
        lyrics_core._save_cache(audio_path.parent, dir_cache, lockfile=folder_lock)

    if external_lock is None:
        lyrics_core._release_folder(folder_lock)
    return counts
