"""Unit-Tests für fetch_songtext.py — reine Logikfunktionen."""

import errno
import subprocess
import tempfile
import time
from pathlib import Path

import json
import unicodedata

import pytest

import cache_store
import fetch_songtext
from fetch_songtext import (
    _CONSENSUS_MIN_JACCARD,
    _FOLDER_BUSY,
    _HALLUCINATION_MAX_UNIQUE_RATIO,
    _HALLUCINATION_MIN_WORDS,
    _RATE_LIMIT_BASE_SEC,
    _RATE_LIMIT_FLOOR_SEC,
    _RATE_LIMIT_LONG_PAUSE_SEC,
    _RATE_LIMIT_MAX_SEC,
    _RATE_LIMIT_STUCK_THRESHOLD,
    _VOCALS_MIN_WORDS,
    _WHISPER_MIN_OVERLAP,
    _build_idf,
    _clean_query_title,
    _extract_lrc_words,
    _first_timestamp,
    _heuristic_best,
    _idf,
    _idf_jaccard,
    _is_hallucination,
    _last_timestamp,
    _load_cache,
    _load_idf,
    _provider_consensus,
    _rate_limit_report,
    _rate_limit_wait,
    _release_folder,
    _save_cache,
    _try_claim_folder,
    _word_overlap,
    fetch_lrc,
)


class TestCleanQueryTitle:
    def test_ohne_klammern_unveraendert(self):
        assert _clean_query_title("Highway Star") == "Highway Star"

    def test_eine_klammer_entfernt(self):
        assert _clean_query_title("Highway Star (Live In Osaka)") == "Highway Star"

    def test_mehrere_klammern_entfernt(self):
        title = "Highway Star (Live In Osaka Japan 16th August 1972) (2014 Remix)"
        assert _clean_query_title(title) == "Highway Star"

    def test_eckige_klammer_entfernt(self):
        assert (
            _clean_query_title("Made In Japan [Deluxe Edition 2014 Remix]")
            == "Made In Japan"
        )

    def test_nur_klammer_faellt_auf_original_zurueck(self):
        assert _clean_query_title("(Live)") == "(Live)"

    def test_klammer_mitten_im_titel(self):
        assert (
            _clean_query_title("I Want You (She's So Heavy) Reprise")
            == "I Want You Reprise"
        )


class TestIsHallucination:
    def test_leer(self):
        assert _is_hallucination([]) is False

    def test_zu_kurz(self):
        # Unter _HALLUCINATION_MIN_WORDS → nie Halluzination
        words = ["lets", "go"] * (_HALLUCINATION_MIN_WORDS // 2 - 1)
        assert len(words) < _HALLUCINATION_MIN_WORDS
        assert _is_hallucination(words) is False

    def test_klare_halluzination(self):
        # "let's go" × 20 → nur 2 einzigartige Wörter
        words = ["lets", "go"] * 20
        assert _is_hallucination(words) is True

    def test_normaler_text(self):
        # Typischer Liedtext — viele einzigartige Wörter
        words = (
            "girl you know its true ooh i love you"
            " i'm in love with you girl cause you're on my mind"
            " you're the one i think about most every time"
            " and when you pack a smile in everything you do"
        ).split()
        assert _is_hallucination(words) is False

    def test_grenzwert_ratio(self):
        # Genau an der Grenze: unique/total = _HALLUCINATION_MAX_UNIQUE_RATIO
        # z.B. 5 einzigartige auf 20 Wörter = 25 % → nicht als Halluzination
        unique = ["a", "b", "c", "d", "e"]
        total = unique * (_HALLUCINATION_MIN_WORDS // len(unique))
        ratio = len(set(total)) / len(total)
        assert ratio == _HALLUCINATION_MAX_UNIQUE_RATIO
        assert _is_hallucination(total) is False

    def test_knapp_unter_grenzwert(self):
        # 4 einzigartige auf 20 Wörter = 20 % → Halluzination
        unique = ["a", "b", "c", "d"]
        total = unique * 5  # 20 Wörter, 4 einzigartig = 20 %
        assert len(set(total)) / len(total) < _HALLUCINATION_MAX_UNIQUE_RATIO
        assert _is_hallucination(total) is True

    def test_mlx_halluzination(self):
        # Echtes Beispiel aus mlx-whisper-Test
        text = "Ot i mean i mean i mean lets go lets go i mean i mean lets go lets go lets go lets go"
        words = text.split()
        assert _is_hallucination(words) is True


class TestVocalsMinWords:
    def test_konstante_gesetzt(self):
        assert _VOCALS_MIN_WORDS >= 2

    def test_sonder_token(self):
        # "(upbeat music)" → 2 Wörter → unter Schwelle
        words = ["upbeat", "music"]
        assert (
            sum(len(w) > 0 for w in [words]) < _VOCALS_MIN_WORDS
            or len(words) < _VOCALS_MIN_WORDS
        )

    def test_echte_vokale(self):
        # Echter Liedtext → deutlich über Schwelle
        words = "girl you know its true i love you".split()
        assert len(words) >= _VOCALS_MIN_WORDS


class TestWordOverlap:
    def test_identisch(self):
        w = ["girl", "you", "know"]
        assert _word_overlap(w, w) == 1.0

    def test_keine_überschneidung(self):
        assert _word_overlap(["a", "b"], ["c", "d"]) == 0.0

    def test_halb_überschneidung(self):
        # {a,b} ∩ {b,c} = {b}, ∪ = {a,b,c} → 1/3
        assert abs(_word_overlap(["a", "b"], ["b", "c"]) - 1 / 3) < 1e-9

    def test_leer(self):
        assert _word_overlap([], ["a"]) == 0.0
        assert _word_overlap(["a"], []) == 0.0

    def test_duplikate_ignoriert(self):
        # Jaccard arbeitet auf Mengen, Duplikate zählen nicht doppelt
        assert _word_overlap(["a", "a", "b"], ["a", "b", "b"]) == 1.0


class TestExtractLrcWords:
    def test_basis(self):
        lrc = "[00:10.00]Girl you know it's true\n[00:15.00]I love you\n"
        words = _extract_lrc_words(lrc)
        assert "girl" in words
        assert "true" in words
        assert "love" in words

    def test_metadaten_überspringen(self):
        lrc = "[ar:Milli Vanilli]\n[ti:Girl]\n[00:10.00]Hello world\n"
        words = _extract_lrc_words(lrc)
        assert "ar" not in words
        assert "hello" in words

    def test_zahlen_nicht_enthalten(self):
        lrc = "[00:10.00]Track 1 of 10\n"
        words = _extract_lrc_words(lrc)
        assert "1" not in words
        assert "10" not in words
        assert "of" in words

    def test_sektion_labels_entfernt(self):
        # C1: Genius-Sektion-Labels dürfen nicht als Wörter landen
        lrc = (
            "[Chorus]\n"
            "[00:10.00]Girl you know it's true\n"
            "[Verse 1]\n"
            "[00:15.00]I love you\n"
            "[Guitar Solo]\n"
        )
        words = _extract_lrc_words(lrc)
        assert "chorus" not in words
        assert "verse" not in words
        assert "guitar" not in words
        assert "solo" not in words
        assert "girl" in words
        assert "love" in words


class TestTimestamps:
    LRC = "[00:05.00]intro\n[01:30.50]verse\n[03:20.00]outro\n"

    def test_first_timestamp(self):
        assert abs(_first_timestamp(self.LRC) - 5.0) < 0.01

    def test_last_timestamp(self):
        assert abs(_last_timestamp(self.LRC) - 200.0) < 0.01

    def test_kein_timestamp(self):
        assert _first_timestamp("kein timestamp hier") == 0.0
        assert _last_timestamp("kein timestamp hier") == 0.0

    def test_metadaten_bei_first_übersprungen(self):
        lrc = "[ar:Artist]\n[00:08.00]first lyric\n"
        assert abs(_first_timestamp(lrc) - 8.0) < 0.01


def _make_lrc(text: str) -> Path:
    """Hilfsfunktion: LRC-Inhalt in eine Temp-Datei schreiben."""
    f = tempfile.NamedTemporaryFile(
        suffix=".lrc", delete=False, mode="w", encoding="utf-8"
    )
    f.write(text)
    f.close()
    return Path(f.name)


class TestProviderConsensus:
    LRC_A = "[00:10.00]Girl you know it's true I love you\n[00:15.00]I'm in love with you girl\n"
    LRC_B = "[00:10.00]Girl you know it's true yes I love you\n[00:15.00]I'm in love girl cause you're on my mind\n"
    LRC_C = "[00:10.00]You know it's true I love you girl oh\n[00:15.00]In love with you girl cause you're my mind\n"
    LRC_WRONG = (
        "[00:10.00]Opa Opa tanzen alle Leute\n[00:15.00]Opa Opa heute und auch morgen\n"
    )

    def _paths(self, *texts):
        return [_make_lrc(t) for t in texts]

    def test_zu_wenig_provider(self):
        paths = self._paths(self.LRC_A, self.LRC_B)
        rep, score = _provider_consensus(paths)
        assert rep is None
        assert score == 0.0
        for p in paths:
            p.unlink(missing_ok=True)

    def test_konsens_erreicht(self):
        paths = self._paths(self.LRC_A, self.LRC_B, self.LRC_C)
        rep, score = _provider_consensus(paths)
        assert rep is not None
        assert score >= _CONSENSUS_MIN_JACCARD
        for p in paths:
            p.unlink(missing_ok=True)

    def test_ausreisser_c3_gerettet(self):
        # C3: 2 ähnliche + 1 komplett falscher LRC → avg unter Schwelle,
        # aber C3 wirft den Ausreißer heraus und findet Konsens unter den 2 guten.
        paths = self._paths(self.LRC_A, self.LRC_B, self.LRC_WRONG)
        rep, score = _provider_consensus(paths)
        assert rep is not None, "C3 sollte Konsens aus LRC_A+LRC_B retten"
        assert score >= _CONSENSUS_MIN_JACCARD
        content = rep.read_text(encoding="utf-8")
        assert "Opa" not in content
        for p in paths:
            p.unlink(missing_ok=True)

    def test_leere_lrc_zählt_nicht(self):
        paths = self._paths(self.LRC_A, self.LRC_B, "")
        rep, score = _provider_consensus(paths)
        assert rep is None  # leere LRC hat keine Wörter → unter MIN_PROVIDERS
        for p in paths:
            p.unlink(missing_ok=True)

    def test_min_providers_2_reicht_fuer_no_whisper_fallback(self):
        # --no-whisper: 2 übereinstimmende Provider reichen (min_providers=2)
        paths = self._paths(self.LRC_A, self.LRC_B)
        rep, score = _provider_consensus(paths, min_providers=2)
        assert rep is not None
        assert score >= _CONSENSUS_MIN_JACCARD
        for p in paths:
            p.unlink(missing_ok=True)

    def test_min_providers_2_bei_uneinigkeit_kein_konsens(self):
        paths = self._paths(self.LRC_A, self.LRC_WRONG)
        rep, score = _provider_consensus(paths, min_providers=2)
        assert rep is None
        for p in paths:
            p.unlink(missing_ok=True)


class TestHeuristicBest:
    LRC = "[00:10.00]Zeile eins\n[00:20.00]Zeile zwei\n[00:190.00]Letzte Zeile\n"

    def test_dauer_passt_liefert_inhalt(self):
        path = _make_lrc(self.LRC)
        content, score = _heuristic_best([path], expected_dur=200.0)
        assert content is not None
        assert score[0] == 1  # valid
        path.unlink(missing_ok=True)

    def test_dauer_weicht_zu_stark_ab_kein_inhalt(self):
        # last_ts=190s, expected_dur=50s → weit über _LRC_TOO_LONG_TOLERANCE
        path = _make_lrc(self.LRC)
        content, score = _heuristic_best([path], expected_dur=50.0)
        assert content is None
        assert score[0] == 0  # invalid
        path.unlink(missing_ok=True)

    def test_wählt_besten_von_mehreren_kandidaten(self):
        good = _make_lrc(self.LRC)  # passt zu expected_dur
        bad = _make_lrc(
            "[00:10.00]Kurz\n"
        )  # kürzer, weniger Zeilen — schlechterer Score
        content, score = _heuristic_best([bad, good], expected_dur=200.0)
        assert content == good.read_bytes()
        good.unlink(missing_ok=True)
        bad.unlink(missing_ok=True)


def _fake_query_provider(contents: dict[str, str]):
    """Ersetzt fetch_songtext._query_provider — liefert LRC-Inhalte ohne Netzwerk."""

    def _fake(
        query: str, provider: str, env: dict, artist: str = "", title: str = ""
    ) -> tuple[str, Path | None]:
        if provider not in contents:
            return provider, None
        return provider, _make_lrc(contents[provider])

    return _fake


class TestFetchLrcNoWhisper:
    """Integrationstests für den elif no_whisper-Zweig in fetch_lrc() selbst —
    nicht nur seine Einzelbausteine (_provider_consensus, _heuristic_best)."""

    LRC_A = TestProviderConsensus.LRC_A
    LRC_B = TestProviderConsensus.LRC_B
    DAUER_LRC = TestHeuristicBest.LRC  # last_ts = 190s

    def test_2p_konsens_wird_geschrieben(self, tmp_path, monkeypatch):
        # Nur 2 Provider treffen, aber inhaltlich einig → 2P-Konsens-Fallback
        monkeypatch.setattr(
            fetch_songtext,
            "_query_provider",
            _fake_query_provider({"lrclib": self.LRC_A, "genius": self.LRC_B}),
        )
        lrc_path = tmp_path / "out.lrc"
        found, info, extras = fetch_lrc("query", lrc_path, env={}, no_whisper=True)
        assert found is True
        assert extras["method"] == "konsens"
        assert extras.get("reason") is None
        assert "(2P)" in info
        assert lrc_path.exists()

    def test_heuristik_akzeptiert_bei_passender_dauer(self, tmp_path, monkeypatch):
        # Nur 1 Provider → kein Konsens möglich (weder 3P noch 2P) → Heuristik
        monkeypatch.setattr(
            fetch_songtext,
            "_query_provider",
            _fake_query_provider({"lrclib": self.DAUER_LRC}),
        )
        lrc_path = tmp_path / "out.lrc"
        found, info, extras = fetch_lrc(
            "query", lrc_path, env={}, expected_dur=200.0, no_whisper=True
        )
        assert found is True
        assert extras["method"] == "heuristik"
        assert extras.get("reason") is None
        assert lrc_path.exists()

    def test_heuristik_lehnt_bei_dauer_abweichung_ab(self, tmp_path, monkeypatch):
        # Gleicher einzelner Kandidat, aber Dauer passt nicht (190s LRC vs. 50s Track)
        monkeypatch.setattr(
            fetch_songtext,
            "_query_provider",
            _fake_query_provider({"lrclib": self.DAUER_LRC}),
        )
        lrc_path = tmp_path / "out.lrc"
        found, info, extras = fetch_lrc(
            "query", lrc_path, env={}, expected_dur=50.0, no_whisper=True
        )
        assert found is False
        assert extras["reason"] == "dauer-abweichung"
        assert not lrc_path.exists()


class TestFetchLrcFast:
    """Integrationstests für den `fast`-Parameter von fetch_lrc(): Phase 1 des
    Zwei-Phasen-Workflows. Konsens und 'kein Provider' laufen wie im
    Normalmodus, der Whisper-Fall wird stattdessen aufgeschoben (kein
    Whisper, keine Heuristik-Vermutung, kein Schreiben der .lrc)."""

    LRC_A = TestProviderConsensus.LRC_A
    LRC_B = TestProviderConsensus.LRC_B
    LRC_C = TestProviderConsensus.LRC_C

    def test_3p_konsens_schreibt_normal_trotz_fast(self, tmp_path, monkeypatch):
        # 3 Provider einig → Konsens wird auch mit fast=True direkt geschrieben,
        # ganz ohne Whisper (wie im Normalmodus).
        monkeypatch.setattr(
            fetch_songtext,
            "_query_provider",
            _fake_query_provider(
                {"lrclib": self.LRC_A, "genius": self.LRC_B, "netease": self.LRC_C}
            ),
        )
        lrc_path = tmp_path / "out.lrc"
        found, info, extras = fetch_lrc("query", lrc_path, env={}, fast=True)
        assert found is True
        assert extras["method"] == "konsens"
        assert extras.get("deferred") is None
        assert lrc_path.exists()

    def test_kein_provider_bleibt_kein_provider_trotz_fast(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetch_songtext, "_query_provider", _fake_query_provider({}))
        lrc_path = tmp_path / "out.lrc"
        found, info, extras = fetch_lrc("query", lrc_path, env={}, fast=True)
        assert found is False
        assert extras["reason"] == "kein-provider"
        assert extras.get("deferred") is None
        assert not lrc_path.exists()

    def test_whisper_fall_wird_aufgeschoben_ohne_whisper_aufruf(
        self, tmp_path, monkeypatch
    ):
        # Nur 2 Provider → kein 3er-Konsens → im Normalmodus liefe jetzt
        # Whisper. Mit fast=True muss stattdessen aufgeschoben werden.
        monkeypatch.setattr(
            fetch_songtext,
            "_query_provider",
            _fake_query_provider({"lrclib": self.LRC_A, "genius": self.LRC_B}),
        )

        def _fail_if_called(*args, **kwargs):
            pytest.fail("Whisper darf im --fast-Modus nicht aufgerufen werden")

        monkeypatch.setattr(fetch_songtext, "_whisper_best", _fail_if_called)
        monkeypatch.setattr(fetch_songtext, "_transcribe", _fail_if_called)

        flac_path = tmp_path / "dummy.flac"
        flac_path.write_bytes(b"")  # nur .exists() zählt, Inhalt irrelevant

        existing_lrc = tmp_path / "existing.lrc"
        existing_content = b"[00:01.00]alte Zeile\n"
        existing_lrc.write_bytes(existing_content)

        lrc_path = tmp_path / "out.lrc"
        found, info, extras = fetch_lrc(
            "query",
            lrc_path,
            env={},
            flac_path=flac_path,
            existing_lrc=existing_lrc,
            fast=True,
        )

        assert found is False
        assert extras["deferred"] is True
        assert "aufgeschoben" in info
        assert not lrc_path.exists()  # nichts geschrieben
        # Vorhandene .lrc bleibt komplett unangetastet
        assert existing_lrc.read_bytes() == existing_content


class TestLoadCache:
    """Dateinamen (ä/ö/ü) können je nach Zugriffsweg (lokal vs. SMB) NFC- oder
    NFD-normalisiert ankommen — ohne Vereinheitlichung beim Laden verpasst der
    Cache-Lookup vorhandene Einträge und legt Duplikate an."""

    NFC = unicodedata.normalize("NFC", "Mücken.flac")  # ue als 1 Zeichen (U+00FC)
    NFD = unicodedata.normalize(
        "NFD", "Mücken.flac"
    )  # u + Kombinierender Akzent (2 Zeichen)

    def test_normal_load_no_duplicates(self, tmp_path):
        (tmp_path / ".fetch_songtext.json").write_text(
            json.dumps({"a.flac": {"r": "ok", "ts": "2026-01-01T00:00:00"}}),
            encoding="utf-8",
        )
        cache = _load_cache(tmp_path)
        assert cache == {"a.flac": {"r": "ok", "ts": "2026-01-01T00:00:00"}}

    def test_missing_file_returns_empty_dict(self, tmp_path):
        assert _load_cache(tmp_path) == {}

    def test_corrupt_json_returns_empty_dict(self, tmp_path):
        (tmp_path / ".fetch_songtext.json").write_text("{kaputt", encoding="utf-8")
        assert _load_cache(tmp_path) == {}

    def test_nfc_nfd_duplicate_merged_keeps_newer(self, tmp_path):
        assert (
            self.NFC != self.NFD
        )  # sicherstellen, dass die Testdaten wirklich unterschiedliche Bytes sind
        assert unicodedata.normalize("NFC", self.NFD) == self.NFC
        raw = {
            self.NFC: {"r": "ok", "ts": "2026-07-11T07:31:35"},
            self.NFD: {"r": "nf", "ts": "2026-07-12T10:48:14"},
        }
        (tmp_path / ".fetch_songtext.json").write_text(
            json.dumps(raw), encoding="utf-8"
        )
        cache = _load_cache(tmp_path)
        assert len(cache) == 1
        assert cache[self.NFC]["r"] == "nf"  # der neuere (per ts) Eintrag gewinnt
        assert cache[self.NFC]["ts"] == "2026-07-12T10:48:14"

    def test_nfd_older_than_nfc_keeps_nfc(self, tmp_path):
        # Reihenfolge in der Datei darf keine Rolle spielen -- nur "ts" zählt
        raw = {
            self.NFD: {"r": "nf", "ts": "2026-07-11T07:31:35"},
            self.NFC: {"r": "ok", "ts": "2026-07-12T10:48:14"},
        }
        (tmp_path / ".fetch_songtext.json").write_text(
            json.dumps(raw), encoding="utf-8"
        )
        cache = _load_cache(tmp_path)
        assert len(cache) == 1
        assert cache[self.NFC]["r"] == "ok"
        assert cache[self.NFC]["ts"] == "2026-07-12T10:48:14"


class TestSaveCache:
    """_save_cache() muss gegen parallel laufende fetch_songtext-Instanzen
    im selben Ordner robust sein: Ohne Lock+Reload-vor-Schreiben würde ein
    Prozess, der vor dem Schreiben eines anderen Prozesses geladen hat,
    dessen Eintrag beim eigenen Schreiben stillschweigend verlieren."""

    def test_normal_save_roundtrips(self, tmp_path):
        _save_cache(tmp_path, {"a.flac": {"r": "ok", "ts": "2026-01-01T00:00:00"}})
        assert _load_cache(tmp_path) == {
            "a.flac": {"r": "ok", "ts": "2026-01-01T00:00:00"}
        }

    def test_concurrent_write_does_not_lose_other_processes_entry(self, tmp_path):
        # Prozess A lädt den (leeren) Ordner.
        cache_a = _load_cache(tmp_path)
        # Prozess B schreibt währenddessen einen anderen Track (A weiß nichts davon).
        _save_cache(tmp_path, {"b.flac": {"r": "ok", "ts": "2026-01-01T00:00:01"}})
        # Prozess A verarbeitet seinen eigenen Track und schreibt jetzt.
        cache_a["a.flac"] = {"r": "ok", "ts": "2026-01-01T00:00:02"}
        _save_cache(tmp_path, cache_a)
        # B's Eintrag darf nicht verloren gegangen sein.
        result = _load_cache(tmp_path)
        assert result == {
            "a.flac": {"r": "ok", "ts": "2026-01-01T00:00:02"},
            "b.flac": {"r": "ok", "ts": "2026-01-01T00:00:01"},
        }

    def test_conflicting_same_key_newer_ts_wins(self, tmp_path):
        cache_a = _load_cache(tmp_path)
        # Prozess B schreibt denselben Track zuerst, mit neuerem ts.
        _save_cache(tmp_path, {"a.flac": {"r": "ok", "ts": "2026-01-01T00:00:05"}})
        # Prozess A hatte den Track vorher geladen (leer) und schreibt mit älterem ts nach.
        cache_a["a.flac"] = {"r": "nf", "ts": "2026-01-01T00:00:01"}
        _save_cache(tmp_path, cache_a)
        assert _load_cache(tmp_path)["a.flac"]["ts"] == "2026-01-01T00:00:05"


class TestFolderClaim:
    """_try_claim_folder()/_release_folder(): non-blocking Ordner-Sperre für
    bewusst parallele Instanzen. EAGAIN/EWOULDBLOCK (echt belegt) -> _FOLDER_BUSY,
    Aufrufer überspringt den Ordner. Jeder andere OSError (z.B. ENOTSUP auf
    Netzwerk-Mounts ohne flock-Support) -> None, Aufrufer arbeitet unkoordiniert
    weiter statt fälschlich die ganze Bibliothek zu überspringen."""

    def test_first_claim_succeeds(self, tmp_path):
        lock = _try_claim_folder(tmp_path)
        assert lock is not None and lock is not _FOLDER_BUSY
        _release_folder(lock)

    def test_second_claim_while_held_returns_busy(self, tmp_path):
        lock_a = _try_claim_folder(tmp_path)
        lock_b = _try_claim_folder(tmp_path)
        assert lock_b is _FOLDER_BUSY
        _release_folder(lock_a)

    def test_claim_possible_again_after_release(self, tmp_path):
        lock_a = _try_claim_folder(tmp_path)
        _release_folder(lock_a)
        lock_b = _try_claim_folder(tmp_path)
        assert lock_b is not None and lock_b is not _FOLDER_BUSY
        _release_folder(lock_b)

    def test_save_cache_reuses_held_lock_without_deadlock(self, tmp_path):
        lock = _try_claim_folder(tmp_path)
        _save_cache(
            tmp_path,
            {"a.flac": {"r": "ok", "ts": "2026-01-01T00:00:00"}},
            lockfile=lock,
        )
        _release_folder(lock)
        assert _load_cache(tmp_path) == {
            "a.flac": {"r": "ok", "ts": "2026-01-01T00:00:00"}
        }

    def test_flock_eagain_maps_to_busy(self, tmp_path, monkeypatch):
        def raise_eagain(*a, **k):
            raise OSError(errno.EAGAIN, "Resource temporarily unavailable")

        monkeypatch.setattr(fetch_songtext.fcntl, "flock", raise_eagain)
        assert _try_claim_folder(tmp_path) is _FOLDER_BUSY

    def test_flock_unsupported_falls_back_to_unlocked(self, tmp_path, monkeypatch):
        def raise_enotsup(*a, **k):
            raise OSError(errno.ENOTSUP, "Operation not supported")

        monkeypatch.setattr(fetch_songtext.fcntl, "flock", raise_enotsup)
        assert _try_claim_folder(tmp_path) is None

    def test_release_survives_externally_closed_fd(self, tmp_path):
        """Regression (v1.7.8): Wird der rohe fd der Lock-Datei quergeschlossen
        (z.B. durch einen nebenläufigen Subprozess/C-Bibliothek), steht das
        Python-Objekt noch offen und flock(LOCK_UN) wirft OSError EBADF. Das darf
        den Lauf nicht mehr abbrechen — der Kernel hat die Sperre beim Schließen
        des fd bereits freigegeben."""
        import os

        lock = _try_claim_folder(tmp_path)
        assert lock is not None and lock is not _FOLDER_BUSY
        os.close(lock.fileno())  # fd quer wegschließen, Objekt bleibt "offen"
        _release_folder(lock)  # darf nicht mehr werfen

    def test_release_survives_closed_lock_object(self, tmp_path):
        """_release_folder muss auch idempotent gegen ein bereits geschlossenes
        Lock-Objekt sein (ValueError statt OSError)."""
        lock = _try_claim_folder(tmp_path)
        assert lock is not None and lock is not _FOLDER_BUSY
        lock.close()
        _release_folder(lock)  # darf nicht mehr werfen


class TestLoadRelease:
    """Gleicher Grund wie TestLoadCache: Titel aus release.json (NFC, JSON-Text)
    müssen gegen den Dateinamen-Stem (kann über SMB als NFD ankommen) matchen."""

    def test_title_lookup_matches_across_normalization_forms(self, tmp_path):
        release = {
            "artist": "Testartist",
            "tracks": [
                {"title": unicodedata.normalize("NFC", "Mücken"), "dur_s": 123.0}
            ],
        }
        (tmp_path / "release.json").write_text(json.dumps(release), encoding="utf-8")
        artist, tracks_by_title = fetch_songtext._load_release(tmp_path)
        assert artist == "Testartist"
        # Lookup mit NFD-Titel (wie er z.B. aus audio.stem über SMB kommen könnte)
        nfd_title = unicodedata.normalize("NFD", "Mücken")
        assert tracks_by_title.get(unicodedata.normalize("NFC", nfd_title)) == 123.0

    def test_missing_release_json_returns_empty(self, tmp_path):
        artist, tracks_by_title = fetch_songtext._load_release(tmp_path)
        assert artist == ""
        assert tracks_by_title == {}


class TestRateLimit:
    """Backoff-Logik für Provider-Rate-Limits (siehe ROADMAP v1.7.3).

    Recherchiert im syncedlyrics-Quellcode: Musixmatch meldet Rate-Limits
    über stderr ("Got status code N"), NetEase nur über eine generische
    Fehlermeldung, Genius/lrclib geben KEIN Signal — dort greift nur der
    proaktive Mindestabstand (_RATE_LIMIT_FLOOR_SEC), auch bei sauberem
    Erfolg (leeres stderr)."""

    @pytest.fixture(autouse=True)
    def _reset_state(self):
        fetch_songtext._rate_limit_state.clear()
        yield
        fetch_songtext._rate_limit_state.clear()

    def test_clean_success_sets_only_proactive_floor(self):
        _rate_limit_report("lrclib", "")
        state = fetch_songtext._rate_limit_state["lrclib"]
        assert state["consecutive_hits"] == 0
        remaining = state["next_allowed"] - time.monotonic()
        assert 0 < remaining <= _RATE_LIMIT_FLOOR_SEC

    def test_status_402_triggers_base_backoff(self):
        _rate_limit_report("musixmatch", "[Musixmatch] Got status code 402 for foo")
        state = fetch_songtext._rate_limit_state["musixmatch"]
        assert state["consecutive_hits"] == 1
        remaining = state["next_allowed"] - time.monotonic()
        assert _RATE_LIMIT_FLOOR_SEC < remaining <= _RATE_LIMIT_BASE_SEC

    def test_status_401_captcha_triggers_longer_backoff_than_402(self):
        _rate_limit_report("musixmatch", "[Musixmatch] Got status code 401 for foo")
        remaining_401 = (
            fetch_songtext._rate_limit_state["musixmatch"]["next_allowed"]
            - time.monotonic()
        )
        fetch_songtext._rate_limit_state.clear()
        _rate_limit_report("musixmatch", "[Musixmatch] Got status code 402 for foo")
        remaining_402 = (
            fetch_songtext._rate_limit_state["musixmatch"]["next_allowed"]
            - time.monotonic()
        )
        assert remaining_401 > remaining_402

    def test_netease_generic_error_treated_like_402(self):
        _rate_limit_report(
            "netease", "An error occurred while searching for an LRC on NetEase"
        )
        assert fetch_songtext._rate_limit_state["netease"]["consecutive_hits"] == 1

    def test_repeated_hits_escalate_up_to_cap_below_threshold(self):
        # Bleibt unterhalb von _RATE_LIMIT_STUCK_THRESHOLD — dort gilt weiterhin
        # die alte, bei _RATE_LIMIT_MAX_SEC gedeckelte Eskalation (siehe unten
        # für das Verhalten AB dem Schwellwert: lange Ruhephase).
        for _ in range(_RATE_LIMIT_STUCK_THRESHOLD - 1):
            _rate_limit_report("musixmatch", "Got status code 402 for foo")
        remaining = (
            fetch_songtext._rate_limit_state["musixmatch"]["next_allowed"]
            - time.monotonic()
        )
        assert remaining <= _RATE_LIMIT_MAX_SEC

    def test_hits_reaching_stuck_threshold_trigger_long_pause(self):
        for _ in range(_RATE_LIMIT_STUCK_THRESHOLD):
            _rate_limit_report("musixmatch", "[Musixmatch] Got status code 401 for foo")
        state = fetch_songtext._rate_limit_state["musixmatch"]
        assert state["consecutive_hits"] == _RATE_LIMIT_STUCK_THRESHOLD
        remaining = state["next_allowed"] - time.monotonic()
        assert _RATE_LIMIT_MAX_SEC < remaining <= _RATE_LIMIT_LONG_PAUSE_SEC

    def test_clean_success_after_hits_resets_consecutive_count(self):
        _rate_limit_report("musixmatch", "Got status code 402 for foo")
        assert fetch_songtext._rate_limit_state["musixmatch"]["consecutive_hits"] == 1
        _rate_limit_report("musixmatch", "")
        assert fetch_songtext._rate_limit_state["musixmatch"]["consecutive_hits"] == 0

    def test_genius_gets_only_proactive_floor_no_reactive_signal_possible(self):
        # Genius/lrclib melden laut syncedlyrics-Quellcode nie ein Rate-Limit-
        # Signal im stderr, auch nicht bei HTTP 429 — stderr bleibt leer.
        _rate_limit_report("genius", "")
        remaining = (
            fetch_songtext._rate_limit_state["genius"]["next_allowed"]
            - time.monotonic()
        )
        assert remaining <= _RATE_LIMIT_FLOOR_SEC

    def test_wait_returns_immediately_without_prior_lock(self):
        start = time.monotonic()
        _rate_limit_wait("unbekannter_provider")
        assert time.monotonic() - start < 0.05

    def test_wait_sleeps_until_next_allowed(self):
        fetch_songtext._rate_limit_state["lrclib"] = {
            "next_allowed": time.monotonic() + 0.1,
            "consecutive_hits": 0,
        }
        start = time.monotonic()
        _rate_limit_wait("lrclib")
        assert time.monotonic() - start >= 0.09

    def test_wait_below_threshold_still_sleeps_and_returns_false(self, monkeypatch):
        # 3 von 5 Treffern: unterhalb von _RATE_LIMIT_STUCK_THRESHOLD, altes
        # Verhalten bleibt unverändert — kurzer sleep, kein Überspringen.
        fetch_songtext._rate_limit_state["musixmatch"] = {
            "next_allowed": time.monotonic() + 0.1,
            "consecutive_hits": _RATE_LIMIT_STUCK_THRESHOLD - 2,
        }
        start = time.monotonic()
        result = _rate_limit_wait("musixmatch")
        assert time.monotonic() - start >= 0.09
        assert result is False

    def test_wait_at_stuck_threshold_skips_without_sleeping(self, monkeypatch):
        def _fail_if_slept(*a, **k):
            pytest.fail("_rate_limit_wait darf in der langen Ruhephase NICHT schlafen")

        monkeypatch.setattr(fetch_songtext.time, "sleep", _fail_if_slept)
        fetch_songtext._rate_limit_state["musixmatch"] = {
            "next_allowed": time.monotonic() + 900.0,
            "consecutive_hits": _RATE_LIMIT_STUCK_THRESHOLD,
        }
        start = time.monotonic()
        result = _rate_limit_wait("musixmatch")
        assert result is True
        assert time.monotonic() - start < 0.05

    def test_wait_after_long_pause_expired_returns_false_fresh_attempt_due(self):
        # Ruhephase künstlich in die Vergangenheit versetzt: kein Überspringen
        # mehr, ein frischer Live-Versuch ist wieder fällig.
        fetch_songtext._rate_limit_state["musixmatch"] = {
            "next_allowed": time.monotonic() - 1.0,
            "consecutive_hits": _RATE_LIMIT_STUCK_THRESHOLD,
        }
        result = _rate_limit_wait("musixmatch")
        assert result is False


class TestIdf:
    # Synthetisches Korpus: n_docs=100, "the"/"a" fast überall (niedrige IDF),
    # "love" mittelhäufig, "xylophone" extrem selten (hohe IDF).
    N_DOCS = 100
    DF = {"the": 99, "a": 99, "love": 50, "xylophone": 1}

    def test_haeufiges_wort_hat_niedrige_idf(self):
        import math

        expected = math.log((self.N_DOCS + 1) / (self.DF["the"] + 1))
        assert abs(_idf("the", self.N_DOCS, self.DF) - expected) < 1e-9

    def test_seltenes_wort_hat_hohe_idf(self):
        assert _idf("xylophone", self.N_DOCS, self.DF) > _idf(
            "the", self.N_DOCS, self.DF
        )

    def test_unbekanntes_wort_laplace_geglaettet(self):
        import math

        # Wort nicht im Korpus (df=0) -> log((N+1)/(0+1)), endlich, nicht unendlich
        expected = math.log((self.N_DOCS + 1) / 1)
        assert abs(_idf("quixotic", self.N_DOCS, self.DF) - expected) < 1e-9

    def test_idf_monoton_fallend_in_df(self):
        assert _idf("love", self.N_DOCS, self.DF) > _idf("the", self.N_DOCS, self.DF)


class TestIdfJaccard:
    N_DOCS = 100
    DF = {"the": 99, "a": 99, "and": 99, "love": 50, "xylophone": 1, "quixotic": 1}

    def test_leer(self):
        assert _idf_jaccard(set(), {"a"}, self.N_DOCS, self.DF) == 0.0
        assert _idf_jaccard({"a"}, set(), self.N_DOCS, self.DF) == 0.0

    def test_identische_mengen_ergeben_eins(self):
        s = {"the", "xylophone"}
        assert abs(_idf_jaccard(s, s, self.N_DOCS, self.DF) - 1.0) < 1e-9

    def test_disjunkte_mengen_ergeben_null(self):
        assert _idf_jaccard({"the"}, {"a"}, self.N_DOCS, self.DF) == 0.0

    def test_seltenes_gemeinsames_wort_dominiert_score(self):
        # Überschneidung ist nur "xylophone" (sehr selten -> hohe IDF), Rest
        # unterscheidet sich nur in häufigen Wörtern (niedrige IDF) -> Score nahe 1.
        transcript = {"xylophone", "the"}
        lrc = {"xylophone", "and"}
        score = _idf_jaccard(transcript, lrc, self.N_DOCS, self.DF)
        assert score > 0.9

    def test_nur_haeufige_woerter_gemeinsam_ergibt_niedrigen_score(self):
        # Überschneidung nur aus häufigen (uninformativen) Wörtern, dazu viele
        # unbekannte (nicht überlappende) Wörter -> Score bleibt klein.
        transcript = {"the", "a", "and", "foo"}
        lrc = {"the", "a", "and", "bar", "baz"}
        score = _idf_jaccard(transcript, lrc, self.N_DOCS, self.DF)
        assert score < _WHISPER_MIN_OVERLAP

    def test_fremder_text_unter_schwelle_passender_text_darueber(self):
        # Realistischeres Mini-Szenario: "passender" Transkript-/LRC-Ausschnitt
        # teilt inhaltstragende (seltene) Wörter -> über Schwelle. Ein fremder/
        # generischer Transkript-Ausschnitt teilt nur Stopwords -> unter Schwelle.
        lrc_words = {"the", "a", "love", "xylophone", "quixotic"}

        fremder_transcript = {"the", "a", "and", "yeah", "oh"}
        passender_transcript = {"the", "love", "xylophone", "quixotic"}

        fremder_score = _idf_jaccard(
            fremder_transcript, lrc_words, self.N_DOCS, self.DF
        )
        passender_score = _idf_jaccard(
            passender_transcript, lrc_words, self.N_DOCS, self.DF
        )

        assert fremder_score < _WHISPER_MIN_OVERLAP
        assert passender_score >= _WHISPER_MIN_OVERLAP


class TestLoadIdf:
    def test_fehlende_datei_bricht_mit_klarer_meldung_ab(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            fetch_songtext, "_IDF_CACHE_PATH", tmp_path / "missing.json"
        )
        monkeypatch.setattr(fetch_songtext, "_idf_cache", None)
        with pytest.raises(SystemExit):
            _load_idf()
        out = capsys.readouterr().out
        assert "--rebuild-idf" in out

    def test_laedt_und_cached_module_level(self, tmp_path, monkeypatch):
        cache_path = tmp_path / "idf.json"
        cache_path.write_text(
            json.dumps({"n_docs": 5, "df": {"a": 1}}), encoding="utf-8"
        )
        monkeypatch.setattr(fetch_songtext, "_IDF_CACHE_PATH", cache_path)
        monkeypatch.setattr(fetch_songtext, "_idf_cache", None)

        n_docs, df = _load_idf()
        assert (n_docs, df) == (5, {"a": 1})

        # Datei danach ändern — zweiter Aufruf muss den module-level Cache
        # zurückgeben, nicht neu von Platte lesen.
        cache_path.write_text(json.dumps({"n_docs": 999, "df": {}}), encoding="utf-8")
        n_docs2, df2 = _load_idf()
        assert (n_docs2, df2) == (5, {"a": 1})


class TestBuildIdf:
    def test_baut_dokumentfrequenz_ueber_lrc_dateien(self, tmp_path, monkeypatch):
        out_path = tmp_path / "out.json"
        monkeypatch.setattr(fetch_songtext, "_IDF_CACHE_PATH", out_path)

        lib = tmp_path / "lib"
        (lib / "A").mkdir(parents=True)
        (lib / "A" / "a.lrc").write_text("[00:01.00]hello world\n", encoding="utf-8")
        (lib / "A" / "b.lrc").write_text("[00:01.00]hello there\n", encoding="utf-8")
        # Leere LRC (kein Text) darf nicht als Dokument zählen
        (lib / "A" / "empty.lrc").write_text("[00:01.00]\n", encoding="utf-8")

        _build_idf(lib)

        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert data["n_docs"] == 2
        assert data["df"]["hello"] == 2
        assert data["df"]["world"] == 1
        assert data["df"]["there"] == 1
        assert "" not in data["df"]


class TestFastFlagMain:
    """End-to-End-Test über main(): --fast darf für einen aufgeschobenen
    Whisper-Fall weder einen Cache-Eintrag schreiben noch die vorhandene
    .lrc anfassen (Voraussetzung für den Zwei-Phasen-Workflow: Phase 2,
    ein normaler Lauf, muss den Track als ungesehen wiederfinden)."""

    def test_fast_defers_without_cache_entry_or_lrc_write(
        self, tmp_path, monkeypatch, capsys
    ):
        album = tmp_path / "Artist - Album"
        album.mkdir()
        audio = album / "01 Song.flac"
        audio.write_bytes(b"")  # Inhalt irrelevant, nur .exists() zählt
        lrc_path = audio.with_suffix(".lrc")
        old_lrc_content = b"[00:01.00]alte Zeile\n"
        lrc_path.write_bytes(old_lrc_content)

        monkeypatch.setattr(
            fetch_songtext, "_read_audio_tags", lambda p: ("Artist", "Song", "")
        )
        # Nur 2 Provider treffen -> kein 3er-Konsens möglich -> im Normalmodus
        # liefe jetzt Whisper.
        monkeypatch.setattr(
            fetch_songtext,
            "_query_provider",
            _fake_query_provider(
                {
                    "lrclib": TestProviderConsensus.LRC_A,
                    "genius": TestProviderConsensus.LRC_B,
                }
            ),
        )

        def _fail_if_called(*args, **kwargs):
            pytest.fail("Whisper darf im --fast-Modus nicht aufgerufen werden")

        monkeypatch.setattr(fetch_songtext, "_whisper_best", _fail_if_called)
        monkeypatch.setattr(fetch_songtext, "_transcribe", _fail_if_called)
        monkeypatch.setattr(fetch_songtext, "_get_whisper_model", _fail_if_called)

        # --no-cache: main() ohne Cache-Mock würde sonst die ECHTE
        # fetch_songtext_cache.db neben dem Skript öffnen (Path(__file__).parent-
        # Pfad) — mit dem oben global gepatchten _read_audio_tags ("Artist"/
        # "Song") wäre das eine reale Datenkorruption der Produktions-DB.
        monkeypatch.setattr(
            "sys.argv", ["fetch_songtext.py", str(album), "--fast", "--no-cache"]
        )
        fetch_songtext.main()

        # Kein Cache-Eintrag für den aufgeschobenen Track.
        cache = _load_cache(album)
        assert cache == {}
        # Vorhandene .lrc bleibt komplett unangetastet.
        assert lrc_path.read_bytes() == old_lrc_content

        out = capsys.readouterr().out
        assert "aufgeschoben" in out
        assert "1 aufgeschoben für Whisper" in out


class TestProviderCache:
    """_query_provider mit echtem cache_store (siehe CACHE_DESIGN.md)."""

    def _open(self, tmp_path):
        conn = cache_store.open_cache(tmp_path / "cache.db")
        fetch_songtext._cache_conn = conn
        fetch_songtext._cache_ttl_days = 30
        fetch_songtext._cache_refresh = False
        fetch_songtext._cache_only = False
        return conn

    def teardown_method(self):
        fetch_songtext._cache_conn = None
        fetch_songtext._cache_refresh = False
        fetch_songtext._cache_only = False

    def test_cache_hit_skips_live_query(self, tmp_path, monkeypatch):
        conn = self._open(tmp_path)
        cache_store.put_provider(
            conn, "lrclib", "the artist", "the title", "treffer", "[00:01.00]Hallo Welt"
        )

        def _fail_if_called(*a, **k):
            pytest.fail("Live-Abfrage darf bei Cache-Treffer nicht laufen")

        monkeypatch.setattr(fetch_songtext.subprocess, "run", _fail_if_called)
        provider, path = fetch_songtext._query_provider(
            "the artist the title", "lrclib", {}, artist="the artist", title="the title"
        )
        assert path is not None
        assert "Hallo Welt" in path.read_text(encoding="utf-8")

    def test_cache_nichts_hit_skips_live_query(self, tmp_path, monkeypatch):
        conn = self._open(tmp_path)
        cache_store.put_provider(conn, "genius", "x", "y", "nichts", None)

        def _fail_if_called(*a, **k):
            pytest.fail("Live-Abfrage darf bei gecachtem 'nichts' nicht laufen")

        monkeypatch.setattr(fetch_songtext.subprocess, "run", _fail_if_called)
        provider, path = fetch_songtext._query_provider(
            "x y", "genius", {}, artist="x", title="y"
        )
        assert path is None

    def test_clean_miss_is_cached_as_nichts(self, tmp_path, monkeypatch):
        conn = self._open(tmp_path)

        class _Result:
            stderr = ""

        def _fake_run(*a, **k):
            return _Result()

        monkeypatch.setattr(fetch_songtext.subprocess, "run", _fake_run)
        provider, path = fetch_songtext._query_provider(
            "a b", "lrclib", {}, artist="a", title="b"
        )
        assert path is None
        cached = cache_store.get_provider(conn, "lrclib", "a", "b")
        assert cached == {"status": "nichts", "content": None}

    def test_transient_error_ist_kein_cache_treffer_aber_wird_festgehalten(
        self, tmp_path, monkeypatch
    ):
        conn = self._open(tmp_path)

        class _Result:
            stderr = "Got status code 402"

        def _fake_run(*a, **k):
            return _Result()

        monkeypatch.setattr(fetch_songtext.subprocess, "run", _fake_run)
        fetch_songtext._query_provider("a b", "musixmatch", {}, artist="a", title="b")
        # Kein gültiger Cache-Treffer beim nächsten Aufruf ...
        assert cache_store.get_provider(conn, "musixmatch", "a", "b") is None
        # ... aber der Fehlschlag steht mit Grund in der Datenbank, nicht spurlos.
        row = conn.execute(
            "SELECT status, fehlergrund FROM ergebnisse e "
            "JOIN songs s ON s.id = e.song_id "
            "WHERE e.quelle=? AND s.artist_key=? AND s.titel_key=?",
            ("musixmatch", "a", "b"),
        ).fetchone()
        assert row == ("fehlschlag", "rate_limit")

    def test_timeout_ist_kein_cache_treffer_aber_wird_festgehalten(
        self, tmp_path, monkeypatch
    ):
        conn = self._open(tmp_path)

        def _fake_run(*a, **k):
            raise subprocess.TimeoutExpired(cmd="x", timeout=1)

        monkeypatch.setattr(fetch_songtext.subprocess, "run", _fake_run)
        fetch_songtext._query_provider("a b", "netease", {}, artist="a", title="b")
        assert cache_store.get_provider(conn, "netease", "a", "b") is None
        row = conn.execute(
            "SELECT status, fehlergrund FROM ergebnisse e "
            "JOIN songs s ON s.id = e.song_id "
            "WHERE e.quelle=? AND s.artist_key=? AND s.titel_key=?",
            ("netease", "a", "b"),
        ).fetchone()
        assert row == ("fehlschlag", "timeout")

    def test_force_umgeht_auch_den_provider_cache(self, tmp_path, monkeypatch):
        """--force (main() setzt dafuer _cache_refresh) muss den Provider-Cache
        genauso umgehen wie --refresh-cache — sonst liefert --force veraltete
        Cache-Treffer statt frisch zu fragen."""
        conn = self._open(tmp_path)
        cache_store.put_provider(conn, "lrclib", "a", "b", "treffer", "alter text")

        fetch_songtext._cache_refresh = True  # simuliert --force bzw. --refresh-cache
        try:
            called = []

            class _Result:
                stderr = ""

            def _fake_run(*a, **k):
                called.append(1)
                return _Result()

            monkeypatch.setattr(fetch_songtext.subprocess, "run", _fake_run)
            fetch_songtext._query_provider("a b", "lrclib", {}, artist="a", title="b")
            assert called, (
                "--force/--refresh-cache muss live abfragen, nicht aus dem Cache bedienen"
            )
        finally:
            fetch_songtext._cache_refresh = False

    def test_stuck_provider_skips_live_query_without_changing_state(
        self, tmp_path, monkeypatch
    ):
        conn = self._open(tmp_path)
        fetch_songtext._rate_limit_state["musixmatch"] = {
            "next_allowed": time.monotonic() + 900.0,
            "consecutive_hits": _RATE_LIMIT_STUCK_THRESHOLD,
        }
        state_before = dict(fetch_songtext._rate_limit_state["musixmatch"])

        def _fail_if_called(*a, **k):
            pytest.fail("Live-Abfrage darf während der langen Ruhephase nicht laufen")

        monkeypatch.setattr(fetch_songtext.subprocess, "run", _fail_if_called)
        try:
            provider, path = fetch_songtext._query_provider(
                "a b", "musixmatch", {}, artist="a", title="b"
            )
            assert (provider, path) == ("musixmatch", None)
            row = conn.execute(
                "SELECT status, fehlergrund FROM ergebnisse e "
                "JOIN songs s ON s.id = e.song_id "
                "WHERE e.quelle=? AND s.artist_key=? AND s.titel_key=?",
                ("musixmatch", "a", "b"),
            ).fetchone()
            assert row == ("fehlschlag", "gesperrt")
            # Kein neuer Versuch fand statt — Ruhephasen-Zustand bleibt exakt
            # unangetastet, bis sie von selbst abläuft.
            assert fetch_songtext._rate_limit_state["musixmatch"] == state_before
        finally:
            fetch_songtext._rate_limit_state.pop("musixmatch", None)

    def test_no_cache_conn_falls_back_to_live(self, monkeypatch):
        fetch_songtext._cache_conn = None  # simuliert --no-cache / fehlende DB

        class _Result:
            stderr = ""

        called = []

        def _fake_run(*a, **k):
            called.append(1)
            return _Result()

        monkeypatch.setattr(fetch_songtext.subprocess, "run", _fake_run)
        fetch_songtext._query_provider("a b", "lrclib", {}, artist="a", title="b")
        assert called, "Ohne offene Cache-Verbindung muss live abgefragt werden"

    def test_cache_only_mit_treffer_liefert_cache_inhalt(self, tmp_path, monkeypatch):
        conn = self._open(tmp_path)
        cache_store.put_provider(
            conn, "lrclib", "the artist", "the title", "treffer", "[00:01.00]Hallo Welt"
        )
        fetch_songtext._cache_only = True

        def _fail_if_called(*a, **k):
            pytest.fail("Live-Abfrage darf bei Cache-Treffer nicht laufen")

        monkeypatch.setattr(fetch_songtext.subprocess, "run", _fail_if_called)
        provider, path = fetch_songtext._query_provider(
            "the artist the title", "lrclib", {}, artist="the artist", title="the title"
        )
        assert path is not None
        assert "Hallo Welt" in path.read_text(encoding="utf-8")

    def test_cache_only_ohne_eintrag_liefert_none_ohne_live_abfrage_und_ohne_cache_schreiben(
        self, tmp_path, monkeypatch
    ):
        conn = self._open(tmp_path)
        fetch_songtext._cache_only = True

        def _fail_if_called(*a, **k):
            pytest.fail("--cache-only darf niemals live abfragen")

        monkeypatch.setattr(fetch_songtext.subprocess, "run", _fail_if_called)
        provider, path = fetch_songtext._query_provider(
            "a b", "lrclib", {}, artist="a", title="b"
        )
        assert (provider, path) == ("lrclib", None)
        assert cache_store.get_provider(conn, "lrclib", "a", "b") is None

    def test_cache_only_bei_gecachtem_fehlschlag_fragt_nicht_live_nach(
        self, tmp_path, monkeypatch
    ):
        conn = self._open(tmp_path)
        cache_store.put_provider(
            conn, "musixmatch", "a", "b", "fehlschlag", None, fehlergrund="rate_limit"
        )
        row_before = conn.execute(
            "SELECT status, fehlergrund, datum FROM ergebnisse e "
            "JOIN songs s ON s.id = e.song_id "
            "WHERE e.quelle=? AND s.artist_key=? AND s.titel_key=?",
            ("musixmatch", "a", "b"),
        ).fetchone()

        fetch_songtext._cache_only = True

        def _fail_if_called(*a, **k):
            pytest.fail("--cache-only darf gecachte Fehlschläge nicht live nachfragen")

        monkeypatch.setattr(fetch_songtext.subprocess, "run", _fail_if_called)
        provider, path = fetch_songtext._query_provider(
            "a b", "musixmatch", {}, artist="a", title="b"
        )
        assert (provider, path) == ("musixmatch", None)
        row_after = conn.execute(
            "SELECT status, fehlergrund, datum FROM ergebnisse e "
            "JOIN songs s ON s.id = e.song_id "
            "WHERE e.quelle=? AND s.artist_key=? AND s.titel_key=?",
            ("musixmatch", "a", "b"),
        ).fetchone()
        assert row_after == row_before

    def test_cache_only_ohne_cache_conn_fragt_trotzdem_nicht_live(self, monkeypatch):
        fetch_songtext._cache_conn = None  # simuliert --no-cache / fehlende DB
        fetch_songtext._cache_only = True

        def _fail_if_called(*a, **k):
            pytest.fail("--cache-only muss auch ohne offene Cache-Verbindung greifen")

        monkeypatch.setattr(fetch_songtext.subprocess, "run", _fail_if_called)
        provider, path = fetch_songtext._query_provider(
            "a b", "lrclib", {}, artist="a", title="b"
        )
        assert (provider, path) == ("lrclib", None)


class TestTranscriptCache:
    """_whisper_best mit echtem cache_store: Song-Identität (Künstler+Titel)

    statt Datei-Identität. Ein gecachtes Transkript gehört zu GENAU EINEM Song
    (artist_key/titel_key) — unabhängig von Datei, Modell oder Fenster-Parametern.
    """

    def teardown_method(self):
        fetch_songtext._cache_conn = None
        fetch_songtext._cache_refresh = False

    def _prep(self, monkeypatch, tmp_path):
        conn = cache_store.open_cache(tmp_path / "cache.db")
        fetch_songtext._cache_conn = conn
        fetch_songtext._cache_ttl_days = 30
        fetch_songtext._cache_refresh = False
        monkeypatch.setattr(fetch_songtext, "_get_whisper_model", lambda name: object())
        monkeypatch.setattr(fetch_songtext, "_load_idf", lambda: (1, {}))
        monkeypatch.setattr(
            fetch_songtext, "_detect_lrc_language", lambda candidates: None
        )
        return conn

    def _make_lrc(self, tmp_path, name, content):
        p = tmp_path / name
        p.write_text(content, encoding="utf-8")
        return p

    def test_cache_hit_skips_transcribe(self, tmp_path, monkeypatch):
        conn = self._prep(monkeypatch, tmp_path)
        cache_store.put_transcript(
            conn, "the artist", "the title", "hello world foo bar", 0.1, -0.2
        )

        def _fail_if_called(*a, **k):
            pytest.fail("_transcribe darf bei Song-Cache-Treffer nicht laufen")

        monkeypatch.setattr(fetch_songtext, "_transcribe", _fail_if_called)

        flac = tmp_path / "song.flac"
        flac.write_bytes(b"x")
        lrc = self._make_lrc(tmp_path, "a.lrc", "[00:01.00]hello world foo bar\n")

        best_path, score, has_vocals, words, model, lang = fetch_songtext._whisper_best(
            flac, [lrc], artist="The Artist", title="The Title"
        )
        assert best_path == lrc
        assert has_vocals is True
        assert words == 4

    def test_miss_transcribes_and_writes_cache(self, tmp_path, monkeypatch):
        self._prep(monkeypatch, tmp_path)

        def _fake_transcribe(path, start, ctx, model, language=None):
            return ["hello", "world", "foo", "bar"], 0.05, -0.3

        monkeypatch.setattr(fetch_songtext, "_transcribe", _fake_transcribe)

        flac = tmp_path / "song2.flac"
        flac.write_bytes(b"y")
        lrc = self._make_lrc(tmp_path, "b.lrc", "[00:01.00]hello world foo bar\n")

        best_path, score, has_vocals, words, model, lang = fetch_songtext._whisper_best(
            flac, [lrc], artist="Another Artist", title="Another Title"
        )
        assert best_path == lrc

        cached = cache_store.get_transcript(
            fetch_songtext._cache_conn, "another artist", "another title"
        )
        assert cached["transcript"] == "hello world foo bar"
        assert cached["no_speech_prob"] == 0.05
        assert cached["avg_logprob"] == -0.3

    def test_zweiter_lauf_selber_song_nutzt_cache_ohne_erneutes_transkribieren(
        self, tmp_path, monkeypatch
    ):
        """Zwei verschiedene Kandidaten-Pfade/Fenster für DENSELBEN Song (artist+title):
        der zweite _whisper_best-Aufruf nutzt den Song-Cache, _transcribe läuft nur einmal."""
        self._prep(monkeypatch, tmp_path)

        calls = []

        def _counting_transcribe(path, start, ctx, model, language=None):
            calls.append(path)
            return ["hello", "world", "foo", "bar"], 0.05, -0.3

        monkeypatch.setattr(fetch_songtext, "_transcribe", _counting_transcribe)

        flac1 = tmp_path / "song_v1.flac"
        flac1.write_bytes(b"y1")
        lrc1 = self._make_lrc(tmp_path, "c1.lrc", "[00:01.00]hello world foo bar\n")

        flac2 = tmp_path / "song_v2.flac"
        flac2.write_bytes(b"y2")
        lrc2 = self._make_lrc(tmp_path, "c2.lrc", "[00:05.00]hello world foo bar\n")

        fetch_songtext._whisper_best(
            flac1, [lrc1], artist="Same Artist", title="Same Title"
        )
        assert len(calls) == 1

        fetch_songtext._whisper_best(
            flac2, [lrc2], artist="Same Artist", title="Same Title"
        )
        assert len(calls) == 1  # kein zweiter _transcribe-Aufruf für denselben Song


class TestCacheCliFlags:
    def test_help_lists_cache_flags(self):
        out = subprocess.run(
            ["python3", "fetch_songtext.py", "--help"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
        ).stdout
        assert "--no-cache" in out
        assert "--refresh-cache" in out
        assert "--cache-ttl" in out
        assert "--cache-only" in out

    def test_cache_only_und_no_cache_schliessen_sich_aus(self):
        result = subprocess.run(
            ["python3", "fetch_songtext.py", "--cache-only", "--no-cache", "x"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert "--cache-only" in result.stderr

    def test_cache_only_und_force_schliessen_sich_aus(self):
        result = subprocess.run(
            ["python3", "fetch_songtext.py", "--cache-only", "--force", "x"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert "--cache-only" in result.stderr

    def test_cache_only_und_refresh_cache_schliessen_sich_aus(self):
        result = subprocess.run(
            ["python3", "fetch_songtext.py", "--cache-only", "--refresh-cache", "x"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert "--cache-only" in result.stderr
