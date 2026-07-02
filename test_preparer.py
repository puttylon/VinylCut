from preparer import fmt_time


class TestFmtTime:
    def test_zero(self):
        assert fmt_time(0) == "0:00.00"

    def test_one_minute(self):
        assert fmt_time(60) == "1:00.00"

    def test_mixed(self):
        assert fmt_time(154) == "2:34.00"

    def test_centiseconds(self):
        assert fmt_time(154.37) == "2:34.37"

    def test_sub_second(self):
        assert fmt_time(0.8) == "0:00.80"

    def test_large(self):
        assert fmt_time(3661) == "61:01.00"
