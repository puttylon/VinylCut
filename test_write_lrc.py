"""Tests für write_lrc.py (Phase 5 der Songtexte-Pipeline, Meilenstein 4).

Der JSON-Ordner-Cache/die Ordner-Sperre selbst (_load_cache/_save_cache/
_try_claim_folder) sind unverändert aus fetch_songtext.py wiederverwendet und
schon dort ausführlich getestet (TestLoadCache, TestSaveCache,
TestFolderClaim) -- hier deshalb nur Tests für write_all()s eigene Logik:
Entscheidung von evaluate_lyrics.evaluate_song() übernehmen, schreiben/
löschen/unverändert lassen, JSON-Cache-Skip bei einem zweiten Lauf.
"""

from __future__ import annotations

import cache_store as cs
import evaluate_lyrics
import fetch_songtext
import write_lrc


class _GlobalsResetMixin:
    def setup_method(self):
        fetch_songtext._cache_conn = None
        fetch_songtext._cache_refresh = False
        fetch_songtext._cache_only = False
        fetch_songtext._lrclib_dump_conn = None

    def teardown_method(self):
        self.setup_method()


def _stub_evaluate_song(result):
    """Ersetzt evaluate_lyrics.evaluate_song durch eine feste Antwort --
    write_all() selbst testet nur, was es MIT der Entscheidung tut, nicht wie
    sie zustande kommt (das deckt test_evaluate_lyrics.py ab)."""

    def _fake(
        conn, artist_key, titel_key, flac_path=None, expected_dur=0.0, existing_lrc=None
    ):
        return result

    return _fake


class TestWriteAllSchreibenLoeschen(_GlobalsResetMixin):
    def test_gefundener_text_wird_geschrieben(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(
            fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        monkeypatch.setattr(
            evaluate_lyrics,
            "evaluate_song",
            _stub_evaluate_song(
                (
                    True,
                    "3/4: lrclib, genius, netease │ Konsens 80%",
                    {
                        "method": "konsens",
                        "content": b"[00:01.00]Hallo Welt\n",
                    },
                )
            ),
        )
        audio = tmp_path / "01 Song.flac"
        audio.write_bytes(b"")

        counts = write_lrc.write_all(conn, [(audio, "artist", "title")])

        lrc_path = audio.with_suffix(".lrc")
        assert lrc_path.read_bytes() == b"[00:01.00]Hallo Welt\n"
        assert counts["updated"] == 1
        assert counts["not_found"] == 0

    def test_nicht_gefunden_loescht_vorhandene_lrc(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(
            fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        monkeypatch.setattr(
            evaluate_lyrics,
            "evaluate_song",
            _stub_evaluate_song(
                (
                    False,
                    "0/4: — │ kein Provider",
                    {
                        "reason": "kein-provider",
                        "content": None,
                    },
                )
            ),
        )
        audio = tmp_path / "01 Song.flac"
        audio.write_bytes(b"")
        lrc_path = audio.with_suffix(".lrc")
        lrc_path.write_text("[00:01.00]Alter Text\n", encoding="utf-8")

        counts = write_lrc.write_all(conn, [(audio, "artist", "title")])

        assert not lrc_path.exists()
        assert counts["not_found"] == 1

    def test_unveraenderter_inhalt_wird_nicht_neu_geschrieben(
        self, tmp_path, monkeypatch
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(
            fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        content = b"[00:01.00]Gleicher Text\n"
        monkeypatch.setattr(
            evaluate_lyrics,
            "evaluate_song",
            _stub_evaluate_song(
                (
                    True,
                    "3/4: … │ Konsens 90%",
                    {"method": "konsens", "content": content},
                )
            ),
        )
        audio = tmp_path / "01 Song.flac"
        audio.write_bytes(b"")
        lrc_path = audio.with_suffix(".lrc")
        lrc_path.write_bytes(content)
        mtime_before = lrc_path.stat().st_mtime_ns

        counts = write_lrc.write_all(conn, [(audio, "artist", "title")])

        assert lrc_path.stat().st_mtime_ns == mtime_before
        assert counts["skipped"] == 1
        assert counts["updated"] == 0


class TestWriteAllJsonCacheSkip(_GlobalsResetMixin):
    def test_zweiter_lauf_ueberspringt_bereits_geschriebenen_song(
        self, tmp_path, monkeypatch
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(
            fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        calls = []

        def _tracking_evaluate_song(conn, artist_key, titel_key, *a, **kw):
            calls.append((artist_key, titel_key))
            return (
                True,
                "3/4: … │ Konsens 90%",
                {"method": "konsens", "content": b"[00:01.00]Text\n"},
            )

        monkeypatch.setattr(evaluate_lyrics, "evaluate_song", _tracking_evaluate_song)
        audio = tmp_path / "01 Song.flac"
        audio.write_bytes(b"")

        write_lrc.write_all(conn, [(audio, "artist", "title")])
        assert len(calls) == 1

        write_lrc.write_all(conn, [(audio, "artist", "title")])
        assert len(calls) == 1  # zweiter Lauf: JSON-Cache-Treffer, kein erneuter Aufruf

    def test_force_umgeht_json_cache_skip(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(
            fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        calls = []

        def _tracking_evaluate_song(conn, artist_key, titel_key, *a, **kw):
            calls.append((artist_key, titel_key))
            return (
                True,
                "3/4: … │ Konsens 90%",
                {"method": "konsens", "content": b"[00:01.00]Text\n"},
            )

        monkeypatch.setattr(evaluate_lyrics, "evaluate_song", _tracking_evaluate_song)
        audio = tmp_path / "01 Song.flac"
        audio.write_bytes(b"")

        write_lrc.write_all(conn, [(audio, "artist", "title")])
        write_lrc.write_all(conn, [(audio, "artist", "title")], force=True)

        assert len(calls) == 2


class TestWriteAllLeererScope(_GlobalsResetMixin):
    def test_leere_file_song_map_tut_nichts(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(
            fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        counts = write_lrc.write_all(conn, [])
        assert counts == {"updated": 0, "skipped": 0, "not_found": 0, "errors": 0}
