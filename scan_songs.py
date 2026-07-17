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
from collections.abc import Iterable, Iterator
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


def _read_tagged_files(
    root: Path, recursive: bool
) -> Iterator[tuple[Path, str, str, str]]:
    """Liest Audiodateien unter root PRO DATEI ein: Pfad + rohe
    Artist/Titel/Genre-Tags (lyrics_core._read_audio_tags) -- als Generator,
    liefert also die erste Datei, sobald sie gefunden ist, statt erst den
    ganzen Baum fertig einzulesen.

    Der Verzeichnis-Walk + das Tag-Lesen sind bei einer großen (insbesondere
    netzwerk-gemounteten) Bibliothek der eigentlich teure Teil -- teurer als
    die anschließenden reinen DB-Abgleiche. scan()/songtext_pipeline.
    build_file_song_map() riefen früher beide unabhängig voneinander
    _iter_audio_files()+_read_audio_tags() auf, songtext_pipeline.main()
    zusätzlich mehrfach für scan/abfragen/bewerten/schreiben -- macht bis zu
    sechs volle Durchläufe desselben Baums in einem einzigen Lauf (siehe
    ROADMAP.md). Diese Funktion wird jetzt GENAU EINMAL pro
    songtext_pipeline.py-Lauf aufgerufen (jede Datei wird dabei trotzdem nur
    einmal getaggt), das Ergebnis an scan()/build_file_song_map()
    durchgereicht (deren `files`-Parameter) statt erneut zu scannen.

    War bis zum Datei-für-Datei-Umbau (siehe ROADMAP.md) eine Liste --
    songtext_pipeline.main() sammelte den kompletten Baum VOR dem ersten
    verarbeiteten Track ein, das wirkte bei einer großen Bibliothek wie ein
    Hänger (Nutzer-Feedback: "Programm startet trotzdem mit einem großen
    Scan über alle Verzeichnisse. Muss das sein?"). Als Generator beginnt
    die Verarbeitung stattdessen sofort bei der ersten gefundenen Datei --
    bewusster Trade-off: die Gesamtzahl der Dateien ist vor Laufende nicht
    mehr bekannt, es gibt deshalb keine "N Datei(en) gefunden."-Zeile vorab
    mehr (siehe songtext_pipeline.main()).
    """
    for audio_path in _iter_audio_files(root, recursive):
        yield (audio_path, *lyrics_core._read_audio_tags(audio_path))


def scan(
    root: Path,
    recursive: bool,
    conn: sqlite3.Connection,
    files: list[tuple[Path, str, str, str]] | None = None,
) -> int:
    """Scannt Audiodateien unter root und trägt jeden Song in "songs" ein.

    Titel-Normalisierung wie beim bestehenden Anlegen von songs-Zeilen: erst
    _clean_query_title auf den Titel, dann cache_store.normalize_key --
    dieselbe Normalisierung wie in songtext_pipeline.build_file_song_map,
    sonst laufen Scan-Ergebnis und die dortige Datei-Zuordnung auseinander.

    Ein leerer Genre-Tag ("" von _read_audio_tags) wird als None übergeben,
    nicht als leerer String -- sonst würde _get_or_create_song das leere
    Genre dauerhaft festschreiben (COALESCE greift nur bei NULL) und ein
    später gefundenes echtes Genre nie mehr übernehmen.

    files: optional vorab per _read_tagged_files() eingelesene Liste --
    erspart einen erneuten Verzeichnis-Walk + Tag-Read, wenn der Aufrufer
    (siehe songtext_pipeline.main()) die Dateien im selben Lauf schon einmal
    gelesen hat. None (Standard, z.B. bei eigenständiger Nutzung dieses
    Moduls) liest wie bisher selbst ein.

    Gibt die Anzahl der Dateien mit lesbaren Tags zurück (= Anzahl
    neuer/aktualisierter songs-Einträge). Zwei Dateien mit identischem
    Künstler/Titel zählen beide einzeln, landen aber wegen des
    UNIQUE-Constraints in genau einer songs-Zeile.
    """
    count = 0
    entries = files if files is not None else _read_tagged_files(root, recursive)
    for audio_path, artist, title, genre in entries:
        if not artist and not title:
            continue
        clean_title = lyrics_core._clean_query_title(title) if title else title
        artist_key = cache_store.normalize_key(artist)
        titel_key = cache_store.normalize_key(clean_title)
        cache_store._get_or_create_song(conn, artist_key, titel_key, genre or None)
        conn.commit()
        count += 1
    return count
