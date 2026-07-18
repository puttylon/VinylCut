from assemble import get_segments, suggest_clean_name


def h(label, pos):
    return {"label": label, "pos": pos}


class TestGetSegments:
    def test_no_boundaries(self):
        history = [h("trim_start", 10.0), h("trim_end", 3800.0)]
        assert get_segments(history, 0) == [(10.0, 3800.0)]

    def test_one_boundary(self):
        history = [
            h("trim_start", 10.0),
            h("boundary_0_a", 1075.0),
            h("boundary_0_b", 1168.0),
            h("trim_end", 3800.0),
        ]
        assert get_segments(history, 1) == [(10.0, 1075.0), (1168.0, 3800.0)]

    def test_two_boundaries(self):
        history = [
            h("trim_start", 10.0),
            h("boundary_0_a", 1000.0),
            h("boundary_0_b", 1100.0),
            h("boundary_1_a", 2000.0),
            h("boundary_1_b", 2100.0),
            h("trim_end", 3800.0),
        ]
        result = get_segments(history, 2)
        assert result == [(10.0, 1000.0), (1100.0, 2000.0), (2100.0, 3800.0)]

    def test_three_boundaries(self):
        history = [
            h("trim_start", 5.0),
            h("boundary_0_a", 900.0),
            h("boundary_0_b", 1000.0),
            h("boundary_1_a", 1900.0),
            h("boundary_1_b", 2000.0),
            h("boundary_2_a", 2900.0),
            h("boundary_2_b", 3000.0),
            h("trim_end", 3800.0),
        ]
        result = get_segments(history, 3)
        assert result == [
            (5.0, 900.0),
            (1000.0, 1900.0),
            (2000.0, 2900.0),
            (3000.0, 3800.0),
        ]

    def test_segment_count(self):
        history = [
            h("trim_start", 0.0),
            h("boundary_0_a", 100.0),
            h("boundary_0_b", 200.0),
            h("trim_end", 300.0),
        ]
        assert len(get_segments(history, 1)) == 2


class TestSuggestCleanName:
    def test_removes_dash_raw(self):
        assert (
            suggest_clean_name("The Subways - When I'm With You-raw")
            == "The Subways - When I'm With You"
        )

    def test_removes_underscore_raw(self):
        assert suggest_clean_name("Artist - Album_raw") == "Artist - Album"

    def test_no_raw_suffix(self):
        assert suggest_clean_name("Artist - Album") == "Artist - Album"

    def test_case_insensitive(self):
        assert suggest_clean_name("Artist - Album-RAW") == "Artist - Album"

    def test_raw_in_middle_unchanged(self):
        assert suggest_clean_name("Raw Artist - Album") == "Raw Artist - Album"
