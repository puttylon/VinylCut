"""Tests für scan_songs.py (Phase 1 der Songtexte-Pipeline, Meilenstein 1)."""

import cache_store as cs
import scan_songs


def _make_audio_file(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"")
    return path


def test_scan_liest_tags_und_traegt_song_ein(tmp_path, monkeypatch):
    db_path = tmp_path / "cache.db"
    conn = cs.open_cache(db_path)

    _make_audio_file(tmp_path, "01 - Naturtraene.flac")
    monkeypatch.setattr(
        scan_songs.fetch_songtext,
        "_read_audio_tags",
        lambda path: ("Nina Hagen", "Naturtraene", "Punk"),
    )

    count = scan_songs.scan(tmp_path, recursive=False, conn=conn)

    assert count == 1
    row = conn.execute("SELECT artist_key, titel_key, genre FROM songs").fetchone()
    assert row == (
        cs.normalize_key("Nina Hagen"),
        cs.normalize_key("Naturtraene"),
        "Punk",
    )


def test_scan_ueberspringt_datei_ohne_tags(tmp_path, monkeypatch):
    db_path = tmp_path / "cache.db"
    conn = cs.open_cache(db_path)

    _make_audio_file(tmp_path, "01 - NoTags.flac")
    monkeypatch.setattr(
        scan_songs.fetch_songtext, "_read_audio_tags", lambda path: ("", "", "")
    )

    count = scan_songs.scan(tmp_path, recursive=False, conn=conn)

    assert count == 0
    assert conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0] == 0


def test_scan_duplikate_erzeugen_nur_einen_songs_eintrag(tmp_path, monkeypatch):
    db_path = tmp_path / "cache.db"
    conn = cs.open_cache(db_path)

    file_a = _make_audio_file(tmp_path, "01 - Song.flac")
    file_b = _make_audio_file(tmp_path, "02 - Song Duplikat.flac")

    tags_by_path = {
        file_a: ("Artist", "Song", "Rock"),
        file_b: ("Artist", "Song", "Rock"),
    }
    monkeypatch.setattr(
        scan_songs.fetch_songtext,
        "_read_audio_tags",
        lambda path: tags_by_path[path],
    )

    count = scan_songs.scan(tmp_path, recursive=False, conn=conn)

    # beide Dateien werden verarbeitet (Rückgabewert zählt Dateien mit Tags) ...
    assert count == 2
    # ... landen aber wegen UNIQUE(artist_key, titel_key) in genau einer Zeile.
    rows = conn.execute("SELECT artist_key, titel_key FROM songs").fetchall()
    assert rows == [(cs.normalize_key("Artist"), cs.normalize_key("Song"))]


def test_scan_uebernimmt_genre(tmp_path, monkeypatch):
    db_path = tmp_path / "cache.db"
    conn = cs.open_cache(db_path)

    _make_audio_file(tmp_path, "01 - Song.flac")
    monkeypatch.setattr(
        scan_songs.fetch_songtext,
        "_read_audio_tags",
        lambda path: ("Artist", "Song", "Krautrock"),
    )

    scan_songs.scan(tmp_path, recursive=False, conn=conn)

    genre = conn.execute("SELECT genre FROM songs").fetchone()[0]
    assert genre == "Krautrock"


def test_scan_leeres_genre_wird_nicht_als_leerstring_gespeichert(tmp_path, monkeypatch):
    db_path = tmp_path / "cache.db"
    conn = cs.open_cache(db_path)

    _make_audio_file(tmp_path, "01 - Song.flac")
    monkeypatch.setattr(
        scan_songs.fetch_songtext,
        "_read_audio_tags",
        lambda path: ("Artist", "Song", ""),
    )

    scan_songs.scan(tmp_path, recursive=False, conn=conn)

    genre = conn.execute("SELECT genre FROM songs").fetchone()[0]
    assert genre is None


def test_scan_bereinigt_titel_klammerzusatz(tmp_path, monkeypatch):
    db_path = tmp_path / "cache.db"
    conn = cs.open_cache(db_path)

    _make_audio_file(tmp_path, "01 - Song Title (Live Version).flac")
    monkeypatch.setattr(
        scan_songs.fetch_songtext,
        "_read_audio_tags",
        lambda path: ("Artist", "Song Title (Live Version)", ""),
    )

    scan_songs.scan(tmp_path, recursive=False, conn=conn)

    titel_key = conn.execute("SELECT titel_key FROM songs").fetchone()[0]
    assert titel_key == cs.normalize_key("Song Title")


def test_scan_nur_artist_ohne_titel_wird_trotzdem_eingetragen(tmp_path, monkeypatch):
    db_path = tmp_path / "cache.db"
    conn = cs.open_cache(db_path)

    _make_audio_file(tmp_path, "01 - Song.flac")
    monkeypatch.setattr(
        scan_songs.fetch_songtext,
        "_read_audio_tags",
        lambda path: ("Artist", "", ""),
    )

    count = scan_songs.scan(tmp_path, recursive=False, conn=conn)

    assert count == 1
    row = conn.execute("SELECT artist_key, titel_key FROM songs").fetchone()
    assert row == (cs.normalize_key("Artist"), cs.normalize_key(""))


def test_scan_leerer_ordner_liefert_null(tmp_path):
    db_path = tmp_path / "cache.db"
    conn = cs.open_cache(db_path)

    count = scan_songs.scan(tmp_path, recursive=False, conn=conn)

    assert count == 0
    assert conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0] == 0
