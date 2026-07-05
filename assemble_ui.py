#!/usr/bin/env python3
"""Terminal-UI für assemble.py: Panel-Builder (Schicht 2).

Keine API-Calls, kein Dateisystem, kein subprocess — nur Rich.
live_input() und fmt_dur werden aus cut_ui importiert.
"""

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.rule import Rule
from rich import box

from cut_ui import fmt_dur


def build_analysis_panel(stem: str, status_lines: list) -> Panel:
    """Phase 0: Analyse-Fortschritt und Seitenanzahl-Eingabe."""
    body = Text()
    for line in status_lines:
        body.append(line + "\n", style="dim")
    return Panel(
        body,
        title=f"[bold]{stem}[/bold]",
        subtitle="[dim]Analyse[/dim]",
        expand=True,
        border_style="blue dim",
    )


def build_points_panel(
    stem: str,
    steps: list,
    history: list,
    current_i: int,
    current_pos: float,
    normton: bool,
) -> Panel:
    """Phase 1: Tabelle aller Punkte mit Status, Position und Delta."""
    n = len(steps)

    table = Table(
        box=box.SIMPLE, show_header=True, expand=True, padding=(0, 1), show_edge=False
    )
    table.add_column("#", width=3, justify="right")
    table.add_column("Beschreibung", no_wrap=True, overflow="ellipsis", ratio=1)
    table.add_column("Position", width=9, justify="right")
    table.add_column("Vorschlag", width=9, justify="right")
    table.add_column("Δ", width=8, justify="right")
    table.add_column("", width=2, justify="center")

    for i, step in enumerate(steps):
        suggested = step["suggested"]
        if i < current_i:
            pos = history[i]["pos"]
            delta = pos - suggested
            delta_str = f"{delta:+.2f}s" if abs(delta) > 0.01 else ""
            pos_text = Text(fmt_dur(pos))
            delta_text = Text(delta_str, style="dim")
            status_sym = Text("✓", style="green")
            row_style = "dim"
        elif i == current_i:
            pos = current_pos
            delta = pos - suggested
            delta_str = f"{delta:+.2f}s"
            delta_style = (
                "green"
                if abs(delta) <= 1.0
                else ("yellow" if abs(delta) <= 5.0 else "red")
            )
            pos_text = Text(fmt_dur(pos), style="bold")
            delta_text = Text(delta_str, style=delta_style)
            status_sym = Text("→", style="bold cyan")
            row_style = "bold"
        else:
            pos_text = Text("~" + fmt_dur(suggested))
            delta_text = Text("")
            status_sym = Text("○", style="dim yellow")
            row_style = "dim"

        table.add_row(
            f"{i + 1:02d}",
            step["desc"],
            pos_text,
            Text(fmt_dur(suggested), style="dim"),
            delta_text,
            status_sym,
            style=row_style,
        )

    step = steps[current_i]
    delta = current_pos - step["suggested"]
    delta_style = (
        "green" if abs(delta) <= 1.0 else ("yellow" if abs(delta) <= 5.0 else "red")
    )
    info = Text()
    info.append(
        f"Schritt {current_i + 1:02d}/{n:02d} · {step['desc']}\n", style="bold cyan"
    )
    info.append(
        f"Position: {fmt_dur(current_pos)}   Vorschlag: {fmt_dur(step['suggested'])}   "
    )
    info.append(f"Δ {delta:+.2f}s\n", style=delta_style)
    info.append("Normton: ", style="dim")
    info.append(
        "EIN\n\n" if normton else "aus\n\n", style="green" if normton else "dim"
    )
    info.append(
        "[p] abspielen  [+/-] ±0.5s  [++/--] ±2s  [ok] bestätigen  "
        "[u] rückgängig  [n] Normton  Offset: ±m:ss",
        style="dim",
    )

    return Panel(
        Group(table, Rule(style="dim"), info),
        title=f"[bold]{stem}[/bold]",
        subtitle=f"[dim]Phase 1 · Punkte setzen · {current_i}/{n} bestätigt[/dim]",
        expand=True,
        border_style="blue dim",
    )


def build_crossfade_panel(
    stem: str,
    boundaries: list,
    cf_done_count: int,
    current_j: int,
    active: str,
    normton: bool,
) -> Panel:
    """Phase 2: Crossfade-Vorschau.

    boundaries: [{"left": "A", "right": "B", "a_pos": float, "b_pos": float}]
    cf_done_count: Anzahl bereits bestätigter Grenzen (von vorne).
    """
    n = len(boundaries)

    table = Table(
        box=box.SIMPLE, show_header=True, expand=True, padding=(0, 1), show_edge=False
    )
    table.add_column("Grenze", width=6, justify="center")
    table.add_column("Ende Seite", width=9, justify="right")
    table.add_column("Anfang Seite", width=12, justify="right")
    table.add_column("Lücke", width=9, justify="right")
    table.add_column("", width=2, justify="center")

    for j, bd in enumerate(boundaries):
        gap = bd["b_pos"] - bd["a_pos"]
        gap_style = (
            "dim"
            if j != current_j
            else ("green" if gap < 120 else ("yellow" if gap < 300 else "red"))
        )
        label = f"{bd['left']}→{bd['right']}"

        if j < cf_done_count:
            row_style = "dim"
            status_sym = Text("✓", style="green")
        elif j == current_j:
            row_style = "bold"
            status_sym = Text("→", style="bold cyan")
        else:
            row_style = "dim"
            status_sym = Text("○", style="dim yellow")

        table.add_row(
            label,
            fmt_dur(bd["a_pos"]),
            fmt_dur(bd["b_pos"]),
            Text(fmt_dur(gap), style=gap_style),
            status_sym,
            style=row_style,
        )

    bd = boundaries[current_j]
    gap = bd["b_pos"] - bd["a_pos"]
    a_active = active == "a"
    b_active = active == "b"
    info = Text()
    info.append(
        f"Grenze {bd['left']}→{bd['right']}  ({current_j + 1}/{n})\n", style="bold cyan"
    )
    info.append(f"Ende Seite {bd['left']}:     ", style="dim")
    info.append(fmt_dur(bd["a_pos"]), style="bold" if a_active else "")
    info.append(
        "  ← aktiv\n" if a_active else "\n", style="cyan" if a_active else "dim"
    )
    info.append(f"Anfang Seite {bd['right']}: ", style="dim")
    info.append(fmt_dur(bd["b_pos"]), style="bold" if b_active else "")
    info.append(
        "  ← aktiv\n" if b_active else "\n", style="cyan" if b_active else "dim"
    )
    info.append(f"Herausgeschnitten: {fmt_dur(gap)}   Normton: ", style="dim")
    info.append(
        "EIN\n\n" if normton else "aus\n\n", style="green" if normton else "dim"
    )
    info.append(
        "[a] Fokus Ende  [b] Fokus Anfang  [+/-] ±0.5s  [++/--] ±2s  "
        "[ok] bestätigen  [u] rückgängig  [n] Normton  Offset: ±m:ss",
        style="dim",
    )

    return Panel(
        Group(table, Rule(style="dim"), info),
        title=f"[bold]{stem}[/bold]",
        subtitle=f"[dim]Phase 2 · Crossfade · {cf_done_count}/{n} bestätigt[/dim]",
        expand=True,
        border_style="blue dim",
    )


def build_export_panel(
    stem: str,
    segments: list,
    export_status: list,
    joining: bool = False,
    crossfade_sec: float = 0.5,
) -> Panel:
    """Phase 3: Segment-Export-Fortschritt.

    segments: [(start, end, label)] z.B. (0.0, 1125.0, "Seite A")
    export_status: ["✓", "…", ""] pro Segment
    """
    n = len(segments)
    done = sum(1 for s in export_status if s == "✓")

    table = Table(
        box=box.SIMPLE, show_header=True, expand=True, padding=(0, 1), show_edge=False
    )
    table.add_column("#", width=3, justify="right")
    table.add_column("Segment", width=8)
    table.add_column("Start", width=9, justify="right")
    table.add_column("Ende", width=9, justify="right")
    table.add_column("Dauer", width=9, justify="right")
    table.add_column("", width=3, justify="center")

    for i, (start, end, label) in enumerate(segments):
        dur = end - start
        status = export_status[i] if i < len(export_status) else ""
        if status == "✓":
            sym = Text("✓", style="green")
            row_style = "dim"
        elif status == "…":
            sym = Text("…", style="bold cyan")
            row_style = "bold"
        else:
            sym = Text("○", style="dim yellow")
            row_style = "dim"
        table.add_row(
            f"{i + 1:02d}",
            label,
            fmt_dur(start),
            fmt_dur(end),
            fmt_dur(dur),
            sym,
            style=row_style,
        )

    info = Text()
    if joining:
        info.append(
            f"Verbinde {n} Segmente mit {crossfade_sec:.1f}s Crossfade...", style="bold"
        )
    elif done == n:
        info.append(f"✓ Alle {n} Segmente geschnitten.", style="green")
    else:
        info.append(f"Schneide Segment {done + 1}/{n}...", style="bold")

    return Panel(
        Group(table, Rule(style="dim"), info),
        title=f"[bold]{stem}[/bold]",
        subtitle=f"[dim]Phase 3 · Schneiden & Verbinden · {done}/{n}[/dim]",
        expand=True,
        border_style="blue dim",
    )


def build_normalize_panel(
    stem: str, left_db: float, right_db: float, status_lines: list
) -> Panel:
    """Phase 4: Normalisierung — Kanalpeaks + Statusmeldungen."""
    diff = right_db - left_db
    diff_style = (
        "green" if abs(diff) < 0.1 else ("yellow" if abs(diff) < 1.0 else "red")
    )

    peaks = Text()
    peaks.append(f"Links:     {left_db:+.2f} dBFS\n")
    peaks.append(f"Rechts:    {right_db:+.2f} dBFS\n")
    peaks.append("Differenz: ", style="dim")
    peaks.append(f"{diff:+.2f} dB\n", style=diff_style)

    status_text = Text()
    for line in status_lines:
        status_text.append(line + "\n", style="dim")

    return Panel(
        Group(peaks, Rule(style="dim"), status_text),
        title=f"[bold]{stem}[/bold]",
        subtitle="[dim]Phase 4 · Normalisierung[/dim]",
        expand=True,
        border_style="blue dim",
    )
