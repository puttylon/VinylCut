import pytest

from library import (
    method_from_cache_entry,
    parse_offset,
    parse_preview_duration,
    reject_reason_from_cache_entry,
)


class TestParseOffset:
    def test_positive_with_colon(self):
        assert parse_offset("+2:34") == pytest.approx(154.0)

    def test_negative_with_colon(self):
        assert parse_offset("-1:30") == pytest.approx(-90.0)

    def test_unsigned_with_colon(self):
        assert parse_offset("2:34") == pytest.approx(154.0)

    def test_zero_minutes(self):
        assert parse_offset("0:30") == pytest.approx(30.0)

    def test_positive_seconds_only(self):
        assert parse_offset("+90") == pytest.approx(90.0)

    def test_negative_float(self):
        assert parse_offset("-45.5") == pytest.approx(-45.5)

    def test_ten_minutes(self):
        assert parse_offset("10:00") == pytest.approx(600.0)

    def test_zero(self):
        assert parse_offset("0") == pytest.approx(0.0)

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_offset("abc")


class TestParsePreviewDuration:
    def test_plain_p_returns_none(self):
        assert parse_preview_duration("p") is None

    def test_valid_value_in_range(self):
        assert parse_preview_duration("p18") == pytest.approx(18.0)

    def test_decimal_value(self):
        assert parse_preview_duration("p5.5") == pytest.approx(5.5)

    def test_lower_bound_inclusive(self):
        assert parse_preview_duration("p2") == pytest.approx(2.0)

    def test_upper_bound_inclusive(self):
        assert parse_preview_duration("p30") == pytest.approx(30.0)

    def test_below_minimum_ignored(self):
        assert parse_preview_duration("p1.9") is None

    def test_above_maximum_ignored(self):
        assert parse_preview_duration("p30.1") is None

    def test_non_numeric_suffix_ignored(self):
        assert parse_preview_duration("px") is None

    def test_unrelated_action_returns_none(self):
        assert parse_preview_duration("ok") is None
        assert parse_preview_duration("+") is None
        assert parse_preview_duration("") is None


class TestMethodFromCacheEntry:
    """War bisher wortgleich in lrc_analyse.py UND whisper_analyse.py
    dupliziert, ohne eigene Tests in beiden -- erste Testabdeckung
    überhaupt (siehe ROADMAP.md, Redundanz-Aufräumen)."""

    def test_aktuelles_feld_method(self):
        assert method_from_cache_entry({"method": "whisper-medium"}) == "whisper-medium"

    def test_konsens_mit_kein_vokal_wird_umbenannt(self):
        assert (
            method_from_cache_entry({"method": "konsens", "no_vocal": True})
            == "konsens-kein-vokal"
        )

    def test_konsens_ohne_kein_vokal_bleibt(self):
        assert (
            method_from_cache_entry({"method": "konsens", "no_vocal": False})
            == "konsens"
        )

    def test_legacy_consensus_mit_no_vocal(self):
        assert (
            method_from_cache_entry({"consensus": True, "no_vocal": True})
            == "konsens-kein-vokal"
        )

    def test_legacy_consensus_ohne_no_vocal(self):
        assert method_from_cache_entry({"consensus": True}) == "konsens"

    def test_legacy_fallback(self):
        assert method_from_cache_entry({"fallback": True}) == "konsens-kein-vokal"

    def test_legacy_model_small(self):
        assert method_from_cache_entry({"model": "small"}) == "whisper-small"

    def test_legacy_model_base(self):
        assert method_from_cache_entry({"model": "base"}) == "whisper-base"

    def test_legacy_score_ohne_model(self):
        assert method_from_cache_entry({"score": 0.4}) == "whisper-base"

    def test_leerer_eintrag_ist_heuristik(self):
        assert method_from_cache_entry({}) == "heuristik"


class TestRejectReasonFromCacheEntry:
    """War bisher wortgleich in lrc_analyse.py, lrc_recheck.py UND
    whisper_analyse.py dupliziert, ohne eigene Tests -- erste
    Testabdeckung überhaupt (siehe ROADMAP.md, Redundanz-Aufräumen)."""

    def test_aktuelles_feld_reason(self):
        assert reject_reason_from_cache_entry({"reason": "kein-vokal"}) == "kein-vokal"

    def test_legacy_kein_provider(self):
        assert reject_reason_from_cache_entry({"providers": 0}) == "kein-provider"

    def test_legacy_kein_whisper_score_none(self):
        assert (
            reject_reason_from_cache_entry({"providers": 1, "score": None})
            == "kein-whisper"
        )

    def test_legacy_kein_vokal_score_und_words_null(self):
        assert (
            reject_reason_from_cache_entry({"providers": 1, "score": 0.0, "words": 0})
            == "kein-vokal"
        )

    def test_legacy_unter_schwelle(self):
        assert (
            reject_reason_from_cache_entry({"providers": 1, "score": 0.1, "words": 5})
            == "unter-schwelle"
        )
