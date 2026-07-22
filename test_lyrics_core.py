"""Unit-Tests für lyrics_core.py -- reine Logikfunktionen.

Ehemals test_fetch_songtext.py (bis zum Umbau auf die Phasen-Pipeline, siehe
Git-Historie): Tests für fetch_lrc(), _whisper_rerun_needed() und das alte
main()/CLI sind entfallen, weil diese Funktionen mit dem Umbau ersatzlos
gestrichen wurden (siehe ROADMAP.md, "Songtexte-Pipeline-Umbau") --
evaluate_lyrics.py/write_lrc.py/songtext_pipeline.py haben ihre eigenen
Tests. Alle anderen Tests hier prüfen unverändert dieselben Funktionen wie
vorher, nur unter dem neuen Modulnamen.
"""

import errno
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

import json
import unicodedata

import pytest

import cache_store
import lyrics_core
from lyrics_core import (
    _CONSENSUS_MIN_JACCARD,
    _CONTRASTIVE_ABSOLUTE_FLOOR,
    _CONTRASTIVE_MARGIN,
    _CONTRASTIVE_MIN_BACKGROUND,
    _FOLDER_BUSY,
    _HALLUCINATION_MAX_UNIQUE_RATIO,
    _HALLUCINATION_MAX_UNIQUE_WORDS,
    _HALLUCINATION_MIN_WORDS,
    _RATE_LIMIT_BASE_SEC,
    _RATE_LIMIT_FLOOR_SEC,
    _RATE_LIMIT_LONG_PAUSE_SEC,
    _RATE_LIMIT_MAX_SEC,
    _RATE_LIMIT_STUCK_THRESHOLD,
    _VOCALS_MIN_WORDS,
    _WHISPER_MIN_OVERLAP,
    _build_contrastive_context,
    _cache_entry_up_to_date,
    _clean_query_title,
    _contrastive_margin_and_decision,
    _dedupe_word_sets,
    _extract_lrc_words,
    _first_timestamp,
    _global_cache_idf,
    _group_candidates,
    _heuristic_best,
    _idf,
    _idf_jaccard,
    _is_hallucination,
    _last_timestamp,
    _load_cache,
    _looks_like_translation,
    _parse_cache_ts,
    _provider_consensus,
    _rate_limit_report,
    _rate_limit_wait,
    _release_folder,
    _resolve_lrc_language,
    _save_cache,
    _song_candidate_words,
    _try_claim_folder,
    _whisper_accept,
    _whisper_threshold_for,
    _word_overlap,
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

    def test_langes_echtes_outro_kein_alarm_trotz_niedriger_ratio(self):
        # Realer Bugfall: Bronski Beat/Communards "Never Can Say Goodbye" hat
        # ein langes echtes "no no no..."-Outro. Reales Whisper-Rohtranskript
        # (Modell medium, aus der Produktionsbibliothek): 405 Wörter, 94
        # einzigartig (23,2 % < 25 %-Ratio), häufigstes Wort "no" x149
        # (36,8 % >= 25 %) -- beide alten Kriterien schlagen an, obwohl der
        # Song echten Gesang hat. Die neue absolute Vokabelgrenze muss das
        # jetzt verhindern.
        text = (
            "no no no no no no no i never cared to say goodbye no no no i i "
            "never cared to say goodbye every time i think i ve had enough "
            "instead of heading for the door massive strange vibrations "
            "fierce in need i tear the code it says turn around to a fool "
            "and know you ll love him more and more tell me why tell me why "
            "is it so don t wanna let you go no i never can say goodbye boy "
            "ooh no no i never can say goodbye "
            + "no "
            * 100
            + "i can t say goodbye i keep thinking that our problems soon "
            "are all over the world but there s a same unhappy feeling "
            "there s an anguish there s a doubt it s a shame oh dizzy i "
            "know i can t get by without you tell me why is it sound i "
            "don t ever let you go no no no no no no no no no no hey you "
            "never can take my heart and never say goodbye and never say "
            "goodbye oh no no no no no no no no goodbye goodbye and say "
            "goodbye and say goodbye every time i think i ve had enough "
            "now we start heading for the door there s no single dizzy "
            "feeling piercing me right to the core it s just turning "
            "around to fool you know you ll love him more and more tell "
            "me why is it so i wanna let you go let you go let you go i "
            "wanna let you go hey i never can say goodbye ooh don t "
            "believe me i never can say goodbye no no no oh no no oh no "
            "no no hey i never can say goodbye boy say goodbye boy"
        )
        words = text.split()
        assert len(set(words)) / len(words) < _HALLUCINATION_MAX_UNIQUE_RATIO
        assert len(set(words)) > _HALLUCINATION_MAX_UNIQUE_WORDS
        assert _is_hallucination(words) is False

    def test_grenzwert_absolute_vokabelgroesse(self):
        # Genau _HALLUCINATION_MAX_UNIQUE_WORDS einzigartige Wörter insgesamt
        # (Fuellwoerter + das dominante Wort) -> beide alten Kriterien greifen
        # weiterhin, Loop-Verdacht bleibt bestehen.
        filler = [f"w{i}" for i in range(_HALLUCINATION_MAX_UNIQUE_WORDS - 1)]
        words = filler + ["dominant"] * 60
        assert len(set(words)) == _HALLUCINATION_MAX_UNIQUE_WORDS
        assert _is_hallucination(words) is True

    def test_ein_wort_ueber_grenzwert_kein_alarm(self):
        # Ein einzigartiges Wort mehr als die Grenze -> nicht mehr als
        # Halluzination behandelt, selbst bei identischem Wiederholungsmuster.
        filler = [f"w{i}" for i in range(_HALLUCINATION_MAX_UNIQUE_WORDS)]
        words = filler + ["dominant"] * 60
        assert len(set(words)) == _HALLUCINATION_MAX_UNIQUE_WORDS + 1
        assert _is_hallucination(words) is False


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


class TestGroupCandidates:
    def test_identischer_inhalt_wird_gruppiert(self):
        a = _make_lrc("[00:10.00]hello world foo bar\n")
        b = _make_lrc("[00:10.00]hello world foo bar\n")
        assert _group_candidates([a, b]) == [a]

    def test_wortgleich_andere_formatierung_wird_gruppiert(self):
        # Bugfix (siehe ROADMAP.md): byte-verschieden (Zeitstempel/Umbrüche),
        # aber wort-identisch -- muss trotzdem als eine Gruppe zaehlen.
        a = _make_lrc("[00:10.00]hello world foo bar\n")
        b = _make_lrc("[00:12.00]hello world foo bar\r\n")
        assert _group_candidates([a, b]) == [a]

    def test_komplett_verschieden_bleibt_getrennt(self):
        a = _make_lrc("[00:10.00]hello world foo bar\n")
        b = _make_lrc("[00:10.00]opa opa tanzen alle\n")
        assert _group_candidates([a, b]) == [a, b]

    def test_erster_in_reihenfolge_bleibt_repraesentant(self):
        a = _make_lrc("[00:10.00]hello world foo bar\n")
        b = _make_lrc("[00:10.00]hello world foo bar\n")
        assert _group_candidates([b, a]) == [b]

    def test_leere_datei_wird_uebersprungen(self):
        a = _make_lrc("[00:10.00]hello world\n")
        b = _make_lrc("")
        assert _group_candidates([a, b]) == [a]

    def test_fehlende_datei_wirft_nicht(self):
        a = _make_lrc("[00:10.00]hello world\n")
        assert _group_candidates([a, Path("/nicht/vorhanden.lrc")]) == [a]

    def test_drei_kandidaten_zwei_gruppen(self):
        a = _make_lrc("[00:10.00]hello world foo bar\n")
        b = _make_lrc("[00:12.00]hello world foo bar\r\n")  # wortgleich zu a
        c = _make_lrc("[00:10.00]opa opa tanzen alle\n")
        assert _group_candidates([a, b, c]) == [a, c]

    def test_unter_schwelle_bleibt_getrennt(self):
        # Nur teilweise Ueberlappung, deutlich unter der 0.90-Gruppierungs-
        # schwelle -- muss als zwei getrennte Kandidaten erhalten bleiben.
        a = _make_lrc("[00:10.00]a b c d\n")
        b = _make_lrc("[00:10.00]a b e f\n")
        assert _group_candidates([a, b]) == [a, b]


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

    def test_raw_count_rettet_starke_einigkeit_trotz_wenig_gruppen(self):
        # Bugfix (siehe ROADMAP.md, "Fernando-Fall"): 5 rohe Quellen, von
        # denen 4 praktisch wortgleich sind -- _group_candidates fasst sie zu
        # EINER Gruppe zusammen (2 Gruppen insgesamt: die große + 1 Ausreißer-
        # aehnliche). Ohne raw_count wuerde "nur 2 Gruppen" faelschlich als
        # "zu wenig Kandidaten" verworfen, obwohl beide Gruppen sich zu >80%
        # einig sind. Mit raw_count=5 (rohe Anzahl) besteht die Mindestanzahl-
        # Pruefung, die Rechnung selbst laeuft weiter auf den 2 Gruppen.
        raw_paths = self._paths(
            self.LRC_A, self.LRC_A, self.LRC_A, self.LRC_A, self.LRC_B
        )
        grouped = _group_candidates(raw_paths)
        assert len(grouped) == 2  # A-Quadrupel zu 1 Gruppe, B eigene Gruppe
        rep, score = _provider_consensus(grouped, raw_count=len(raw_paths))
        assert rep is not None, "starke Einigkeit zwischen den 2 Gruppen muss reichen"
        assert score >= _CONSENSUS_MIN_JACCARD
        for p in raw_paths:
            p.unlink(missing_ok=True)

    def test_raw_count_ohne_uebergabe_zaehlt_nur_auswertbare_kandidaten(self):
        # Regressionsschutz: OHNE explizites raw_count darf eine leere/
        # unlesbare Kandidatendatei nicht mitzaehlen (siehe
        # test_leere_lrc_zählt_nicht) -- der Default muss sich wie zuvor
        # verhalten, nicht einfach die rohe Listenlaenge nehmen.
        paths = self._paths(self.LRC_A, self.LRC_B, "")
        rep, score = _provider_consensus(paths)
        assert rep is None
        for p in paths:
            p.unlink(missing_ok=True)

    def test_raw_count_schuetzt_weiterhin_vor_zirkelschluss(self):
        # Fables Szenario (siehe ROADMAP.md): 3 rohe Quellen, 2 davon
        # (existing + ihr Herkunfts-Provider) gruppieren zusammen, die
        # dritte (echt abweichende, korrekte) Quelle bleibt eigene Gruppe.
        # raw_count=3 besteht die Mindestanzahl-Pruefung, aber die 2 Gruppen
        # sind sich UNEINIG (LRC_WRONG vs. LRC_A) -- kein Konsens, C3 kann
        # nicht rebten (braucht selbst >=3 Gruppen).
        raw_paths = self._paths(self.LRC_WRONG, self.LRC_WRONG, self.LRC_A)
        grouped = _group_candidates(raw_paths)
        assert len(grouped) == 2
        rep, score = _provider_consensus(grouped, raw_count=len(raw_paths))
        assert rep is None
        for p in raw_paths:
            p.unlink(missing_ok=True)


class TestWhisperAccept:
    """_whisper_accept() ohne Marge (margin=None): faellt auf die alte
    absolute IDF-Jaccard-Schwelle zurueck (_whisper_threshold_for)."""

    def test_standardmodus_nutzt_idf_jaccard_schwelle(self):
        assert _whisper_accept(_WHISPER_MIN_OVERLAP, None) is True
        assert _whisper_accept(_WHISPER_MIN_OVERLAP - 0.001, None) is False


class TestWhisperAcceptContrastive:
    """_whisper_accept() Standardverhalten (seit v1.10.0, vormals
    --contrastive-experiment): Hybrid-Regel (v1.9.14) -- akzeptiert wenn
    margin >= _CONTRASTIVE_MARGIN ODER score >= _CONTRASTIVE_ABSOLUTE_FLOOR.
    Der absolute Boden fängt Fälle ab, in denen ein einzelner fehlerhafter
    Kandidat im Hintergrund-Pool die Marge eines eigentlich korrekten
    Songtexts unter die Schwelle drückt (siehe Garth-Brooks-Fall,
    ROADMAP.md). margin=None (kein/zu kleiner Hintergrund-Pool) fällt
    unverändert auf die alte absolute Schwelle zurück."""

    def test_marge_ueber_schwelle_akzeptiert_unabhaengig_vom_score(self):
        # Score liegt weit UNTER dem absoluten Boden -- akzeptiert trotzdem,
        # weil die Marge allein schon ausreicht.
        assert _whisper_accept(0.001, None, margin=_CONTRASTIVE_MARGIN) is True

    def test_marge_unter_schwelle_und_score_unter_boden_lehnt_ab(self):
        # Weder Marge noch absoluter Boden erreicht -- abgelehnt.
        assert _whisper_accept(0.05, None, margin=_CONTRASTIVE_MARGIN - 0.001) is False

    def test_hoher_score_akzeptiert_trotz_negativer_marge_hybrid_boden(self):
        # Garth-Brooks-Fall: hoher Score (>= _CONTRASTIVE_ABSOLUTE_FLOOR), aber
        # Marge negativ (Hintergrund-Pool durch einen fehlerhaften Kandidaten
        # kontaminiert) -- der Hybrid-Boden greift trotzdem.
        assert (
            _whisper_accept(_CONTRASTIVE_ABSOLUTE_FLOOR + 0.2, None, margin=-0.02)
            is True
        )

    def test_niedriger_score_mit_negativer_marge_bleibt_abgelehnt(self):
        # Niedriger Score (< _CONTRASTIVE_ABSOLUTE_FLOOR) UND negative Marge --
        # kein Fehlalarm durch den neuen Boden, weiterhin abgelehnt.
        assert (
            _whisper_accept(_CONTRASTIVE_ABSOLUTE_FLOOR - 0.25, None, margin=-0.02)
            is False
        )

    def test_positive_marge_bei_niedrigem_score_bleibt_akzeptiert(self):
        # Alte Margen-Regel bleibt unverändert wirksam, auch wenn der Score
        # weit unter dem absoluten Boden liegt.
        assert (
            _whisper_accept(_CONTRASTIVE_ABSOLUTE_FLOOR - 0.25, None, margin=0.02)
            is True
        )

    def test_margin_none_faellt_auf_alte_absolute_schwelle_zurueck(self):
        assert _whisper_accept(_WHISPER_MIN_OVERLAP, None, margin=None) is True
        assert _whisper_accept(_WHISPER_MIN_OVERLAP - 0.001, None, margin=None) is False


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
    """Ersetzt lyrics_core._query_provider — liefert LRC-Inhalte ohne Netzwerk."""

    def _fake(
        query: str, provider: str, env: dict, artist: str = "", title: str = ""
    ) -> tuple[str, Path | None]:
        if provider not in contents:
            return provider, None
        return provider, _make_lrc(contents[provider])

    return _fake


class TestCurrentSig:
    """Signatur-Snapshot der Entscheidungs-Eingaben (siehe ROADMAP.md,
    "Songdatei als Single Point of Truth", "Big City Beats"-Fall)."""

    def test_ohne_conn_liefert_none(self):
        assert lyrics_core._current_sig(None, "a", "b") is None

    def test_ohne_song_in_db_ist_is_skip_false(self, tmp_path):
        conn = cache_store.open_cache(tmp_path / "cache.db")
        assert lyrics_core._current_sig(conn, "unbekannt", "song") == [
            "song",
            "unbekannt",
            False,
        ]

    def test_normales_genre_ist_is_skip_false(self, tmp_path):
        conn = cache_store.open_cache(tmp_path / "cache.db")
        cache_store._get_or_create_song(conn, "artist", "title", "Pop")
        assert lyrics_core._current_sig(conn, "artist", "title") == [
            "title",
            "artist",
            False,
        ]

    def test_skip_genre_ist_is_skip_true(self, tmp_path):
        conn = cache_store.open_cache(tmp_path / "cache.db")
        cache_store._get_or_create_song(
            conn, "artist", "title", "Club Remix Instrumental"
        )
        assert lyrics_core._current_sig(conn, "artist", "title") == [
            "title",
            "artist",
            True,
        ]

    def test_geaendertes_genre_aendert_die_signatur(self, tmp_path):
        # Kern des Bugfixes: dieselbe Song-Identitaet, aber ein Genre-
        # Wechsel zu Skip-worthy aendert die Signatur -- macht einen
        # bestehenden Cache-Eintrag automatisch veraltet.
        conn = cache_store.open_cache(tmp_path / "cache.db")
        cache_store._get_or_create_song(conn, "artist", "title", "Pop")
        sig_before = lyrics_core._current_sig(conn, "artist", "title")
        cache_store._get_or_create_song(conn, "artist", "title", "Instrumental")
        sig_after = lyrics_core._current_sig(conn, "artist", "title")
        assert sig_before != sig_after
        assert sig_before[2] is False
        assert sig_after[2] is True


class TestSigBackfill:
    """_sig_backfill(): reines Nachtragen der "sig" fuer Eintraege von vor
    dem Signatur-Fix, ohne echte Neubewertung -- nur wenn der Genre-Skip-
    Status seither nachweislich unveraendert ist (siehe ROADMAP.md, "Sig-
    Backfill")."""

    def test_kein_eintrag_liefert_none(self, tmp_path):
        conn = cache_store.open_cache(tmp_path / "cache.db")
        assert lyrics_core._sig_backfill(None, conn, "artist", "title") is None

    def test_eintrag_hat_bereits_sig_liefert_none(self, tmp_path):
        conn = cache_store.open_cache(tmp_path / "cache.db")
        entry = {"sig": ["title", "artist", False]}
        assert lyrics_core._sig_backfill(entry, conn, "artist", "title") is None

    def test_ohne_conn_liefert_none(self):
        entry = {"r": "ok"}
        assert lyrics_core._sig_backfill(entry, None, "artist", "title") is None

    def test_fehlender_ts_darf_nicht_nachgetragen_werden(self, tmp_path):
        # Ohne "ts" kann nicht geprueft werden, ob seitdem neue DB-Aktivitaet
        # dazukam -- konservativ ablehnen, echte Neubewertung noetig.
        conn = cache_store.open_cache(tmp_path / "cache.db")
        cache_store._get_or_create_song(conn, "artist", "title", "Pop")
        entry = {"v": "1.13.17", "r": "ok", "method": "konsens"}
        assert lyrics_core._sig_backfill(entry, conn, "artist", "title") is None

    def test_neuere_db_aktivitaet_darf_nicht_nachgetragen_werden(self, tmp_path):
        # Fehlendes "sig" ist nicht der EINZIGE Makel -- seit "ts" kam ein
        # neuer Provider-Treffer dazu, das braucht eine echte Neubewertung.
        conn = cache_store.open_cache(tmp_path / "cache.db")
        cache_store._get_or_create_song(conn, "artist", "title", "Pop")
        cache_store.put_provider(conn, "lrclib", "artist", "title", "treffer", "x")
        conn.commit()
        entry = {
            "v": "1.13.17",
            "r": "ok",
            "method": "konsens",
            "ts": "2000-01-01T00:00:00",
        }
        assert lyrics_core._sig_backfill(entry, conn, "artist", "title") is None

    def test_normaler_song_unveraendert_liefert_sig(self, tmp_path):
        # Alter Eintrag war eine normale Konsens-Bewertung (kein "reason"),
        # Genre ist weiterhin kein Skip-Genre -- gefahrloses Nachtragen.
        conn = cache_store.open_cache(tmp_path / "cache.db")
        cache_store._get_or_create_song(conn, "artist", "title", "Pop")
        entry = {
            "v": "1.13.17",
            "r": "ok",
            "method": "konsens",
            "ts": "2026-01-01T00:00:00",
        }
        result = lyrics_core._sig_backfill(entry, conn, "artist", "title")
        assert result == ["title", "artist", False]

    def test_skip_genre_unveraendert_liefert_sig(self, tmp_path):
        # Alter Eintrag war schon damals wegen Genre geskippt und ist es
        # immer noch -- gefahrloses Nachtragen.
        conn = cache_store.open_cache(tmp_path / "cache.db")
        cache_store._get_or_create_song(
            conn, "artist", "title", "Club Remix Instrumental"
        )
        entry = {
            "v": "1.13.17",
            "r": "ok",
            "reason": "kein-provider",
            "ts": "2026-01-01T00:00:00",
        }
        result = lyrics_core._sig_backfill(entry, conn, "artist", "title")
        assert result == ["title", "artist", True]

    def test_genre_wurde_zu_skip_darf_nicht_nachgetragen_werden(self, tmp_path):
        # Kern der Sicherung ("Big City Beats"-Fall): der alte Eintrag war
        # eine echte Bewertung (kein "reason"="kein-provider"), das Genre ist
        # inzwischen aber ein Skip-Genre -- braucht echte Neubewertung, sonst
        # bliebe eine veraltete Entscheidung fuer immer als "aktuell" stehen.
        conn = cache_store.open_cache(tmp_path / "cache.db")
        cache_store._get_or_create_song(
            conn, "artist", "title", "Club Remix Instrumental"
        )
        entry = {
            "v": "1.13.17",
            "r": "ok",
            "method": "konsens",
            "ts": "2026-01-01T00:00:00",
        }
        assert lyrics_core._sig_backfill(entry, conn, "artist", "title") is None

    def test_genre_ist_nicht_mehr_skip_darf_nicht_nachgetragen_werden(self, tmp_path):
        # Umgekehrter Fall: war geskippt, Genre ist jetzt normal -- braucht
        # eine echte Erstbewertung, nicht nur ein Nachtragen der sig.
        conn = cache_store.open_cache(tmp_path / "cache.db")
        cache_store._get_or_create_song(conn, "artist", "title", "Pop")
        entry = {
            "v": "1.13.17",
            "r": "ok",
            "reason": "kein-provider",
            "ts": "2026-01-01T00:00:00",
        }
        assert lyrics_core._sig_backfill(entry, conn, "artist", "title") is None


class TestCacheEntryUpToDate:
    """War als fast identisches Prädikat dreifach unabhängig implementiert:
    inline in write_lrc.write_all(), inline in cut.py (ohne DB-Check), als
    eigene Funktion evaluate_lyrics._skip_reevaluation() (siehe ROADMAP.md,
    Redundanz-Aufräumen)."""

    def test_kein_eintrag_ist_nicht_aktuell(self, tmp_path):
        assert _cache_entry_up_to_date(None, tmp_path / "a.lrc") is False

    def test_zu_alte_version_ist_nicht_aktuell(self, tmp_path):
        entry = {"v": "0.1", "r": "nf"}
        assert _cache_entry_up_to_date(entry, tmp_path / "a.lrc") is False

    def test_r_ok_ohne_vorhandene_lrc_ist_nicht_aktuell(self, tmp_path):
        entry = {"v": lyrics_core.__version__, "r": "ok"}
        assert _cache_entry_up_to_date(entry, tmp_path / "fehlt.lrc") is False

    def test_r_nf_ohne_conn_ist_aktuell(self, tmp_path):
        # Ohne conn (cut.py-Variante): kein DB-Aktualitaets-Check.
        entry = {"v": lyrics_core.__version__, "r": "nf"}
        assert _cache_entry_up_to_date(entry, tmp_path / "fehlt.lrc") is True

    def test_r_ok_mit_vorhandener_lrc_ohne_conn_ist_aktuell(self, tmp_path):
        lrc_path = tmp_path / "a.lrc"
        lrc_path.write_text("[00:01.00]x", encoding="utf-8")
        entry = {"v": lyrics_core.__version__, "r": "ok"}
        assert _cache_entry_up_to_date(entry, lrc_path) is True

    def test_mit_conn_veraltet_wenn_db_neuer_als_json(self, tmp_path):
        conn = cache_store.open_cache(tmp_path / "cache.db")
        cache_store._get_or_create_song(conn, "artist", "title", None)
        conn.commit()
        cache_store.put_provider(conn, "lrclib", "artist", "title", "treffer", "x")
        conn.commit()
        sig = lyrics_core._current_sig(conn, "artist", "title")
        entry = {
            "v": lyrics_core.__version__,
            "r": "nf",
            "ts": "2000-01-01T00:00:00",
            "sig": sig,
        }
        assert (
            _cache_entry_up_to_date(
                entry, tmp_path / "fehlt.lrc", conn, "artist", "title"
            )
            is False
        )

    def test_mit_conn_aktuell_wenn_json_neuer_als_db(self, tmp_path):
        import datetime

        conn = cache_store.open_cache(tmp_path / "cache.db")
        cache_store._get_or_create_song(conn, "artist", "title", None)
        conn.commit()
        cache_store.put_provider(conn, "lrclib", "artist", "title", "treffer", "x")
        conn.commit()
        future_ts = (
            datetime.datetime.now().astimezone() + datetime.timedelta(days=1)
        ).isoformat(timespec="seconds")
        sig = lyrics_core._current_sig(conn, "artist", "title")
        entry = {
            "v": lyrics_core.__version__,
            "r": "nf",
            "ts": future_ts,
            "sig": sig,
        }
        assert (
            _cache_entry_up_to_date(
                entry, tmp_path / "fehlt.lrc", conn, "artist", "title"
            )
            is True
        )

    def test_fehlende_signatur_ist_veraltet_selbstheilung(self, tmp_path):
        # Bugfix (siehe ROADMAP.md, "Big City Beats"-Fall): ein Eintrag ohne
        # "sig" (jeder vor diesem Fix geschriebene Eintrag) gilt automatisch
        # als veraltet -- Selbstheilung ohne Migrationsskript, auch wenn der
        # reine Zeitstempel-Vergleich fuer sich genommen "aktuell" saehe.
        import datetime

        conn = cache_store.open_cache(tmp_path / "cache.db")
        cache_store._get_or_create_song(conn, "artist", "title", None)
        future_ts = (
            datetime.datetime.now().astimezone() + datetime.timedelta(days=1)
        ).isoformat(timespec="seconds")
        entry = {"v": lyrics_core.__version__, "r": "nf", "ts": future_ts}
        assert (
            _cache_entry_up_to_date(
                entry, tmp_path / "fehlt.lrc", conn, "artist", "title"
            )
            is False
        )

    def test_genre_wechsel_zu_skip_macht_eintrag_veraltet(self, tmp_path):
        # Kern des Bugfixes: Datei wird zu Skip-Genre (z.B. "Instrumental")
        # umgetaggt, DB-Genre ist bereits aktualisiert (siehe
        # cache_store._get_or_create_song) -- ein bestehender, sonst noch
        # "aktueller" Cache-Eintrag muss trotzdem veraltet gelten.
        conn = cache_store.open_cache(tmp_path / "cache.db")
        cache_store._get_or_create_song(conn, "artist", "title", "Pop")
        sig_pop = lyrics_core._current_sig(conn, "artist", "title")
        entry = {
            "v": lyrics_core.__version__,
            "r": "nf",
            "ts": "2099-01-01T00:00:00",
            "sig": sig_pop,
        }

        # Genre wechselt zu Skip-worthy -- DB wird (wie --scan es tut) aktualisiert.
        cache_store._get_or_create_song(conn, "artist", "title", "Instrumental")

        assert (
            _cache_entry_up_to_date(
                entry, tmp_path / "fehlt.lrc", conn, "artist", "title"
            )
            is False
        )

    def test_unveraenderte_signatur_bleibt_aktuell(self, tmp_path):
        conn = cache_store.open_cache(tmp_path / "cache.db")
        cache_store._get_or_create_song(conn, "artist", "title", "Pop")
        sig = lyrics_core._current_sig(conn, "artist", "title")
        entry = {
            "v": lyrics_core.__version__,
            "r": "nf",
            "ts": "2099-01-01T00:00:00",
            "sig": sig,
        }
        assert (
            _cache_entry_up_to_date(
                entry, tmp_path / "fehlt.lrc", conn, "artist", "title"
            )
            is True
        )


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
    """_save_cache() muss gegen parallel laufende lyrics_core-Instanzen
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

    def test_geaenderte_sig_gewinnt_trotz_aelterem_ts(self, tmp_path):
        """Regressionstest fuer einen realen Produktions-Bug (siehe
        ROADMAP.md, "Signatur-Snapshot"): "ts" stammt aus
        cache_store.latest_result_timestamp() (juengster DB-Datensatz), nicht
        aus der Wanduhr. Ein Song ohne neue DB-Zeile (Provider-TTL noch
        gueltig, Whisper frueh gestoppt -> nie persistiert) bekommt beim
        Selbstheilungs-Fix einen "ts", der AELTER sein kann als der bereits
        auf der Platte stehende -- ohne die sig-Ausnahme wuerde der frische,
        sig-tragende Eintrag JEDES Mal wieder verworfen, der Song bliebe fuer
        immer "veraltet" und wuerde bei jedem Lauf erneut gewhispert."""
        _save_cache(
            tmp_path,
            {
                "a.flac": {
                    "r": "ok",
                    "ts": "2026-07-17T18:08:25",
                    "sig": ["a", "b", False],
                }
            },
        )
        _save_cache(
            tmp_path,
            {
                "a.flac": {
                    "r": "ok",
                    "ts": "2026-07-16T11:25:55.398618+00:00",
                    "sig": ["a", "b", True],
                }
            },
        )
        assert _load_cache(tmp_path)["a.flac"]["sig"] == ["a", "b", True]

    def test_gleiche_sig_mit_aelterem_ts_verliert_weiterhin(self, tmp_path):
        """Gegenprobe: bleibt die sig gleich, gilt weiterhin der reine
        ts-Vergleich (Lost-Update-Schutz zwischen parallelen Instanzen bleibt
        fuer den unveraenderten Fall intakt)."""
        _save_cache(
            tmp_path,
            {
                "a.flac": {
                    "r": "ok",
                    "ts": "2026-07-17T18:08:25",
                    "sig": ["a", "b", False],
                }
            },
        )
        _save_cache(
            tmp_path,
            {
                "a.flac": {
                    "r": "nf",
                    "ts": "2026-07-16T11:25:55.398618+00:00",
                    "sig": ["a", "b", False],
                }
            },
        )
        assert _load_cache(tmp_path)["a.flac"]["r"] == "ok"

    def test_neuer_utc_zeitstempel_schlaegt_aelteren_trotz_kleinerer_stunde(
        self, tmp_path
    ):
        """Regressionstest fuer einen realen Produktions-Bug (siehe ROADMAP.md):
        ein neuer Zeitstempel mit kleinerer Stundenzahl als Text (z.B. "18:02"
        UTC) wurde von einem reinen Stringvergleich faelschlich als AELTER
        gewertet als ein alter Zeitstempel mit groesserer Stundenzahl (z.B.
        "20:02" in einer anderen Zeitzone, real aber frueher) -- der frisch
        korrekt berechnete Eintrag wurde dadurch beim Schreiben
        stillschweigend wieder verworfen. Bewusst mit EXPLIZITEN Zeitzonen-
        Offsets (nicht Lokalzeit-Interpretation) formuliert, damit der Test
        unabhaengig von der Zeitzone der Testmaschine deterministisch bleibt."""
        # Alter Eintrag: "20:02" Text, aber in der Zone +05:00 -> 15:02 UTC.
        _save_cache(
            tmp_path, {"a.flac": {"r": "ok", "ts": "2026-07-17T20:02:03+05:00"}}
        )
        # Neuer Eintrag: "18:02" Text in UTC -- als Text kleiner, real aber
        # 3 Stunden SPAETER (18:02 UTC > 15:02 UTC).
        _save_cache(
            tmp_path,
            {"a.flac": {"r": "ok", "ts": "2026-07-17T18:02:03.719825+00:00"}},
        )
        assert (
            _load_cache(tmp_path)["a.flac"]["ts"] == "2026-07-17T18:02:03.719825+00:00"
        )


class TestParseCacheTs:
    """_parse_cache_ts(): macht "ts"-Werte aus zwei im Umlauf befindlichen
    Formaten (siehe ROADMAP.md) vergleichbar -- naive Lokalzeit (aeltere
    Eintraege) und timezone-aware UTC mit Mikrosekunden (neue, DB-basierte
    Eintraege, siehe write_lrc.py)."""

    def test_utc_und_lokalzeit_desselben_moments_sind_gleich(self):
        # 20:02:03 Lokalzeit (system-tz) vs. dieselbe Sekunde in UTC waere nur
        # bei UTC+0 identisch -- hier wird stattdessen geprueft, dass ein
        # bereits aware UTC-Wert unveraendert durchgereicht wird.
        aware = _parse_cache_ts("2026-07-17T18:02:03.719825+00:00")
        assert aware.tzinfo is not None
        assert aware.utcoffset().total_seconds() == 0

    def test_naiver_wert_wird_als_lokalzeit_interpretiert(self):
        naive_parsed = _parse_cache_ts("2026-07-17T20:02:03")
        assert naive_parsed.tzinfo is not None  # wurde aware gemacht

    def test_fehlender_ts_ist_minimal_alt(self):
        assert _parse_cache_ts("") < _parse_cache_ts("2026-01-01T00:00:00")

    def test_kaputter_ts_ist_minimal_alt(self):
        assert _parse_cache_ts("kaputt") < _parse_cache_ts("2026-01-01T00:00:00")

    def test_verschiedene_zeitzonen_korrekt_verglichen_trotz_kleinerer_stundenzahl(
        self,
    ):
        # Zeitzonen-unabhaengig von der System-Uhr (explizite Offsets statt
        # Lokalzeit-Interpretation, siehe test_neuer_utc_zeitstempel_...
        # in TestSaveCache fuer den Fall mit echter Lokalzeit-Interpretation).
        # "20:02+05:00" = 15:02 UTC, objektiv FRUEHER als "18:02...+00:00",
        # obwohl "20" als Text groesser ist als "18" -- reiner Stringvergleich
        # waere hier falsch (siehe ROADMAP.md, echter Produktions-Bug).
        frueher = _parse_cache_ts("2026-07-17T20:02:03+05:00")
        spaeter = _parse_cache_ts("2026-07-17T18:02:03.719825+00:00")
        assert spaeter > frueher


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

        monkeypatch.setattr(lyrics_core.fcntl, "flock", raise_eagain)
        assert _try_claim_folder(tmp_path) is _FOLDER_BUSY

    def test_flock_unsupported_falls_back_to_unlocked(self, tmp_path, monkeypatch):
        def raise_enotsup(*a, **k):
            raise OSError(errno.ENOTSUP, "Operation not supported")

        monkeypatch.setattr(lyrics_core.fcntl, "flock", raise_enotsup)
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
        artist, tracks_by_title = lyrics_core._load_release(tmp_path)
        assert artist == "Testartist"
        # Lookup mit NFD-Titel (wie er z.B. aus audio.stem über SMB kommen könnte)
        nfd_title = unicodedata.normalize("NFD", "Mücken")
        assert tracks_by_title.get(unicodedata.normalize("NFC", nfd_title)) == 123.0

    def test_missing_release_json_returns_empty(self, tmp_path):
        artist, tracks_by_title = lyrics_core._load_release(tmp_path)
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
        lyrics_core._rate_limit_state.clear()
        yield
        lyrics_core._rate_limit_state.clear()

    def test_clean_success_sets_only_proactive_floor(self):
        _rate_limit_report("lrclib", "")
        state = lyrics_core._rate_limit_state["lrclib"]
        assert state["consecutive_hits"] == 0
        remaining = state["next_allowed"] - time.monotonic()
        assert 0 < remaining <= _RATE_LIMIT_FLOOR_SEC

    def test_status_402_triggers_base_backoff(self):
        _rate_limit_report("musixmatch", "[Musixmatch] Got status code 402 for foo")
        state = lyrics_core._rate_limit_state["musixmatch"]
        assert state["consecutive_hits"] == 1
        remaining = state["next_allowed"] - time.monotonic()
        assert _RATE_LIMIT_FLOOR_SEC < remaining <= _RATE_LIMIT_BASE_SEC

    def test_status_401_captcha_triggers_longer_backoff_than_402(self):
        _rate_limit_report("musixmatch", "[Musixmatch] Got status code 401 for foo")
        remaining_401 = (
            lyrics_core._rate_limit_state["musixmatch"]["next_allowed"]
            - time.monotonic()
        )
        lyrics_core._rate_limit_state.clear()
        _rate_limit_report("musixmatch", "[Musixmatch] Got status code 402 for foo")
        remaining_402 = (
            lyrics_core._rate_limit_state["musixmatch"]["next_allowed"]
            - time.monotonic()
        )
        assert remaining_401 > remaining_402

    def test_netease_generic_error_treated_like_402(self):
        _rate_limit_report(
            "netease", "An error occurred while searching for an LRC on NetEase"
        )
        assert lyrics_core._rate_limit_state["netease"]["consecutive_hits"] == 1

    def test_repeated_hits_escalate_up_to_cap_below_threshold(self):
        # Bleibt unterhalb von _RATE_LIMIT_STUCK_THRESHOLD — dort gilt weiterhin
        # die alte, bei _RATE_LIMIT_MAX_SEC gedeckelte Eskalation (siehe unten
        # für das Verhalten AB dem Schwellwert: lange Ruhephase).
        for _ in range(_RATE_LIMIT_STUCK_THRESHOLD - 1):
            _rate_limit_report("musixmatch", "Got status code 402 for foo")
        remaining = (
            lyrics_core._rate_limit_state["musixmatch"]["next_allowed"]
            - time.monotonic()
        )
        assert remaining <= _RATE_LIMIT_MAX_SEC

    def test_hits_reaching_stuck_threshold_trigger_long_pause(self):
        for _ in range(_RATE_LIMIT_STUCK_THRESHOLD):
            _rate_limit_report("musixmatch", "[Musixmatch] Got status code 401 for foo")
        state = lyrics_core._rate_limit_state["musixmatch"]
        assert state["consecutive_hits"] == _RATE_LIMIT_STUCK_THRESHOLD
        remaining = state["next_allowed"] - time.monotonic()
        assert _RATE_LIMIT_MAX_SEC < remaining <= _RATE_LIMIT_LONG_PAUSE_SEC

    def test_clean_success_after_hits_resets_consecutive_count(self):
        _rate_limit_report("musixmatch", "Got status code 402 for foo")
        assert lyrics_core._rate_limit_state["musixmatch"]["consecutive_hits"] == 1
        _rate_limit_report("musixmatch", "")
        assert lyrics_core._rate_limit_state["musixmatch"]["consecutive_hits"] == 0

    def test_genius_gets_only_proactive_floor_no_reactive_signal_possible(self):
        # Genius/lrclib melden laut syncedlyrics-Quellcode nie ein Rate-Limit-
        # Signal im stderr, auch nicht bei HTTP 429 — stderr bleibt leer.
        _rate_limit_report("genius", "")
        remaining = (
            lyrics_core._rate_limit_state["genius"]["next_allowed"] - time.monotonic()
        )
        assert remaining <= _RATE_LIMIT_FLOOR_SEC

    def test_wait_returns_immediately_without_prior_lock(self):
        start = time.monotonic()
        _rate_limit_wait("unbekannter_provider")
        assert time.monotonic() - start < 0.05

    def test_wait_sleeps_until_next_allowed(self):
        lyrics_core._rate_limit_state["lrclib"] = {
            "next_allowed": time.monotonic() + 0.1,
            "consecutive_hits": 0,
        }
        start = time.monotonic()
        _rate_limit_wait("lrclib")
        assert time.monotonic() - start >= 0.09

    def test_wait_below_threshold_still_sleeps_and_returns_false(self, monkeypatch):
        # 3 von 5 Treffern: unterhalb von _RATE_LIMIT_STUCK_THRESHOLD, altes
        # Verhalten bleibt unverändert — kurzer sleep, kein Überspringen.
        lyrics_core._rate_limit_state["musixmatch"] = {
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

        monkeypatch.setattr(lyrics_core.time, "sleep", _fail_if_slept)
        lyrics_core._rate_limit_state["musixmatch"] = {
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
        lyrics_core._rate_limit_state["musixmatch"] = {
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


class TestWhisperThresholdFor:
    def test_kalibrierte_sprache_liefert_eigene_schwelle(self):
        assert _whisper_threshold_for("de") == 0.043

    def test_englisch_liefert_default(self):
        assert _whisper_threshold_for("en") == _WHISPER_MIN_OVERLAP

    def test_keine_sprache_liefert_default(self):
        assert _whisper_threshold_for(None) == _WHISPER_MIN_OVERLAP

    def test_unkalibrierte_sprache_liefert_default(self):
        assert _whisper_threshold_for("fr") == _WHISPER_MIN_OVERLAP


_EN_LYRICS = (
    "the sun is shining and the sky is blue today i feel so happy "
    "walking down this empty street"
)
_ES_LYRICS = (
    "el sol esta brillando y el cielo esta azul hoy me siento tan feliz "
    "caminando por esta calle vacia"
)


class TestResolveLrcLanguage:
    """_resolve_lrc_language() ersetzt _detect_lrc_language() als Sprach-
    Hinweis fuer Whisper -- Bugfix zum Telepatía-Fall (ROADMAP.md): dort
    kippte eine falsche Übersetzungsseite eines Providers die per-Textmix
    erkannte Sprache auf 'en', obwohl der Song spanisch war."""

    def test_einigkeit_liefert_die_sprache(self, tmp_path):
        a = tmp_path / "a.lrc"
        a.write_text(_ES_LYRICS, encoding="utf-8")
        b = tmp_path / "b.lrc"
        b.write_text(_ES_LYRICS, encoding="utf-8")
        assert _resolve_lrc_language([a, b]) == "es"

    def test_widerspruch_liefert_none(self, tmp_path):
        en = tmp_path / "en.lrc"
        en.write_text(_EN_LYRICS, encoding="utf-8")
        es = tmp_path / "es.lrc"
        es.write_text(_ES_LYRICS, encoding="utf-8")
        assert _resolve_lrc_language([en, es]) is None

    def test_widerspruch_kippt_nicht_wie_beim_alten_textmix(self, tmp_path):
        """Direkte Gegenprobe zum Telepatía-Bug: der alte _detect_lrc_language()
        vermischt beide Texte zu einem Textblock und erkennt dabei fälschlich
        eine der beiden Sprachen (hier: 'en', weil der englische Textblock
        zuerst kommt) -- _resolve_lrc_language() darf das NICHT tun."""
        en = tmp_path / "en.lrc"
        en.write_text(_EN_LYRICS, encoding="utf-8")
        es = tmp_path / "es.lrc"
        es.write_text(_ES_LYRICS, encoding="utf-8")
        assert lyrics_core._detect_lrc_language([en, es]) == "en"
        assert _resolve_lrc_language([en, es]) is None

    def test_nur_ein_kandidat_mit_erkannter_sprache_gilt_als_einigkeit(self, tmp_path):
        es = tmp_path / "es.lrc"
        es.write_text(_ES_LYRICS, encoding="utf-8")
        unbekannt = tmp_path / "unbekannt.lrc"
        unbekannt.write_text("x y z", encoding="utf-8")  # zu kurz fuer langdetect
        assert _resolve_lrc_language([es, unbekannt]) == "es"

    def test_keine_erkennbare_sprache_liefert_none(self, tmp_path):
        a = tmp_path / "a.lrc"
        a.write_text("x y z", encoding="utf-8")
        assert _resolve_lrc_language([a]) is None


class TestLooksLikeTranslation:
    """_looks_like_translation() filtert Übersetzungsseiten aus Provider-
    Fetches heraus (siehe Telepatía-Fall, ROADMAP.md: Genius lieferte statt
    des spanischen Originals die Seite "(English Translation)")."""

    def test_erkennt_genius_uebersetzungs_kopfzeile(self):
        content = (
            "27 Contributors\nTranslations\nEspañol\nPortuguês\nDeutsch\n\n"
            "Kali Uchis - telepatía (English Translation) Lyrics\n\n"
            "[Chorus]\nWho would have known\n"
        )
        assert _looks_like_translation(content) is True

    def test_erkennt_klammer_zusatz_auch_ohne_kopfzeile(self):
        assert (
            _looks_like_translation("Some Song (Deutsche Übersetzung) Lyrics\n") is True
        )
        assert _looks_like_translation("Une Chanson (Traduction Française)\n") is True

    def test_normaler_songtext_wird_nicht_erkannt(self):
        content = "[00:12.43]Quién lo diría\n[00:15.18]Que se podría hacer el amor\n"
        assert _looks_like_translation(content) is False

    def test_translations_menue_allein_loest_nicht_aus(self):
        """Regressionstest: Genius zeigt das "Translations"-Sprachauswahl-
        Menü auf JEDER Seite eines Songs, der irgendeine Übersetzung hat --
        auch auf der Original-Seite selbst (echter Fund in der Produktions-
        DB, song_id 5, "The Hollies - Long Cool Woman in a Black Dress":
        korrekte Original-Lyrics, aber mit "Translations\\nTürkçe\\n..."-Kopf,
        weil eine türkische Übersetzung existiert). Ein früherer Anlauf nutzte
        genau diese Zeile als zweites Signal und erzeugte dadurch 1191 von
        23504 False Positives in der echten DB -- das Signal wurde deshalb
        wieder entfernt (siehe _looks_like_translation-Docstring)."""
        content = (
            "37 Contributors\nTranslations\nTürkçe\n"
            "Long Cool Woman (In a Black Dress) Lyrics\n"
            "The 1972 hit, one of the last for the Hollies...\n"
        )
        assert _looks_like_translation(content) is False

    def test_translations_wort_tief_im_text_loest_nicht_aus(self):
        content = "\n".join([f"line {i}" for i in range(20)] + ["Translations"])
        assert _looks_like_translation(content) is False

    def test_schluesselwort_ohne_sprachnamen_loest_nicht_aus(self):
        """Regressionstest: 2 weitere echte False Positives aus der
        Produktions-DB -- "Translation" allein (ohne Sprachnamen im selben
        Klammer-Zusatz) sind legitime Songtext-Anmerkungen, keine
        Übersetzungsseiten-Titel."""
        assert (
            _looks_like_translation(
                "[03:00.83] (Translation in brackets)\n"
                "[03:02.67] Vous etiez de l'autre cote de la salle\n"
            )
            is False
        )
        assert (
            _looks_like_translation(
                "[01:51.19] (Translation:)\n"
                "[01:52.07] You have Adhikara over your respective duty only,\n"
            )
            is False
        )


class TestProviderCache:
    """_query_provider mit echtem cache_store (siehe CACHE_DESIGN.md)."""

    def _open(self, tmp_path):
        conn = cache_store.open_cache(tmp_path / "cache.db")
        lyrics_core._cache_conn = conn
        lyrics_core._cache_ttl_days = 30
        lyrics_core._cache_refresh = False
        lyrics_core._cache_only = False
        return conn

    def teardown_method(self):
        lyrics_core._cache_conn = None
        lyrics_core._cache_refresh = False
        lyrics_core._cache_only = False

    def test_cache_hit_skips_live_query(self, tmp_path, monkeypatch):
        conn = self._open(tmp_path)
        cache_store.put_provider(
            conn, "lrclib", "the artist", "the title", "treffer", "[00:01.00]Hallo Welt"
        )

        def _fail_if_called(*a, **k):
            pytest.fail("Live-Abfrage darf bei Cache-Treffer nicht laufen")

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fail_if_called)
        provider, path = lyrics_core._query_provider(
            "the artist the title", "lrclib", {}, artist="the artist", title="the title"
        )
        assert path is not None
        assert "Hallo Welt" in path.read_text(encoding="utf-8")

    def test_cache_nichts_hit_skips_live_query(self, tmp_path, monkeypatch):
        conn = self._open(tmp_path)
        cache_store.put_provider(conn, "genius", "x", "y", "nichts", None)

        def _fail_if_called(*a, **k):
            pytest.fail("Live-Abfrage darf bei gecachtem 'nichts' nicht laufen")

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fail_if_called)
        provider, path = lyrics_core._query_provider(
            "x y", "genius", {}, artist="x", title="y"
        )
        assert path is None

    def test_clean_miss_is_cached_as_nichts(self, tmp_path, monkeypatch):
        conn = self._open(tmp_path)

        class _Result:
            stderr = ""

        def _fake_run(*a, **k):
            return _Result()

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fake_run)
        provider, path = lyrics_core._query_provider(
            "a b", "lrclib", {}, artist="a", title="b"
        )
        assert path is None
        cached = cache_store.get_provider(conn, "lrclib", "a", "b")
        assert cached == {"status": "nichts", "content": None}

    def test_uebersetzungsseite_wird_beim_live_fetch_verworfen(
        self, tmp_path, monkeypatch
    ):
        """Bugfix Telepatía-Fall (ROADMAP.md): Genius' Suche kann statt des
        Originals eine Übersetzungsseite als ersten Treffer liefern -- die
        darf nicht als 'treffer' gelten."""
        conn = self._open(tmp_path)
        translation_content = (
            "27 Contributors\nTranslations\nEspañol\n\n"
            "Kali Uchis - telepatía (English Translation) Lyrics\n\n"
            "[Chorus]\nWho would have known\n"
        )

        class _Result:
            stderr = ""

        def _fake_run(cmd, **k):
            Path(cmd[3]).write_text(translation_content, encoding="utf-8")
            return _Result()

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fake_run)
        provider, path = lyrics_core._query_provider(
            "kali uchis telepatia", "genius", {}, artist="kali uchis", title="telepatía"
        )
        assert path is None
        cached = cache_store.get_provider(conn, "genius", "kali uchis", "telepatía")
        assert cached == {"status": "nichts", "content": None}

    def test_alter_uebersetzungs_cache_eintrag_wird_beim_replay_verworfen(
        self, tmp_path, monkeypatch
    ):
        """Selbstheilung fuer Cache-Einträge, die VOR diesem Filter als
        'treffer' geschrieben wurden (z.B. der echte Telepatía-Fall in der
        Produktions-DB) -- ein Replay aus dem Cache muss sie genauso
        verwerfen wie ein frischer Live-Fetch."""
        conn = self._open(tmp_path)
        cache_store.put_provider(
            conn,
            "genius",
            "kali uchis",
            "telepatía",
            "treffer",
            "27 Contributors\nTranslations\nEspañol\n\n"
            "Kali Uchis - telepatía (English Translation) Lyrics\n\n[Chorus]\n",
        )

        def _fail_if_called(*a, **k):
            pytest.fail("Live-Abfrage darf bei Cache-Treffer nicht laufen")

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fail_if_called)
        provider, path = lyrics_core._query_provider(
            "kali uchis telepatia", "genius", {}, artist="kali uchis", title="telepatía"
        )
        assert path is None

    def test_transient_error_ist_kein_cache_treffer_aber_wird_festgehalten(
        self, tmp_path, monkeypatch
    ):
        conn = self._open(tmp_path)

        class _Result:
            stderr = "Got status code 402"

        def _fake_run(*a, **k):
            return _Result()

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fake_run)
        lyrics_core._query_provider("a b", "musixmatch", {}, artist="a", title="b")
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

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fake_run)
        lyrics_core._query_provider("a b", "netease", {}, artist="a", title="b")
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

        lyrics_core._cache_refresh = True  # simuliert --force bzw. --refresh-cache
        try:
            called = []

            class _Result:
                stderr = ""

            def _fake_run(*a, **k):
                called.append(1)
                return _Result()

            monkeypatch.setattr(lyrics_core.subprocess, "run", _fake_run)
            lyrics_core._query_provider("a b", "lrclib", {}, artist="a", title="b")
            assert called, (
                "--force/--refresh-cache muss live abfragen, nicht aus dem Cache bedienen"
            )
        finally:
            lyrics_core._cache_refresh = False

    def test_stuck_provider_skips_live_query_without_changing_state(
        self, tmp_path, monkeypatch
    ):
        conn = self._open(tmp_path)
        lyrics_core._rate_limit_state["musixmatch"] = {
            "next_allowed": time.monotonic() + 900.0,
            "consecutive_hits": _RATE_LIMIT_STUCK_THRESHOLD,
        }
        state_before = dict(lyrics_core._rate_limit_state["musixmatch"])

        def _fail_if_called(*a, **k):
            pytest.fail("Live-Abfrage darf während der langen Ruhephase nicht laufen")

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fail_if_called)
        try:
            provider, path = lyrics_core._query_provider(
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
            assert lyrics_core._rate_limit_state["musixmatch"] == state_before
        finally:
            lyrics_core._rate_limit_state.pop("musixmatch", None)

    def test_no_cache_conn_falls_back_to_live(self, monkeypatch):
        lyrics_core._cache_conn = None  # simuliert --no-cache / fehlende DB

        class _Result:
            stderr = ""

        called = []

        def _fake_run(*a, **k):
            called.append(1)
            return _Result()

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fake_run)
        lyrics_core._query_provider("a b", "lrclib", {}, artist="a", title="b")
        assert called, "Ohne offene Cache-Verbindung muss live abgefragt werden"

    def test_cache_only_mit_treffer_liefert_cache_inhalt(self, tmp_path, monkeypatch):
        conn = self._open(tmp_path)
        cache_store.put_provider(
            conn, "lrclib", "the artist", "the title", "treffer", "[00:01.00]Hallo Welt"
        )
        lyrics_core._cache_only = True

        def _fail_if_called(*a, **k):
            pytest.fail("Live-Abfrage darf bei Cache-Treffer nicht laufen")

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fail_if_called)
        provider, path = lyrics_core._query_provider(
            "the artist the title", "lrclib", {}, artist="the artist", title="the title"
        )
        assert path is not None
        assert "Hallo Welt" in path.read_text(encoding="utf-8")

    def test_cache_only_ohne_eintrag_liefert_none_ohne_live_abfrage_und_ohne_cache_schreiben(
        self, tmp_path, monkeypatch
    ):
        conn = self._open(tmp_path)
        lyrics_core._cache_only = True

        def _fail_if_called(*a, **k):
            pytest.fail("--cache-only darf niemals live abfragen")

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fail_if_called)
        provider, path = lyrics_core._query_provider(
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

        lyrics_core._cache_only = True

        def _fail_if_called(*a, **k):
            pytest.fail("--cache-only darf gecachte Fehlschläge nicht live nachfragen")

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fail_if_called)
        provider, path = lyrics_core._query_provider(
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
        lyrics_core._cache_conn = None  # simuliert --no-cache / fehlende DB
        lyrics_core._cache_only = True

        def _fail_if_called(*a, **k):
            pytest.fail("--cache-only muss auch ohne offene Cache-Verbindung greifen")

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fail_if_called)
        provider, path = lyrics_core._query_provider(
            "a b", "lrclib", {}, artist="a", title="b"
        )
        assert (provider, path) == ("lrclib", None)


def _make_dump_conn(
    synced: dict[tuple[str, str], str] | None = None,
) -> sqlite3.Connection:
    """In-memory-Nachbau des externen LRCLib-Datenbank-Abzugs (Tabellen
    `tracks`/`lyrics`, siehe cache_store.lookup_lrclib_dump) für Tests von
    _query_provider — NICHT die echte 112GB-Netzwerk-Datei."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name_lower TEXT, artist_name_lower TEXT,
            last_lyrics_id INTEGER
        );
        CREATE TABLE lyrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plain_lyrics TEXT, synced_lyrics TEXT,
            has_plain_lyrics BOOLEAN, has_synced_lyrics BOOLEAN
        );
        """
    )
    for (artist_lower, title_lower), content in (synced or {}).items():
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
    return conn


def _make_dump_conn_instrumental(
    artist_lower: str, title_lower: str
) -> sqlite3.Connection:
    """Track im Dump gefunden, aber ohne jeglichen Songtext (Instrumental)."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name_lower TEXT, artist_name_lower TEXT,
            last_lyrics_id INTEGER
        );
        CREATE TABLE lyrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plain_lyrics TEXT, synced_lyrics TEXT,
            has_plain_lyrics BOOLEAN, has_synced_lyrics BOOLEAN
        );
        """
    )
    cur = conn.execute(
        "INSERT INTO lyrics (synced_lyrics, plain_lyrics, has_synced_lyrics, has_plain_lyrics) "
        "VALUES (NULL, NULL, 0, 0)"
    )
    conn.execute(
        "INSERT INTO tracks (name_lower, artist_name_lower, last_lyrics_id) VALUES (?, ?, ?)",
        (title_lower, artist_lower, cur.lastrowid),
    )
    conn.commit()
    return conn


class TestLrclibDumpLookup:
    """_query_provider mit dem lokalen LRCLib-Datenbank-Abzug (_lrclib_dump_conn,
    siehe cache_store.lookup_lrclib_dump) — Beschleuniger VOR der echten
    Live-Abfrage bei der lrclib-Quelle."""

    def _open(self, tmp_path):
        conn = cache_store.open_cache(tmp_path / "cache.db")
        lyrics_core._cache_conn = conn
        lyrics_core._cache_ttl_days = 30
        lyrics_core._cache_refresh = False
        lyrics_core._cache_only = False
        lyrics_core._lrclib_dump_conn = None
        return conn

    def teardown_method(self):
        lyrics_core._cache_conn = None
        lyrics_core._cache_refresh = False
        lyrics_core._cache_only = False
        lyrics_core._lrclib_dump_conn = None

    def _fail_if_called(self, *a, **k):
        pytest.fail("Live-Abfrage darf bei einem Dump-Treffer nicht laufen")

    def test_dump_treffer_mit_songtext_wird_zurueckgegeben_und_gecacht(
        self, tmp_path, monkeypatch
    ):
        conn = self._open(tmp_path)
        lyrics_core._lrclib_dump_conn = _make_dump_conn(
            {("the artist", "the title"): "[00:01.00]Hallo Welt"}
        )
        monkeypatch.setattr(lyrics_core.subprocess, "run", self._fail_if_called)

        provider, path = lyrics_core._query_provider(
            "the artist the title", "lrclib", {}, artist="the artist", title="the title"
        )
        assert path is not None
        assert "Hallo Welt" in path.read_text(encoding="utf-8")

        # Genau wie ein Live-Treffer im eigenen Cache abgelegt.
        cached = cache_store.get_provider(conn, "lrclib", "the artist", "the title")
        assert cached == {"status": "treffer", "content": "[00:01.00]Hallo Welt"}

    def test_dump_treffer_ohne_songtext_gilt_als_nichts_und_wird_gecacht(
        self, tmp_path, monkeypatch
    ):
        conn = self._open(tmp_path)
        lyrics_core._lrclib_dump_conn = _make_dump_conn_instrumental("a", "b")
        monkeypatch.setattr(lyrics_core.subprocess, "run", self._fail_if_called)

        provider, path = lyrics_core._query_provider(
            "a b", "lrclib", {}, artist="a", title="b"
        )
        assert (provider, path) == ("lrclib", None)
        assert cache_store.get_provider(conn, "lrclib", "a", "b") == {
            "status": "nichts",
            "content": None,
        }

    def test_dump_ohne_treffer_faellt_auf_live_abfrage_zurueck(
        self, tmp_path, monkeypatch
    ):
        conn = self._open(tmp_path)
        lyrics_core._lrclib_dump_conn = _make_dump_conn()  # leer -- 0 Treffer

        class _Result:
            stderr = ""

        called = []

        def _fake_run(*a, **k):
            called.append(1)
            return _Result()

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fake_run)
        lyrics_core._query_provider("a b", "lrclib", {}, artist="a", title="b")
        assert called, "0 Treffer im Dump muss weiterhin live nachfragen"
        # Kein Cache-Eintrag allein durch den erfolglosen Dump-Blick (kein
        # echter Versuch) -- der anschließende Live-Fall schreibt seinen
        # eigenen Eintrag ("nichts", da _fake_run keinen Text liefert).
        assert cache_store.get_provider(conn, "lrclib", "a", "b") == {
            "status": "nichts",
            "content": None,
        }

    def test_dump_conn_none_faellt_auf_live_abfrage_zurueck(
        self, tmp_path, monkeypatch
    ):
        self._open(tmp_path)
        lyrics_core._lrclib_dump_conn = None  # z.B. Mount nicht verfügbar

        class _Result:
            stderr = ""

        called = []

        def _fake_run(*a, **k):
            called.append(1)
            return _Result()

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fake_run)
        lyrics_core._query_provider("a b", "lrclib", {}, artist="a", title="b")
        assert called, "Ohne Dump-Verbindung muss weiterhin live abgefragt werden"

    def test_dump_wird_nur_fuer_lrclib_geprueft(self, tmp_path, monkeypatch):
        """Andere Provider (musixmatch/netease/genius) ignorieren den Dump
        komplett, auch wenn er zufällig einen passenden Eintrag hätte."""
        self._open(tmp_path)
        lyrics_core._lrclib_dump_conn = _make_dump_conn(
            {("a", "b"): "[00:01.00]sollte nie verwendet werden"}
        )

        class _Result:
            stderr = ""

        called = []

        def _fake_run(*a, **k):
            called.append(1)
            return _Result()

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fake_run)
        lyrics_core._query_provider("a b", "musixmatch", {}, artist="a", title="b")
        assert called, "Nicht-lrclib-Provider müssen weiterhin live abgefragt werden"

    def test_refresh_cache_umgeht_auch_den_dump(self, tmp_path, monkeypatch):
        """--refresh-cache/--force erzwingen eine WIRKLICH frische Live-Abfrage
        — genau wie beim eigenen Cache-Lookup wird auch der Dump übersprungen."""
        self._open(tmp_path)
        lyrics_core._lrclib_dump_conn = _make_dump_conn(
            {("a", "b"): "[00:01.00]dump-inhalt"}
        )
        lyrics_core._cache_refresh = True
        try:

            class _Result:
                stderr = ""

            called = []

            def _fake_run(*a, **k):
                called.append(1)
                return _Result()

            monkeypatch.setattr(lyrics_core.subprocess, "run", _fake_run)
            lyrics_core._query_provider("a b", "lrclib", {}, artist="a", title="b")
            assert called, "--refresh-cache/--force muss auch den Dump umgehen"
        finally:
            lyrics_core._cache_refresh = False

    def test_cache_only_mit_dump_treffer_wird_trotzdem_verwendet(
        self, tmp_path, monkeypatch
    ):
        """--cache-only verbietet nur ECHTE Live-Abfragen -- der Dump ist keine
        Live-Abfrage und darf daher auch unter --cache-only genutzt werden."""
        self._open(tmp_path)
        lyrics_core._lrclib_dump_conn = _make_dump_conn(
            {("a", "b"): "[00:01.00]dump-inhalt"}
        )
        lyrics_core._cache_only = True
        monkeypatch.setattr(lyrics_core.subprocess, "run", self._fail_if_called)

        provider, path = lyrics_core._query_provider(
            "a b", "lrclib", {}, artist="a", title="b"
        )
        assert path is not None
        assert "dump-inhalt" in path.read_text(encoding="utf-8")

    def test_cache_only_mit_dump_miss_liefert_none_ohne_live_versuch(
        self, tmp_path, monkeypatch
    ):
        self._open(tmp_path)
        lyrics_core._lrclib_dump_conn = _make_dump_conn()  # 0 Treffer im Dump
        lyrics_core._cache_only = True
        monkeypatch.setattr(lyrics_core.subprocess, "run", self._fail_if_called)

        provider, path = lyrics_core._query_provider(
            "a b", "lrclib", {}, artist="a", title="b"
        )
        assert (provider, path) == ("lrclib", None)

    def test_dump_fehler_stoert_den_lauf_nicht_und_faellt_auf_live_zurueck(
        self, tmp_path, monkeypatch
    ):
        """Ein defekter/geschlossener Dump-Connection darf den Lauf nicht
        stören — still degradieren, wie beim regulären Cache (siehe
        CACHE_DESIGN.md)."""
        self._open(tmp_path)
        broken_conn = _make_dump_conn()
        broken_conn.close()  # jede Abfrage wirft jetzt ProgrammingError
        lyrics_core._lrclib_dump_conn = broken_conn

        class _Result:
            stderr = ""

        called = []

        def _fake_run(*a, **k):
            called.append(1)
            return _Result()

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fake_run)
        lyrics_core._query_provider("a b", "lrclib", {}, artist="a", title="b")
        assert called, "Ein Dump-Fehler muss auf die Live-Abfrage zurückfallen"


class TestTranscriptCache:
    """_whisper_best mit echtem cache_store: Song-Identität (Künstler+Titel)

    statt Datei-Identität. Ein gecachtes Transkript gehört zu GENAU EINEM Song
    (artist_key/titel_key) — unabhängig von Datei, Modell oder Fenster-Parametern.
    """

    def teardown_method(self):
        lyrics_core._cache_conn = None
        lyrics_core._cache_refresh = False

    def _prep(self, monkeypatch, tmp_path):
        conn = cache_store.open_cache(tmp_path / "cache.db")
        lyrics_core._cache_conn = conn
        lyrics_core._cache_ttl_days = 30
        lyrics_core._cache_refresh = False
        monkeypatch.setattr(lyrics_core, "_get_whisper_model", lambda name: object())
        monkeypatch.setattr(lyrics_core, "_contrastive_idf", (1, {}))
        monkeypatch.setattr(
            lyrics_core, "_detect_lrc_language", lambda candidates: None
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
            pytest.fail(
                "_transcribe_with_early_stop darf bei Song-Cache-Treffer nicht laufen"
            )

        monkeypatch.setattr(lyrics_core, "_transcribe_with_early_stop", _fail_if_called)

        flac = tmp_path / "song.flac"
        flac.write_bytes(b"x")
        lrc = self._make_lrc(tmp_path, "a.lrc", "[00:01.00]hello world foo bar\n")

        best_path, score, has_vocals, words, model, lang, margin, early_stopped = (
            lyrics_core._whisper_best(
                flac, [lrc], artist="The Artist", title="The Title"
            )
        )
        assert best_path == lrc
        assert has_vocals is True
        assert words == 4

    def test_score_nicht_genullt_bei_erkannter_halluzination(
        self, tmp_path, monkeypatch
    ):
        """Bugfix: _score_against_idf nutzt jetzt IMMER raw_words, nicht die
        halluzinations-gefilterte Wortliste -- set-basiertes IDF-Jaccard ist
        gegen Wiederholungshäufigkeit ohnehin immun, das frühere Nullsetzen
        bot dort keinen Schutz, konnte aber einen echten Treffer zerstören.
        "lets go" x20 ist eine echte Halluzinationsschleife (2 einzigartige
        Wörter, siehe TestIsHallucination) -- has_vocals muss weiterhin False
        bleiben, der Score gegen eine exakt passende LRC darf aber nicht mehr
        auf 0.0 gezwungen werden."""
        conn = self._prep(monkeypatch, tmp_path)
        transcript = " ".join(["lets", "go"] * 20)
        cache_store.put_transcript(
            conn, "the artist", "the title", transcript, 0.9, -0.2
        )

        flac = tmp_path / "song.flac"
        flac.write_bytes(b"x")
        lrc = self._make_lrc(tmp_path, "a.lrc", "[00:01.00]lets go\n")

        best_path, score, has_vocals, words, model, lang, margin, early_stopped = (
            lyrics_core._whisper_best(
                flac, [lrc], artist="The Artist", title="The Title"
            )
        )
        assert has_vocals is False  # weiterhin korrekt als Halluzination erkannt
        assert words == 0
        assert score == pytest.approx(1.0)  # Score bleibt erhalten, nicht mehr 0.0
        assert best_path == lrc

    def test_cache_hit_laedt_kein_whisper_modell(self, tmp_path, monkeypatch):
        """Regressionstest (siehe ROADMAP.md): _get_whisper_model() lädt bei
        einem Cache-Miss ein volles Modell in den Speicher -- bei einem
        Song-Transkript-Cache-TREFFER wird das Modell-Objekt selbst nie
        gebraucht (nur der Modell-NAME als String für die Anzeige). Realer
        Befund: ein zweiter/dritter Lauf über denselben Ordner lud
        weiterhin medium/large-v3 neu, obwohl die Transkripte längst
        gecacht waren. _get_whisper_model() darf bei einem Cache-Treffer
        also gar nicht erst aufgerufen werden."""
        conn = cache_store.open_cache(tmp_path / "cache.db")
        lyrics_core._cache_conn = conn
        lyrics_core._cache_ttl_days = 30
        lyrics_core._cache_refresh = False
        monkeypatch.setattr(lyrics_core, "_contrastive_idf", (1, {}))
        monkeypatch.setattr(
            lyrics_core, "_detect_lrc_language", lambda candidates: None
        )
        cache_store.put_transcript(
            conn, "the artist", "the title", "hello world foo bar", 0.1, -0.2
        )

        def _fail_if_called(*a, **k):
            pytest.fail("_get_whisper_model darf bei Song-Cache-Treffer nicht laufen")

        monkeypatch.setattr(lyrics_core, "_get_whisper_model", _fail_if_called)

        flac = tmp_path / "song.flac"
        flac.write_bytes(b"x")
        lrc = self._make_lrc(tmp_path, "a.lrc", "[00:01.00]hello world foo bar\n")

        lyrics_core._whisper_best(flac, [lrc], artist="The Artist", title="The Title")

    def test_miss_transcribes_and_writes_cache(self, tmp_path, monkeypatch):
        self._prep(monkeypatch, tmp_path)

        def _fake_transcribe(*a, **k):
            return ["hello", "world", "foo", "bar"], 0.05, -0.3, False

        monkeypatch.setattr(
            lyrics_core, "_transcribe_with_early_stop", _fake_transcribe
        )

        flac = tmp_path / "song2.flac"
        flac.write_bytes(b"y")
        lrc = self._make_lrc(tmp_path, "b.lrc", "[00:01.00]hello world foo bar\n")

        best_path, score, has_vocals, words, model, lang, margin, early_stopped = (
            lyrics_core._whisper_best(
                flac, [lrc], artist="Another Artist", title="Another Title"
            )
        )
        assert best_path == lrc

        cached = cache_store.get_transcript(
            lyrics_core._cache_conn, "another artist", "another title"
        )
        assert cached["transcript"] == "hello world foo bar"
        assert cached["no_speech_prob"] == 0.05
        assert cached["avg_logprob"] == -0.3

    def test_langes_echtes_outro_hat_vocals_trotz_wiederholung(
        self, tmp_path, monkeypatch
    ):
        """Bugfix-Regressionstest, Cache-Miss-Pfad: ein Song mit langem echtem
        Outro (viele einzigartige Wörter im Song, aber ein dominant
        wiederholtes Hook-Wort am Ende, wie bei "Never Can Say Goodbye" oder
        "Lost Your Number") darf nicht mehr als kein-Vokal/Halluzination
        behandelt werden -- die absolute Vokabelgrenze (94/51 einzigartige
        Wörter >> _HALLUCINATION_MAX_UNIQUE_WORDS) muss das verhindern."""
        self._prep(monkeypatch, tmp_path)

        # Rein alphabetische Fuellwoerter (keine Ziffern): _extract_lrc_words
        # nutzt "[^\W\d_]+" und wuerde z.B. "wort0"/"wort1" beide zu "wort"
        # zusammenstutzen -- das LRC unten muss dieselben Woerter enthalten.
        unique_words = [f"wort{chr(97 + i)}" for i in range(20)]
        raw_words = unique_words + ["no"] * 60  # 80 Wörter, 21 einzigartig

        def _fake_transcribe(*a, **k):
            return raw_words, 0.7, -0.3, False  # no_speech > 0,65-Schwelle

        monkeypatch.setattr(
            lyrics_core, "_transcribe_with_early_stop", _fake_transcribe
        )

        flac = tmp_path / "song3.flac"
        flac.write_bytes(b"z")
        lrc = self._make_lrc(
            tmp_path, "c.lrc", "[00:01.00]" + " ".join(unique_words) + "\n"
        )

        best_path, score, has_vocals, words, model, lang, margin, early_stopped = (
            lyrics_core._whisper_best(
                flac, [lrc], artist="Outro Artist", title="Outro Title"
            )
        )
        assert has_vocals is True  # nicht mehr faelschlich "kein Vokal"
        assert words == 80
        assert best_path == lrc
        assert score > 0.0

    def test_zweiter_lauf_selber_song_nutzt_cache_ohne_erneutes_transkribieren(
        self, tmp_path, monkeypatch
    ):
        """Zwei verschiedene Kandidaten-Pfade/Fenster für DENSELBEN Song (artist+title):
        der zweite _whisper_best-Aufruf nutzt den Song-Cache, _transcribe läuft nur einmal."""
        self._prep(monkeypatch, tmp_path)

        calls = []

        def _counting_transcribe(path, start, ctx, model, *a, **k):
            calls.append(path)
            return ["hello", "world", "foo", "bar"], 0.05, -0.3, False

        monkeypatch.setattr(
            lyrics_core, "_transcribe_with_early_stop", _counting_transcribe
        )

        flac1 = tmp_path / "song_v1.flac"
        flac1.write_bytes(b"y1")
        lrc1 = self._make_lrc(tmp_path, "c1.lrc", "[00:01.00]hello world foo bar\n")

        flac2 = tmp_path / "song_v2.flac"
        flac2.write_bytes(b"y2")
        lrc2 = self._make_lrc(tmp_path, "c2.lrc", "[00:05.00]hello world foo bar\n")

        lyrics_core._whisper_best(
            flac1, [lrc1], artist="Same Artist", title="Same Title"
        )
        assert len(calls) == 1

        lyrics_core._whisper_best(
            flac2, [lrc2], artist="Same Artist", title="Same Title"
        )
        assert len(calls) == 1  # kein zweiter _transcribe-Aufruf für denselben Song

    def test_mehrere_kandidaten_unterschiedlicher_start_nur_ein_transcribe_aufruf(
        self, tmp_path, monkeypatch
    ):
        """Mehrere Kandidaten mit UNTERSCHIEDLICHEN ersten Zeitstempeln
        beschreiben dieselbe Audiodatei -- _whisper_best darf pro Aufruf nur
        EINMAL transkribieren (frühester Kandidaten-Start), nicht einmal pro
        unterschiedlichem Start wie vor v1.10.1."""
        self._prep(monkeypatch, tmp_path)

        calls = []

        def _counting_transcribe(path, start, ctx, model, *a, **k):
            calls.append(start)
            return ["hello", "world", "foo", "bar"], 0.05, -0.3, False

        monkeypatch.setattr(
            lyrics_core, "_transcribe_with_early_stop", _counting_transcribe
        )

        flac = tmp_path / "song.flac"
        flac.write_bytes(b"z")
        lrc_spaet = self._make_lrc(
            tmp_path, "spaet.lrc", "[00:40.00]hello world foo bar\n"
        )
        lrc_frueh = self._make_lrc(
            tmp_path, "frueh.lrc", "[00:05.00]hello world foo bar\n"
        )

        lyrics_core._whisper_best(
            flac,
            [lrc_spaet, lrc_frueh],
            artist="Multi Artist",
            title="Multi Title",
        )
        assert len(calls) == 1
        assert calls[0] == pytest.approx(5.0)  # frühester Kandidaten-Start


class TestGlobalCacheIdf:
    """_global_cache_idf() baut df/n_docs aus ALLEN texte.inhalt der Cache-DB
    -- ein Zählschritt pro Text (Dokumentfrequenz), Tokenisierung wie
    _extract_lrc_words. Kein Sprach-Split -- keine Datei-basierte Tabelle."""

    def test_zaehlt_jeden_text_einmal(self, tmp_path):
        conn = cache_store.open_cache(tmp_path / "cache.db")
        cache_store.put_provider(
            conn,
            "lrclib",
            "artist a",
            "title a",
            "treffer",
            "[00:01.00]hello world\n[00:02.00]foo bar\n",
        )
        cache_store.put_provider(
            conn,
            "genius",
            "artist b",
            "title b",
            "treffer",
            "[00:01.00]hello there\n",
        )
        n_docs, df = _global_cache_idf(conn)
        assert n_docs == 2
        assert df["hello"] == 2
        assert df["world"] == 1
        assert df["there"] == 1

    def test_identischer_inhalt_wird_ueber_fingerabdruck_dedupliziert(self, tmp_path):
        """Zwei Provider mit identischem Content landen (via Fingerabdruck) nur
        einmal in `texte` -- die IDF zählt ihn dann auch nur einmal."""
        conn = cache_store.open_cache(tmp_path / "cache.db")
        content = "[00:01.00]hello world\n"
        cache_store.put_provider(conn, "lrclib", "a", "b", "treffer", content)
        cache_store.put_provider(conn, "genius", "a", "b", "treffer", content)
        n_docs, df = _global_cache_idf(conn)
        assert n_docs == 1
        assert df["hello"] == 1

    def test_leere_cache_db_liefert_leere_tabelle(self, tmp_path):
        conn = cache_store.open_cache(tmp_path / "cache.db")
        n_docs, df = _global_cache_idf(conn)
        assert n_docs == 0
        assert df == {}

    def test_fehlschlaege_ohne_content_zaehlen_nicht(self, tmp_path):
        conn = cache_store.open_cache(tmp_path / "cache.db")
        cache_store.put_provider(
            conn, "lrclib", "a", "b", "fehlschlag", None, fehlergrund="timeout"
        )
        n_docs, df = _global_cache_idf(conn)
        assert n_docs == 0


class TestContrastiveMarginAndDecision:
    """_contrastive_margin_and_decision(): Marge = best_score - bester Score
    von K zufälligen ANDEREN Songs gleicher Sprache aus dem Cache
    (Hintergrund). Mit leerem df-Dict entspricht _idf_jaccard exakt dem
    unweighted Jaccard (alle Wörter bekommen dieselbe IDF) -- macht die
    erwarteten Werte hier exakt berechenbar."""

    def teardown_method(self):
        lyrics_core._contrastive_lang_pools = None
        lyrics_core._contrastive_song_texts = None
        lyrics_core._contrastive_song_words_cache = {}

    def test_marge_ist_best_score_minus_bester_hintergrund_score(self):
        # 5 Hintergrund-Songs (>= _CONTRASTIVE_MIN_BACKGROUND) gleicher Sprache.
        lyrics_core._contrastive_song_texts = {
            201: ["aaa bbb ccc"],  # kein Overlap -> Jaccard 0
            202: ["hello world baz qux"],  # {hello,world} / 6 -> 0.333...
            203: ["zzz"],  # kein Overlap -> 0
            204: ["foo bar mmm nnn"],  # {foo,bar} / 6 -> 0.333...
            205: ["hello world foo bar"],  # exakt -> Jaccard 1.0 (Maximum)
        }
        lyrics_core._contrastive_lang_pools = {"en": [201, 202, 203, 204, 205]}

        bg_max, margin, fallback = _contrastive_margin_and_decision(
            ["hello", "world", "foo", "bar"],
            0.9,
            "en",
            999,  # exclude_song_id -- nicht im Pool, ändert nichts
            n_docs=5,
            df={},
        )
        assert fallback is False
        assert bg_max == pytest.approx(1.0)
        assert margin == pytest.approx(0.9 - 1.0)

    def test_andere_sprache_ohne_pool_liefert_fallback(self):
        lyrics_core._contrastive_lang_pools = {"en": [1, 2, 3, 4, 5, 6]}
        bg_max, margin, fallback = _contrastive_margin_and_decision(
            ["a"], 0.5, "de", None, n_docs=5, df={}
        )
        assert fallback is True
        assert bg_max is None
        assert margin is None

    def test_lang_none_liefert_fallback(self):
        lyrics_core._contrastive_lang_pools = {"en": [1, 2, 3, 4, 5, 6]}
        bg_max, margin, fallback = _contrastive_margin_and_decision(
            ["a"], 0.5, None, None, n_docs=5, df={}
        )
        assert fallback is True

    def test_pool_kleiner_als_min_background_liefert_fallback(self):
        pool = list(range(1, _CONTRASTIVE_MIN_BACKGROUND))  # genau eins zu wenig
        assert len(pool) < _CONTRASTIVE_MIN_BACKGROUND
        lyrics_core._contrastive_lang_pools = {"en": pool}
        _, _, fallback = _contrastive_margin_and_decision(
            ["a"], 0.5, "en", None, n_docs=5, df={}
        )
        assert fallback is True

    def test_exclude_song_id_verkleinert_pool_bis_zum_fallback(self):
        pool = list(range(1, _CONTRASTIVE_MIN_BACKGROUND + 1))  # genau ausreichend
        lyrics_core._contrastive_song_texts = {i: ["x y z"] for i in pool}
        lyrics_core._contrastive_lang_pools = {"en": pool}

        _, _, fallback_full = _contrastive_margin_and_decision(
            ["x"], 0.5, "en", None, n_docs=5, df={}
        )
        assert fallback_full is False

        _, _, fallback_excluded = _contrastive_margin_and_decision(
            ["x"], 0.5, "en", pool[0], n_docs=5, df={}
        )
        assert fallback_excluded is True

    def test_reproduzierbar_bei_gleichem_song_ueber_mehrere_aufrufe(self):
        """Fester Seed pro Song (Sprache + song_id) -- zwei Aufrufe mit
        identischen Argumenten müssen dieselbe Hintergrund-Ziehung/Marge
        liefern (grosser Pool, K < Pool-Größe -- die Ziehung ist also
        tatsächlich zufällig, nicht einfach 'alle')."""
        pool = list(range(1, 101))
        lyrics_core._contrastive_song_texts = {
            i: [f"word{i} common shared"] for i in pool
        }
        lyrics_core._contrastive_lang_pools = {"en": pool}

        r1 = _contrastive_margin_and_decision(
            ["common", "shared", "unique"], 0.5, "en", 42, n_docs=5, df={}
        )
        lyrics_core._contrastive_song_words_cache = {}  # Memo-Cache zurücksetzen
        r2 = _contrastive_margin_and_decision(
            ["common", "shared", "unique"], 0.5, "en", 42, n_docs=5, df={}
        )
        assert r1 == r2


class TestDedupeWordSets:
    """_dedupe_word_sets(): gruppiert wortidentische Kandidaten (Jaccard >=
    _EARLY_STOP_DEDUPE_JACCARD) -- Regressionstest für den realen Bug aus
    der Early-Stop-Validierung: ohne Dedupe zählen mehrere Provider-Texte
    desselben Songs (nur andere Formatierung) als "eigene" Kandidaten, der
    Separations-Check schlägt dann IMMER fehl (best_score == second_score)."""

    def test_fast_identische_kandidaten_werden_zusammengefasst(self):
        a = {"hello", "world", "foo", "bar", "baz"}
        b = {
            "hello",
            "world",
            "foo",
            "bar",
        }  # Jaccard 4/5 = 0.8 -- Grenzfall, gilt als gleich
        groups = _dedupe_word_sets([a, b])
        assert len(groups) == 1

    def test_verschiedene_songs_bleiben_getrennt(self):
        a = {"hello", "world", "foo", "bar"}
        b = {"zzz", "yyy", "xxx", "www"}  # kein Overlap
        groups = _dedupe_word_sets([a, b])
        assert len(groups) == 2

    def test_leere_wortmengen_werden_ignoriert(self):
        groups = _dedupe_word_sets([set(), {"a", "b"}, set()])
        assert len(groups) == 1


class TestTranscribeWithEarlyStop:
    """_transcribe_with_early_stop(): inkrementeller Segment-Konsum mit
    frühem Abbruch, sobald ein Kandidat über mehrere Checkpoints stabil
    sicher erkannt ist. Fake-Modell statt echtem Whisper/ffmpeg -- prüft
    nur die Abbruchlogik (Gate/Konfirmation/Marge/Separation), nicht die
    Audio-Verarbeitung selbst (dafür gibt es keinen Unit-Test, siehe
    _transcribe())."""

    MODEL_NAME = "fake-model"

    class _FakeSegment:
        def __init__(self, text, end, no_speech_prob=0.05, avg_logprob=-0.2):
            self.text = text
            self.end = end
            self.no_speech_prob = no_speech_prob
            self.avg_logprob = avg_logprob

    class _FakeModel:
        def __init__(self, segments):
            self._segments = segments

        def transcribe(self, path, **kwargs):
            return iter(self._segments), None

    def _run(self, monkeypatch, segments, candidate_word_sets):
        monkeypatch.setattr(lyrics_core, "_get_whisper_model", lambda name: object())
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
        monkeypatch.setitem(
            lyrics_core._whisper_models, self.MODEL_NAME, self._FakeModel(segments)
        )
        # Hintergrund-Pool fuer die kontrastive Marge: 5 Songs, alle disjunkt
        # zu den Kandidatenwoertern unten (siehe TestContrastiveMarginAndDecision).
        # Rein alphabetische Woerter -- die Tokenisierung (_extract_lrc_words/
        # re.findall "[^\\W\\d_]+") trennt an Ziffern, Zahlen im Wort wuerden
        # das Wort in Fragmente zerreissen und den Test verfaelschen.
        lyrics_core._contrastive_song_texts = {
            i: [w]
            for i, w in enumerate(
                ["bgwordone", "bgwordtwo", "bgwordthree", "bgwordfour", "bgwordfive"],
                start=1,
            )
        }
        lyrics_core._contrastive_lang_pools = {"en": list(range(1, 6))}
        return lyrics_core._transcribe_with_early_stop(
            Path("nonexistent.flac"),
            0.0,
            480.0,
            self.MODEL_NAME,
            "en",
            candidate_word_sets,
            None,
            None,
            n_docs=5,
            df={},
        )

    def teardown_method(self):
        lyrics_core._contrastive_lang_pools = None
        lyrics_core._contrastive_song_texts = None
        lyrics_core._whisper_models.pop(self.MODEL_NAME, None)

    @staticmethod
    def _alpha_words(prefix: str, n: int) -> set:
        """n eindeutige, rein alphabetische Testwoerter -- Ziffern wuerden
        von der Tokenisierung (re.findall "[^\\W\\d_]+") mitten im Wort
        getrennt und wuerden Transkript- und Kandidatenwoerter inkonsistent
        machen."""
        import itertools
        import string

        combos = itertools.islice(
            itertools.product(string.ascii_lowercase, repeat=3), n
        )
        return {prefix + "".join(c) for c in combos}

    def test_stoppt_frueh_wenn_kandidat_stabil_erkannt_wird(self, monkeypatch):
        a_words = self._alpha_words("aw", 25)
        b_words = self._alpha_words("bw", 25)
        a_sorted = sorted(a_words)

        segments = [
            self._FakeSegment(
                " ".join(a_sorted[:3]), end=10.0
            ),  # zu wenig Woerter/Zeit -> Gate blockiert
            self._FakeSegment(
                " ".join(a_sorted[3:15]), end=20.0
            ),  # 15 Woerter, Gate noch zu
            self._FakeSegment(
                " ".join(a_sorted[15:]), end=35.0
            ),  # jetzt alle 25 -> 1. Bestaetigung
            self._FakeSegment("", end=50.0),  # 2. Bestaetigung
            self._FakeSegment("", end=65.0),  # 3. Bestaetigung -> frueher Stop
            self._FakeSegment(
                " ".join(sorted(b_words)), end=80.0
            ),  # darf NIE verarbeitet werden
        ]
        words, no_speech, logprob, early_stopped = self._run(
            monkeypatch, segments, [a_words, b_words]
        )
        assert early_stopped is True
        assert (
            set(words) == a_words
        )  # das "Gift"-Segment (b_words) wurde nie konsumiert

    def test_kein_stop_ohne_hintergrund_pool(self, monkeypatch):
        """Zu kleiner Hintergrund-Pool -> fallback=True -> darf NIE frueh stoppen
        (Sicherheitsregel: keine fruehe Akzeptanz ohne Vergleichsbasis)."""
        a_words = self._alpha_words("aw", 25)
        a_text = " ".join(sorted(a_words))
        segments = [self._FakeSegment(a_text, end=t) for t in (35.0, 50.0, 65.0, 80.0)]

        monkeypatch.setattr(lyrics_core, "_get_whisper_model", lambda name: object())
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
        monkeypatch.setitem(
            lyrics_core._whisper_models, self.MODEL_NAME, self._FakeModel(segments)
        )
        lyrics_core._contrastive_song_texts = {}
        lyrics_core._contrastive_lang_pools = {}  # kein Pool -> fallback

        words, no_speech, logprob, early_stopped = (
            lyrics_core._transcribe_with_early_stop(
                Path("nonexistent.flac"),
                0.0,
                480.0,
                self.MODEL_NAME,
                "en",
                [a_words],
                None,
                None,
                n_docs=5,
                df={},
            )
        )
        assert early_stopped is False

    def test_dedupe_verhindert_ewigen_separations_fehlschlag(self, monkeypatch):
        """Regressionstest fuer den realen Bug: zwei Kandidaten desselben Songs
        (nur andere Formatierung, Jaccard 0.9) duerfen den Separations-Check
        NICHT permanent blockieren -- ohne _dedupe_word_sets waere
        best_score == second_score und consecutive_ok bliebe fuer immer 0."""
        a_words = self._alpha_words("aw", 20)
        a_list = sorted(a_words)
        a_words_variant = set(a_list[:18]) | {
            "variantone",
            "varianttwo",
        }  # Jaccard 18/22 ≈ 0.82
        a_text = " ".join(a_list)
        segments = [self._FakeSegment(a_text, end=t) for t in (35.0, 50.0, 65.0, 80.0)]

        words, no_speech, logprob, early_stopped = self._run(
            monkeypatch, segments, [a_words, a_words_variant]
        )
        assert early_stopped is True

    def test_bricht_bei_ueberschrittenem_zeit_deckel_ab(self, monkeypatch):
        """Regressionstest fuer den realen "Dooh Dooh"-Fall (ROADMAP.md):
        extrem repetitive, wortlose Vocals liessen Whisper 26+ Minuten
        haengen, weit ueber dem Deckel. Segmente kommen zwar weiter (kein
        echter Deadlock), aber die Wanduhr laeuft dem Audio-Fortschritt
        davon -- _TRANSCRIBE_TIMEOUT_SEC muss dann unabhaengig vom
        Konfidenz-Checkpoint abbrechen."""
        segments = [
            self._FakeSegment("dooh dooh dooh", end=5.0),
            self._FakeSegment("dooh dooh dooh", end=10.0),
            self._FakeSegment("dooh dooh dooh", end=15.0),
            self._FakeSegment("darf nie verarbeitet werden", end=20.0),
        ]
        # t0, dann je Segment ein Aufruf -- erst beim 3. Segment ueberschreitet
        # die verstrichene Zeit den Deckel (300s).
        times = iter([0.0, 10.0, 50.0, 350.0, 999.0])
        monkeypatch.setattr(lyrics_core.time, "monotonic", lambda: next(times))
        stats_before = lyrics_core._early_stop_stats["timeout"]

        words, no_speech, logprob, early_stopped = self._run(
            monkeypatch, segments, [self._alpha_words("aw", 25)]
        )

        assert early_stopped is True
        assert "verarbeitet" not in words  # 4. Segment nie konsumiert
        assert lyrics_core._early_stop_stats["timeout"] == stats_before + 1

    def test_stoppt_frueh_bei_anhaltend_score_nahe_null(self, monkeypatch):
        """Regressionstest fuer die realen Haenger-Faelle "Dooh Dooh" und
        "Dragostea Din Tei" (ohne Sprachvorgabe, ROADMAP.md): Whisper
        halluziniert komplett am Kandidatentext vorbei (dort: kyrillisch statt
        lateinisch, hier simuliert durch disjunkte Wortmengen) -- der Score
        bleibt ueber mehrere Checkpoints praktisch bei Null. Muss lange vor
        dem 300s-Wall-Clock-Deckel abbrechen, OHNE selbst "kein Match" zu
        entscheiden (nur frueh gestoppt, wie beim Timeout -- die eigentliche
        Annahme/Ablehnung faellt weiterhin in _whisper_best/_whisper_accept)."""
        x_words = self._alpha_words("xw", 25)  # disjunkt zu a_words/b_words/bgword*
        x_text = " ".join(sorted(x_words))
        poison_words = self._alpha_words("aw", 25)  # entspricht candidate a

        segments = [
            self._FakeSegment(x_text, end=35.0),  # Checkpoint 1: Score 0
            self._FakeSegment("", end=50.0),  # Checkpoint 2: Score 0
            self._FakeSegment("", end=65.0),  # Checkpoint 3: Score 0 -> Stop
            self._FakeSegment(
                " ".join(sorted(poison_words)), end=80.0
            ),  # darf NIE verarbeitet werden
        ]
        stats_before = lyrics_core._early_stop_stats["nahe_null"]

        words, no_speech, logprob, early_stopped = self._run(
            monkeypatch, segments, [poison_words, self._alpha_words("bw", 25)]
        )

        assert early_stopped is True
        assert set(words) == x_words  # das 4. (poison) Segment wurde nie konsumiert
        assert lyrics_core._early_stop_stats["nahe_null"] == stats_before + 1

    def test_kurzer_score_einbruch_z_b_instrumental_intro_stoppt_nicht_faelschlich(
        self, monkeypatch
    ):
        """Robustheit gegen Fehlalarm: ein einzelner (oder zweier) Nahe-Null-
        Checkpoint allein darf nicht abbrechen -- z.B. ein Instrumental-Intro,
        bevor der eigentliche (passende) Gesang einsetzt. Sobald wieder ein
        Treffer-Score auftaucht, muss der Nahe-Null-Zaehler zuruecksetzen und
        die Transkription normal weiterlaufen."""
        x_words = self._alpha_words("xw", 25)  # erste zwei Checkpoints: kein Treffer
        a_words = self._alpha_words("aw", 25)  # dritter Checkpoint: echter Treffer

        segments = [
            self._FakeSegment(" ".join(sorted(x_words)), end=35.0),  # Score 0
            self._FakeSegment("", end=50.0),  # weiterhin Score 0 (2. in Folge)
            self._FakeSegment(
                " ".join(sorted(a_words)), end=65.0
            ),  # Treffer -> Zaehler reset
        ]
        words, no_speech, logprob, early_stopped = self._run(
            monkeypatch, segments, [a_words, self._alpha_words("bw", 25)]
        )

        assert early_stopped is False  # weder Nahe-Null- noch Akzeptanz-Stop ausgeloest
        assert set(words) == x_words | a_words  # alle Segmente wurden konsumiert


class TestSongCandidateWords:
    """_song_candidate_words() tokenisiert die Kandidatentexte eines
    Cache-Songs und memoisiert das Ergebnis (siehe _build_contrastive_context)."""

    def teardown_method(self):
        lyrics_core._contrastive_song_texts = None
        lyrics_core._contrastive_song_words_cache = {}

    def test_tokenisiert_alle_kandidatentexte_eines_songs(self):
        lyrics_core._contrastive_song_texts = {
            7: ["[00:01.00]hello world\n", "[00:02.00]foo bar\n"]
        }
        words = _song_candidate_words(7)
        assert words == [["hello", "world"], ["foo", "bar"]]

    def test_unbekannte_song_id_liefert_leere_liste(self):
        lyrics_core._contrastive_song_texts = {}
        assert _song_candidate_words(999) == []

    def test_memoisiert_ergebnis(self):
        lyrics_core._contrastive_song_texts = {7: ["hello world"]}
        first = _song_candidate_words(7)
        lyrics_core._contrastive_song_texts = {7: ["completely different"]}
        second = _song_candidate_words(7)
        assert first == second  # aus dem Memo-Cache, nicht neu tokenisiert


class TestBuildContrastiveContext:
    """_build_contrastive_context(): baut einmal pro Lauf die globale
    Cache-IDF + song_id -> Sprache-Map aus einer echten Cache-DB."""

    def teardown_method(self):
        lyrics_core._cache_conn = None
        lyrics_core._contrastive_idf = None
        lyrics_core._contrastive_lang_pools = None
        lyrics_core._contrastive_song_texts = None
        lyrics_core._contrastive_song_words_cache = {}

    def test_baut_idf_und_sprach_pools_aus_cache_db(self, tmp_path, monkeypatch):
        conn = cache_store.open_cache(tmp_path / "cache.db")
        # 6 englische Songs (genug für einen Pool >= _CONTRASTIVE_MIN_BACKGROUND),
        # je EIGENER Inhalt (sonst dedupliziert texte.inhalt über den
        # Fingerabdruck auf eine einzige Zeile -- gewollt für echte Duplikate,
        # hier aber ein Testartefakt, das umgangen werden muss).
        for i in range(6):
            cache_store.put_provider(
                conn,
                "lrclib",
                f"artist {i}",
                f"title {i}",
                "treffer",
                f"[00:01.00]this is definitely an english song number {i} about "
                "nothing important at all today\n",
            )
        lyrics_core._cache_conn = conn

        _build_contrastive_context()

        n_docs, df = lyrics_core._contrastive_idf
        assert n_docs == 6
        assert df["english"] == 6
        assert "en" in lyrics_core._contrastive_lang_pools
        assert len(lyrics_core._contrastive_lang_pools["en"]) == 6
        assert len(lyrics_core._contrastive_song_texts) == 6

    def test_ohne_cache_conn_bricht_mit_fehlermeldung_ab(self, capsys):
        lyrics_core._cache_conn = None
        with pytest.raises(SystemExit):
            _build_contrastive_context()
        out = capsys.readouterr().out
        assert "Cache-DB" in out
        assert "--no-cache" in out


class TestWhisperBestContrastiveExperiment:
    """_whisper_best() nutzt die globale Cache-IDF statt einer Datei-basierten
    Tabelle und gibt die kontrastive Marge direkt als letzten Rückgabewert
    zurück (seit v1.11.0, vorher via debug_scores-Dict). PFLICHT-Verifikation
    für den debug_scores->Rückgabewert-Refactor: die Marge muss tatsächlich
    berechnet werden (nicht None) und _whisper_accept() muss mit GENAU dieser
    Marge entscheiden -- nicht stillschweigend auf margin=None zurückfallen."""

    def teardown_method(self):
        lyrics_core._cache_conn = None
        lyrics_core._cache_refresh = False
        lyrics_core._contrastive_idf = None
        lyrics_core._contrastive_lang_pools = None
        lyrics_core._contrastive_song_texts = None
        lyrics_core._contrastive_song_words_cache = {}

    def _prep(self, monkeypatch, tmp_path):
        conn = cache_store.open_cache(tmp_path / "cache.db")
        lyrics_core._cache_conn = conn
        lyrics_core._cache_ttl_days = 30
        lyrics_core._cache_refresh = False
        monkeypatch.setattr(lyrics_core, "_get_whisper_model", lambda name: object())
        monkeypatch.setattr(
            lyrics_core, "_detect_lrc_language", lambda candidates: "en"
        )
        return conn

    def test_nutzt_globale_cache_idf_und_gibt_marge_direkt_zurueck(
        self, tmp_path, monkeypatch
    ):
        conn = self._prep(monkeypatch, tmp_path)
        # 5 Hintergrund-Songs gleicher Sprache im Pool, ausreichend für die
        # Marge. IDs bewusst weit weg von 1 -- put_transcript() unten legt den
        # aktuellen Song als song_id=1 in derselben (frischen) DB an, eine
        # Kollision mit dem Pool würde ihn dort faelschlich ausschliessen.
        lyrics_core._contrastive_idf = (10, {})  # leeres df -> Jaccard unweighted
        lyrics_core._contrastive_lang_pools = {"en": list(range(101, 106))}
        lyrics_core._contrastive_song_texts = {
            i: ["completely unrelated background text here"] for i in range(101, 106)
        }
        cache_store.put_transcript(
            conn, "the artist", "the title", "hello world foo bar", 0.1, -0.2
        )

        flac = tmp_path / "song.flac"
        flac.write_bytes(b"x")
        lrc = tmp_path / "a.lrc"
        lrc.write_text("[00:01.00]hello world foo bar\n", encoding="utf-8")

        best_path, score, has_vocals, words, model, lang, margin, early_stopped = (
            lyrics_core._whisper_best(
                flac, [lrc], artist="The Artist", title="The Title"
            )
        )
        assert best_path == lrc
        assert model == lyrics_core._WHISPER_MODEL
        # kein Overlap im Hintergrund-Pool -> bg_max=0.0 -> Marge == best_score.
        # Die Marge ist eine ECHT BERECHNETE Zahl (nicht None) -- genau das
        # muss der debug_scores->Rückgabewert-Refactor weiterhin liefern.
        assert margin is not None
        assert margin == pytest.approx(score)
        # PFLICHT-Verifikation: _whisper_accept() entscheidet MIT dieser
        # tatsaechlich berechneten Marge (>= _CONTRASTIVE_MARGIN) -- die Marge
        # kommt also in der echten Akzeptanz-Entscheidung an, nicht nur als
        # toter Rückgabewert.
        assert margin >= _CONTRASTIVE_MARGIN
        assert lyrics_core._whisper_accept(score, lang, margin=margin) is True

    def test_zu_kleiner_pool_gibt_margin_none_zurueck(self, tmp_path, monkeypatch):
        conn = self._prep(monkeypatch, tmp_path)
        lyrics_core._contrastive_idf = (10, {})
        lyrics_core._contrastive_lang_pools = {"en": [101, 102]}  # zu klein
        lyrics_core._contrastive_song_texts = {101: ["x"], 102: ["y"]}
        cache_store.put_transcript(
            conn, "the artist", "the title", "hello world foo bar", 0.1, -0.2
        )

        flac = tmp_path / "song.flac"
        flac.write_bytes(b"x")
        lrc = tmp_path / "a.lrc"
        lrc.write_text("[00:01.00]hello world foo bar\n", encoding="utf-8")

        best_path, score, has_vocals, words, model, lang, margin, early_stopped = (
            lyrics_core._whisper_best(
                flac, [lrc], artist="The Artist", title="The Title"
            )
        )
        assert margin is None


class TestContrastiveExperimentWhisperSafetyNet:
    """--cache-only betrifft nur Live-PROVIDER-Abfragen (siehe _cache_only-
    Docstring), NICHT Whisper (Bugfix v1.10.1 -- ein v1.10.0-Refactor hatte
    das faelschlich gekoppelt). Ein Cache-Miss transkribiert daher IMMER live,
    unabhaengig von --cache-only -- sonst koennte kein neuer Song je zum
    ersten Mal verifiziert werden."""

    def teardown_method(self):
        lyrics_core._cache_conn = None
        lyrics_core._cache_refresh = False
        lyrics_core._cache_only = False
        lyrics_core._contrastive_idf = None

    def test_cache_only_transkribiert_trotzdem_live_bei_cache_miss(
        self, tmp_path, monkeypatch
    ):
        conn = cache_store.open_cache(tmp_path / "cache.db")
        lyrics_core._cache_conn = conn
        lyrics_core._cache_ttl_days = 30
        lyrics_core._cache_refresh = False
        lyrics_core._cache_only = True
        lyrics_core._contrastive_idf = (1, {})
        monkeypatch.setattr(lyrics_core, "_get_whisper_model", lambda name: object())
        monkeypatch.setattr(
            lyrics_core, "_detect_lrc_language", lambda candidates: None
        )

        def _fake_transcribe(*a, **k):
            return ["hello", "world"], 0.05, -0.3, False

        monkeypatch.setattr(
            lyrics_core, "_transcribe_with_early_stop", _fake_transcribe
        )

        flac = tmp_path / "song.flac"
        flac.write_bytes(b"x")
        lrc = tmp_path / "a.lrc"
        lrc.write_text("[00:01.00]hello world\n", encoding="utf-8")

        best_path, score, has_vocals, words, model, lang, margin, early_stopped = (
            lyrics_core._whisper_best(flac, [lrc], artist="X", title="Y")
        )
        assert best_path == lrc

    def test_ohne_cache_only_transkribiert_neuen_song_trotzdem_live(
        self, tmp_path, monkeypatch
    ):
        """Gegenprobe: OHNE --cache-only muss ein Cache-Miss weiterhin live
        transkribieren -- die kontrastive Marge darf neue Songs nicht
        pauschal blockieren."""
        conn = cache_store.open_cache(tmp_path / "cache.db")
        lyrics_core._cache_conn = conn
        lyrics_core._cache_ttl_days = 30
        lyrics_core._cache_refresh = False
        lyrics_core._cache_only = False
        lyrics_core._contrastive_idf = (1, {})
        monkeypatch.setattr(lyrics_core, "_get_whisper_model", lambda name: object())
        monkeypatch.setattr(
            lyrics_core, "_detect_lrc_language", lambda candidates: None
        )

        def _fake_transcribe(*a, **k):
            return ["hello", "world"], 0.05, -0.3, False

        monkeypatch.setattr(
            lyrics_core, "_transcribe_with_early_stop", _fake_transcribe
        )

        flac = tmp_path / "song.flac"
        flac.write_bytes(b"x")
        lrc = tmp_path / "a.lrc"
        lrc.write_text("[00:01.00]hello world\n", encoding="utf-8")

        best_path, score, has_vocals, words, model, lang, margin, early_stopped = (
            lyrics_core._whisper_best(flac, [lrc], artist="X", title="Y")
        )
        assert best_path == lrc


class TestRetryMissing:
    """--retry-missing: gezielte Live-Neuabfrage für Cache-Einträge mit
    status='nichts'/'fehlschlag' (Auslöser: lrclib steckte einmal stundenlang
    fälschlich in der "gesperrt"-Ruhephase, obwohl der Provider einwandfrei
    funktionierte — siehe ROADMAP.md). Testet _retry_missing() direkt
    (nicht über main()/subprocess): main() öffnet die Cache-DB immer relativ
    zu __file__, ein Subprozess-Test würde also die ECHTE Produktions-
    Cache-DB neben dem Skript öffnen und ggf. live abfragen (siehe
    TestFastFlagMain-Kommentar zum selben Problem bei --fast) -- deshalb hier
    stattdessen lyrics_core._cache_conn direkt auf eine tmp_path-DB
    gesetzt, wie bei TestProviderCache."""

    def _open(self, tmp_path):
        conn = cache_store.open_cache(tmp_path / "cache.db")
        lyrics_core._cache_conn = conn
        lyrics_core._cache_refresh = False
        lyrics_core._cache_only = False
        return conn

    def teardown_method(self):
        lyrics_core._cache_conn = None
        lyrics_core._cache_refresh = False
        lyrics_core._cache_only = False

    @staticmethod
    def _fake_run(
        responses: dict[str, str], fail_needles: dict[str, str] | None = None
    ):
        """Ersetzt lyrics_core.subprocess.run -- schreibt für Queries, die
        einen der `responses`-Schlüssel enthalten, LRC-Inhalt in die Ziel-
        datei, sonst bleibt sie leer (= kein Treffer, wie ein sauberer
        Fehlschlag ohne Rate-Limit-Signal). `fail_needles` simuliert einen
        TRANSIENTEN Fehler (z.B. Rate-Limit) statt eines echten "nichts
        gefunden": liefert für Queries, die einen dieser Schlüssel enthalten,
        das stderr-Signal, das _rate_limit_report als solchen erkennt (siehe
        _rate_limit_report-Docstring in lyrics_core.py)."""
        fail_needles = fail_needles or {}

        class _Result:
            def __init__(self, stderr: str = ""):
                self.stderr = stderr

        calls: list[tuple[str, str]] = []

        def _run(cmd, **kwargs):
            query, provider = cmd[1], cmd[-1]
            calls.append((query, provider))
            out_path = Path(cmd[3])
            for needle, content in responses.items():
                if needle in query:
                    out_path.write_text(content, encoding="utf-8")
                    return _Result()
            for needle, stderr in fail_needles.items():
                if needle in query:
                    return _Result(stderr=stderr)
            return _Result()

        _run.calls = calls
        return _run

    def test_nur_passende_song_provider_kombis_werden_retried(
        self, tmp_path, monkeypatch
    ):
        conn = self._open(tmp_path)
        cache_store.put_provider(conn, "lrclib", "artist a", "title a", "nichts", None)
        cache_store.put_provider(
            conn, "genius", "artist a", "title a", "treffer", "[00:01.00]hallo"
        )
        cache_store.put_provider(
            conn,
            "lrclib",
            "artist b",
            "title b",
            "fehlschlag",
            None,
            fehlergrund="gesperrt",
        )
        cache_store.put_provider(
            conn, "musixmatch", "artist c", "title c", "nichts", None
        )

        fake_run = self._fake_run({"artist a": "[00:01.00]neuer Text\n"})
        monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)

        lyrics_core._retry_missing(["lrclib"], None, None)

        # Nur die zwei lrclib-Zeilen mit status nichts/fehlschlag wurden angefragt --
        # weder der genius-treffer noch der musixmatch-Eintrag eines anderen Providers.
        assert len(fake_run.calls) == 2
        assert {p for _, p in fake_run.calls} == {"lrclib"}

        assert cache_store.get_provider(conn, "lrclib", "artist a", "title a") == {
            "status": "treffer",
            "content": "[00:01.00]neuer Text\n",
        }
        # weiterhin kein Treffer, aber neu als "nichts" geschrieben (kein
        # "gesperrt"-Fehlschlag mehr, da diesmal ein echter Versuch stattfand)
        row_b = conn.execute(
            "SELECT status, fehlergrund FROM ergebnisse e JOIN songs s ON s.id=e.song_id "
            "WHERE e.quelle='lrclib' AND s.artist_key='artist b' AND s.titel_key='title b'"
        ).fetchone()
        assert row_b == ("nichts", None)

        # unberührte Einträge (schon treffer, oder anderer Provider) bleiben unverändert
        row_genius = conn.execute(
            "SELECT status FROM ergebnisse e JOIN songs s ON s.id=e.song_id "
            "WHERE e.quelle='genius' AND s.artist_key='artist a' AND s.titel_key='title a'"
        ).fetchone()
        assert row_genius == ("treffer",)
        row_musixmatch = conn.execute(
            "SELECT status FROM ergebnisse e JOIN songs s ON s.id=e.song_id "
            "WHERE e.quelle='musixmatch' AND s.artist_key='artist c' AND s.titel_key='title c'"
        ).fetchone()
        assert row_musixmatch == ("nichts",)

    def test_artist_title_beschraenkt_auf_einen_song(self, tmp_path, monkeypatch):
        conn = self._open(tmp_path)
        cache_store.put_provider(conn, "lrclib", "artist a", "title a", "nichts", None)
        cache_store.put_provider(conn, "lrclib", "artist b", "title b", "nichts", None)

        fake_run = self._fake_run({})
        monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)

        lyrics_core._retry_missing(["lrclib"], "Artist A", "Title A")

        assert len(fake_run.calls) == 1
        assert "artist a" in fake_run.calls[0][0]

    def test_unbekannter_song_bei_artist_title_bricht_ab(self, tmp_path, capsys):
        self._open(tmp_path)
        with pytest.raises(SystemExit) as exc:
            lyrics_core._retry_missing(["lrclib"], "Nobody", "Nothing")
        assert exc.value.code == 1
        assert "nicht in der Cache-Datenbank gefunden" in capsys.readouterr().out

    def test_nur_artist_beschraenkt_auf_alle_songs_dieses_kuenstlers(
        self, tmp_path, monkeypatch
    ):
        conn = self._open(tmp_path)
        cache_store.put_provider(conn, "lrclib", "artist a", "title a", "nichts", None)
        cache_store.put_provider(conn, "lrclib", "artist a", "title b", "nichts", None)
        # anderer Künstler -- darf nicht mit angefragt werden
        cache_store.put_provider(conn, "lrclib", "artist z", "title z", "nichts", None)

        fake_run = self._fake_run({})
        monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)

        lyrics_core._retry_missing(["lrclib"], "Artist A", None)

        assert len(fake_run.calls) == 2
        assert {q for q, _ in fake_run.calls} == {
            "artist a title a",
            "artist a title b",
        }

    def test_unbekannter_artist_ohne_title_bricht_ab(self, tmp_path, capsys):
        self._open(tmp_path)
        with pytest.raises(SystemExit) as exc:
            lyrics_core._retry_missing(["lrclib"], "Nobody", None)
        assert exc.value.code == 1
        assert "Kein Song von Artist" in capsys.readouterr().out

    def test_ergebnisse_sortiert_nach_artist_titel(self, tmp_path, monkeypatch):
        conn = self._open(tmp_path)
        # bewusst in "falscher" Einfügereihenfolge angelegt
        cache_store.put_provider(conn, "lrclib", "zeta", "song", "nichts", None)
        cache_store.put_provider(conn, "lrclib", "alpha", "zzz", "nichts", None)
        cache_store.put_provider(conn, "lrclib", "alpha", "aaa", "nichts", None)

        fake_run = self._fake_run({})
        monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)

        lyrics_core._retry_missing(["lrclib"], None, None)

        assert [q for q, _ in fake_run.calls] == [
            "alpha aaa",
            "alpha zzz",
            "zeta song",
        ]

    def test_all_fragt_alle_provider_ab(self, tmp_path, monkeypatch):
        conn = self._open(tmp_path)
        for provider in lyrics_core._ALL_PROVIDERS:
            cache_store.put_provider(
                conn, provider, "artist a", "title a", "nichts", None
            )

        fake_run = self._fake_run({})
        monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)

        lyrics_core._retry_missing(lyrics_core._ALL_PROVIDERS, None, None)

        assert {p for _, p in fake_run.calls} == set(lyrics_core._ALL_PROVIDERS)

    def test_leere_treffermenge_gibt_hinweis_ohne_fehler(self, tmp_path, capsys):
        self._open(tmp_path)
        lyrics_core._retry_missing(["lrclib"], None, None)
        assert "Keine passenden Cache-Einträge" in capsys.readouterr().out

    def test_stellt_cache_refresh_nach_lauf_wieder_her(self, tmp_path, monkeypatch):
        conn = self._open(tmp_path)
        cache_store.put_provider(conn, "lrclib", "artist a", "title a", "nichts", None)
        lyrics_core._cache_refresh = False

        fake_run = self._fake_run({})
        monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)

        lyrics_core._retry_missing(["lrclib"], None, None)

        assert lyrics_core._cache_refresh is False

    def test_song_ids_beschraenkt_auf_diese_songs(self, tmp_path, monkeypatch):
        """song_ids ist die von fetch_providers.retry_missing genutzte
        PFAD-Scope-Eingrenzung (siehe dortiger Docstring) -- unabhängig von
        artist/title."""
        conn = self._open(tmp_path)
        cache_store.put_provider(conn, "lrclib", "artist a", "title a", "nichts", None)
        cache_store.put_provider(conn, "lrclib", "artist b", "title b", "nichts", None)
        song_id_a = conn.execute(
            "SELECT id FROM songs WHERE artist_key='artist a'"
        ).fetchone()[0]

        fake_run = self._fake_run({})
        monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)

        lyrics_core._retry_missing(["lrclib"], None, None, song_ids=[song_id_a])

        assert len(fake_run.calls) == 1
        assert "artist a" in fake_run.calls[0][0]

    def test_song_ids_hat_vorrang_vor_artist_title(self, tmp_path, monkeypatch):
        conn = self._open(tmp_path)
        cache_store.put_provider(conn, "lrclib", "artist a", "title a", "nichts", None)
        cache_store.put_provider(conn, "lrclib", "artist b", "title b", "nichts", None)
        song_id_b = conn.execute(
            "SELECT id FROM songs WHERE artist_key='artist b'"
        ).fetchone()[0]

        fake_run = self._fake_run({})
        monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)

        # artist zeigt auf "artist a", song_ids zeigt auf "artist b" --
        # song_ids gewinnt.
        lyrics_core._retry_missing(["lrclib"], "Artist A", None, song_ids=[song_id_b])

        assert len(fake_run.calls) == 1
        assert "artist b" in fake_run.calls[0][0]

    def test_leere_song_ids_liste_fragt_nichts_ab_kein_fallback_auf_ganze_db(
        self, tmp_path, monkeypatch, capsys
    ):
        """Eine leere Liste (PFAD ohne passende Songs) bedeutet "nichts zu
        tun" -- NICHT dieselbe Bedeutung wie song_ids=None (keine
        Eingrenzung, ganze DB)."""
        conn = self._open(tmp_path)
        cache_store.put_provider(conn, "lrclib", "artist a", "title a", "nichts", None)

        def _fail_if_called(*a, **k):
            raise AssertionError("leere song_ids-Liste darf nie live abfragen")

        monkeypatch.setattr(lyrics_core.subprocess, "run", _fail_if_called)

        lyrics_core._retry_missing(["lrclib"], None, None, song_ids=[])

        assert "Keine passenden Cache-Einträge" in capsys.readouterr().out

    def test_transienter_fehlschlag_wird_von_echtem_nichttreffer_unterschieden(
        self, tmp_path, monkeypatch, capsys
    ):
        """Realer Fall (siehe ROADMAP.md): ein --retry-missing-Lauf für
        Simon & Garfunkel / El Condor Pasa meldete "weiterhin kein Treffer",
        obwohl der Song bei lrclib nachweislich existiert (manuell per
        syncedlyrics live verifiziert) -- die Cache-DB zeigte hinterher
        status='fehlschlag'/fehlergrund='rate_limit', nicht 'nichts'. Der
        Bug: path is None wird bei EINEM transienten Fehler (erneutes
        Rate-Limit/Timeout/Captcha während des Retry-Versuchs selbst) exakt
        genauso gemeldet wie ein bestätigtes "gibt es nicht" -- obwohl genau
        in diesem Fall ein weiterer Versuch am ehesten lohnt."""
        conn = self._open(tmp_path)
        cache_store.put_provider(conn, "lrclib", "artist a", "title a", "nichts", None)

        fake_run = self._fake_run(
            {},
            fail_needles={
                "artist a": "An error occurred while searching for an LRC on Lrclib"
            },
        )
        monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)

        lyrics_core._retry_missing(["lrclib"], None, None)

        out = capsys.readouterr().out
        assert "weiterhin Fehler (rate_limit) — später erneut versuchen" in out
        assert "1 weiterhin mit Fehler" in out
        assert "0 weiterhin ohne Treffer" in out

        row = conn.execute(
            "SELECT status, fehlergrund FROM ergebnisse e JOIN songs s ON s.id=e.song_id "
            "WHERE e.quelle='lrclib' AND s.artist_key='artist a' AND s.titel_key='title a'"
        ).fetchone()
        assert row == ("fehlschlag", "rate_limit")

    def test_echtes_nichts_wird_weiterhin_als_kein_treffer_gemeldet(
        self, tmp_path, monkeypatch, capsys
    ):
        conn = self._open(tmp_path)
        cache_store.put_provider(conn, "lrclib", "artist a", "title a", "nichts", None)

        fake_run = self._fake_run({})  # kein Treffer, kein Fehlersignal
        monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)

        lyrics_core._retry_missing(["lrclib"], None, None)

        out = capsys.readouterr().out
        assert "weiterhin kein Treffer" in out
        assert "1 weiterhin ohne Treffer" in out
        assert "0 weiterhin mit Fehler" in out

        row = conn.execute(
            "SELECT status, fehlergrund FROM ergebnisse e JOIN songs s ON s.id=e.song_id "
            "WHERE e.quelle='lrclib' AND s.artist_key='artist a' AND s.titel_key='title a'"
        ).fetchone()
        assert row == ("nichts", None)
