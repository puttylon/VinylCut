import pytest

from library import parse_offset, parse_preview_duration


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
