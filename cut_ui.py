#!/usr/bin/env python3
"""Terminal-UI für cut.py: Panel-Builder (Schicht 2) und zeichenweise Eingabe.

Keine API-Calls, kein Dateisystem, kein subprocess — nur Rich + tty.
"""

import sys
import termios
import tty

from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.rule import Rule
from rich import box

# --- Zentrales Farbschema (an einer Stelle definiert statt an jeder Panel-
# Funktion einzeln wiederholt, damit z.B. der Hintergrund-Bugfix nicht durch
# vergessene Einzelstellen wieder inkonsistent wird) ---
MUTED = "grey35"
BORDER = "blue dim"
STATUS_ICON = {"done": "✓", "current": "→", "pending": "○"}
STATUS_STYLE = {"done": "green", "current": "bold cyan", "pending": "dim yellow"}
ROW_STYLE = {"done": MUTED, "current": "bold", "pending": MUTED}


def status_symbol(state: str) -> Text:
    """state: 'done' | 'current' | 'pending' -- liefert Symbol + Farbe."""
    return Text(STATUS_ICON[state], style=STATUS_STYLE[state])


def row_style(state: str) -> str:
    return ROW_STYLE[state]


def severity_style(value: float, warn: float, bad: float) -> str:
    """green/yellow/red je nach abs(value) gegen die Schwellen warn/bad."""
    magnitude = abs(value)
    if magnitude <= warn:
        return "green"
    if magnitude <= bad:
        return "yellow"
    return "red"


def normton_text(normton: bool) -> tuple:
    """Label + Style für den Normton-EIN/aus-Status."""
    return ("EIN\n\n", "green") if normton else ("aus\n\n", MUTED)


def fmt_dur(seconds: float) -> str:
    sign = "-" if seconds < 0 else ""
    total = abs(seconds)
    m = int(total) // 60
    s = total - m * 60
    return f"{sign}{m}:{s:05.2f}"


def build_metadata_panel(
    artist: str,
    album: str,
    status_lines: list,
    candidate: dict = None,
    error: str = None,
) -> Panel:
    parts = []

    status_text = Text()
    for line in status_lines[-8:]:
        status_text.append(line + "\n", style=MUTED)
    parts.append(status_text)

    if error:
        parts.append(Text(f"\n✗ {error}", style="bold red"))
    elif candidate:
        parts.append(Rule(style=MUTED))

        cid = candidate["id"]
        source = (
            f"https://musicbrainz.org/release/{cid[3:]}"
            if cid.startswith("mb:")
            else f"https://www.discogs.com/release/{cid}"
        )
        info = Text()
        info.append(f"{candidate['title']}\n", style="bold")
        info.append(f"Format: {candidate['format']}   Quelle: {source}\n", style=MUTED)
        parts.append(info)

        table = Table(
            box=box.SIMPLE,
            show_header=False,
            expand=True,
            padding=(0, 1),
            show_edge=False,
        )
        table.add_column("#", width=3, justify="right", style=MUTED)
        table.add_column("Titel", no_wrap=True, overflow="ellipsis", ratio=1)
        table.add_column("Länge", width=8, justify="right", style=MUTED)
        for idx, t in enumerate(candidate["tracks"], 1):
            dur = fmt_dur(t["dur_s"]) if t.get("dur_s") else "?:??"
            table.add_row(f"{idx:02d}", t["title"], dur)
        parts.append(table)

    return Panel(
        Group(*parts),
        title=f"[bold]{artist} · {album}[/bold]",
        subtitle=f"[{MUTED}]Metadatensuche[/{MUTED}]",
        expand=True,
        border_style=BORDER,
    )


def build_cutting_panel(
    artist: str,
    album: str,
    tracks: list,
    confirmed_starts: list,
    current_i: int,
    current_pos: float,
    normton: bool,
    last_gap: float,
    est: float = 0.0,
    phase: str = "cutting",
    export_status: list = None,
    lrc_status: list = None,
    preview_duration: float = 3.0,
    total_flac_dur: float = 0.0,
) -> Panel:
    """Baut das Haupt-Panel für Schneiden, Export und Songtext-Suche.

    est: vorberechneter Schätzwert für current_i (aus estimate_start in cut.py).
         Wird nur im phase='cutting'-Infoteil verwendet.
    total_flac_dur: tatsächliche Gesamtdauer der Quelldatei (ffprobe). Dient als
         virtueller Endpunkt, damit der letzte Track auch ohne dur_s/nächsten
         Startpunkt eine Länge anzeigen kann (zur Absicherung).
    """
    n = len(tracks)
    total_dur = sum(t.get("dur_s", 0.0) for t in tracks) or total_flac_dur

    if phase == "cutting":
        display_starts = list(confirmed_starts) + [current_pos]
        prev = current_pos
        for i in range(current_i + 1, n):
            dur = tracks[i - 1].get("dur_s")
            prev = prev + dur + last_gap if dur is not None else prev
            display_starts.append(prev)
    else:
        display_starts = list(confirmed_starts)

    if total_flac_dur and len(display_starts) == n:
        display_starts.append(total_flac_dur)

    show_export = export_status is not None
    show_lrc = lrc_status is not None

    table = Table(
        box=box.SIMPLE, show_header=True, expand=True, padding=(0, 1), show_edge=False
    )
    table.add_column("#", width=3, justify="right")
    table.add_column("Titel", no_wrap=True, overflow="ellipsis", ratio=1)
    table.add_column("Länge", width=8, justify="right")
    table.add_column("Start", width=10, justify="right")
    table.add_column("", width=2, justify="center")
    if show_export:
        table.add_column("Export", width=7, justify="center")
    if show_lrc:
        table.add_column("LRC", width=5, justify="center")

    for i, track in enumerate(tracks):
        if i + 1 < len(display_starts):
            dur_str = fmt_dur(display_starts[i + 1] - display_starts[i])
        elif "dur_s" in track:
            dur_str = fmt_dur(track["dur_s"])
        else:
            dur_str = "?:??"
        start_val = display_starts[i] if i < len(display_starts) else 0.0

        if phase != "cutting" or i < current_i:
            state = "done"
            start_text = Text(fmt_dur(start_val))
        elif i == current_i:
            state = "current"
            start_text = Text(fmt_dur(start_val), style="bold")
        else:
            state = "pending"
            start_text = Text("~" + fmt_dur(start_val))
        status_sym = status_symbol(state)

        row = [f"{i + 1:02d}", track["title"], dur_str, start_text, status_sym]
        if show_export:
            exp = export_status[i] if i < len(export_status) else ""
            row.append(Text(exp, style="green" if exp == "✓" else MUTED))
        if show_lrc:
            lrc = lrc_status[i] if i < len(lrc_status) else ""
            row.append(
                Text(
                    lrc,
                    style="green" if lrc == "✓" else ("red" if lrc == "✗" else MUTED),
                )
            )
        table.add_row(*row, style=row_style(state))

    if phase == "cutting":
        delta = current_pos - est
        info = Text()
        info.append(
            f"Track {current_i + 1:02d} · {tracks[current_i]['title']}\n",
            style="bold cyan",
        )
        info.append(f"Position: {fmt_dur(current_pos)}   Schätzung: {fmt_dur(est)}   ")
        info.append(f"Δ {delta:+.2f}s\n", style=severity_style(delta, 1.0, 5.0))
        info.append("Normton: ", style=MUTED)
        normton_label, normton_style = normton_text(normton)
        info.append(normton_label, style=normton_style)
        info.append(
            f"[p] {preview_duration:g}s abspielen  [p<Sek>] Dauer ändern (2-30s)  "
            "[+/-] ±0.5s  [++/--] ±2s  [ok] bestätigen  "
            "[u] rückgängig  [n] Normton  Offset: ±m:ss",
            style=MUTED,
        )
    elif phase == "export":
        done = sum(1 for s in (export_status or []) if s == "✓")
        info = Text()
        info.append(f"Exportiere Tracks: {done}/{n}\n", style="bold")
        info.append(
            "✓ Abgeschlossen." if done == n else "Bitte warten...",
            style="green" if done == n else MUTED,
        )
    elif phase == "songtext":
        found = sum(1 for s in (lrc_status or []) if s == "✓")
        missing = sum(1 for s in (lrc_status or []) if s == "✗")
        checked = found + missing
        info = Text()
        info.append(f"Suche Songtexte: {checked}/{n}\n", style="bold")
        if checked == 0:
            info.append("Bitte warten...", style=MUTED)
        else:
            info.append(
                f"✓ {found} gefunden, {missing} nicht gefunden.",
                style="green" if missing == 0 else "yellow",
            )
    else:
        info = Text("✓ Fertig.", style="bold green")

    total_str = fmt_dur(total_dur) if total_dur else "?:??"
    return Panel(
        Group(table, Rule(style=MUTED), info),
        title=f"[bold]{artist} · {album}[/bold]",
        subtitle=f"[{MUTED}]{n} Tracks · {total_str}[/{MUTED}]",
        expand=True,
        border_style=BORDER,
    )


def live_input(live: Live, renderable, prompt: str = "", initial: str = "") -> str:
    """Zeichenweise lesen; Prompt + aktuelle Eingabe erscheinen im Panel.

    `initial` füllt die Eingabe vorab (z.B. Namensvorschlag), Cursor steht ans
    Ende — mit Backspace kürzbar, weitertippen hängt an statt alles neu zu tippen.

    Kein Cursor ausserhalb des Panels: tty.setcbreak + Group(renderable, Rule,
    input_line). setcbreak lässt OPOST aktiv → Rich rendert korrekt.
    """
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    chars: list[str] = list(initial)

    def _render():
        inp = Text()
        inp.append(f"  {prompt}", style=MUTED)
        inp.append("".join(chars), style="bold")
        inp.append("▌", style=MUTED)
        return Group(renderable, Rule(style=MUTED), inp, Text(""))

    try:
        tty.setcbreak(fd)
        while True:
            live.update(_render())
            live.refresh()
            ch = sys.stdin.buffer.read(1)
            if ch in (b"\r", b"\n"):
                break
            if ch == b"\x03":
                raise KeyboardInterrupt
            if ch in (b"\x7f", b"\x08"):
                if chars:
                    chars.pop()
            elif ch == b"\x1b":
                sys.stdin.buffer.read(2)  # Escape-Sequenz (Pfeiltasten etc.) verwerfen
            elif ch and 32 <= ch[0] < 128:
                chars.append(ch.decode("ascii"))
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    return "".join(chars)
