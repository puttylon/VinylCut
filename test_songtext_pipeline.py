"""Tests für songtext_pipeline.py (Steuer-Skript-Grundgerüst, Meilenstein 0)."""

import pytest

import cache_store as cs
import songtext_pipeline


# --- _parse_phase_list -------------------------------------------------


def test_parse_phase_list_einzelwert():
    assert songtext_pipeline._parse_phase_list("3") == [3]


def test_parse_phase_list_mehrfachauswahl():
    assert songtext_pipeline._parse_phase_list("2,4,5") == [2, 4, 5]


def test_parse_phase_list_unsortiert_und_leerzeichen():
    assert songtext_pipeline._parse_phase_list(" 5, 2 ,4") == [2, 4, 5]


def test_parse_phase_list_dedupliziert():
    assert songtext_pipeline._parse_phase_list("2,2,4") == [2, 4]


@pytest.mark.parametrize("spec", ["0", "6", "abc", "", "2,9", "-1"])
def test_parse_phase_list_ungueltiger_wert_wirft_value_error(spec):
    with pytest.raises(ValueError):
        songtext_pipeline._parse_phase_list(spec)


# --- CLI: --phase-Auswahl und Platzhalter-Ausgabe -----------------------


def test_main_ohne_phase_aktiviert_alle_5(tmp_path, monkeypatch, capsys):
    # eigene DB in tmp_path -- sonst würde main() die echte Produktions-Cache-DB
    # öffnen (siehe _default_db_path).
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(songtext_pipeline, "_default_db_path", lambda: db_path)
    monkeypatch.setattr("sys.argv", ["songtext_pipeline.py", str(tmp_path)])
    songtext_pipeline.main()

    out = capsys.readouterr().out
    assert "Phase 1 (scan_songs): 0 Song(s) gescannt/aktualisiert." in out
    assert "Phase 2 (fetch_providers, Normal-Modus) würde hier laufen." in out
    assert "Phase 3 (fetch_providers, Nachhol-Modus) würde hier laufen." in out
    assert "Phase 4 (evaluate_lyrics) würde hier laufen." in out
    assert "Phase 5 (write_lrc) würde hier laufen." in out


def test_main_phase_3_funktioniert_ohne_pfad(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["songtext_pipeline.py", "--phase", "3"])
    songtext_pipeline.main()

    out = capsys.readouterr().out
    assert "Phase 3 (fetch_providers, Nachhol-Modus) würde hier laufen." in out
    assert "Phase 1" not in out
    assert "Phase 2" not in out
    assert "Phase 4" not in out
    assert "Phase 5" not in out


def test_main_phase_mehrfachauswahl_nur_gewaehlte_phasen(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv", ["songtext_pipeline.py", str(tmp_path), "--phase", "2,4,5"]
    )
    songtext_pipeline.main()

    out = capsys.readouterr().out
    assert "Phase 1" not in out
    assert "Phase 2 (fetch_providers, Normal-Modus) würde hier laufen." in out
    assert "Phase 3" not in out
    assert "Phase 4 (evaluate_lyrics) würde hier laufen." in out
    assert "Phase 5 (write_lrc) würde hier laufen." in out


def test_main_phase_1_ohne_pfad_meldet_und_ueberspringt(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["songtext_pipeline.py", "--phase", "1"])
    songtext_pipeline.main()

    out = capsys.readouterr().out
    assert "Phase 1 (scan_songs): kein PFAD angegeben, nichts zu scannen." in out


# --- --phase 1 Ende-zu-Ende: Song landet wirklich in der DB -------------


def test_main_phase_1_end_to_end_song_landet_in_songs_tabelle(
    tmp_path, monkeypatch, capsys
):
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(songtext_pipeline, "_default_db_path", lambda: db_path)

    audio_file = tmp_path / "01 - Naturtraene.flac"
    audio_file.write_bytes(b"")

    monkeypatch.setattr(
        songtext_pipeline.fetch_songtext,
        "_read_audio_tags",
        lambda path: ("Nina Hagen", "Naturtraene", "Punk"),
    )
    monkeypatch.setattr(
        "sys.argv", ["songtext_pipeline.py", str(tmp_path), "--phase", "1"]
    )

    songtext_pipeline.main()

    out = capsys.readouterr().out
    assert "Phase 1 (scan_songs): 1 Song(s) gescannt/aktualisiert." in out

    conn = cs.open_cache(db_path)
    row = conn.execute("SELECT artist_key, titel_key, genre FROM songs").fetchone()
    assert row == (
        cs.normalize_key("Nina Hagen"),
        cs.normalize_key("Naturtraene"),
        "Punk",
    )


def test_main_phase_ungueltiger_wert_exit_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv", ["songtext_pipeline.py", str(tmp_path), "--phase", "9"]
    )
    with pytest.raises(SystemExit) as exc_info:
        songtext_pipeline.main()
    assert exc_info.value.code == 2
    assert "Ungültige Phase" in capsys.readouterr().err


# --- build_file_song_map -------------------------------------------------


def test_build_file_song_map_ordnet_bekannte_datei_zu_und_ueberspringt_rest(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "cache.db"
    conn = cs.open_cache(db_path)
    cs.put_provider(
        conn,
        "genius",
        cs.normalize_key("Nina Hagen"),
        cs.normalize_key("Naturtraene"),
        "treffer",
        "Ein Text",
    )

    known_file = tmp_path / "01 - Naturtraene.flac"
    known_file.write_bytes(b"")
    unknown_file = tmp_path / "02 - Unknown Song.flac"
    unknown_file.write_bytes(b"")
    no_tags_file = tmp_path / "03 - NoTags.flac"
    no_tags_file.write_bytes(b"")

    tags_by_path = {
        known_file: ("Nina Hagen", "Naturtraene", ""),
        unknown_file: ("Someone Else", "Some Song", ""),
        no_tags_file: ("", "", ""),
    }

    def fake_read_audio_tags(path):
        return tags_by_path.get(path, ("", "", ""))

    monkeypatch.setattr(
        songtext_pipeline.fetch_songtext, "_read_audio_tags", fake_read_audio_tags
    )

    mapping = songtext_pipeline.build_file_song_map(
        tmp_path, recursive=False, conn=conn
    )

    assert mapping == [
        (known_file, cs.normalize_key("Nina Hagen"), cs.normalize_key("Naturtraene"))
    ]


def test_build_file_song_map_bereinigt_titel_klammerzusatz(tmp_path, monkeypatch):
    db_path = tmp_path / "cache.db"
    conn = cs.open_cache(db_path)
    cs.put_provider(
        conn,
        "genius",
        cs.normalize_key("Artist"),
        cs.normalize_key("Song Title"),
        "treffer",
        "Ein Text",
    )

    live_file = tmp_path / "01 - Song Title (Live Version).flac"
    live_file.write_bytes(b"")

    def fake_read_audio_tags(path):
        return ("Artist", "Song Title (Live Version)", "")

    monkeypatch.setattr(
        songtext_pipeline.fetch_songtext, "_read_audio_tags", fake_read_audio_tags
    )

    mapping = songtext_pipeline.build_file_song_map(
        tmp_path, recursive=False, conn=conn
    )

    assert mapping == [
        (live_file, cs.normalize_key("Artist"), cs.normalize_key("Song Title"))
    ]


def test_build_file_song_map_leere_db_liefert_leere_liste(tmp_path, monkeypatch):
    db_path = tmp_path / "cache.db"
    conn = cs.open_cache(db_path)

    audio_file = tmp_path / "01 - Song.flac"
    audio_file.write_bytes(b"")

    monkeypatch.setattr(
        songtext_pipeline.fetch_songtext,
        "_read_audio_tags",
        lambda path: ("Artist", "Song", ""),
    )

    mapping = songtext_pipeline.build_file_song_map(
        tmp_path, recursive=False, conn=conn
    )
    assert mapping == []
