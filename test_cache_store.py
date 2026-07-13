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
    assert {"texte", "quelle", "gehoert"} <= tables
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
    cs.put_provider(conn, "lrclib", "the beatles", "hey jude", "treffer", "Hey Jude, don't...")
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
        "UPDATE quelle SET datum=? WHERE quelle=? AND kuenstler_key=? AND titel_key=?",
        (alt, "lrclib", "artist", "title"),
    )
    conn.commit()

    assert cs.get_provider(conn, "lrclib", "artist", "title") is None


def test_ttl_innerhalb_frist_bleibt_gueltig(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    cs.put_provider(conn, "lrclib", "artist", "title", "treffer", "text")

    jung = (datetime.now(timezone.utc) - timedelta(days=29)).isoformat()
    conn.execute(
        "UPDATE quelle SET datum=? WHERE quelle=? AND kuenstler_key=? AND titel_key=?",
        (jung, "lrclib", "artist", "title"),
    )
    conn.commit()

    assert cs.get_provider(conn, "lrclib", "artist", "title") is not None


def test_ttl_custom_ttl_days(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    cs.put_provider(conn, "lrclib", "artist", "title", "treffer", "text")

    vor_2_tagen = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    conn.execute(
        "UPDATE quelle SET datum=? WHERE quelle=? AND kuenstler_key=? AND titel_key=?",
        (vor_2_tagen, "lrclib", "artist", "title"),
    )
    conn.commit()

    assert cs.get_provider(conn, "lrclib", "artist", "title", ttl_days=1) is None
    assert cs.get_provider(conn, "lrclib", "artist", "title", ttl_days=30) is not None


def test_transcript_roundtrip(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    cs.put_transcript(conn, "audio-key-1", "small", '{"beam_size": 5}', "gesungener text", 0.02, -0.3)
    ergebnis = cs.get_transcript(conn, "audio-key-1", "small", '{"beam_size": 5}')
    assert ergebnis == {
        "transcript": "gesungener text",
        "no_speech_prob": 0.02,
        "avg_logprob": -0.3,
    }


def test_transcript_anderer_params_key_kein_treffer(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    cs.put_transcript(conn, "audio-key-1", "small", '{"beam_size": 5}', "text", 0.02, -0.3)
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
