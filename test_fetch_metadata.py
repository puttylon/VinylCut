import pytest
from fetch_metadata import score_release


class TestScoreRelease:
    def test_missing_dur_s_key_does_not_crash(self):
        # MusicBrainz-Tracks ohne Länge haben den Key "dur_s" gar nicht
        # (anders als Discogs-Tracks, die immer einen Wert setzen, ggf. None).
        cand = {
            "title": "Album",
            "is_vinyl": True,
            "tracks": [{"title": "A"}, {"title": "B", "dur_s": 200.0}],
        }
        score = score_release(cand, flac_total=200.0, album="Album")
        assert isinstance(score, float)

    def test_all_durations_present_matching_total_scores_zero(self):
        cand = {
            "title": "Album",
            "is_vinyl": True,
            "tracks": [{"title": "A", "dur_s": 100.0}, {"title": "B", "dur_s": 100.0}],
        }
        score = score_release(cand, flac_total=200.0, album="Album")
        assert score == pytest.approx(0.0)

    def test_title_mismatch_penalized(self):
        cand = {
            "title": "Wrong Title",
            "is_vinyl": True,
            "tracks": [{"title": "A", "dur_s": 100.0}],
        }
        score = score_release(cand, flac_total=100.0, album="Album")
        assert score >= 100.0

    def test_missing_durations_penalized_but_no_crash(self):
        cand = {
            "title": "Album",
            "is_vinyl": True,
            "tracks": [{"title": "A"}, {"title": "B"}, {"title": "C", "dur_s": 100.0}],
        }
        score = score_release(cand, flac_total=300.0, album="Album")
        assert score > 0.0  # fehlende Längen werden bestraft, aber kein Crash
