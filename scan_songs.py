#!/usr/bin/env python3
"""Phase 1 der Songtexte-Pipeline: Audiodateien scannen, Song-Identität eintragen.

Geht die Audiodateien im gewählten Umfang durch (siehe _iter_audio_files),
liest Künstler/Titel/Genre-Tags (lyrics_core._read_audio_tags) und trägt
jede Song-Identität in die Tabelle "songs" ein (cache_store._get_or_create_song).
Dateien ohne lesbare Tags (weder Artist noch Titel) werden übersprungen -- kein
DB-Eintrag, genau wie im bisherigen Verhalten im alten fetch_songtext.py.

Bewusst OHNE Songdauer: die Tabelle "songs" hat keine Dauer-Spalte (siehe
cache_store.py, Schema-Dokumentation) -- eine Erweiterung dafür wäre eine
Schema-Änderung, die (Stand Meilenstein 1) nicht abgesegnet ist. _load_release
(liest Dauer aus release.json) wird deshalb hier bewusst NICHT eingebunden.

Wird von songtext_pipeline.py für --scan aufgerufen (siehe dort, main()).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

import cache_store
import lyrics_core


def _iter_audio_files(root: Path, recursive: bool) -> Iterable[Path]:
    """Liefert die Audiodateien unter root: einzelne Datei, ein Ordner (nur
    oberste Ebene) oder rekursiv via lyrics_core._iter_audio_dfs.

    Spiegelt die Pfad-Logik aus dem alten fetch_songtext.py (Datei/Album/
    rekursiv). Wird auch von songtext_pipeline.build_file_song_map
    wiederverwendet -- lebt hier, damit songtext_pipeline.py diese Phase
    importieren kann, ohne einen Zirkelimport zu erzeugen.
    """
    if root.is_file():
        if root.suffix.lower() in lyrics_core._AUDIO_EXTENSIONS:
            return [root]
        return []
    if recursive:
        return lyrics_core._iter_audio_dfs(root)
    return sorted(
        p for p in root.glob("*") if p.suffix.lower() in lyrics_core._AUDIO_EXTENSIONS
    )


def scan(root: Path, recursive: bool, conn: sqlite3.Connection) -> int:
    """Scannt Audiodateien unter root und trägt jeden Song in "songs" ein.

    Titel-Normalisierung wie beim bestehenden Anlegen von songs-Zeilen: erst
    _clean_query_title auf den Titel, dann cache_store.normalize_key --
    dieselbe Normalisierung wie in songtext_pipeline.build_file_song_map,
    sonst laufen Scan-Ergebnis und die dortige Datei-Zuordnung auseinander.

    Ein leerer Genre-Tag ("" von _read_audio_tags) wird als None übergeben,
    nicht als leerer String -- sonst würde _get_or_create_song das leere
    Genre dauerhaft festschreiben (COALESCE greift nur bei NULL) und ein
    später gefundenes echtes Genre nie mehr übernehmen.

    Gibt die Anzahl der Dateien mit lesbaren Tags zurück (= Anzahl
    neuer/aktualisierter songs-Einträge). Zwei Dateien mit identischem
    Künstler/Titel zählen beide einzeln, landen aber wegen des
    UNIQUE-Constraints in genau einer songs-Zeile.
    """
    count = 0
    for audio_path in _iter_audio_files(root, recursive):
        artist, title, genre = lyrics_core._read_audio_tags(audio_path)
        if not artist and not title:
            continue
        clean_title = lyrics_core._clean_query_title(title) if title else title
        artist_key = cache_store.normalize_key(artist)
        titel_key = cache_store.normalize_key(clean_title)
        cache_store._get_or_create_song(conn, artist_key, titel_key, genre or None)
        conn.commit()
        count += 1
    return count
