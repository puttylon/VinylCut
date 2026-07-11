"""Tests für cut_ui.py — laufen ohne echtes Terminal (Console(force_terminal=False))."""

from rich.console import Console

from cut_ui import build_cutting_panel, build_metadata_panel, fmt_dur


def render(renderable) -> str:
    console = Console(force_terminal=False, width=120)
    with console.capture() as cap:
        console.print(renderable)
    return cap.get()


TRACKS = [
    {"title": "Intro", "dur_s": 90.0},
    {"title": "Main Theme", "dur_s": 210.5},
    {"title": "Outro", "dur_s": 60.0},
]


class TestFmtDur:
    def test_zero(self):
        assert fmt_dur(0.0) == "0:00.00"

    def test_one_minute(self):
        assert fmt_dur(60.0) == "1:00.00"

    def test_fractional(self):
        assert fmt_dur(90.5) == "1:30.50"

    def test_negative(self):
        assert fmt_dur(-30.0) == "-0:30.00"

    def test_leading_zero_seconds(self):
        assert fmt_dur(65.0) == "1:05.00"


class TestBuildCuttingPanel:
    def test_renders_without_error(self):
        panel = build_cutting_panel(
            "Gary Numan", "Warriors", TRACKS, [], 0, 0.0, True, 0.0
        )
        out = render(panel)
        assert "Gary Numan" in out
        assert "Warriors" in out

    def test_track_titles_present(self):
        panel = build_cutting_panel(
            "Gary Numan", "Warriors", TRACKS, [], 0, 0.0, True, 0.0
        )
        out = render(panel)
        assert "Intro" in out
        assert "Main Theme" in out

    def test_cutting_phase_shows_current_track(self):
        panel = build_cutting_panel(
            "Gary Numan", "Warriors", TRACKS, [5.0], 1, 100.0, True, 0.5, est=98.0
        )
        out = render(panel)
        assert "Main Theme" in out

    def test_export_phase_no_exception(self):
        panel = build_cutting_panel(
            "A",
            "B",
            TRACKS,
            [0.0, 95.0, 310.0],
            0,
            0.0,
            False,
            0.0,
            phase="export",
            export_status=["✓", "✓", ""],
        )
        out = render(panel)
        assert "Exportiere" in out

    def test_songtext_phase_no_exception(self):
        panel = build_cutting_panel(
            "A",
            "B",
            TRACKS,
            [0.0, 95.0, 310.0],
            0,
            0.0,
            False,
            0.0,
            phase="songtext",
            export_status=["✓", "✓", "✓"],
            lrc_status=["✓", "✗", ""],
        )
        out = render(panel)
        assert "Songtext" in out

    def test_track_count_in_subtitle(self):
        panel = build_cutting_panel("A", "B", TRACKS, [], 0, 0.0, True, 0.0)
        out = render(panel)
        assert "3 Tracks" in out

    def test_normton_ein_shown(self):
        panel = build_cutting_panel(
            "A", "B", TRACKS, [], 0, 0.0, normton=True, last_gap=0.0
        )
        out = render(panel)
        assert "EIN" in out

    def test_normton_aus_shown(self):
        panel = build_cutting_panel(
            "A", "B", TRACKS, [], 0, 0.0, normton=False, last_gap=0.0
        )
        out = render(panel)
        assert "aus" in out

    def test_tracks_without_duration(self):
        tracks = [{"title": "Unknown"}, {"title": "Also Unknown"}]
        panel = build_cutting_panel("A", "B", tracks, [], 0, 0.0, True, 0.0)
        out = render(panel)
        assert "Unknown" in out

    def test_default_preview_duration_shown(self):
        panel = build_cutting_panel("A", "B", TRACKS, [], 0, 0.0, True, 0.0)
        out = render(panel)
        assert "[p] 3s abspielen" in out

    def test_custom_preview_duration_shown(self):
        panel = build_cutting_panel(
            "A", "B", TRACKS, [], 0, 0.0, True, 0.0, preview_duration=18.0
        )
        out = render(panel)
        assert "[p] 18s abspielen" in out


TRACKS_NO_DUR = [{"title": "A"}, {"title": "B"}, {"title": "C"}]


class TestLastTrackLength:
    """Letzter Track ohne dur_s/nächsten Startpunkt: Länge via total_flac_dur."""

    def test_without_total_flac_dur_shows_unknown(self):
        panel = build_cutting_panel(
            "A", "B", TRACKS_NO_DUR, [0.0, 100.0, 250.0], 2, 250.0, True, 0.0,
            phase="export", export_status=["✓", "✓", "✓"],
        )
        out = render(panel)
        assert "?:??" in out

    def test_with_total_flac_dur_shows_real_length(self):
        panel = build_cutting_panel(
            "A", "B", TRACKS_NO_DUR, [0.0, 100.0, 250.0], 2, 250.0, True, 0.0,
            phase="export", export_status=["✓", "✓", "✓"],
            total_flac_dur=300.0,
        )
        out = render(panel)
        assert "0:50.00" in out  # 300.0 - 250.0
        assert "?:??" not in out

    def test_total_footer_uses_flac_dur_when_metadata_missing(self):
        panel = build_cutting_panel(
            "A", "B", TRACKS_NO_DUR, [0.0, 100.0, 250.0], 2, 250.0, True, 0.0,
            phase="export", export_status=["✓", "✓", "✓"],
            total_flac_dur=300.0,
        )
        out = render(panel)
        assert "5:00.00" in out  # Gesamtdauer im Footer

    def test_does_not_affect_tracks_with_known_next_start(self):
        # Mittlerer Track (B) hat schon einen echten nächsten Startpunkt —
        # total_flac_dur darf dessen Länge nicht verändern.
        panel = build_cutting_panel(
            "A", "B", TRACKS_NO_DUR, [0.0, 100.0, 250.0], 2, 250.0, True, 0.0,
            phase="export", export_status=["✓", "✓", "✓"],
            total_flac_dur=300.0,
        )
        out = render(panel)
        assert "2:30.00" in out  # B: 250.0 - 100.0


class TestBuildMetadataPanel:
    def test_renders_without_error(self):
        panel = build_metadata_panel("Joy Division", "Unknown Pleasures", ["Suche..."])
        out = render(panel)
        assert "Joy Division" in out
        assert "Unknown Pleasures" in out

    def test_status_lines_shown(self):
        panel = build_metadata_panel("A", "B", ["Zeile 1", "Zeile 2"])
        out = render(panel)
        assert "Zeile 1" in out

    def test_error_shown(self):
        panel = build_metadata_panel("A", "B", [], error="Nix gefunden")
        out = render(panel)
        assert "Nix gefunden" in out

    def test_candidate_title_shown(self):
        cand = {
            "id": "12345",
            "title": "Unknown Pleasures",
            "format": "Vinyl",
            "tracks": [{"title": "Disorder", "dur_s": 210.0}],
        }
        panel = build_metadata_panel(
            "Joy Division", "Unknown Pleasures", [], candidate=cand
        )
        out = render(panel)
        assert "Unknown Pleasures" in out
        assert "Disorder" in out

    def test_candidate_without_duration(self):
        cand = {
            "id": "99",
            "title": "Album",
            "format": "CD",
            "tracks": [{"title": "Track 1"}],
        }
        panel = build_metadata_panel("Artist", "Album", [], candidate=cand)
        out = render(panel)
        assert "Track 1" in out

    def test_mb_id_uses_musicbrainz_url(self):
        cand = {
            "id": "mb:550e8400-e29b-41d4-a716-446655440000",
            "title": "Album",
            "format": "Vinyl",
            "tracks": [{"title": "T1", "dur_s": 100.0}],
        }
        panel = build_metadata_panel("A", "B", [], candidate=cand)
        out = render(panel)
        assert "musicbrainz" in out.lower()

    def test_only_last_8_status_lines_shown(self):
        lines = [f"Zeile {i}" for i in range(15)]
        panel = build_metadata_panel("A", "B", lines)
        out = render(panel)
        assert "Zeile 14" in out
        assert "Zeile 0" not in out
