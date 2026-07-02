import pytest
from interactive_cutter import parse_offset, fmt_dur, estimate_start


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


class TestFmtDur:
    def test_zero(self):
        assert fmt_dur(0) == "0:00.00"

    def test_one_minute(self):
        assert fmt_dur(60) == "1:00.00"

    def test_ninety_seconds(self):
        assert fmt_dur(90) == "1:30.00"

    def test_154_seconds(self):
        assert fmt_dur(154) == "2:34.00"

    def test_centiseconds(self):
        assert fmt_dur(154.37) == "2:34.37"

    def test_sub_second(self):
        assert fmt_dur(0.4) == "0:00.40"

    def test_negative(self):
        assert fmt_dur(-90) == "-1:30.00"

    def test_one_hour(self):
        assert fmt_dur(3600) == "60:00.00"


class TestEstimateStart:
    def test_first_track_always_zero(self):
        assert estimate_start(0, [], [], 0.0) == 0.0

    def test_first_track_ignores_gap(self):
        assert estimate_start(0, [], [], 99.0) == 0.0

    def test_with_dur_s(self):
        tracks = [{"title": "A", "dur_s": 120.0}, {"title": "B"}]
        assert estimate_start(1, tracks, [10.0], 5.0) == pytest.approx(135.0)

    def test_without_dur_s_falls_back_to_prev_start(self):
        tracks = [{"title": "A"}, {"title": "B"}]
        assert estimate_start(1, tracks, [154.0], 0.0) == pytest.approx(154.0)

    def test_gap_not_used_without_dur_s(self):
        tracks = [{"title": "A"}, {"title": "B"}]
        assert estimate_start(1, tracks, [154.0], 33.0) == pytest.approx(154.0)

    def test_second_transition_with_dur_s(self):
        tracks = [{"title": "A", "dur_s": 100.0}, {"title": "B", "dur_s": 200.0}, {"title": "C"}]
        assert estimate_start(2, tracks, [0.0, 102.0], 2.0) == pytest.approx(304.0)
