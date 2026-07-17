"""Tests für evaluate_lyrics.py (Phase 4 der Songtexte-Pipeline, Meilenstein 3).

Die eigentlichen Algorithmen (_provider_consensus, _whisper_best,
_whisper_accept, _heuristic_best) sind unverändert aus lyrics_core.py
wiederverwendet und schon dort ausführlich getestet (TestProviderConsensus,
TestWhisperAccept, TestWhisperBest...) -- hier deshalb nur Tests für die neue
Modul-Struktur: Kandidaten aus der Cache-DB statt Live-Abfrage, kein
Datei-Schreibvorgang, Modellwahl nach Sprache, Scope/IDF-Refresh-Orchestrierung.

_get_whisper_model wird in jedem Test, der Whisper-Pfade durchläuft, gemockt
-- nie ein echtes Modell laden.
"""

from __future__ import annotations


import cache_store as cs
import evaluate_lyrics
import lyrics_core
import write_lrc

LRC_A = "[00:10.00]Girl you know it's true I love you\n[00:15.00]I'm in love with you girl\n"
LRC_B = "[00:10.00]Girl you know it's true yes I love you\n[00:15.00]I'm in love girl cause you're on my mind\n"
LRC_C = "[00:10.00]You know it's true I love you girl oh\n[00:15.00]In love with you girl cause you're my mind\n"
LRC_WRONG = (
    "[00:10.00]Opa Opa tanzen alle Leute\n[00:15.00]Opa Opa heute und auch morgen\n"
)


class _GlobalsResetMixin:
    def setup_method(self):
        lyrics_core._cache_conn = None
        lyrics_core._cache_refresh = False
        lyrics_core._cache_only = False
        lyrics_core._lrclib_dump_conn = None
        lyrics_core._contrastive_idf = None
        lyrics_core._contrastive_lang_pools = None
        lyrics_core._contrastive_song_texts = None
        lyrics_core._contrastive_song_words_cache = {}
        lyrics_core._contrastive_context_built_ever = False
        lyrics_core._contrastive_context_evaluations_since_refresh = 0

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

        monkeypatch.setattr(lyrics_core, "_whisper_best", _fail_if_called)

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

        def _fake_whisper_best(flac, candidates, expected_dur, artist="", title="", reason=""):
            # bevorzugt den Kandidaten mit LRC_A-Inhalt
            best = next(p for p in candidates if "true I love you" in p.read_text())
            return (best, 0.9, True, 42, "medium", "en", 0.5)

        monkeypatch.setattr(lyrics_core, "_whisper_best", _fake_whisper_best)

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

        def _fake_whisper_best(flac, candidates, expected_dur, artist="", title="", reason=""):
            return (candidates[0], 0.01, True, 5, "medium", "en", -0.5)

        monkeypatch.setattr(lyrics_core, "_whisper_best", _fake_whisper_best)

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

        def _fake_whisper_best(flac, candidates, expected_dur, artist="", title="", reason=""):
            return (None, 0.0, False, 0, "medium", "en", None)

        monkeypatch.setattr(lyrics_core, "_whisper_best", _fake_whisper_best)

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

        def _fake_whisper_best(flac, candidates, expected_dur, artist="", title="", reason=""):
            return (None, 0.0, False, 0, "medium", "en", None)

        monkeypatch.setattr(lyrics_core, "_whisper_best", _fake_whisper_best)

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

        monkeypatch.setattr(lyrics_core, "_whisper_best", _fail_if_called)

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

        def _fake_whisper_best(flac, candidates, expected_dur, artist="", title="", reason=""):
            assert existing in candidates
            return (existing, 0.9, True, 10, "medium", "en", 0.5)

        monkeypatch.setattr(lyrics_core, "_whisper_best", _fake_whisper_best)
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
        monkeypatch.setattr(lyrics_core, "_detect_lrc_language", lambda c: "en")
        assert evaluate_lyrics._select_whisper_model([p]) == "medium"

    def test_deutsch_waehlt_large_v3(self, monkeypatch, tmp_path):
        p = tmp_path / "a.lrc"
        p.write_text(LRC_A, encoding="utf-8")
        monkeypatch.setattr(lyrics_core, "_detect_lrc_language", lambda c: "de")
        assert evaluate_lyrics._select_whisper_model([p]) == "large-v3"

    def test_unbekannte_sprache_waehlt_large_v3(self, monkeypatch, tmp_path):
        p = tmp_path / "a.lrc"
        p.write_text(LRC_A, encoding="utf-8")
        monkeypatch.setattr(lyrics_core, "_detect_lrc_language", lambda c: None)
        assert evaluate_lyrics._select_whisper_model([p]) == "large-v3"


class TestWhisperModelOverrideRestored(_GlobalsResetMixin):
    def test_modell_wird_nach_aufruf_zurueckgesetzt_auch_bei_exception(
        self, tmp_path, monkeypatch
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        _put_texts(conn, "artist", "title", {"lrclib": LRC_A})
        flac_path = tmp_path / "song.flac"
        flac_path.write_bytes(b"")

        original = lyrics_core._WHISPER_MODEL
        monkeypatch.setattr(lyrics_core, "_detect_lrc_language", lambda c: "de")
        seen_models = []

        def _raising_whisper_best(flac, candidates, expected_dur, artist="", title="", reason=""):
            seen_models.append(lyrics_core._WHISPER_MODEL)
            raise RuntimeError("boom")

        monkeypatch.setattr(lyrics_core, "_whisper_best", _raising_whisper_best)

        try:
            evaluate_lyrics.evaluate_song(conn, "artist", "title", flac_path=flac_path)
        except RuntimeError:
            pass

        assert seen_models == ["large-v3"]
        assert lyrics_core._WHISPER_MODEL == original


class TestEvaluateAll(_GlobalsResetMixin):
    def test_kein_whisper_verfuegbar_bricht_sauber_ab(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(lyrics_core, "_faster_whisper_available", lambda: False)
        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        result = evaluate_lyrics.evaluate_all(conn)
        assert result == {}

    def test_verfuegbarkeits_check_laedt_kein_modell(self, tmp_path, monkeypatch):
        """Regressionstest (siehe ROADMAP.md): die Verfügbarkeits-Prüfung am
        Anfang von evaluate_all() darf KEIN Whisper-Modell laden -- ein
        Lauf, bei dem kein einziger Song im Scope überhaupt Whisper
        braucht (hier: leere DB), soll auch keins laden. Vorher wurde
        `medium` hier immer als Sonde voll geladen, selbst wenn kein Song
        `medium` gebraucht hätte."""
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )

        def _fail_if_called(*a, **k):
            raise AssertionError("_get_whisper_model darf hier nicht aufgerufen werden")

        monkeypatch.setattr(lyrics_core, "_get_whisper_model", _fail_if_called)

        result = evaluate_lyrics.evaluate_all(conn)
        assert result == {
            "konsens": 0,
            "whisper-akzeptiert": 0,
            "abgelehnt": 0,
            "kein-provider": 0,
            "uebersprungen": 0,
        }

    def test_scope_grenzt_auf_angegebene_songs_ein(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "in scope", "song a")
        cs._get_or_create_song(conn, "out of scope", "song b")
        monkeypatch.setattr(lyrics_core, "_get_whisper_model", lambda name: object())
        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
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
        monkeypatch.setattr(lyrics_core, "_get_whisper_model", lambda name: object())
        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        monkeypatch.setattr(evaluate_lyrics, "_IDF_REFRESH_INTERVAL", 2)

        refresh_calls = []
        monkeypatch.setattr(
            lyrics_core,
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

    def test_idf_refresh_zaehler_bleibt_ueber_mehrere_evaluate_all_aufrufe_erhalten(
        self, tmp_path, monkeypatch
    ):
        """Regressionstest für Task #15 (Phasen pro Ordner, siehe ROADMAP.md):
        songtext_pipeline.py wird evaluate_all() künftig mehrfach im selben
        Prozess aufrufen (einmal pro Ordner). Der "wurde je gebaut"/"wie
        viele Songs seit dem letzten Aufbau"-Fortschritt muss dabei ÜBER
        mehrere Aufrufe hinweg erhalten bleiben -- sonst würde der Kontext
        bei jedem Aufruf (jedem Ordner) erneut als "noch nie gebaut" gelten
        und viel öfter als die beabsichtigten _IDF_REFRESH_INTERVAL Songs neu
        aufgebaut werden."""
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist a", "song a")
        cs._get_or_create_song(conn, "artist b", "song b")
        monkeypatch.setattr(lyrics_core, "_get_whisper_model", lambda name: object())
        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        monkeypatch.setattr(evaluate_lyrics, "_IDF_REFRESH_INTERVAL", 2)

        refresh_calls = []
        monkeypatch.setattr(
            lyrics_core,
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

        # simuliert zwei Ordner mit je einem Song, statt einem Aufruf mit
        # scope=None über beide -- genau das, was eine Ordner-für-Ordner-
        # Schleife in main() künftig tun würde.
        evaluate_lyrics.evaluate_all(conn, scope={("artist a", "song a")})
        evaluate_lyrics.evaluate_all(conn, scope={("artist b", "song b")})

        # 1x initial (erster Song, erster Aufruf) -- der zweite Song im
        # ZWEITEN Aufruf ist erst der insgesamt zweite bewertete Song, löst
        # also (Refresh-Intervall=2) noch KEINEN erneuten Aufbau aus.
        assert len(refresh_calls) == 1


class TestEvaluateAllSkipUnveraendert(_GlobalsResetMixin):
    """Regressionstests für ROADMAP.md, Songtexte-Pipeline-Umbau, "'bewerten'
    hat keinen Skip für unveränderte Songs": evaluate_all() bewertete bisher
    JEDEN Song im Scope bei JEDEM Lauf neu, auch wenn write_lrc.write_all()
    (--schreiben) für denselben Track schon einen gültigen, unveränderten
    JSON-Cache-Eintrag hatte -- reale Whisper-/Kontext-Arbeit verpuffte
    ungenutzt bei jedem Wiederholungslauf. evaluate_all() nutzt jetzt
    denselben Skip wie write_lrc.write_all() (_skip_reevaluation, teilt sich
    lyrics_core._db_newer_than_json_entry mit write_lrc.py)."""

    def test_track_mit_gueltigem_json_cache_wird_uebersprungen(
        self, tmp_path, monkeypatch
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist", "title")
        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        audio = tmp_path / "01 Song.flac"
        audio.write_bytes(b"")

        calls = []

        def _tracking_evaluate_song(conn, artist_key, titel_key, *a, **kw):
            calls.append((artist_key, titel_key))
            return (
                True,
                "3/4: … │ Konsens 90%",
                {"method": "konsens", "content": b"[00:01.00]Text\n"},
            )

        monkeypatch.setattr(evaluate_lyrics, "evaluate_song", _tracking_evaluate_song)

        # Ein --schreiben-Lauf legt den echten JSON-Cache-Eintrag an -- so
        # wie er nach einem normalen Durchlauf tatsächlich aussieht.
        write_lrc.write_all(conn, [(audio, "artist", "title")])
        assert len(calls) == 1
        calls.clear()

        counts = evaluate_lyrics.evaluate_all(
            conn, file_song_map={("artist", "title"): audio}
        )

        assert calls == []  # nicht erneut bewertet
        assert counts["uebersprungen"] == 1
        assert counts["konsens"] == 0

    def test_track_mit_neuerem_db_eintrag_wird_trotzdem_bewertet(
        self, tmp_path, monkeypatch
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist", "title")
        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        audio = tmp_path / "01 Song.flac"
        audio.write_bytes(b"")

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
        calls.clear()

        # simuliert: --nachholen hat inzwischen einen neuen Provider-Treffer
        # gefunden -- ein neuer ergebnisse-Datensatz NACH dem JSON-Eintrag.
        cs.put_provider(conn, "genius", "artist", "title", "treffer", "[00:01.00]y")
        conn.commit()

        counts = evaluate_lyrics.evaluate_all(
            conn, file_song_map={("artist", "title"): audio}
        )

        assert calls == [("artist", "title")]  # erneut bewertet, nicht übersprungen
        assert counts["uebersprungen"] == 0
        assert counts["konsens"] == 1

    def test_ohne_datei_zuordnung_wird_immer_bewertet(self, tmp_path, monkeypatch):
        """Ohne file_song_map-Eintrag (z.B. --bewerten ohne PFAD, ganze
        Bibliothek) gibt es keinen JSON-Ordner-Cache zu prüfen -- der Song
        wird wie bisher immer neu bewertet."""
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist", "title")
        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )

        calls = []

        def _tracking_evaluate_song(conn, artist_key, titel_key, *a, **kw):
            calls.append((artist_key, titel_key))
            return (
                False,
                "0/4: — │ kein Provider",
                {"reason": "kein-provider", "content": None},
            )

        monkeypatch.setattr(evaluate_lyrics, "evaluate_song", _tracking_evaluate_song)

        counts = evaluate_lyrics.evaluate_all(conn)
        counts2 = evaluate_lyrics.evaluate_all(conn)

        assert calls == [("artist", "title"), ("artist", "title")]
        assert counts["uebersprungen"] == 0
        assert counts2["uebersprungen"] == 0


class TestEvaluateAllFileOrder(_GlobalsResetMixin):
    """Regressionstests für Nutzer-Feedback: die Durchläufe sollen nach
    Dateiname sortiert sein (nicht alphabetisch nach Künstler/Titel wie
    bisher) und den Dateinamen anzeigen, falls einer bekannt ist."""

    def test_file_song_map_reihenfolge_bestimmt_bewertungsreihenfolge(
        self, tmp_path, monkeypatch
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        # Alphabetisch nach Künstler wäre "apple band" zuerst -- die
        # Einfügereihenfolge von file_song_map ist aber umgekehrt.
        cs._get_or_create_song(conn, "apple band", "apple song")
        cs._get_or_create_song(conn, "zebra band", "zebra song")
        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )

        seen: list[tuple[str, str]] = []

        def _tracking_evaluate_song(conn, artist_key, titel_key, *a, **kw):
            seen.append((artist_key, titel_key))
            return (
                False,
                "0/4: — │ kein Provider",
                {"reason": "kein-provider", "content": None},
            )

        monkeypatch.setattr(evaluate_lyrics, "evaluate_song", _tracking_evaluate_song)

        file_song_map = {
            ("zebra band", "zebra song"): tmp_path / "01 - Zebra Song.flac",
            ("apple band", "apple song"): tmp_path / "02 - Apple Song.flac",
        }

        evaluate_lyrics.evaluate_all(conn, file_song_map=file_song_map)

        assert seen == [("zebra band", "zebra song"), ("apple band", "apple song")]

    def test_file_song_map_zeigt_dateinamen_statt_artist_titel(
        self, tmp_path, monkeypatch, capsys
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist a", "title a")
        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        monkeypatch.setattr(
            evaluate_lyrics,
            "evaluate_song",
            lambda conn, a, t, *ar, **kw: (
                False,
                "0/4: — │ kein Provider",
                {"reason": "kein-provider", "content": None},
            ),
        )

        file_song_map = {("artist a", "title a"): tmp_path / "01 - Mein Song.flac"}

        evaluate_lyrics.evaluate_all(conn, file_song_map=file_song_map)

        out = capsys.readouterr().out
        assert "01 - Mein Song.flac" in out
        assert "artist a / title a" not in out


class TestResolveExpectedDur(_GlobalsResetMixin):
    def test_liest_dauer_aus_release_json(self, tmp_path, monkeypatch):
        flac_path = tmp_path / "01 Song.flac"
        flac_path.write_bytes(b"")
        monkeypatch.setattr(
            lyrics_core,
            "_read_audio_tags",
            lambda p: ("Artist", "Song", ""),
        )
        monkeypatch.setattr(
            lyrics_core,
            "_load_release",
            lambda folder: ("Artist", {"Song": 123.4}),
        )
        assert evaluate_lyrics._resolve_expected_dur(flac_path) == 123.4

    def test_ohne_release_json_liefert_null(self, tmp_path, monkeypatch):
        flac_path = tmp_path / "01 Song.flac"
        flac_path.write_bytes(b"")
        monkeypatch.setattr(
            lyrics_core, "_read_audio_tags", lambda p: ("Artist", "Song", "")
        )
        monkeypatch.setattr(lyrics_core, "_load_release", lambda folder: ("", {}))
        assert evaluate_lyrics._resolve_expected_dur(flac_path) == 0.0
