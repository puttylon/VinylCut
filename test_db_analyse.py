"""Tests für db_analyse.py -- Aggregat-Statistiken über die Cache-DB.

Nur collect_stats() wird getestet (reine SQL-Aggregation, fehleranfällig
genug für Unit-Tests) -- print_stats() ist reine Formatierung ohne
Fallunterscheidungen von Belang und wird hier bewusst nicht geprüft."""

from __future__ import annotations

import cache_store as cs
import db_analyse


def test_leere_db_liefert_nullen(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    stats = db_analyse.collect_stats(conn)

    assert stats["songs_gesamt"] == 0
    assert stats["texte_gesamt"] == 0
    assert stats["songs_ohne_treffer"] == 0
    assert stats["songs_alle_fehlgeschlagen"] == 0
    assert stats["transkripte_gesamt"] == 0
    for p in db_analyse._ALL_PROVIDERS:
        assert stats["provider_status"][p] == {
            "treffer": 0,
            "nichts": 0,
            "fehlschlag": 0,
        }


def test_provider_status_zaehlt_je_anbieter_getrennt(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    cs.put_provider(conn, "lrclib", "artist a", "title a", "treffer", "[00:01.00]x")
    cs.put_provider(conn, "genius", "artist a", "title a", "nichts", None)
    cs.put_provider(
        conn, "musixmatch", "artist a", "title a", "fehlschlag", None, "timeout"
    )

    stats = db_analyse.collect_stats(conn)

    assert stats["songs_gesamt"] == 1
    assert stats["provider_status"]["lrclib"]["treffer"] == 1
    assert stats["provider_status"]["genius"]["nichts"] == 1
    assert stats["provider_status"]["musixmatch"]["fehlschlag"] == 1
    assert stats["provider_status"]["netease"] == {
        "treffer": 0,
        "nichts": 0,
        "fehlschlag": 0,
    }


def test_fehlschlag_gruende_werden_je_provider_gruppiert(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    cs.put_provider(
        conn, "musixmatch", "artist a", "title a", "fehlschlag", None, "captcha"
    )
    cs.put_provider(
        conn, "musixmatch", "artist b", "title b", "fehlschlag", None, "captcha"
    )
    cs.put_provider(
        conn, "musixmatch", "artist c", "title c", "fehlschlag", None, "timeout"
    )

    stats = db_analyse.collect_stats(conn)

    assert stats["fehlschlag_gruende"]["musixmatch"] == {"captcha": 2, "timeout": 1}
    assert stats["fehlschlag_gruende"]["lrclib"] == {}


def test_songs_ohne_treffer(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    # Song a: ein Treffer -- zaehlt NICHT als "ohne Treffer".
    cs.put_provider(conn, "lrclib", "artist a", "title a", "treffer", "[00:01.00]x")
    # Song b: nur "nichts"/"fehlschlag", kein Treffer -- zaehlt.
    cs.put_provider(conn, "lrclib", "artist b", "title b", "nichts", None)
    cs.put_provider(
        conn, "genius", "artist b", "title b", "fehlschlag", None, "timeout"
    )

    stats = db_analyse.collect_stats(conn)

    assert stats["songs_gesamt"] == 2
    assert stats["songs_ohne_treffer"] == 1


def test_songs_alle_vier_provider_fehlgeschlagen(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    for provider in db_analyse._ALL_PROVIDERS:
        cs.put_provider(
            conn, provider, "artist a", "title a", "fehlschlag", None, "timeout"
        )
    # Song b: nur 3 von 4 fehlgeschlagen -- zaehlt NICHT.
    for provider in db_analyse._ALL_PROVIDERS[:3]:
        cs.put_provider(
            conn, provider, "artist b", "title b", "fehlschlag", None, "timeout"
        )

    stats = db_analyse.collect_stats(conn)

    assert stats["songs_alle_fehlgeschlagen"] == 1


def test_transkripte_je_modell(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    cs.put_transcript(conn, "artist a", "title a", "hallo welt", 0.1, -0.5, "medium")
    cs.put_transcript(conn, "artist b", "title b", "hello world", 0.1, -0.5, "medium")
    cs.put_transcript(conn, "artist c", "title c", "bonjour", 0.1, -0.5, "large-v3")

    stats = db_analyse.collect_stats(conn)

    assert stats["transkripte_gesamt"] == 3
    assert stats["transkripte_je_modell"] == {"medium": 2, "large-v3": 1}


def test_texte_werden_dedupliziert_gezaehlt(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    # Zwei verschiedene Songs, aber identischer Text -- ein Fingerabdruck.
    cs.put_provider(conn, "lrclib", "artist a", "title a", "treffer", "[00:01.00]x")
    cs.put_provider(conn, "genius", "artist a", "title a", "treffer", "[00:01.00]x")
    cs.put_provider(conn, "lrclib", "artist b", "title b", "treffer", "[00:02.00]y")

    stats = db_analyse.collect_stats(conn)

    assert stats["texte_gesamt"] == 2
