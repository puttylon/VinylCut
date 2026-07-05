"""Tests für assemble_ui.py — laufen ohne echtes Terminal (Console(force_terminal=False))."""

from rich.console import Console

from assemble_ui import (
    build_analysis_panel,
    build_crossfade_panel,
    build_export_panel,
    build_normalize_panel,
    build_points_panel,
)


def render(renderable) -> str:
    console = Console(force_terminal=False, width=120)
    with console.capture() as cap:
        console.print(renderable)
    return cap.get()


STEPS = [
    {"label": "trim_start", "desc": "Anfang Seite A", "suggested": 3.0},
    {"label": "boundary_0_a", "desc": "Ende Seite A", "suggested": 1125.0},
    {"label": "boundary_0_b", "desc": "Anfang Seite B", "suggested": 1202.0},
    {"label": "trim_end", "desc": "Ende Seite B", "suggested": 2250.0},
]

BOUNDARIES = [
    {"left": "A", "right": "B", "a_pos": 1125.0, "b_pos": 1202.0},
]

SEGMENTS = [
    (3.0, 1125.0, "Seite A"),
    (1202.0, 2250.0, "Seite B"),
]


class TestBuildAnalysisPanel:
    def test_renders_without_error(self):
        panel = build_analysis_panel("Album-raw", ["Analysiere..."])
        out = render(panel)
        assert "Album-raw" in out

    def test_status_lines_shown(self):
        panel = build_analysis_panel("Stem", ["Zeile 1", "Zeile 2"])
        out = render(panel)
        assert "Zeile 1" in out
        assert "Zeile 2" in out

    def test_empty_status(self):
        panel = build_analysis_panel("Stem", [])
        out = render(panel)
        assert "Stem" in out

    def test_analyse_subtitle(self):
        panel = build_analysis_panel("Stem", [])
        out = render(panel)
        assert "Analyse" in out


class TestBuildPointsPanel:
    def test_renders_without_error(self):
        panel = build_points_panel("Album-raw", STEPS, [], 0, 3.0, True)
        out = render(panel)
        assert "Album-raw" in out

    def test_step_descriptions_shown(self):
        panel = build_points_panel("Album-raw", STEPS, [], 0, 3.0, True)
        out = render(panel)
        assert "Anfang Seite A" in out
        assert "Ende Seite B" in out

    def test_current_step_marker(self):
        history = [{"label": "trim_start", "pos": 3.0}]
        panel = build_points_panel("Album-raw", STEPS, history, 1, 1130.0, False)
        out = render(panel)
        assert "Ende Seite A" in out

    def test_normton_ein_shown(self):
        panel = build_points_panel("Album-raw", STEPS, [], 0, 3.0, normton=True)
        out = render(panel)
        assert "EIN" in out

    def test_normton_aus_shown(self):
        panel = build_points_panel("Album-raw", STEPS, [], 0, 3.0, normton=False)
        out = render(panel)
        assert "aus" in out

    def test_history_checkmarks(self):
        history = [{"label": "trim_start", "pos": 3.0}]
        panel = build_points_panel("Album-raw", STEPS, history, 1, 1125.0, True)
        out = render(panel)
        assert "✓" in out

    def test_subtitle_shows_count(self):
        panel = build_points_panel("Album-raw", STEPS, [], 0, 3.0, True)
        out = render(panel)
        assert "Phase 1" in out


class TestBuildCrossfadePanel:
    def test_renders_without_error(self):
        panel = build_crossfade_panel("Album-raw", BOUNDARIES, 0, 0, "a", True)
        out = render(panel)
        assert "Album-raw" in out

    def test_boundary_labels_shown(self):
        panel = build_crossfade_panel("Album-raw", BOUNDARIES, 0, 0, "a", True)
        out = render(panel)
        assert "A" in out
        assert "B" in out

    def test_normton_ein_shown(self):
        panel = build_crossfade_panel("Album-raw", BOUNDARIES, 0, 0, "a", normton=True)
        out = render(panel)
        assert "EIN" in out

    def test_active_a_marked(self):
        panel = build_crossfade_panel("Album-raw", BOUNDARIES, 0, 0, "a", True)
        out = render(panel)
        assert "aktiv" in out

    def test_phase2_subtitle(self):
        panel = build_crossfade_panel("Album-raw", BOUNDARIES, 0, 0, "a", True)
        out = render(panel)
        assert "Phase 2" in out


class TestBuildExportPanel:
    def test_renders_without_error(self):
        panel = build_export_panel("Album-raw", SEGMENTS, ["", ""])
        out = render(panel)
        assert "Album-raw" in out

    def test_segment_labels_shown(self):
        panel = build_export_panel("Album-raw", SEGMENTS, ["✓", "…"])
        out = render(panel)
        assert "Seite A" in out
        assert "Seite B" in out

    def test_joining_message(self):
        panel = build_export_panel("Album-raw", SEGMENTS, ["✓", "✓"], joining=True)
        out = render(panel)
        assert "Verbinde" in out

    def test_done_message(self):
        panel = build_export_panel("Album-raw", SEGMENTS, ["✓", "✓"])
        out = render(panel)
        assert "geschnitten" in out

    def test_phase3_subtitle(self):
        panel = build_export_panel("Album-raw", SEGMENTS, ["", ""])
        out = render(panel)
        assert "Phase 3" in out


class TestBuildNormalizePanel:
    def test_renders_without_error(self):
        panel = build_normalize_panel("Album-raw", -0.42, -0.65, [])
        out = render(panel)
        assert "Album-raw" in out

    def test_channel_peaks_shown(self):
        panel = build_normalize_panel("Album-raw", -0.42, -0.65, [])
        out = render(panel)
        assert "-0.42" in out
        assert "-0.65" in out

    def test_status_lines_shown(self):
        panel = build_normalize_panel("Album-raw", -0.1, -0.1, ["DC-Offset entfernt"])
        out = render(panel)
        assert "DC-Offset entfernt" in out

    def test_phase4_subtitle(self):
        panel = build_normalize_panel("Album-raw", 0.0, 0.0, [])
        out = render(panel)
        assert "Phase 4" in out
