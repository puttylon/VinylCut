import pytest

import cache_store as cs
import evaluate_lyrics
import lyrics_core
from cut import _fetch_lyrics_for_track, compute_last_gap, estimate_start
from cut_ui import fmt_dur


class _QueryProviderNoopMixin:
    """_fetch_lyrics_for_track fragt vor evaluate_song erst live alle 4
    Provider ab (ThreadPoolExecutor) -- fuer Tests der Loesch-/Behalten-Logik
    danach uninteressant, hier neutralisiert (kein Netzwerk, kein Treffer)."""

    @pytest.fixture(autouse=True)
    def _noop_query_provider(self, monkeypatch):
        monkeypatch.setattr(
            lyrics_core,
            "_query_provider",
            lambda query, provider, env, artist="", title="": (provider, None),
        )


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
        tracks = [
            {"title": "A", "dur_s": 100.0},
            {"title": "B", "dur_s": 200.0},
            {"title": "C"},
        ]
        assert estimate_start(2, tracks, [0.0, 102.0], 2.0) == pytest.approx(304.0)


class TestComputeLastGap:
    def test_no_deviation_is_zero_gap(self):
        assert compute_last_gap(
            current_start=100.0, prev_start=0.0, prev_dur_s=100.0
        ) == pytest.approx(0.0)

    def test_small_positive_deviation_is_real_gap(self):
        # 2s Pause zwischen Tracks — plausibel, wird übernommen
        assert compute_last_gap(
            current_start=102.0, prev_start=0.0, prev_dur_s=100.0
        ) == pytest.approx(2.0)

    def test_small_negative_deviation_is_real_gap(self):
        assert compute_last_gap(
            current_start=99.0, prev_start=0.0, prev_dur_s=100.0
        ) == pytest.approx(-1.0)

    def test_just_under_threshold_is_kept(self):
        assert compute_last_gap(
            current_start=109.9, prev_start=0.0, prev_dur_s=100.0
        ) == pytest.approx(9.9)

    def test_at_threshold_is_discarded(self):
        # |deviation| == _MAX_PLAUSIBLE_GAP (10.0) fällt raus (strikt <, nicht <=)
        assert compute_last_gap(
            current_start=110.0, prev_start=0.0, prev_dur_s=100.0
        ) == pytest.approx(0.0)

    def test_large_positive_deviation_discarded_as_wrong_metadata(self):
        # Realer Fall: Discogs-Länge um 71s falsch — keine Pause, wird verworfen
        assert compute_last_gap(
            current_start=171.15, prev_start=0.0, prev_dur_s=100.0
        ) == pytest.approx(0.0)

    def test_large_negative_deviation_discarded_too(self):
        assert compute_last_gap(
            current_start=50.0, prev_start=0.0, prev_dur_s=100.0
        ) == pytest.approx(0.0)


class TestFetchLyricsForTrackExistingBest(_QueryProviderNoopMixin):
    """Bugfix (siehe ROADMAP.md, evaluate_lyrics.py existing_best): cut.py
    hatte dieselbe Lücke wie write_lrc.py -- eine bereits vorhandene .lrc
    wurde bei found=False bedingungslos gelöscht, auch wenn sie selbst der
    beste Kandidat am Audio war."""

    def test_existing_best_wird_nicht_geloescht(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(
            evaluate_lyrics,
            "evaluate_song",
            lambda *a, **kw: (
                False,
                "1/4: lrclib │ unter Schwelle",
                {"reason": "unter-schwelle", "existing_best": True, "content": None},
            ),
        )
        flac_path = tmp_path / "song.flac"
        flac_path.write_bytes(b"")
        lrc_path = tmp_path / "song.lrc"
        lrc_path.write_text("[00:01.00]Bereits korrekter Text\n", encoding="utf-8")

        found, _info, _extras = _fetch_lyrics_for_track(
            conn, "artist title", lrc_path, {}, 0.0, flac_path, "artist", "title"
        )

        assert found is False
        assert lrc_path.exists()
        assert (
            lrc_path.read_text(encoding="utf-8") == "[00:01.00]Bereits korrekter Text\n"
        )

    def test_ohne_existing_best_wird_geloescht(self, tmp_path, monkeypatch):
        conn = cs.open_cache(tmp_path / "cache.db")
        monkeypatch.setattr(
            evaluate_lyrics,
            "evaluate_song",
            lambda *a, **kw: (
                False,
                "0/4: — │ kein Provider",
                {"reason": "kein-provider", "content": None},
            ),
        )
        flac_path = tmp_path / "song.flac"
        flac_path.write_bytes(b"")
        lrc_path = tmp_path / "song.lrc"
        lrc_path.write_text("[00:01.00]Alter Text\n", encoding="utf-8")

        found, _info, _extras = _fetch_lyrics_for_track(
            conn, "artist title", lrc_path, {}, 0.0, flac_path, "artist", "title"
        )

        assert found is False
        assert not lrc_path.exists()
