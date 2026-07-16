"""Tests für fetch_providers.py (Phase 2 + Nachhol-Modus/Phase 3 der
Songtexte-Pipeline, Meilenstein 2).

Das Grundverhalten von _query_provider/Cache/Rate-Limit selbst ist
unverändert wiederverwendet (siehe fetch_providers.py-Modul-Docstring) und
schon in test_fetch_songtext.py (TestProviderCache, TestRetryMissing, ...)
ausführlich getestet -- hier deshalb nur ein schlanker Smoke-Test für die
neue Modul-Struktur: die Normal-Modus-Schleife über "songs" und das
Globals-Setup, das beide Modi vor dem Aufruf von fetch_songtext braucht.

_open_lrclib_dump_conn wird in jedem Test auf einen No-Op gemockt: die echte
Funktion öffnet den externen LRCLib-Datenbank-Abzug -- ein reiner
Beschleuniger (siehe dortiger Docstring), der auf Maschinen ohne den
Netzwerk-Mount ohnehin still auf None degradiert. Für deterministische,
maschinenunabhängige Tests wird das hier bewusst erzwungen, NIE eine echte
Live-Provider-Abfrage.
"""

from __future__ import annotations

from pathlib import Path

import cache_store as cs
import fetch_providers
import fetch_songtext


def _fake_run(responses: dict[str, str] | None = None):
    """Ersetzt fetch_songtext.subprocess.run -- schreibt für Queries, die
    einen der `responses`-Schlüssel enthalten, LRC-Inhalt in die Zieldatei,
    sonst bleibt sie leer (= sauberer Fehlschlag ohne Rate-Limit-Signal).
    Analog zu TestRetryMissing._fake_run in test_fetch_songtext.py."""
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


class _CacheGlobalsResetMixin:
    """Setzt die fetch_songtext-Modul-Globals vor/nach jedem Test zurück --
    dieselben Globals, die _prepare_fetch_songtext_globals setzt, dürfen
    keinen Zustand zwischen Tests durchsickern lassen."""

    def setup_method(self):
        fetch_songtext._cache_conn = None
        fetch_songtext._cache_refresh = False
        fetch_songtext._cache_only = False
        fetch_songtext._lrclib_dump_conn = None

    def teardown_method(self):
        fetch_songtext._cache_conn = None
        fetch_songtext._cache_refresh = False
        fetch_songtext._cache_only = False
        fetch_songtext._lrclib_dump_conn = None


class TestPrepareFetchSongtextGlobals(_CacheGlobalsResetMixin):
    def test_setzt_cache_conn_und_ttl_und_resettet_refresh_only(
        self, tmp_path, monkeypatch
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(
            fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        # simuliert Zustand, den ein früherer Aufruf im selben Prozess
        # stehen gelassen haben könnte
        fetch_songtext._cache_refresh = True
        fetch_songtext._cache_only = True

        fetch_providers._prepare_fetch_songtext_globals(conn)

        assert fetch_songtext._cache_conn is conn
        assert fetch_songtext._cache_ttl_days == cs.DEFAULT_TTL_DAYS
        assert fetch_songtext._cache_refresh is False
        assert fetch_songtext._cache_only is False


class TestFetchAll(_CacheGlobalsResetMixin):
    def test_fragt_jeden_song_bei_allen_4_anbietern_ab(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist a", "title a", None)
        cs._get_or_create_song(conn, "artist b", "title b", None)
        conn.commit()

        monkeypatch.setattr(
            fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        # Neutralisiert den uncommitteten lokalen Debug-Hack in fetch_songtext.py
        # (_LRCLIB_LIVE_FALLBACK=False, siehe dortiger Modulkommentar) -- dieser
        # Test prüft das COMMITTETE Verhalten (alle 4 Anbieter werden live
        # gefragt), nicht den temporären Hack. raising=False macht das robust,
        # falls das Attribut nach Entfernen des Hacks gar nicht mehr existiert.
        monkeypatch.setattr(
            fetch_songtext, "_LRCLIB_LIVE_FALLBACK", True, raising=False
        )
        fake_run = _fake_run({"artist a": "[00:01.00]hallo"})
        monkeypatch.setattr(fetch_songtext.subprocess, "run", fake_run)

        queried, skipped = fetch_providers.fetch_all(conn)

        assert (queried, skipped) == (2, 0)
        assert len(fake_run.calls) == 8  # 2 Songs x 4 Provider
        assert {p for _, p in fake_run.calls} == set(fetch_songtext._ALL_PROVIDERS)

        assert cs.get_provider(conn, "lrclib", "artist a", "title a") == {
            "status": "treffer",
            "content": "[00:01.00]hallo",
        }
        assert cs.get_provider(conn, "lrclib", "artist b", "title b") == {
            "status": "nichts",
            "content": None,
        }

    def test_leere_songs_tabelle_liefert_null_ohne_live_abfrage(
        self, tmp_path, monkeypatch
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(
            fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None
        )

        def _fail_if_called(*a, **k):
            raise AssertionError(
                "Live-Abfrage darf bei leerer songs-Tabelle nicht laufen"
            )

        monkeypatch.setattr(fetch_songtext.subprocess, "run", _fail_if_called)

        assert fetch_providers.fetch_all(conn) == (0, 0)

    def test_skip_genre_song_wird_uebersprungen_ohne_jede_anbieter_anfrage(
        self, tmp_path, monkeypatch
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist a", "title a", "Hörbuch")
        conn.commit()

        monkeypatch.setattr(
            fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None
        )

        def _fail_if_called(*a, **k):
            raise AssertionError(
                "Ein Song mit Skip-Genre darf bei keinem Anbieter live abgefragt werden"
            )

        monkeypatch.setattr(fetch_songtext.subprocess, "run", _fail_if_called)

        queried, skipped = fetch_providers.fetch_all(conn)

        assert (queried, skipped) == (0, 1)
        # kein Cache-Eintrag entstand -- der Song wurde nie angefasst
        assert cs.get_provider(conn, "lrclib", "artist a", "title a") is None

    def test_leeres_genre_none_wird_normal_abgefragt(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist a", "title a", None)
        conn.commit()

        monkeypatch.setattr(
            fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        # Neutralisiert den uncommitteten lokalen Debug-Hack in fetch_songtext.py
        # (_LRCLIB_LIVE_FALLBACK=False) -- dieser Test prüft das COMMITTETE
        # Verhalten (alle 4 Anbieter werden live gefragt), nicht den Hack.
        monkeypatch.setattr(
            fetch_songtext, "_LRCLIB_LIVE_FALLBACK", True, raising=False
        )
        fake_run = _fake_run({})
        monkeypatch.setattr(fetch_songtext.subprocess, "run", fake_run)

        # genre=None darf _is_skip_genre (ruft intern .lower() auf) nicht mit
        # einem AttributeError abstürzen lassen -- der Song muss stattdessen
        # ganz normal (alle 4 Provider) abgefragt werden.
        queried, skipped = fetch_providers.fetch_all(conn)

        assert (queried, skipped) == (1, 0)
        assert len(fake_run.calls) == 4

    def test_scope_grenzt_auf_angegebene_songs_ein(self, tmp_path, monkeypatch):
        """Regressionstest für den realen Produktionsbug (siehe ROADMAP.md):
        ohne scope fragte fetch_all() JEDEN Song ab, der jemals in der
        Cache-DB gelandet ist -- nicht nur die Songs des aktuellen PFAD-
        Laufs."""
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist a", "title a", None)
        cs._get_or_create_song(conn, "andere band", "anderer song", None)
        conn.commit()

        monkeypatch.setattr(
            fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        monkeypatch.setattr(
            fetch_songtext, "_LRCLIB_LIVE_FALLBACK", True, raising=False
        )
        fake_run = _fake_run({})
        monkeypatch.setattr(fetch_songtext.subprocess, "run", fake_run)

        queried, skipped = fetch_providers.fetch_all(
            conn, scope={("artist a", "title a")}
        )

        assert (queried, skipped) == (1, 0)
        assert len(fake_run.calls) == 4  # nur "artist a" x 4 Provider
        assert all("andere band" not in q for q, _p in fake_run.calls)
        assert cs.get_provider(conn, "lrclib", "andere band", "anderer song") is None

    def test_gibt_header_und_pro_song_status_und_ergebniszeile_aus(
        self, tmp_path, monkeypatch, capsys
    ):
        """Regressionstest für eine reale Nutzer-Rückmeldung (siehe
        ROADMAP.md): ohne Fortschrittsanzeige wirkte ein Lauf mit mehreren
        Songs und mehrsekündigen Live-Timeouts wie ein Hänger -- "falls sich
        hier was tut, sieht man nichts davon". Prüft NICHT die genaue
        Formatierung im Detail, nur: es wird während der Verarbeitung
        tatsächlich etwas ausgegeben, nicht erst am Ende."""
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist a", "title a", None)
        cs._get_or_create_song(conn, "artist b", "title b", None)
        conn.commit()

        monkeypatch.setattr(
            fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        fake_run = _fake_run({})
        monkeypatch.setattr(fetch_songtext.subprocess, "run", fake_run)

        fetch_providers.fetch_all(conn)

        out = capsys.readouterr().out
        assert "Frage 2 Song(s) bei 4 Anbietern ab" in out
        # pro Song eine Ergebniszeile (_tprint, persistent -- die
        # überschreibbare _print_status-Zeile per \r ist im capsys-Text
        # ebenfalls enthalten, aber ihr genauer Inhalt wird hier bewusst
        # nicht geprüft, siehe Spy-Test unten für die Status-Reihenfolge)
        assert out.count("0/4: —") == 2
        assert "artist a / title a" in out
        assert "artist b / title b" in out

    def test_statuszeile_erscheint_vor_der_provider_abfrage(
        self, tmp_path, monkeypatch
    ):
        """Beweist die Reihenfolge (nicht nur dass irgendwann etwas
        ausgegeben wird): die überschreibbare Statuszeile pro Song MUSS vor
        der eigentlichen (potenziell mehrsekündigen) Live-Abfrage erscheinen,
        nicht erst danach -- sonst wäre die Anzeige kein echter Fortschritt,
        sondern nur ein nachträgliches Protokoll."""
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist a", "title a", None)
        conn.commit()

        monkeypatch.setattr(
            fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None
        )

        status_calls: list[str] = []
        tprint_calls: list[str] = []
        monkeypatch.setattr(
            fetch_songtext, "_print_status", lambda msg: status_calls.append(msg)
        )
        monkeypatch.setattr(
            fetch_songtext, "_tprint", lambda msg: tprint_calls.append(msg)
        )

        def _fake_query_provider(query, provider, env, artist="", title=""):
            # zum Zeitpunkt der Abfrage muss die Statuszeile für diesen Song
            # bereits ausgegeben worden sein -- sonst hinge der Nutzer wieder
            # ohne jede Rückmeldung vor einer mehrsekündigen Live-Anfrage.
            assert status_calls, "Statuszeile muss vor der Provider-Abfrage stehen"
            assert not tprint_calls, "Ergebniszeile darf noch nicht dastehen"
            return provider, None

        monkeypatch.setattr(fetch_songtext, "_query_provider", _fake_query_provider)

        queried, skipped = fetch_providers.fetch_all(conn)

        assert (queried, skipped) == (1, 0)
        assert len(status_calls) == 1
        assert "1/1" in status_calls[0]
        assert "artist a" in status_calls[0]
        assert len(tprint_calls) == 1
        assert "artist a / title a" in tprint_calls[0]
        assert "0/4" in tprint_calls[0]  # kein Provider lieferte einen Treffer

    def test_skip_genre_song_bekommt_keine_eigene_status_oder_ergebniszeile(
        self, tmp_path, monkeypatch
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist a", "title a", "Hörbuch")
        conn.commit()

        monkeypatch.setattr(
            fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        status_calls: list[str] = []
        tprint_calls: list[str] = []
        monkeypatch.setattr(
            fetch_songtext, "_print_status", lambda msg: status_calls.append(msg)
        )
        monkeypatch.setattr(
            fetch_songtext, "_tprint", lambda msg: tprint_calls.append(msg)
        )

        def _fail_if_called(*a, **k):
            raise AssertionError(
                "Ein Song mit Skip-Genre darf bei keinem Anbieter live abgefragt werden"
            )

        monkeypatch.setattr(fetch_songtext.subprocess, "run", _fail_if_called)

        fetch_providers.fetch_all(conn)

        assert status_calls == []
        assert tprint_calls == []

    def test_leerer_scope_fragt_gar_nichts_ab(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist a", "title a", None)
        conn.commit()

        monkeypatch.setattr(
            fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None
        )

        def _fail_if_called(*a, **k):
            raise AssertionError("leerer scope darf nie live abfragen")

        monkeypatch.setattr(fetch_songtext.subprocess, "run", _fail_if_called)

        assert fetch_providers.fetch_all(conn, scope=set()) == (0, 0)

    def test_scope_none_bleibt_wie_bisher_die_ganze_datenbank(
        self, tmp_path, monkeypatch
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist a", "title a", None)
        cs._get_or_create_song(conn, "andere band", "anderer song", None)
        conn.commit()

        monkeypatch.setattr(
            fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        fake_run = _fake_run({})
        monkeypatch.setattr(fetch_songtext.subprocess, "run", fake_run)

        queried, skipped = fetch_providers.fetch_all(conn, scope=None)

        assert (queried, skipped) == (2, 0)
        assert any("andere band" in q for q, _p in fake_run.calls)

    def test_temporaere_lrc_pfade_werden_nach_dem_cachen_geloescht(
        self, tmp_path, monkeypatch
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist a", "title a", None)
        conn.commit()

        monkeypatch.setattr(
            fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        fake_run = _fake_run({"artist a": "[00:01.00]hallo"})
        monkeypatch.setattr(fetch_songtext.subprocess, "run", fake_run)

        written_paths: list[Path] = []
        orig_query_provider = fetch_songtext._query_provider

        def _spy(*a, **k):
            provider, path = orig_query_provider(*a, **k)
            if path is not None:
                written_paths.append(path)
            return provider, path

        monkeypatch.setattr(fetch_songtext, "_query_provider", _spy)

        fetch_providers.fetch_all(conn)

        assert written_paths, "mindestens ein Treffer sollte einen Temp-Pfad erzeugen"
        for path in written_paths:
            assert not path.exists()


class TestRetryMissing(_CacheGlobalsResetMixin):
    def test_ohne_providers_arg_werden_alle_4_anbieter_angefragt(
        self, tmp_path, monkeypatch
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(
            fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        captured = {}
        monkeypatch.setattr(
            fetch_songtext,
            "_retry_missing",
            lambda providers, artist, title: captured.update(
                providers=providers, artist=artist, title=title
            ),
        )

        fetch_providers.retry_missing(conn)

        assert captured == {
            "providers": fetch_songtext._ALL_PROVIDERS,
            "artist": None,
            "title": None,
        }
        assert fetch_songtext._cache_conn is conn

    def test_providers_arg_wird_durchgereicht(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(
            fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        captured = {}
        monkeypatch.setattr(
            fetch_songtext,
            "_retry_missing",
            lambda providers, artist, title: captured.update(providers=providers),
        )

        fetch_providers.retry_missing(conn, providers=["lrclib"])

        assert captured["providers"] == ["lrclib"]

    def test_end_to_end_nur_nichts_fehlschlag_kombis_werden_retried(
        self, tmp_path, monkeypatch
    ):
        """Smoke-Test des kompletten Nachhol-Pfads über die echte (nicht
        gemockte) fetch_songtext._retry_missing -- deren Kernverhalten ist
        bereits in test_fetch_songtext.TestRetryMissing ausführlich getestet;
        hier nur: landet der Aufruf inkl. Globals-Setup wirklich bei ihr."""
        conn = cs.open_cache(tmp_path / "cache.db")
        cs.put_provider(conn, "lrclib", "artist a", "title a", "nichts", None)
        cs.put_provider(conn, "genius", "artist a", "title a", "treffer", "[00:01.00]x")

        monkeypatch.setattr(
            fetch_songtext, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        # Neutralisiert den uncommitteten lokalen Debug-Hack in fetch_songtext.py
        # (_LRCLIB_LIVE_FALLBACK=False) -- dieser Test prüft das COMMITTETE
        # Verhalten (lrclib wird live gefragt), nicht den temporären Hack.
        monkeypatch.setattr(
            fetch_songtext, "_LRCLIB_LIVE_FALLBACK", True, raising=False
        )
        fake_run = _fake_run({"artist a": "[00:01.00]neu"})
        monkeypatch.setattr(fetch_songtext.subprocess, "run", fake_run)

        fetch_providers.retry_missing(conn)

        # nur der lrclib-nichts-Eintrag wurde retried, nicht der genius-Treffer
        assert len(fake_run.calls) == 1
        assert fake_run.calls[0][1] == "lrclib"
        assert cs.get_provider(conn, "lrclib", "artist a", "title a") == {
            "status": "treffer",
            "content": "[00:01.00]neu",
        }
