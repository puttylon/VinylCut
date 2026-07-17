"""Tests für fetch_providers.py (Phase 2 + Nachhol-Modus/Phase 3 der
Songtexte-Pipeline, Meilenstein 2).

Das Grundverhalten von _query_provider/Cache/Rate-Limit selbst ist
unverändert wiederverwendet (siehe fetch_providers.py-Modul-Docstring) und
schon in test_lyrics_core.py (TestProviderCache, TestRetryMissing, ...)
ausführlich getestet -- hier deshalb nur ein schlanker Smoke-Test für die
neue Modul-Struktur: die Normal-Modus-Schleife über "songs" und das
Globals-Setup, das beide Modi vor dem Aufruf von lyrics_core braucht.

_open_lrclib_dump_conn wird in jedem Test auf einen No-Op gemockt: die echte
Funktion öffnet den externen LRCLib-Datenbank-Abzug -- ein reiner
Beschleuniger (siehe dortiger Docstring), der auf Maschinen ohne den
Netzwerk-Mount ohnehin still auf None degradiert. Für deterministische,
maschinenunabhängige Tests wird das hier bewusst erzwungen, NIE eine echte
Live-Provider-Abfrage.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import cache_store as cs
import fetch_providers
import lyrics_core


def _fake_run(responses: dict[str, str] | None = None):
    """Ersetzt lyrics_core.subprocess.run -- schreibt für Queries, die
    einen der `responses`-Schlüssel enthalten, LRC-Inhalt in die Zieldatei,
    sonst bleibt sie leer (= sauberer Fehlschlag ohne Rate-Limit-Signal).
    Analog zu TestRetryMissing._fake_run in test_lyrics_core.py."""
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
    """Setzt die lyrics_core-Modul-Globals vor/nach jedem Test zurück --
    dieselben Globals, die _prepare_lyrics_core_globals setzt, dürfen
    keinen Zustand zwischen Tests durchsickern lassen."""

    def setup_method(self):
        lyrics_core._cache_conn = None
        lyrics_core._cache_refresh = False
        lyrics_core._cache_only = False
        lyrics_core._lrclib_dump_conn = None

    def teardown_method(self):
        lyrics_core._cache_conn = None
        lyrics_core._cache_refresh = False
        lyrics_core._cache_only = False
        lyrics_core._lrclib_dump_conn = None


class TestPrepareFetchSongtextGlobals(_CacheGlobalsResetMixin):
    def test_setzt_cache_conn_und_ttl_und_resettet_refresh_only(
        self, tmp_path, monkeypatch
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        # simuliert Zustand, den ein früherer Aufruf im selben Prozess
        # stehen gelassen haben könnte
        lyrics_core._cache_refresh = True
        lyrics_core._cache_only = True

        fetch_providers._prepare_lyrics_core_globals(conn)

        assert lyrics_core._cache_conn is conn
        assert lyrics_core._cache_ttl_days == cs.DEFAULT_TTL_DAYS
        assert lyrics_core._cache_refresh is False
        assert lyrics_core._cache_only is False


class TestFetchAll(_CacheGlobalsResetMixin):
    def test_fragt_jeden_song_bei_allen_4_anbietern_ab(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist a", "title a", None)
        cs._get_or_create_song(conn, "artist b", "title b", None)
        conn.commit()

        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        # Neutralisiert den uncommitteten lokalen Debug-Hack in lyrics_core.py
        # (_LRCLIB_LIVE_FALLBACK=False, siehe dortiger Modulkommentar) -- dieser
        # Test prüft das COMMITTETE Verhalten (alle 4 Anbieter werden live
        # gefragt), nicht den temporären Hack. raising=False macht das robust,
        # falls das Attribut nach Entfernen des Hacks gar nicht mehr existiert.
        monkeypatch.setattr(lyrics_core, "_LRCLIB_LIVE_FALLBACK", True, raising=False)
        fake_run = _fake_run({"artist a": "[00:01.00]hallo"})
        monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)

        queried, skipped = fetch_providers.fetch_all(conn)

        assert (queried, skipped) == (2, 0)
        assert len(fake_run.calls) == 8  # 2 Songs x 4 Provider
        assert {p for _, p in fake_run.calls} == set(lyrics_core._ALL_PROVIDERS)

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
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )

        def _fail_if_called(*a, **k):
            raise AssertionError(
                "Live-Abfrage darf bei leerer songs-Tabelle nicht laufen"
            )

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fail_if_called)

        assert fetch_providers.fetch_all(conn) == (0, 0)

    def test_skip_genre_song_wird_uebersprungen_ohne_jede_anbieter_anfrage(
        self, tmp_path, monkeypatch
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist a", "title a", "Hörbuch")
        conn.commit()

        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )

        def _fail_if_called(*a, **k):
            raise AssertionError(
                "Ein Song mit Skip-Genre darf bei keinem Anbieter live abgefragt werden"
            )

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fail_if_called)

        queried, skipped = fetch_providers.fetch_all(conn)

        assert (queried, skipped) == (0, 1)
        # kein Cache-Eintrag entstand -- der Song wurde nie angefasst
        assert cs.get_provider(conn, "lrclib", "artist a", "title a") is None

    def test_leeres_genre_none_wird_normal_abgefragt(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist a", "title a", None)
        conn.commit()

        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        # Neutralisiert den uncommitteten lokalen Debug-Hack in lyrics_core.py
        # (_LRCLIB_LIVE_FALLBACK=False) -- dieser Test prüft das COMMITTETE
        # Verhalten (alle 4 Anbieter werden live gefragt), nicht den Hack.
        monkeypatch.setattr(lyrics_core, "_LRCLIB_LIVE_FALLBACK", True, raising=False)
        fake_run = _fake_run({})
        monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)

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
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        monkeypatch.setattr(lyrics_core, "_LRCLIB_LIVE_FALLBACK", True, raising=False)
        fake_run = _fake_run({})
        monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)

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
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        fake_run = _fake_run({})
        monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)

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
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )

        status_calls: list[str] = []
        tprint_calls: list[str] = []
        monkeypatch.setattr(
            lyrics_core, "_print_status", lambda msg: status_calls.append(msg)
        )
        monkeypatch.setattr(
            lyrics_core, "_tprint", lambda msg: tprint_calls.append(msg)
        )

        def _fake_query_provider(query, provider, env, artist="", title=""):
            # zum Zeitpunkt der Abfrage muss die Statuszeile für diesen Song
            # bereits ausgegeben worden sein -- sonst hinge der Nutzer wieder
            # ohne jede Rückmeldung vor einer mehrsekündigen Live-Anfrage.
            assert status_calls, "Statuszeile muss vor der Provider-Abfrage stehen"
            assert not tprint_calls, "Ergebniszeile darf noch nicht dastehen"
            return provider, None

        monkeypatch.setattr(lyrics_core, "_query_provider", _fake_query_provider)

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
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        status_calls: list[str] = []
        tprint_calls: list[str] = []
        monkeypatch.setattr(
            lyrics_core, "_print_status", lambda msg: status_calls.append(msg)
        )
        monkeypatch.setattr(
            lyrics_core, "_tprint", lambda msg: tprint_calls.append(msg)
        )

        def _fail_if_called(*a, **k):
            raise AssertionError(
                "Ein Song mit Skip-Genre darf bei keinem Anbieter live abgefragt werden"
            )

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fail_if_called)

        fetch_providers.fetch_all(conn)

        assert status_calls == []
        assert tprint_calls == []

    def test_leerer_scope_fragt_gar_nichts_ab(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist a", "title a", None)
        conn.commit()

        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )

        def _fail_if_called(*a, **k):
            raise AssertionError("leerer scope darf nie live abfragen")

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fail_if_called)

        assert fetch_providers.fetch_all(conn, scope=set()) == (0, 0)

    def test_scope_none_bleibt_wie_bisher_die_ganze_datenbank(
        self, tmp_path, monkeypatch
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist a", "title a", None)
        cs._get_or_create_song(conn, "andere band", "anderer song", None)
        conn.commit()

        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        fake_run = _fake_run({})
        monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)

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
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        fake_run = _fake_run({"artist a": "[00:01.00]hallo"})
        monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)

        written_paths: list[Path] = []
        orig_query_provider = lyrics_core._query_provider

        def _spy(*a, **k):
            provider, path = orig_query_provider(*a, **k)
            if path is not None:
                written_paths.append(path)
            return provider, path

        monkeypatch.setattr(lyrics_core, "_query_provider", _spy)

        fetch_providers.fetch_all(conn)

        assert written_paths, "mindestens ein Treffer sollte einen Temp-Pfad erzeugen"
        for path in written_paths:
            assert not path.exists()

    def test_provider_mit_gecachtem_fehlschlag_wird_nicht_erneut_live_gefragt(
        self, tmp_path, monkeypatch
    ):
        """Regressionstest für ROADMAP.md-Nachtrag "Phase 2 soll
        fehlschlag-Einträge nicht automatisch mit-retryen": lyrics_core.
        get_provider() wertet "fehlschlag" nie als gültigen Cache-Treffer --
        ohne den Skip in fetch_all() selbst würde _query_provider also bei
        JEDEM Phase-2-Lauf erneut live nachfragen. Das ist exklusiv die
        Aufgabe von retry_missing() (Phase 3, "nachholen")."""
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist a", "title a", None)
        cs.put_provider(
            conn, "lrclib", "artist a", "title a", "fehlschlag", None, "timeout"
        )
        conn.commit()

        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        monkeypatch.setattr(lyrics_core, "_LRCLIB_LIVE_FALLBACK", True, raising=False)
        fake_run = _fake_run({})
        monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)

        queried, skipped = fetch_providers.fetch_all(conn)

        assert (queried, skipped) == (1, 0)
        # nur die 3 NICHT fehlgeschlagenen Anbieter wurden live gefragt
        assert len(fake_run.calls) == 3
        assert {p for _, p in fake_run.calls} == set(lyrics_core._ALL_PROVIDERS) - {
            "lrclib"
        }
        # der gecachte Fehlschlag blieb unverändert stehen
        assert cs.get_provider(conn, "lrclib", "artist a", "title a") is None

    def test_andere_provider_desselben_songs_werden_trotzdem_gefragt(
        self, tmp_path, monkeypatch
    ):
        """Der Skip greift pro (Song, Provider), nicht pro Song: ein
        gecachter Fehlschlag bei EINEM Anbieter darf die anderen 3 nicht
        mit blockieren."""
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist a", "title a", None)
        cs.put_provider(
            conn, "musixmatch", "artist a", "title a", "fehlschlag", None, "captcha"
        )
        conn.commit()

        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        monkeypatch.setattr(lyrics_core, "_LRCLIB_LIVE_FALLBACK", True, raising=False)
        fake_run = _fake_run({"artist a": "[00:01.00]hallo"})
        monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)

        fetch_providers.fetch_all(conn)

        assert cs.get_provider(conn, "lrclib", "artist a", "title a") == {
            "status": "treffer",
            "content": "[00:01.00]hallo",
        }

    def test_song_mit_allen_vier_providern_fehlgeschlagen_wird_ganz_uebersprungen(
        self, tmp_path, monkeypatch
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist a", "title a", None)
        for provider in lyrics_core._ALL_PROVIDERS:
            cs.put_provider(
                conn, provider, "artist a", "title a", "fehlschlag", None, "timeout"
            )
        conn.commit()

        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )

        def _fail_if_called(*a, **k):
            raise AssertionError(
                "kein Anbieter darf live gefragt werden, wenn alle 4 bereits "
                "als Fehlschlag gecacht sind"
            )

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fail_if_called)

        queried, skipped = fetch_providers.fetch_all(conn)

        assert (queried, skipped) == (1, 0)

    def test_retry_missing_fragt_gecachten_fehlschlag_trotzdem_ab(
        self, tmp_path, monkeypatch
    ):
        """Kehrseite des Skips in fetch_all(): retry_missing() (Phase 3,
        "nachholen") ist weiterhin dafür zuständig, genau solche
        Fehlschlag-Einträge erneut live zu prüfen."""
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist a", "title a", None)
        cs.put_provider(
            conn, "lrclib", "artist a", "title a", "fehlschlag", None, "timeout"
        )
        conn.commit()

        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        fake_run = _fake_run({"artist a": "[00:01.00]hallo"})
        monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)

        fetch_providers.retry_missing(conn)

        assert any(p == "lrclib" for _, p in fake_run.calls)
        assert cs.get_provider(conn, "lrclib", "artist a", "title a") == {
            "status": "treffer",
            "content": "[00:01.00]hallo",
        }


class TestRetryMissing(_CacheGlobalsResetMixin):
    def test_ohne_providers_arg_werden_alle_4_anbieter_angefragt(
        self, tmp_path, monkeypatch
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        captured = {}
        monkeypatch.setattr(
            lyrics_core,
            "_retry_missing",
            lambda providers, artist, title, song_ids=None: captured.update(
                providers=providers, artist=artist, title=title, song_ids=song_ids
            ),
        )

        fetch_providers.retry_missing(conn)

        assert captured == {
            "providers": lyrics_core._ALL_PROVIDERS,
            "artist": None,
            "title": None,
            "song_ids": None,
        }
        assert lyrics_core._cache_conn is conn

    def test_providers_arg_wird_durchgereicht(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        captured = {}
        monkeypatch.setattr(
            lyrics_core,
            "_retry_missing",
            lambda providers, artist, title, song_ids=None: captured.update(
                providers=providers
            ),
        )

        fetch_providers.retry_missing(conn, providers=["lrclib"])

        assert captured["providers"] == ["lrclib"]

    def test_scope_none_bleibt_ohne_eingrenzung(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        captured = {}
        monkeypatch.setattr(
            lyrics_core,
            "_retry_missing",
            lambda providers, artist, title, song_ids=None: captured.update(
                song_ids=song_ids
            ),
        )

        fetch_providers.retry_missing(conn, scope=None)

        assert captured["song_ids"] is None

    def test_scope_wird_zu_song_ids_aufgeloest(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        cs._get_or_create_song(conn, "artist a", "title a", None)
        cs._get_or_create_song(conn, "artist b", "title b", None)
        conn.commit()
        song_id_a = conn.execute(
            "SELECT id FROM songs WHERE artist_key='artist a'"
        ).fetchone()[0]

        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        captured = {}
        monkeypatch.setattr(
            lyrics_core,
            "_retry_missing",
            lambda providers, artist, title, song_ids=None: captured.update(
                song_ids=song_ids
            ),
        )

        fetch_providers.retry_missing(conn, scope={("artist a", "title a")})

        assert captured["song_ids"] == [song_id_a]

    def test_scope_mit_unbekanntem_song_wird_ignoriert(self, tmp_path, monkeypatch):
        """Ein (artist_key, titel_key)-Paar im scope, das gar nicht in der
        songs-Tabelle steht (kann bei einer PFAD-Datei ohne DB-Eintrag
        passieren), darf nicht crashen -- einfach nicht mitgezählt."""
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        captured = {}
        monkeypatch.setattr(
            lyrics_core,
            "_retry_missing",
            lambda providers, artist, title, song_ids=None: captured.update(
                song_ids=song_ids
            ),
        )

        fetch_providers.retry_missing(conn, scope={("unbekannt", "unbekannt")})

        assert captured["song_ids"] == []

    def test_end_to_end_scope_grenzt_auf_pfad_songs_ein(self, tmp_path, monkeypatch):
        """Kernstück des --nachholen-mit-PFAD-Umbaus (siehe ROADMAP.md):
        über den echten (nicht gemockten) lyrics_core._retry_missing landet
        nur der Song aus scope beim Live-Retry, der andere fehlgeschlagene
        Song bleibt unangetastet."""
        conn = cs.open_cache(tmp_path / "cache.db")
        cs.put_provider(
            conn, "lrclib", "artist a", "title a", "fehlschlag", None, "timeout"
        )
        cs.put_provider(
            conn, "lrclib", "artist b", "title b", "fehlschlag", None, "timeout"
        )

        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        monkeypatch.setattr(lyrics_core, "_LRCLIB_LIVE_FALLBACK", True, raising=False)
        fake_run = _fake_run({"artist a": "[00:01.00]neu"})
        monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)

        fetch_providers.retry_missing(conn, scope={("artist a", "title a")})

        assert len(fake_run.calls) == 1
        assert "artist a" in fake_run.calls[0][0]
        assert cs.get_provider(conn, "lrclib", "artist a", "title a") == {
            "status": "treffer",
            "content": "[00:01.00]neu",
        }
        # unveraendert, nicht retried
        row = conn.execute(
            "SELECT status FROM ergebnisse e JOIN songs s ON s.id=e.song_id "
            "WHERE s.artist_key='artist b' AND s.titel_key='title b'"
        ).fetchone()
        assert row == ("fehlschlag",)

    def test_end_to_end_nur_nichts_fehlschlag_kombis_werden_retried(
        self, tmp_path, monkeypatch
    ):
        """Smoke-Test des kompletten Nachhol-Pfads über die echte (nicht
        gemockte) lyrics_core._retry_missing -- deren Kernverhalten ist
        bereits in test_lyrics_core.TestRetryMissing ausführlich getestet;
        hier nur: landet der Aufruf inkl. Globals-Setup wirklich bei ihr."""
        conn = cs.open_cache(tmp_path / "cache.db")
        cs.put_provider(conn, "lrclib", "artist a", "title a", "nichts", None)
        cs.put_provider(conn, "genius", "artist a", "title a", "treffer", "[00:01.00]x")

        monkeypatch.setattr(
            lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None
        )
        # Neutralisiert den uncommitteten lokalen Debug-Hack in lyrics_core.py
        # (_LRCLIB_LIVE_FALLBACK=False) -- dieser Test prüft das COMMITTETE
        # Verhalten (lrclib wird live gefragt), nicht den temporären Hack.
        monkeypatch.setattr(lyrics_core, "_LRCLIB_LIVE_FALLBACK", True, raising=False)
        fake_run = _fake_run({"artist a": "[00:01.00]neu"})
        monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)

        fetch_providers.retry_missing(conn)

        # nur der lrclib-nichts-Eintrag wurde retried, nicht der genius-Treffer
        assert len(fake_run.calls) == 1
        assert fake_run.calls[0][1] == "lrclib"
        assert cs.get_provider(conn, "lrclib", "artist a", "title a") == {
            "status": "treffer",
            "content": "[00:01.00]neu",
        }


class TestRetryMissingUsesLrclibDump:
    """Regressionstest für einen echten Bug (Live-Test durch den Nutzer, siehe
    ROADMAP.md v1.13.1): der --retry-missing-Zweig im alten fetch_songtext.py
    main() endete mit `return`, BEVOR der Codeblock erreicht wurde, der
    _lrclib_dump_conn öffnet -- _lrclib_dump_conn blieb für den GESAMTEN
    --retry-missing-Lauf beim Modul-Default None, der lokale Datenbank-Abzug
    wurde nie konsultiert, jede Anfrage ging sofort live raus (beobachtet:
    5-10s pro Song, "weiterhin Fehler (rate_limit)"). Fix (damals):
    _open_lrclib_dump_conn() wird aus main() aufgerufen.

    In der neuen Pipeline ist dieser konkrete Bug strukturell nicht mehr
    möglich -- es gibt nur noch EINEN Einstiegspunkt für den Nachhol-Modus
    (fetch_providers.retry_missing), und _prepare_lyrics_core_globals() öffnet
    die Dump-Verbindung dort IMMER, bevor _retry_missing() läuft (kein
    main()-Verzweigungs-Bug mehr möglich). Test bleibt trotzdem als
    Regressionsschutz erhalten: prüft end-to-end (nicht nur Einzelfunktionen
    wie in test_lyrics_core.TestRetryMissing), dass ein Dump-Treffer einen
    Nachhol-Lauf wirklich vor einer Live-Abfrage bewahrt -- über
    fetch_providers.retry_missing() statt (nicht mehr existierendem)
    lyrics_core.main()."""

    def teardown_method(self):
        lyrics_core._cache_conn = None
        lyrics_core._lrclib_dump_conn = None
        lyrics_core._cache_refresh = False
        lyrics_core._cache_only = False
        lyrics_core._retry_missing_active = False

    def _make_dump_file(
        self, tmp_path, artist_lower: str, title_lower: str, content: str
    ) -> Path:
        dump_path = tmp_path / "dump.db"
        conn = sqlite3.connect(str(dump_path))
        conn.executescript(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name_lower TEXT, artist_name_lower TEXT, last_lyrics_id INTEGER);"
            "CREATE TABLE lyrics (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "plain_lyrics TEXT, synced_lyrics TEXT, has_plain_lyrics BOOLEAN, "
            "has_synced_lyrics BOOLEAN);"
        )
        cur = conn.execute(
            "INSERT INTO lyrics (synced_lyrics, has_synced_lyrics, has_plain_lyrics) "
            "VALUES (?, 1, 0)",
            (content,),
        )
        conn.execute(
            "INSERT INTO tracks (name_lower, artist_name_lower, last_lyrics_id) "
            "VALUES (?, ?, ?)",
            (title_lower, artist_lower, cur.lastrowid),
        )
        conn.commit()
        conn.close()
        return dump_path

    def test_end_to_end_dump_treffer_verhindert_live_abfrage(
        self, tmp_path, monkeypatch
    ):
        conn = cs.open_cache(tmp_path / "cache.db")
        cs.put_provider(conn, "lrclib", "the artist", "the title", "nichts", None)

        dump_path = self._make_dump_file(
            tmp_path, "the artist", "the title", "[00:01.00]dump-inhalt"
        )
        monkeypatch.setattr(lyrics_core, "_LRCLIB_DUMP_PATH", dump_path)

        def _fail_if_called(*a, **k):
            pytest.fail(
                "retry_missing muss einen Dump-Treffer nutzen, nicht live fragen"
            )

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fail_if_called)

        fetch_providers.retry_missing(conn)

        # Regressionscheck: die Dump-Verbindung wurde tatsächlich geöffnet.
        assert lyrics_core._lrclib_dump_conn is not None
        assert cs.get_provider(conn, "lrclib", "the artist", "the title") == {
            "status": "treffer",
            "content": "[00:01.00]dump-inhalt",
        }

    def test_end_to_end_fehlschlag_wird_durch_dump_treffer_zu_treffer(
        self, tmp_path, monkeypatch
    ):
        """Startstatus 'fehlschlag' (nicht 'nichts') -- passend zum vom Nutzer
        live beobachteten Symptom ("weiterhin Fehler (rate_limit)")."""
        conn = cs.open_cache(tmp_path / "cache.db")
        cs.put_provider(
            conn,
            "lrclib",
            "the artist",
            "the title",
            "fehlschlag",
            None,
            fehlergrund="rate_limit",
        )

        dump_path = self._make_dump_file(
            tmp_path, "the artist", "the title", "[00:01.00]dump-inhalt"
        )
        monkeypatch.setattr(lyrics_core, "_LRCLIB_DUMP_PATH", dump_path)

        def _fail_if_called(*a, **k):
            pytest.fail(
                "retry_missing muss einen Dump-Treffer nutzen, nicht live fragen"
            )

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fail_if_called)

        fetch_providers.retry_missing(conn)

        # Ohne jede Live-Abfrage: der Cache-Eintrag ist von 'fehlschlag' auf
        # 'treffer' mit dem Dump-Inhalt gewechselt.
        assert cs.get_provider(conn, "lrclib", "the artist", "the title") == {
            "status": "treffer",
            "content": "[00:01.00]dump-inhalt",
        }
