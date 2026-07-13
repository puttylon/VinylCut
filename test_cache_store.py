"""Tests für die Cache-Speicherschicht (cache_store.py)."""

import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

import cache_store as cs

_HAS_FFMPEG = shutil.which("ffmpeg") is not None


def test_open_cache_legt_schema_an(tmp_path):
    db_path = tmp_path / "cache.db"
    conn = cs.open_cache(db_path)
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"songs", "texte", "ergebnisse", "transkripte"} <= tables
    conn.close()


def test_open_cache_ist_idempotent(tmp_path):
    db_path = tmp_path / "cache.db"
    conn = cs.open_cache(db_path)
    conn.close()
    # zweites Öffnen darf nicht scheitern, auch wenn Schema schon existiert
    conn2 = cs.open_cache(db_path)
    conn2.close()


def test_put_get_provider_treffer(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    cs.put_provider(
        conn, "lrclib", "the beatles", "hey jude", "treffer", "Hey Jude, don't..."
    )
    ergebnis = cs.get_provider(conn, "lrclib", "the beatles", "hey jude")
    assert ergebnis == {"status": "treffer", "content": "Hey Jude, don't..."}


def test_put_get_provider_nichts(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    cs.put_provider(conn, "genius", "unknown artist", "unknown title", "nichts", None)
    ergebnis = cs.get_provider(conn, "genius", "unknown artist", "unknown title")
    assert ergebnis == {"status": "nichts", "content": None}


def test_get_provider_ohne_eintrag_gibt_none(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    assert cs.get_provider(conn, "lrclib", "nobody", "nothing") is None


def test_dedup_gleicher_inhalt_zwei_quellen(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    inhalt = "identischer liedtext"
    cs.put_provider(conn, "lrclib", "artist", "title", "treffer", inhalt)
    cs.put_provider(conn, "lokal", "artist", "title", "treffer", inhalt)

    anzahl = conn.execute("SELECT COUNT(*) FROM texte").fetchone()[0]
    assert anzahl == 1

    lrclib_ergebnis = cs.get_provider(conn, "lrclib", "artist", "title")
    lokal_ergebnis = cs.get_provider(conn, "lokal", "artist", "title")
    assert lrclib_ergebnis["content"] == inhalt
    assert lokal_ergebnis["content"] == inhalt


def test_put_provider_upsert_ueberschreibt(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    cs.put_provider(conn, "musixmatch", "a", "b", "nichts", None)
    cs.put_provider(conn, "musixmatch", "a", "b", "treffer", "jetzt doch ein text")
    ergebnis = cs.get_provider(conn, "musixmatch", "a", "b")
    assert ergebnis == {"status": "treffer", "content": "jetzt doch ein text"}


def test_ttl_abgelaufener_eintrag_gibt_none(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    cs.put_provider(conn, "lrclib", "artist", "title", "treffer", "text")

    alt = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    conn.execute(
        "UPDATE ergebnisse SET datum=? WHERE quelle=? AND song_id=(SELECT id FROM songs WHERE artist_key=? AND titel_key=?)",
        (alt, "lrclib", "artist", "title"),
    )
    conn.commit()

    assert cs.get_provider(conn, "lrclib", "artist", "title") is None


def test_ttl_innerhalb_frist_bleibt_gueltig(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    cs.put_provider(conn, "lrclib", "artist", "title", "treffer", "text")

    jung = (datetime.now(timezone.utc) - timedelta(days=29)).isoformat()
    conn.execute(
        "UPDATE ergebnisse SET datum=? WHERE quelle=? AND song_id=(SELECT id FROM songs WHERE artist_key=? AND titel_key=?)",
        (jung, "lrclib", "artist", "title"),
    )
    conn.commit()

    assert cs.get_provider(conn, "lrclib", "artist", "title") is not None


def test_ttl_custom_ttl_days(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    cs.put_provider(conn, "lrclib", "artist", "title", "treffer", "text")

    vor_2_tagen = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    conn.execute(
        "UPDATE ergebnisse SET datum=? WHERE quelle=? AND song_id=(SELECT id FROM songs WHERE artist_key=? AND titel_key=?)",
        (vor_2_tagen, "lrclib", "artist", "title"),
    )
    conn.commit()

    assert cs.get_provider(conn, "lrclib", "artist", "title", ttl_days=1) is None
    assert cs.get_provider(conn, "lrclib", "artist", "title", ttl_days=30) is not None


def test_fehlschlag_wird_festgehalten(tmp_path):
    """Ein Fehlschlag (Timeout/Rate-Limit/Captcha) darf nie stillschweigend fehlen."""
    conn = cs.open_cache(tmp_path / "cache.db")
    cs.put_provider(
        conn,
        "musixmatch",
        "artist",
        "title",
        "fehlschlag",
        None,
        fehlergrund="rate_limit",
    )
    row = conn.execute(
        "SELECT status, fehlergrund FROM ergebnisse e "
        "JOIN songs s ON s.id = e.song_id "
        "WHERE e.quelle=? AND s.artist_key=? AND s.titel_key=?",
        ("musixmatch", "artist", "title"),
    ).fetchone()
    assert row == ("fehlschlag", "rate_limit")


def test_fehlschlag_ist_nie_ein_cache_treffer(tmp_path):
    """get_provider darf einen Fehlschlag nie als gueltiges Ergebnis zurueckgeben."""
    conn = cs.open_cache(tmp_path / "cache.db")
    cs.put_provider(
        conn, "musixmatch", "a", "b", "fehlschlag", None, fehlergrund="timeout"
    )
    assert cs.get_provider(conn, "musixmatch", "a", "b") is None


def test_fehlschlag_dann_treffer_ueberschreibt(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    cs.put_provider(
        conn, "musixmatch", "a", "b", "fehlschlag", None, fehlergrund="rate_limit"
    )
    cs.put_provider(conn, "musixmatch", "a", "b", "treffer", "text")
    assert cs.get_provider(conn, "musixmatch", "a", "b") == {
        "status": "treffer",
        "content": "text",
    }


def test_put_provider_ungueltiger_status_wirft():
    import pytest

    conn = cs.open_cache(":memory:")
    with pytest.raises(ValueError):
        cs.put_provider(conn, "lrclib", "a", "b", "kaputt", None)


def test_songs_tabelle_normalisiert_ein_song_vier_provider(tmp_path):
    """Ein Song = eine Zeile in `songs`, verknuepft mit bis zu 4 Ergebnis-Zeilen."""
    conn = cs.open_cache(tmp_path / "cache.db")
    for provider in ("lrclib", "musixmatch", "netease", "genius"):
        cs.put_provider(conn, provider, "motoerhead", "ace of spades", "nichts", None)

    n_songs = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    assert n_songs == 1

    n_ergebnisse = conn.execute(
        "SELECT COUNT(*) FROM ergebnisse e JOIN songs s ON s.id=e.song_id "
        "WHERE s.artist_key=? AND s.titel_key=?",
        ("motoerhead", "ace of spades"),
    ).fetchone()[0]
    assert n_ergebnisse == 4


def test_genre_wird_am_song_gespeichert(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    cs.put_provider(conn, "lrclib", "a", "b", "treffer", "text", genre="Rock")
    genre = conn.execute(
        "SELECT genre FROM songs WHERE artist_key=? AND titel_key=?", ("a", "b")
    ).fetchone()[0]
    assert genre == "Rock"


def test_transcript_roundtrip(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    cs.put_transcript(conn, "artist a", "title a", "gesungener text", 0.02, -0.3)
    ergebnis = cs.get_transcript(conn, "artist a", "title a")
    assert ergebnis == {
        "transcript": "gesungener text",
        "no_speech_prob": 0.02,
        "avg_logprob": -0.3,
    }


def test_transcript_anderer_song_kein_treffer(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    cs.put_transcript(conn, "artist a", "title a", "text", 0.02, -0.3)
    assert cs.get_transcript(conn, "artist a", "andere titel") is None


def test_transcript_ohne_song_gibt_none_und_legt_nichts_an(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    assert cs.get_transcript(conn, "unbekannt", "unbekannt") is None
    assert conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0] == 0


def test_transcript_upsert(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    cs.put_transcript(conn, "artist a", "title a", "alter text", 0.1, -0.5)
    cs.put_transcript(conn, "artist a", "title a", "neuer text", 0.05, -0.2)
    ergebnis = cs.get_transcript(conn, "artist a", "title a")
    assert ergebnis["transcript"] == "neuer text"


def test_transcript_teilt_sich_song_mit_provider_cache(tmp_path):
    """Ein Song = eine Zeile in `songs`, gemeinsam genutzt von Provider- und Transkript-Cache."""
    conn = cs.open_cache(tmp_path / "cache.db")
    cs.put_provider(conn, "lrclib", "artist a", "title a", "treffer", "lyrics")
    cs.put_transcript(conn, "artist a", "title a", "text", 0.1, -0.5)
    assert conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0] == 1


def test_transcript_modell_ist_reine_info_spalte(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    cs.put_transcript(conn, "artist a", "title a", "text", 0.1, -0.5, modell="small")
    modell = conn.execute(
        "SELECT t.modell FROM transkripte t JOIN songs s ON s.id=t.song_id "
        "WHERE s.artist_key=? AND s.titel_key=?",
        ("artist a", "title a"),
    ).fetchone()[0]
    assert modell == "small"
    # modell ist nicht Teil des Schluessels: Upsert mit anderem Modell ersetzt
    # dieselbe Zeile statt eine zweite anzulegen.
    cs.put_transcript(
        conn, "artist a", "title a", "neuer text", 0.05, -0.2, modell="anderes-modell"
    )
    anzahl = conn.execute("SELECT COUNT(*) FROM transkripte").fetchone()[0]
    assert anzahl == 1


def test_normalize_key():
    assert cs.normalize_key("  The Beatles  ") == "the beatles"
    assert cs.normalize_key("HEY JUDE") == "hey jude"


def _make_tagged_flac(path, artist: str, title: str) -> None:
    """Erzeugt eine winzige, echte FLAC-Datei mit lesbaren ARTIST/TITLE-Tags."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=8000:cl=mono",
            "-t",
            "0.2",
            "-metadata",
            f"artist={artist}",
            "-metadata",
            f"title={title}",
            str(path),
        ],
        capture_output=True,
        check=True,
    )


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg nicht verfügbar")
class TestMigrationTranskripteV1ZuV2:
    """_migrate_transkripte_v1_to_v2: alte Datei-Zeilen -> neue Song-Identität."""

    def _old_format_db(
        self, db_path, datei_kennung, transkript, no_speech, logprob, datum
    ):
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE songs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artist_key TEXT NOT NULL,
                titel_key TEXT NOT NULL,
                genre TEXT,
                UNIQUE (artist_key, titel_key)
            );
            CREATE TABLE texte (fingerabdruck TEXT PRIMARY KEY, inhalt TEXT);
            CREATE TABLE ergebnisse (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                song_id INTEGER NOT NULL REFERENCES songs(id),
                quelle TEXT NOT NULL,
                status TEXT NOT NULL,
                fehlergrund TEXT,
                fingerabdruck TEXT REFERENCES texte(fingerabdruck),
                datum TEXT NOT NULL,
                UNIQUE (song_id, quelle)
            );
            CREATE TABLE transkripte (
                datei_kennung TEXT,
                modell TEXT,
                parameter_key TEXT,
                transkript TEXT,
                no_speech_prob REAL,
                avg_logprob REAL,
                datum TEXT,
                PRIMARY KEY (datei_kennung, modell, parameter_key)
            );
            """
        )
        conn.execute(
            "INSERT INTO transkripte "
            "(datei_kennung, modell, parameter_key, transkript, no_speech_prob, avg_logprob, datum) "
            "VALUES (?, 'small', '{\"start\": 0.0}', ?, ?, ?, ?)",
            (datei_kennung, transkript, no_speech, logprob, datum),
        )
        conn.commit()
        conn.close()

    def test_migriert_bestehende_zeile_unter_artist_titel_key(self, tmp_path):
        flac = tmp_path / "song.flac"
        _make_tagged_flac(flac, "The Migrators", "Old Song (Live Version)")
        stat = flac.stat()
        datei_kennung = f"{flac.resolve()}|{stat.st_size}|{int(stat.st_mtime)}"

        db_path = tmp_path / "cache.db"
        self._old_format_db(
            db_path,
            datei_kennung,
            "alter transkribierter text",
            0.05,
            -0.3,
            "2026-01-01T00:00:00+00:00",
        )

        conn = cs.open_cache(db_path)

        cached = cs.get_transcript(conn, "the migrators", "old song")
        assert cached == {
            "transcript": "alter transkribierter text",
            "no_speech_prob": 0.05,
            "avg_logprob": -0.3,
        }

        # Backup-Tabelle bleibt mit der Originalzeile erhalten.
        alt_row = conn.execute(
            "SELECT datei_kennung, transkript FROM transkripte_alt_v1"
        ).fetchone()
        assert alt_row == (datei_kennung, "alter transkribierter text")

    def test_fehlende_datei_wird_nicht_migriert_aber_gewarnt(self, tmp_path, capsys):
        datei_kennung = f"{tmp_path / 'verschwunden.flac'}|1234|1700000000"
        db_path = tmp_path / "cache.db"
        self._old_format_db(
            db_path,
            datei_kennung,
            "verlorener text",
            0.9,
            -1.0,
            "2026-01-01T00:00:00+00:00",
        )

        conn = cs.open_cache(db_path)

        assert conn.execute("SELECT COUNT(*) FROM transkripte").fetchone()[0] == 0
        out = capsys.readouterr().out
        assert "nicht migrierbar" in out
        assert "Datei fehlt" in out

    def test_migration_ist_idempotent(self, tmp_path):
        flac = tmp_path / "song.flac"
        _make_tagged_flac(flac, "Idempotent Artist", "Idempotent Song")
        stat = flac.stat()
        datei_kennung = f"{flac.resolve()}|{stat.st_size}|{int(stat.st_mtime)}"

        db_path = tmp_path / "cache.db"
        self._old_format_db(
            db_path, datei_kennung, "text", 0.05, -0.3, "2026-01-01T00:00:00+00:00"
        )

        conn = cs.open_cache(db_path)
        conn.close()
        # zweites Oeffnen darf nicht erneut migrieren/scheitern (alte Spalte ist weg)
        conn2 = cs.open_cache(db_path)
        cached = cs.get_transcript(conn2, "idempotent artist", "idempotent song")
        assert cached["transcript"] == "text"
