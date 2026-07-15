"""Tests für inspect_song.py (Diagnose-Werkzeug für einzelne Songs)."""

import pytest

import cache_store as cs
import inspect_song


def test_sanitize_filename_ersetzt_leerzeichen_und_sonderzeichen():
    assert inspect_song.sanitize_filename("Nina Hagen") == "Nina_Hagen"
    assert (
        inspect_song.sanitize_filename('AC/DC: "Best" <of>?') == "AC_DC___Best___of__"
    )


def test_build_report_song_nicht_gefunden_gibt_none(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    assert inspect_song.build_report(conn, "Unbekannt", "Nichts") is None


def test_build_report_alle_provider_zustaende_und_whisper(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    artist, title = "Nina Hagen", "Naturträne"
    artist_key = cs.normalize_key(artist)
    titel_key = cs.normalize_key(title)

    # genius: treffer
    cs.put_provider(
        conn, "genius", artist_key, titel_key, "treffer", "Genius-Text hier"
    )
    # netease: nichts
    cs.put_provider(conn, "netease", artist_key, titel_key, "nichts", None)
    # lrclib: fehlschlag
    cs.put_provider(
        conn,
        "lrclib",
        artist_key,
        titel_key,
        "fehlschlag",
        None,
        fehlergrund="rate_limit",
    )
    # musixmatch: nie abgefragt (kein put_provider-Aufruf)

    cs.put_transcript(conn, artist_key, titel_key, "Whisper-Transkript hier", 0.1, -0.5)

    report = inspect_song.build_report(conn, artist, title)
    assert report is not None

    expected = (
        "Artist: Nina Hagen\n"
        "Titel: Naturträne\n"
        "\n"
        "=== Genius ===\n"
        "Genius-Text hier\n"
        "\n"
        "=== Netease ===\n"
        "(kein Treffer)\n"
        "\n"
        "=== Lrclib ===\n"
        "(Fehlschlag: rate_limit)\n"
        "\n"
        "=== Musixmatch ===\n"
        "(nie abgefragt)\n"
        "\n"
        "=== Whisper ===\n"
        "Whisper-Transkript hier\n"
    )
    assert report == expected


def test_build_report_kein_transkript_vorhanden(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    artist, title = "Kraftwerk", "Autobahn"
    artist_key = cs.normalize_key(artist)
    titel_key = cs.normalize_key(title)
    # Song existiert (mind. ein Provider-Eintrag), aber kein Transkript.
    cs.put_provider(conn, "genius", artist_key, titel_key, "nichts", None)

    report = inspect_song.build_report(conn, artist, title)
    assert "=== Whisper ===\n(kein Transkript vorhanden)" in report


def test_main_end_to_end_schreibt_datei(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "cache.db"
    conn = cs.open_cache(db_path)
    artist, title = "Test Artist", "Test Title"
    artist_key = cs.normalize_key(artist)
    titel_key = cs.normalize_key(title)
    cs.put_provider(conn, "genius", artist_key, titel_key, "treffer", "Ein Text")
    conn.close()

    monkeypatch.setattr(inspect_song, "_default_db_path", lambda: db_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv", ["inspect_song.py", "--artist", artist, "--title", title]
    )

    inspect_song.main()

    out_path = tmp_path / "Test_Artist_Test_Title.txt"
    assert out_path.exists()
    content = out_path.read_text(encoding="utf-8")
    assert "Ein Text" in content
    assert "Geschrieben:" in capsys.readouterr().out


def test_main_song_nicht_gefunden_exit_1_keine_datei(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "cache.db"
    cs.open_cache(db_path).close()

    monkeypatch.setattr(inspect_song, "_default_db_path", lambda: db_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["inspect_song.py", "--artist", "Nobody", "--title", "Nothing"],
    )

    with pytest.raises(SystemExit) as exc_info:
        inspect_song.main()
    assert exc_info.value.code == 1

    assert list(tmp_path.glob("*.txt")) == []
    assert "nicht in der Cache-Datenbank gefunden" in capsys.readouterr().err


def test_main_output_flag_custom_pfad(tmp_path, monkeypatch):
    db_path = tmp_path / "cache.db"
    conn = cs.open_cache(db_path)
    artist, title = "X", "Y"
    cs.put_provider(
        conn,
        "genius",
        cs.normalize_key(artist),
        cs.normalize_key(title),
        "nichts",
        None,
    )
    conn.close()

    custom = tmp_path / "custom_name.txt"
    monkeypatch.setattr(inspect_song, "_default_db_path", lambda: db_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "inspect_song.py",
            "--artist",
            artist,
            "--title",
            title,
            "--output",
            str(custom),
        ],
    )

    inspect_song.main()
    assert custom.exists()
