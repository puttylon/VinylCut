"""Unit-Tests für fetch_songtext.py — reine Logikfunktionen."""

import tempfile
from pathlib import Path

import fetch_songtext
from fetch_songtext import (
    _CONSENSUS_MIN_JACCARD,
    _CONSENSUS_MIN_PROVIDERS,
    _HALLUCINATION_MAX_UNIQUE_RATIO,
    _HALLUCINATION_MIN_WORDS,
    _VOCALS_MIN_WORDS,
    _extract_lrc_words,
    _first_timestamp,
    _heuristic_best,
    _is_hallucination,
    _last_timestamp,
    _provider_consensus,
    _word_overlap,
    fetch_lrc,
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
        assert sum(len(w) > 0 for w in [words]) < _VOCALS_MIN_WORDS or len(words) < _VOCALS_MIN_WORDS

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
    f = tempfile.NamedTemporaryFile(suffix=".lrc", delete=False, mode="w", encoding="utf-8")
    f.write(text)
    f.close()
    return Path(f.name)


class TestProviderConsensus:
    LRC_A = "[00:10.00]Girl you know it's true I love you\n[00:15.00]I'm in love with you girl\n"
    LRC_B = "[00:10.00]Girl you know it's true yes I love you\n[00:15.00]I'm in love girl cause you're on my mind\n"
    LRC_C = "[00:10.00]You know it's true I love you girl oh\n[00:15.00]In love with you girl cause you're my mind\n"
    LRC_WRONG = "[00:10.00]Opa Opa tanzen alle Leute\n[00:15.00]Opa Opa heute und auch morgen\n"

    def _paths(self, *texts):
        return [_make_lrc(t) for t in texts]

    def test_zu_wenig_provider(self):
        paths = self._paths(self.LRC_A, self.LRC_B)
        rep, score = _provider_consensus(paths)
        assert rep is None
        assert score == 0.0
        for p in paths: p.unlink(missing_ok=True)

    def test_konsens_erreicht(self):
        paths = self._paths(self.LRC_A, self.LRC_B, self.LRC_C)
        rep, score = _provider_consensus(paths)
        assert rep is not None
        assert score >= _CONSENSUS_MIN_JACCARD
        for p in paths: p.unlink(missing_ok=True)

    def test_ausreisser_c3_gerettet(self):
        # C3: 2 ähnliche + 1 komplett falscher LRC → avg unter Schwelle,
        # aber C3 wirft den Ausreißer heraus und findet Konsens unter den 2 guten.
        paths = self._paths(self.LRC_A, self.LRC_B, self.LRC_WRONG)
        rep, score = _provider_consensus(paths)
        assert rep is not None, "C3 sollte Konsens aus LRC_A+LRC_B retten"
        assert score >= _CONSENSUS_MIN_JACCARD
        content = rep.read_text(encoding="utf-8")
        assert "Opa" not in content
        for p in paths: p.unlink(missing_ok=True)

    def test_leere_lrc_zählt_nicht(self):
        paths = self._paths(self.LRC_A, self.LRC_B, "")
        rep, score = _provider_consensus(paths)
        assert rep is None  # leere LRC hat keine Wörter → unter MIN_PROVIDERS
        for p in paths: p.unlink(missing_ok=True)

    def test_min_providers_2_reicht_fuer_no_whisper_fallback(self):
        # --no-whisper: 2 übereinstimmende Provider reichen (min_providers=2)
        paths = self._paths(self.LRC_A, self.LRC_B)
        rep, score = _provider_consensus(paths, min_providers=2)
        assert rep is not None
        assert score >= _CONSENSUS_MIN_JACCARD
        for p in paths: p.unlink(missing_ok=True)

    def test_min_providers_2_bei_uneinigkeit_kein_konsens(self):
        paths = self._paths(self.LRC_A, self.LRC_WRONG)
        rep, score = _provider_consensus(paths, min_providers=2)
        assert rep is None
        for p in paths: p.unlink(missing_ok=True)


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
        bad = _make_lrc("[00:10.00]Kurz\n")  # kürzer, weniger Zeilen — schlechterer Score
        content, score = _heuristic_best([bad, good], expected_dur=200.0)
        assert content == good.read_bytes()
        good.unlink(missing_ok=True)
        bad.unlink(missing_ok=True)


def _fake_query_provider(contents: dict[str, str]):
    """Ersetzt fetch_songtext._query_provider — liefert LRC-Inhalte ohne Netzwerk."""

    def _fake(query: str, provider: str, env: dict) -> tuple[str, Path | None]:
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
