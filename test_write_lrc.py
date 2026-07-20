"""Tests für write_lrc.py (Phase 5 der Songtexte-Pipeline, Meilenstein 4).

Der JSON-Ordner-Cache/die Ordner-Sperre selbst (_load_cache/_save_cache/
_try_claim_folder) sind unverändert aus lyrics_core.py wiederverwendet und
schon dort ausführlich getestet (TestLoadCache, TestSaveCache,
TestFolderClaim) -- hier deshalb nur Tests für write_all()s eigene Logik:
Entscheidung von evaluate_lyrics.evaluate_song() übernehmen, schreiben/
löschen/unverändert lassen, JSON-Cache-Skip bei einem zweiten Lauf.
"""

from __future__ import annotations

import cache_store as cs
import evaluate_lyrics
import lyrics_core
import write_lrc


class _GlobalsResetMixin:
    def setup_method(self):
        lyrics_core._cache_conn = None
        lyrics_core._cache_refresh = False
        lyrics_core._cache_only = False
        lyrics_core._lrclib_dump_conn = None

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
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
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
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
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

    def test_existing_best_wird_nicht_geloescht(self, tmp_path, monkeypatch):
        """Bugfix (siehe ROADMAP.md): existing_lrc war selbst der beste
        Kandidat am Audio (extras["existing_best"]=True) -- ein found=False
        dieser Runde darf sie dann nicht mehr loeschen."""
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        monkeypatch.setattr(
            evaluate_lyrics,
            "evaluate_song",
            _stub_evaluate_song(
                (
                    False,
                    "1/4: lrclib │ [medium] en Whisper 40W unter Schwelle idf-jacc=0.100",
                    {
                        "reason": "unter-schwelle",
                        "existing_best": True,
                        "content": None,
                    },
                )
            ),
        )
        audio = tmp_path / "01 Song.flac"
        audio.write_bytes(b"")
        lrc_path = audio.with_suffix(".lrc")
        lrc_path.write_text("[00:01.00]Bereits korrekter Text\n", encoding="utf-8")

        counts = write_lrc.write_all(conn, [(audio, "artist", "title")])

        assert lrc_path.exists()
        assert (
            lrc_path.read_text(encoding="utf-8") == "[00:01.00]Bereits korrekter Text\n"
        )
        assert counts["not_found"] == 0
        assert counts["skipped"] == 1

    def test_unveraenderter_inhalt_wird_nicht_neu_geschrieben(
        self, tmp_path, monkeypatch
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
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
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
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
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
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


class TestWriteAllDbNeuerAlsJsonEintrag(_GlobalsResetMixin):
    """Regressionstests für ROADMAP.md-Nachtrag "Kein Bindeglied zwischen
    JSON-Cache und SQLite-Cache" (live an einem Produktionslauf bestätigt):
    ein gültiger JSON-Eintrag darf einen Song nicht für immer überspringen,
    wenn die Cache-DB seitdem einen neueren Provider- oder Whisper-Datensatz
    für diesen Song bekommen hat."""

    def test_neuerer_db_eintrag_erzwingt_erneute_bewertung(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
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

        # simuliert: Phase "nachholen" hat inzwischen einen neuen
        # Provider-Treffer für denselben Song gefunden -- ein neuer
        # ergebnisse-Datensatz mit einem Zeitstempel NACH dem JSON-Eintrag.
        cs.put_provider(conn, "genius", "artist", "title", "treffer", "[00:01.00]y")
        conn.commit()

        write_lrc.write_all(conn, [(audio, "artist", "title")])
        assert len(calls) == 2  # erneut bewertet, nicht übersprungen

    def test_unveraenderte_db_bleibt_beim_skip(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
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
        write_lrc.write_all(conn, [(audio, "artist", "title")])

        assert len(calls) == 1  # nichts Neues in der DB -- Skip bleibt gültig

    def test_fehlender_ts_im_json_eintrag_erzwingt_erneute_bewertung(
        self, tmp_path, monkeypatch
    ):
        """Ein JSON-Eintrag ohne "ts" (z.B. sehr alt/handgebaut) kann nicht
        gegen die DB verglichen werden -- konservativ nicht überspringen,
        statt eine potenziell veraltete Entscheidung für immer stehen zu
        lassen."""
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        audio = tmp_path / "01 Song.flac"
        audio.write_bytes(b"")
        lrc_path = audio.with_suffix(".lrc")
        lrc_path.write_bytes(b"[00:01.00]Text\n")
        lyrics_core._save_cache(
            tmp_path,
            {
                "01 Song.flac": {
                    "v": lyrics_core.__version__,
                    "r": "ok",
                    "outcome": "write",
                }
            },
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

        write_lrc.write_all(conn, [(audio, "artist", "title")])

        assert len(calls) == 1


class TestWriteAllLeererScope(_GlobalsResetMixin):
    def test_leere_file_song_map_tut_nichts(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        counts = write_lrc.write_all(conn, [])
        assert counts == {"updated": 0, "skipped": 0, "not_found": 0, "errors": 0}
