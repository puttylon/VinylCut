"""Tests für die Cache-Speicherschicht (cache_store.py)."""

from datetime import datetime, timedelta, timezone

import cache_store as cs


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
    cs.put_transcript(
        conn, "audio-key-1", "small", '{"beam_size": 5}', "gesungener text", 0.02, -0.3
    )
    ergebnis = cs.get_transcript(conn, "audio-key-1", "small", '{"beam_size": 5}')
    assert ergebnis == {
        "transcript": "gesungener text",
        "no_speech_prob": 0.02,
        "avg_logprob": -0.3,
    }


def test_transcript_anderer_params_key_kein_treffer(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    cs.put_transcript(
        conn, "audio-key-1", "small", '{"beam_size": 5}', "text", 0.02, -0.3
    )
    assert cs.get_transcript(conn, "audio-key-1", "small", '{"beam_size": 1}') is None


def test_transcript_upsert(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    cs.put_transcript(conn, "audio-key-1", "small", "params", "alter text", 0.1, -0.5)
    cs.put_transcript(conn, "audio-key-1", "small", "params", "neuer text", 0.05, -0.2)
    ergebnis = cs.get_transcript(conn, "audio-key-1", "small", "params")
    assert ergebnis["transcript"] == "neuer text"


def test_audio_key_for_deterministisch(tmp_path):
    datei = tmp_path / "song.wav"
    datei.write_bytes(b"x" * 1000)
    schluessel1 = cs.audio_key_for(datei)
    schluessel2 = cs.audio_key_for(datei)
    assert schluessel1 == schluessel2
    assert str(datei.resolve()) in schluessel1
    assert "1000" in schluessel1


def test_audio_key_for_aendert_sich_bei_groessenaenderung(tmp_path):
    datei = tmp_path / "song.wav"
    datei.write_bytes(b"x" * 1000)
    schluessel1 = cs.audio_key_for(datei)
    datei.write_bytes(b"x" * 2000)
    schluessel2 = cs.audio_key_for(datei)
    assert schluessel1 != schluessel2


def test_params_key_for_deterministisch_und_ordnungsunabhaengig():
    a = cs.params_key_for(beam_size=5, language="de", vad=True)
    b = cs.params_key_for(vad=True, language="de", beam_size=5)
    assert a == b


def test_params_key_for_unterschiedliche_werte_unterschiedlicher_key():
    a = cs.params_key_for(beam_size=5)
    b = cs.params_key_for(beam_size=1)
    assert a != b


def test_normalize_key():
    assert cs.normalize_key("  The Beatles  ") == "the beatles"
    assert cs.normalize_key("HEY JUDE") == "hey jude"
