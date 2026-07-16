"""Tests für evaluate_lyrics.py (Phase 4 der Songtexte-Pipeline, Meilenstein 3).

Die eigentlichen Algorithmen (_provider_consensus, _whisper_best,
_whisper_accept, _heuristic_best) sind unverändert aus fetch_songtext.py
wiederverwendet und schon dort ausführlich getestet (TestProviderConsensus,
TestWhisperAccept, TestWhisperBest...) -- hier deshalb nur Tests für die neue
Modul-Struktur: Kandidaten aus der Cache-DB statt Live-Abfrage, kein
Datei-Schreibvorgang, Modellwahl nach Sprache, Scope/IDF-Refresh-Orchestrierung.

_get_whisper_model wird in jedem Test, der Whisper-Pfade durchläuft, gemockt
-- nie ein echtes Modell laden.
"""

from __future__ import annotations

from pathlib import Path

import cache_store as cs
import evaluate_lyrics
import fetch_songtext

LRC_A = "[00:10.00]Girl you know it's true I love you\n[00:15.00]I'm in love with you girl\n"
LRC_B = "[00:10.00]Girl you know it's true yes I love you\n[00:15.00]I'm in love girl cause you're on my mind\n"
LRC_C = "[00:10.00]You know it's true I love you girl oh\n[00:15.00]In love with you girl cause you're my mind\n"
LRC_WRONG = (
    "[00:10.00]Opa Opa tanzen alle Leute\n[00:15.00]Opa Opa heute und auch morgen\n"
)


class _GlobalsResetMixin:
    def setup_method(self):
        fetch_songtext._cache_conn = None
        fetch_songtext._cache_refresh = False
        fetch_songtext._cache_only = False
        fetch_songtext._lrclib_dump_conn = None
        fetch_songtext._contrastive_idf = None
        fetch_songtext._contrastive_lang_pools = None
        fetch_songtext._contrastive_song_texts = None
        fetch_songtext._contrastive_song_words_cache = {}

    def teardown_method(self):
        self.setup_method()


def _put_texts(conn, artist_key, titel_key, by_provider: dict[str, str]) -> None:
    for provider, content in by_provider.items():
        cs.put_provider(conn, provider, artist_key, titel_key, "treffer", content)


class TestLoadCandidateTexts(_GlobalsResetMixin):
    def test_liefert_nur_treffer_in_provider_reihenfolge(self, tmp_path):
        conn = cs.open_cache(tmp_path / "cache.db")
        song_id = cs._get_or_create_song(conn, "artist", "title")
        cs.put_provider(conn, "genius", "artist", "title", "treffer", "Text G")
        cs.put_provider(conn, "lrclib", "artist", "title", "treffer", "Text L")
        cs.put_provider(conn, "musixmatch", "artist", "title", "nichts", None)

        result = evaluate_lyrics._load_candidate_texts(conn, song_id)

        assert result == [("lrclib", "Text L"), ("genius", "Text G")]


class TestEvaluateSongKeinProvider(_GlobalsResetMixin):
    def test_kein_song_in_db_liefert_kein_provider(self, tmp_path):
        conn = cs.open_cache(tmp_path / "cache.db")
        found, info_str, extras = evaluate_lyrics.evaluate_song(
            conn, "unbekannt", "song"
        )
        assert found is False
        assert extras["reason"] == "kein-provider"
        assert extras["content"] is None
        assert "kein Provider" in info_str

    def test_song_ohne_treffer_liefert_kein_provider(self, tmp_path):
        conn = cs.open_cache(tmp_path / "cache.db")
        cs.put_provider(conn, "genius", "artist", "title", "nichts", None)
        found, _info, extras = evaluate_lyrics.evaluate_song(conn, "artist", "title")
        assert found is False
        assert extras["reason"] == "kein-provider"


class TestEvaluateSongKonsens(_GlobalsResetMixin):
    def test_drei_uebereinstimmende_provider_ergeben_konsens_ohne_whisper(
        self, tmp_path, monkeypatch
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        _put_texts(
            conn,
            "artist",
            "title",
            {"lrclib": LRC_A, "musixmatch": LRC_B, "genius": LRC_C},
        )

        def _fail_if_called(*a, **kw):
            raise AssertionError("Whisper sollte bei Konsens nicht aufgerufen werden")

        monkeypatch.setattr(fetch_songtext, "_whisper_best", _fail_if_called)

        found, info_str, extras = evaluate_lyrics.evaluate_song(conn, "artist", "title")

        assert found is True
        assert extras["method"] == "konsens"
        assert "Konsens" in info_str
        assert extras["content"] is not None


class TestEvaluateSongWhisper(_GlobalsResetMixin):
    def test_kein_konsens_kein_flac_faellt_auf_heuristik_zurueck(self, tmp_path):
        conn = cs.open_cache(tmp_path / "cache.db")
        _put_texts(conn, "artist", "title", {"lrclib": LRC_A})

        found, info_str, extras = evaluate_lyrics.evaluate_song(
            conn, "artist", "title", flac_path=None
        )

        assert extras["method"] == "heuristik"
        assert "Heuristik" in info_str
        # ohne expected_dur (0.0) ist die Dauer-Toleranz nicht verletzt -> Treffer
        assert found is True

    def test_whisper_akzeptiert_liefert_besten_kandidaten(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        _put_texts(conn, "artist", "title", {"lrclib": LRC_A, "genius": LRC_WRONG})

        flac_path = tmp_path / "song.flac"
        flac_path.write_bytes(b"")

        def _fake_whisper_best(flac, candidates, expected_dur, artist="", title=""):
            # bevorzugt den Kandidaten mit LRC_A-Inhalt
            best = next(p for p in candidates if "true I love you" in p.read_text())
            return (best, 0.9, True, 42, "medium", "en", 0.5)

        monkeypatch.setattr(fetch_songtext, "_whisper_best", _fake_whisper_best)

        found, info_str, extras = evaluate_lyrics.evaluate_song(
            conn, "artist", "title", flac_path=flac_path
        )

        assert found is True
        assert extras["method"] == "whisper-medium"
        assert extras["language"] == "en"
        assert "idf-jacc" in info_str

    def test_whisper_unter_schwelle_wird_abgelehnt(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        _put_texts(conn, "artist", "title", {"lrclib": LRC_A, "genius": LRC_WRONG})
        flac_path = tmp_path / "song.flac"
        flac_path.write_bytes(b"")

        def _fake_whisper_best(flac, candidates, expected_dur, artist="", title=""):
            return (candidates[0], 0.01, True, 5, "medium", "en", -0.5)

        monkeypatch.setattr(fetch_songtext, "_whisper_best", _fake_whisper_best)

        found, info_str, extras = evaluate_lyrics.evaluate_song(
            conn, "artist", "title", flac_path=flac_path
        )

        assert found is False
        assert extras["reason"] == "unter-schwelle"
        assert "unter Schwelle" in info_str

    def test_kein_vokal_faellt_auf_2er_konsens_zurueck(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        _put_texts(conn, "artist", "title", {"lrclib": LRC_A, "genius": LRC_B})
        flac_path = tmp_path / "song.flac"
        flac_path.write_bytes(b"")

        def _fake_whisper_best(flac, candidates, expected_dur, artist="", title=""):
            return (None, 0.0, False, 0, "medium", "en", None)

        monkeypatch.setattr(fetch_songtext, "_whisper_best", _fake_whisper_best)

        found, info_str, extras = evaluate_lyrics.evaluate_song(
            conn, "artist", "title", flac_path=flac_path
        )

        assert found is True
        assert extras["method"] == "konsens"
        assert extras["no_vocal"] is True
        assert "kein Vokal" in info_str

    def test_kein_vokal_ohne_2er_konsens_wird_abgelehnt(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        _put_texts(conn, "artist", "title", {"lrclib": LRC_A})
        flac_path = tmp_path / "song.flac"
        flac_path.write_bytes(b"")

        def _fake_whisper_best(flac, candidates, expected_dur, artist="", title=""):
            return (None, 0.0, False, 0, "medium", "en", None)

        monkeypatch.setattr(fetch_songtext, "_whisper_best", _fake_whisper_best)

        found, _info, extras = evaluate_lyrics.evaluate_song(
            conn, "artist", "title", flac_path=flac_path
        )

        assert found is False
        assert extras["reason"] == "kein-vokal"

    def test_nicht_existierende_flac_faellt_auf_heuristik_zurueck(
        self, tmp_path, monkeypatch
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        _put_texts(conn, "artist", "title", {"lrclib": LRC_A})

        def _fail_if_called(*a, **kw):
            raise AssertionError("Whisper sollte bei fehlender Datei nicht laufen")

        monkeypatch.setattr(fetch_songtext, "_whisper_best", _fail_if_called)

        found, _info, extras = evaluate_lyrics.evaluate_song(
            conn, "artist", "title", flac_path=tmp_path / "nicht_da.flac"
        )
        assert extras["method"] == "heuristik"
        assert found is True


class TestEvaluateSongExistingLrc(_GlobalsResetMixin):
    def test_vorhandene_lrc_wird_als_kandidat_einbezogen_nicht_geloescht(
        self, tmp_path, monkeypatch
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        # nur EIN Provider-Treffer -> allein kein Konsens, existing_lrc macht 2
        _put_texts(conn, "artist", "title", {"lrclib": LRC_A})
        existing = tmp_path / "song.lrc"
        existing.write_text(LRC_B, encoding="utf-8")

        def _fake_whisper_best(flac, candidates, expected_dur, artist="", title=""):
            assert existing in candidates
            return (existing, 0.9, True, 10, "medium", "en", 0.5)

        monkeypatch.setattr(fetch_songtext, "_whisper_best", _fake_whisper_best)
        flac_path = tmp_path / "song.flac"
        flac_path.write_bytes(b"")

        found, _info, extras = evaluate_lyrics.evaluate_song(
            conn, "artist", "title", flac_path=flac_path, existing_lrc=existing
        )
        assert found is True
        assert existing.exists()  # evaluate_song schreibt/löscht nie selbst


class TestSelectWhisperModel(_GlobalsResetMixin):
    def test_englisch_waehlt_medium(self, monkeypatch, tmp_path):
        p = tmp_path / "a.lrc"
        p.write_text(LRC_A, encoding="utf-8")
        monkeypatch.setattr(fetch_songtext, "_detect_lrc_language", lambda c: "en")
        assert evaluate_lyrics._select_whisper_model([p]) == "medium"

    def test_deutsch_waehlt_large_v3(self, monkeypatch, tmp_path):
        p = tmp_path / "a.lrc"
        p.write_text(LRC_A, encoding="utf-8")
        monkeypatch.setattr(fetch_songtext, "_detect_lrc_language", lambda c: "de")
        assert evaluate_lyrics._select_whisper_model([p]) == "large-v3"

    def test_unbekannte_sprache_waehlt_large_v3(self, monkeypatch, tmp_path):
        p = tmp_path / "a.lrc"
        p.write_text(LRC_A, encoding="utf-8")
        monkeypatch.setattr(fetch_songtext, "_detect_lrc_language", lambda c: None)
        assert evaluate_lyrics._select_whisper_model([p]) == "large-v3"


class TestWhisperModelOverrideRestored(_GlobalsResetMixin):
    def test_modell_wird_nach_aufruf_zurueckgesetzt_auch_bei_exception(
        self, tmp_path, monkeypatch
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        _put_texts(conn, "artist", "title", {"lrclib": LRC_A})
        flac_path = tmp_path / "song.flac"
        flac_path.write_bytes(b"")

        original = fetch_songtext._WHISPER_MODEL
        monkeypatch.setattr(fetch_songtext, "_detect_lrc_language", lambda c: "de")
        seen_models = []

        def _raising_whisper_best(flac, candidates, expected_dur, artist="", title=""):
            seen_models.append(fetch_songtext._WHISPER_MODEL)
            raise RuntimeError("boom")

        monkeypatch.setattr(fetch_songtext, "_whisper_best", _raising_whisper_best)

        try:
            evaluate_lyrics.evaluate_song(conn, "artist", "title", flac_path=flac_path)
        except RuntimeError:
            pass

        assert seen_models == ["large-v3"]
        assert fetch_songtext._WHISPER_MODEL == original


class TestEvaluateAll(_GlobalsResetMixin):
    def test_kein_whisper_verfuegbar_bricht_sauber_ab(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(fetch_songtext, "_get_whisper_model", lambda name: None)
        monkeypatch.setattr(
            fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        result = evaluate_lyrics.evaluate_all(conn)
        assert result == {}

    def test_scope_grenzt_auf_angegebene_songs_ein(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "in scope", "song a")
        cs._get_or_create_song(conn, "out of scope", "song b")
        monkeypatch.setattr(fetch_songtext, "_get_whisper_model", lambda name: object())
        monkeypatch.setattr(
            fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        seen = []

        def _fake_evaluate_song(conn, artist_key, titel_key, *a, **kw):
            seen.append((artist_key, titel_key))
            return (
                False,
                "0/4: — │ kein Provider",
                {
                    "reason": "kein-provider",
                    "content": None,
                },
            )

        monkeypatch.setattr(evaluate_lyrics, "evaluate_song", _fake_evaluate_song)

        counts = evaluate_lyrics.evaluate_all(conn, scope={("in scope", "song a")})

        assert seen == [("in scope", "song a")]
        assert counts["kein-provider"] == 1

    def test_idf_wird_alle_n_songs_aufgefrischt(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        for i in range(3):
            cs._get_or_create_song(conn, f"artist {i}", "song")
        monkeypatch.setattr(fetch_songtext, "_get_whisper_model", lambda name: object())
        monkeypatch.setattr(
            fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        monkeypatch.setattr(evaluate_lyrics, "_IDF_REFRESH_INTERVAL", 2)

        refresh_calls = []
        monkeypatch.setattr(
            fetch_songtext,
            "_build_contrastive_context",
            lambda: refresh_calls.append(1),
        )
        monkeypatch.setattr(
            evaluate_lyrics,
            "evaluate_song",
            lambda conn, a, t, *ar, **kw: (
                False,
                "x",
                {"reason": "kein-provider", "content": None},
            ),
        )

        evaluate_lyrics.evaluate_all(conn)

        # 1x initial + 1x nach Song 2 (Refresh-Intervall=2) = 2 Aufrufe fuer 3 Songs
        assert len(refresh_calls) == 2


class TestResolveExpectedDur(_GlobalsResetMixin):
    def test_liest_dauer_aus_release_json(self, tmp_path, monkeypatch):
        flac_path = tmp_path / "01 Song.flac"
        flac_path.write_bytes(b"")
        monkeypatch.setattr(
            fetch_songtext,
            "_read_audio_tags",
            lambda p: ("Artist", "Song", ""),
        )
        monkeypatch.setattr(
            fetch_songtext,
            "_load_release",
            lambda folder: ("Artist", {"Song": 123.4}),
        )
        assert evaluate_lyrics._resolve_expected_dur(flac_path) == 123.4

    def test_ohne_release_json_liefert_null(self, tmp_path, monkeypatch):
        flac_path = tmp_path / "01 Song.flac"
        flac_path.write_bytes(b"")
        monkeypatch.setattr(
            fetch_songtext, "_read_audio_tags", lambda p: ("Artist", "Song", "")
        )
        monkeypatch.setattr(fetch_songtext, "_load_release", lambda folder: ("", {}))
        assert evaluate_lyrics._resolve_expected_dur(flac_path) == 0.0
