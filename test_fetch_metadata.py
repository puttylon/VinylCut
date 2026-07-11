import pytest
import fetch_metadata
from fetch_metadata import fill_missing_durations, score_release


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


class TestFillMissingDurations:
    def test_fills_track_with_confident_match(self, monkeypatch):
        def fake_mb_json(url):
            return {"recordings": [{"title": "Song A", "length": 123000}]}

        monkeypatch.setattr(fetch_metadata, "_get_mb_json", fake_mb_json)
        cand = {"tracks": [{"title": "Song A"}]}
        filled = fill_missing_durations(cand, artist="Artist")
        assert filled == 1
        assert cand["tracks"][0]["dur_s"] == pytest.approx(123.0)

    def test_no_mb_result_leaves_track_untouched(self, monkeypatch):
        monkeypatch.setattr(fetch_metadata, "_get_mb_json", lambda url: None)
        cand = {"tracks": [{"title": "Song A"}]}
        filled = fill_missing_durations(cand, artist="Artist")
        assert filled == 0
        assert "dur_s" not in cand["tracks"][0]

    def test_title_mismatch_not_filled(self, monkeypatch):
        def fake_mb_json(url):
            return {"recordings": [{"title": "Completely Different Song", "length": 123000}]}

        monkeypatch.setattr(fetch_metadata, "_get_mb_json", fake_mb_json)
        cand = {"tracks": [{"title": "Song A"}]}
        filled = fill_missing_durations(cand, artist="Artist")
        assert filled == 0
        assert "dur_s" not in cand["tracks"][0]

    def test_recording_without_length_not_filled(self, monkeypatch):
        def fake_mb_json(url):
            return {"recordings": [{"title": "Song A"}]}  # kein "length"

        monkeypatch.setattr(fetch_metadata, "_get_mb_json", fake_mb_json)
        cand = {"tracks": [{"title": "Song A"}]}
        filled = fill_missing_durations(cand, artist="Artist")
        assert filled == 0

    def test_already_present_duration_is_not_queried(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            fetch_metadata, "_get_mb_json", lambda url: calls.append(url) or None
        )
        cand = {"tracks": [{"title": "Song A", "dur_s": 200.0}]}
        filled = fill_missing_durations(cand, artist="Artist")
        assert filled == 0
        assert calls == []

    def test_uses_median_not_first_result_against_wrong_variant(self, monkeypatch):
        # Realer Fall: "Bohemian Rhapsody" lieferte als erstes einen 157s-Edit,
        # obwohl die echte Studio-Länge ~355s beträgt und mehrfach vorkommt.
        # Der erste Treffer allein wäre falsch -- der Median muss ihn ausgleichen.
        def fake_mb_json(url):
            return {
                "recordings": [
                    {"title": "Song A", "length": 157000},  # Ausreißer (z.B. Radio-Edit)
                    {"title": "Song A", "length": 355000},
                    {"title": "Song A", "length": 355000},
                    {"title": "Song A", "length": 356000},
                ]
            }

        monkeypatch.setattr(fetch_metadata, "_get_mb_json", fake_mb_json)
        cand = {"tracks": [{"title": "Song A"}]}
        filled = fill_missing_durations(cand, artist="Artist")
        assert filled == 1
        assert cand["tracks"][0]["dur_s"] == pytest.approx(355.0)  # Median, nicht 157

    def test_quotes_in_title_are_escaped_in_query(self, monkeypatch):
        captured_urls = []

        def fake_mb_json(url):
            captured_urls.append(url)
            return {"recordings": [{"title": 'She Said "Yes"', "length": 100000}]}

        monkeypatch.setattr(fetch_metadata, "_get_mb_json", fake_mb_json)
        cand = {"tracks": [{"title": 'She Said "Yes"'}]}
        filled = fill_missing_durations(cand, artist="Artist")
        assert filled == 1
        assert '\\"' in captured_urls[0] or "%5C%22" in captured_urls[0]

    def test_partial_success_across_multiple_tracks(self, monkeypatch):
        def fake_mb_json(url):
            if "Song%20A" in url or "Song A" in url:
                return {"recordings": [{"title": "Song A", "length": 100000}]}
            return None

        monkeypatch.setattr(fetch_metadata, "_get_mb_json", fake_mb_json)
        cand = {"tracks": [{"title": "Song A"}, {"title": "Song B"}]}
        filled = fill_missing_durations(cand, artist="Artist")
        assert filled == 1
        assert cand["tracks"][0]["dur_s"] == pytest.approx(100.0)
        assert "dur_s" not in cand["tracks"][1]
