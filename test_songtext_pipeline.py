"""Tests für songtext_pipeline.py (Steuer-Skript, Meilenstein 0-2)."""

from pathlib import Path

import pytest

import cache_store as cs
import fetch_songtext
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
    # öffnen (siehe _default_db_path). Die DB ist leer (keine Songs, keine
    # Ergebnisse), Phase 2/3 fragen daher real, aber ohne einen einzigen
    # (Song, Provider) -- keine Live-Netzwerk-Abfrage findet statt.
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(songtext_pipeline, "_default_db_path", lambda: db_path)
    monkeypatch.setattr("sys.argv", ["songtext_pipeline.py", str(tmp_path)])
    try:
        songtext_pipeline.main()

        out = capsys.readouterr().out
        assert "Phase 1 (scan_songs): 0 Song(s) gescannt/aktualisiert." in out
        assert "Phase 2 (fetch_providers, Normal-Modus): 0 Song(s) abgefragt." in out
        assert "Phase 3 (fetch_providers, Nachhol-Modus):" in out
        assert "Keine passenden Cache-Einträge gefunden" in out
        assert "Phase 4 (evaluate_lyrics) würde hier laufen." in out
        assert "Phase 5 (write_lrc) würde hier laufen." in out
    finally:
        _reset_fetch_songtext_globals()


def test_main_phase_3_funktioniert_ohne_pfad(tmp_path, monkeypatch, capsys):
    # eigene, leere DB -- sonst würde main() die echte Produktions-Cache-DB
    # öffnen und fetch_providers.retry_missing() könnte live abfragen.
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(songtext_pipeline, "_default_db_path", lambda: db_path)
    monkeypatch.setattr("sys.argv", ["songtext_pipeline.py", "--phase", "3"])
    try:
        songtext_pipeline.main()

        out = capsys.readouterr().out
        assert "Phase 3 (fetch_providers, Nachhol-Modus):" in out
        assert "Keine passenden Cache-Einträge gefunden" in out
        assert "Phase 1" not in out
        assert "Phase 2" not in out
        assert "Phase 4" not in out
        assert "Phase 5" not in out
    finally:
        _reset_fetch_songtext_globals()


def test_main_phase_mehrfachauswahl_nur_gewaehlte_phasen(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(songtext_pipeline, "_default_db_path", lambda: db_path)
    monkeypatch.setattr(
        "sys.argv", ["songtext_pipeline.py", str(tmp_path), "--phase", "2,4,5"]
    )
    try:
        songtext_pipeline.main()

        out = capsys.readouterr().out
        assert "Phase 1" not in out
        assert "Phase 2 (fetch_providers, Normal-Modus): 0 Song(s) abgefragt." in out
        assert "Phase 3" not in out
        assert "Phase 4 (evaluate_lyrics) würde hier laufen." in out
        assert "Phase 5 (write_lrc) würde hier laufen." in out
    finally:
        _reset_fetch_songtext_globals()


def test_main_phase_1_ohne_pfad_meldet_und_ueberspringt(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(songtext_pipeline, "_default_db_path", lambda: db_path)
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


# --- --phase 2/3: fetch_providers-Anbindung (Meilenstein 2) -------------
#
# Netzwerk/subprocess werden IMMER gemockt -- niemals echte Live-Provider-
# Abfragen in Tests. _open_lrclib_dump_conn wird ebenfalls auf None gemockt,
# damit die Tests unabhängig davon laufen, ob der externe LRCLib-Datenbank-
# Abzug auf der jeweiligen Maschine gemountet ist (reiner Beschleuniger,
# siehe fetch_providers._prepare_fetch_songtext_globals-Docstring).


def _fake_subprocess_run(responses: dict[str, str] | None = None):
    responses = responses or {}

    class _Result:
        stderr = ""

    calls: list[tuple[str, str]] = []

    def _run(cmd, **kwargs):
        query, provider = cmd[1], cmd[-1]
        calls.append((query, provider))
        out_path = Path(cmd[3])
        for needle, content in responses.items():
            if needle in query:
                out_path.write_text(content, encoding="utf-8")
                return _Result()
        return _Result()

    _run.calls = calls
    return _run


def _reset_fetch_songtext_globals():
    """fetch_providers setzt beim Aufruf über main() fetch_songtext-Modul-
    Globals direkt (nicht über monkeypatch, siehe fetch_providers.
    _prepare_fetch_songtext_globals) -- ohne manuellen Reset bliebe nach dem
    Test eine geschlossene tmp_path-Connection als _cache_conn stehen und
    könnte andere Tests im selben pytest-Lauf stören."""
    fetch_songtext._cache_conn = None
    fetch_songtext._cache_refresh = False
    fetch_songtext._cache_only = False
    fetch_songtext._lrclib_dump_conn = None


def test_main_phase_2_fragt_songs_ab_und_schreibt_in_cache_db(
    tmp_path, monkeypatch, capsys
):
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(songtext_pipeline, "_default_db_path", lambda: db_path)
    conn = cs.open_cache(db_path)
    cs._get_or_create_song(conn, "artist a", "title a", None)
    conn.commit()
    conn.close()

    monkeypatch.setattr(fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None)
    # Neutralisiert den uncommitteten lokalen Debug-Hack in fetch_songtext.py
    # (_LRCLIB_LIVE_FALLBACK=False) -- dieser Test prüft das COMMITTETE
    # Verhalten (alle 4 Anbieter werden live gefragt), nicht den Hack.
    monkeypatch.setattr(fetch_songtext, "_LRCLIB_LIVE_FALLBACK", True, raising=False)
    fake_run = _fake_subprocess_run({"artist a": "[00:01.00]hallo"})
    monkeypatch.setattr(fetch_songtext.subprocess, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["songtext_pipeline.py", "--phase", "2"])

    try:
        songtext_pipeline.main()

        out = capsys.readouterr().out
        assert "Phase 2 (fetch_providers, Normal-Modus): 1 Song(s) abgefragt." in out
        assert len(fake_run.calls) == 4  # ein Song x 4 Anbieter
        assert {p for _, p in fake_run.calls} == set(fetch_songtext._ALL_PROVIDERS)

        conn = cs.open_cache(db_path)
        assert cs.get_provider(conn, "lrclib", "artist a", "title a") == {
            "status": "treffer",
            "content": "[00:01.00]hallo",
        }
    finally:
        _reset_fetch_songtext_globals()


def test_main_phase_3_holt_nur_nichts_fehlschlag_kombis_nach(
    tmp_path, monkeypatch, capsys
):
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(songtext_pipeline, "_default_db_path", lambda: db_path)
    conn = cs.open_cache(db_path)
    cs.put_provider(conn, "lrclib", "artist a", "title a", "nichts", None)
    cs.put_provider(conn, "genius", "artist a", "title a", "treffer", "[00:01.00]x")
    conn.close()

    monkeypatch.setattr(fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None)
    # Neutralisiert den uncommitteten lokalen Debug-Hack in fetch_songtext.py
    # (_LRCLIB_LIVE_FALLBACK=False) -- dieser Test prüft das COMMITTETE
    # Verhalten (lrclib wird live gefragt), nicht den Hack.
    monkeypatch.setattr(fetch_songtext, "_LRCLIB_LIVE_FALLBACK", True, raising=False)
    fake_run = _fake_subprocess_run({"artist a": "[00:01.00]neu"})
    monkeypatch.setattr(fetch_songtext.subprocess, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["songtext_pipeline.py", "--phase", "3"])

    try:
        songtext_pipeline.main()

        out = capsys.readouterr().out
        assert "Phase 3 (fetch_providers, Nachhol-Modus):" in out
        assert "jetzt gefunden" in out
        # nur der lrclib-nichts-Eintrag wurde retried, nicht der genius-Treffer
        assert len(fake_run.calls) == 1
        assert fake_run.calls[0][1] == "lrclib"

        conn = cs.open_cache(db_path)
        assert cs.get_provider(conn, "lrclib", "artist a", "title a") == {
            "status": "treffer",
            "content": "[00:01.00]neu",
        }
    finally:
        _reset_fetch_songtext_globals()
