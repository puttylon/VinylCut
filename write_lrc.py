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
seitdem etwas Neues in der DB gefunden haben. _db_newer_than_json_entry()
vergleicht deshalb vor jedem Skip den JSON-Eintrags-Zeitstempel mit dem
jüngsten DB-Zeitstempel für diesen Song (cache_store.latest_result_timestamp)
-- ist die DB neuer, wird trotz gültigem JSON-Eintrag neu bewertet.
"""

from __future__ import annotations

import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path

import cache_store
import evaluate_lyrics
import fetch_providers
import lyrics_core


def _db_newer_than_json_entry(
    conn: sqlite3.Connection, artist_key: str, titel_key: str, entry_ts: str | None
) -> bool:
    """True wenn die Cache-DB einen jüngeren Provider- oder Whisper-Datensatz
    für diesen Song hat als entry_ts (der "ts"-Wert des JSON-Ordner-Cache-
    Eintrags, lokale Zeit ohne Zeitzone -- siehe write_all weiter unten).

    entry_ts fehlt oder ist nicht parsbar -> konservativ True (nicht
    überspringen, lieber einmal zu oft neu bewerten als für immer eine
    veraltete Entscheidung stehen lassen). Kein DB-Datensatz für diesen Song
    -> False (nichts Neues, der bisherige Skip bleibt gültig).
    """
    if not entry_ts:
        return True
    try:
        entry_dt = datetime.fromisoformat(entry_ts).astimezone()
    except ValueError:
        return True

    db_ts = cache_store.latest_result_timestamp(conn, artist_key, titel_key)
    if db_ts is None:
        return False
    try:
        db_dt = datetime.fromisoformat(db_ts)
    except ValueError:
        return True

    return db_dt > entry_dt


def write_all(
    conn: sqlite3.Connection,
    file_song_map: list[tuple[Path, str, str]],
    force: bool = False,
) -> dict[str, int]:
    """Phase 5: schreibt/löscht .lrc je Song aus file_song_map (siehe
    songtext_pipeline.build_file_song_map), gruppiert nach Ordner für
    Ordner-Sperre + JSON-Cache -- exakt wie main()s Datei-Schleife.

    force=True ignoriert den JSON-Cache-Skip (wie --force im alten Skript).

    Bereitet dieselben lyrics_core-Modul-Globals vor wie Phase 4 (siehe
    fetch_providers._prepare_lyrics_core_globals) -- notwendig, damit
    evaluate_lyrics.evaluate_song() bei einem eigenständigen --phase 5-Lauf
    (ohne vorheriges Phase 4 im selben Prozess) den Whisper-Transkript-Cache
    findet, statt jeden Song ohne Cache neu zu transkribieren.
    """
    fetch_providers._prepare_lyrics_core_globals(conn)
    counts = {"updated": 0, "skipped": 0, "not_found": 0, "errors": 0}
    total = len(file_song_map)
    if total:
        print(f"Schreibe/prüfe {total} Datei(en) ...")

    current_parent: Path | None = None
    dir_cache: dict = {}
    folder_lock: "object | None" = None

    for i, (audio_path, artist_key, titel_key) in enumerate(file_song_map, start=1):
        lrc_path = audio_path.with_suffix(".lrc")
        cache_key = unicodedata.normalize("NFC", audio_path.name)

        if audio_path.parent != current_parent:
            lyrics_core._release_folder(folder_lock)
            current_parent = audio_path.parent
            folder_lock = lyrics_core._try_claim_folder(audio_path.parent)
            if folder_lock is lyrics_core._FOLDER_BUSY:
                lyrics_core._print_status(
                    f"  Übersprungen (andere Instanz aktiv): {audio_path.parent}"
                )
                continue
            dir_cache = lyrics_core._load_cache(audio_path.parent)
        elif folder_lock is lyrics_core._FOLDER_BUSY:
            continue

        if not force:
            entry = dir_cache.get(cache_key)
            if (
                entry
                and lyrics_core._cache_entry_valid(entry)
                and (entry.get("r") != "ok" or lrc_path.exists())
                and not _db_newer_than_json_entry(
                    conn, artist_key, titel_key, entry.get("ts")
                )
            ):
                counts["skipped"] += 1
                continue

        lyrics_core._print_status(f"  {i}/{total}: {audio_path.name} ...")

        expected_dur = evaluate_lyrics._resolve_expected_dur(audio_path)
        existing_lrc = lrc_path if lrc_path.exists() else None
        found, info_str, extras = evaluate_lyrics.evaluate_song(
            conn, artist_key, titel_key, audio_path, expected_dur, existing_lrc
        )
        new_content = extras.pop("content", None)

        if not found:
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

        dir_cache[cache_key] = {
            "v": lyrics_core.__version__,
            "r": cache_result,
            "ts": datetime.now().isoformat(timespec="seconds"),
            **extras,
        }
        lyrics_core._save_cache(audio_path.parent, dir_cache, lockfile=folder_lock)

    lyrics_core._release_folder(folder_lock)
    return counts
