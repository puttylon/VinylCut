from preparer import fmt_time


class TestFmtTime:
    def test_zero(self):
        assert fmt_time(0) == "0:00"

    def test_one_minute(self):
        assert fmt_time(60) == "1:00"

    def test_mixed(self):
        assert fmt_time(154) == "2:34"

    def test_rounding(self):
        assert fmt_time(59.6) == "1:00"

    def test_large(self):
        assert fmt_time(3661) == "61:01"
