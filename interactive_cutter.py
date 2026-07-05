#!/usr/bin/env python3
import sys
import json
import subprocess
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.rule import Rule
from rich import box

__version__ = "1.7.0"

DEFAULT_PLAY_DURATION_SEC = 3.0

console = Console()


def parse_offset(s: str) -> float:
    s = s.strip()
    sign = 1.0
    if s.startswith('+'):
        s, sign = s[1:], 1.0
    elif s.startswith('-'):
        s, sign = s[1:], -1.0
    if ':' in s:
        m, sec = s.split(':', 1)
        return sign * (int(m) * 60 + float(sec))
    return sign * float(s)


def fmt_dur(seconds: float) -> str:
    sign = "-" if seconds < 0 else ""
    total = abs(seconds)
    m = int(total) // 60
    s = total - m * 60
    return f"{sign}{m}:{s:05.2f}"


def estimate_start(i: int, tracks: list, starts: list, last_gap: float) -> float:
    if i == 0:
        return 0.0
    if "dur_s" in tracks[i - 1]:
        return starts[i - 1] + tracks[i - 1]["dur_s"] + last_gap
    return starts[i - 1]


def save_progress(progress_path: Path, history: list) -> None:
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump({"history": history}, f)


def play_snippet(flac_path: Path, start_time: float, duration: float) -> None:
    subprocess.run(
        ["ffplay", "-nodisp", "-autoexit", "-v", "quiet",
         "-ss", f"{start_time:.3f}", "-t", str(duration), str(flac_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def play_snippet_with_tone(flac_path: Path, start_time: float, duration: float) -> None:
    filter_complex = (
        "[0:a]aformat=channel_layouts=stereo[tone];"
        "[1:a]aformat=channel_layouts=stereo[audio];"
        "[tone][audio]concat=n=2:v=0:a=1[out]"
    )
    cmd = [
        "ffmpeg", "-v", "quiet",
        "-f", "lavfi", "-i", "sine=frequency=220:duration=0.25",
        "-ss", f"{start_time:.3f}", "-t", str(duration), "-i", str(flac_path),
        "-filter_complex", filter_complex,
        "-map", "[out]", "-f", "wav", "pipe:1",
    ]
    ffmpeg = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    subprocess.run(["ffplay", "-nodisp", "-autoexit", "-v", "quiet", "-"],
                   stdin=ffmpeg.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ffmpeg.wait()


def cut_and_tag(flac_path, out_file, track_num, title, artist, album, start_s, length_s, cover_path):
    comment = f"interactive_cutter.py v{__version__}"
    subprocess.run(["sox", str(flac_path), str(out_file), "trim", f"{start_s}s", f"{length_s}s"],
                   capture_output=True)
    subprocess.run(["metaflac", "--remove-all-tags",
                    f"--set-tag=ARTIST={artist}", f"--set-tag=ALBUM={album}",
                    f"--set-tag=TITLE={title}", f"--set-tag=TRACKNUMBER={track_num}",
                    f"--set-tag=COMMENT={comment}", str(out_file)], capture_output=True)
    if cover_path.exists():
        subprocess.run(["metaflac", f"--import-picture-from={cover_path}", str(out_file)],
                       capture_output=True)


def build_panel(artist: str, album: str, tracks: list, confirmed_starts: list,
                current_i: int, current_pos: float, normton: bool, last_gap: float,
                phase: str = "cutting",
                export_status: list = None, lrc_status: list = None) -> Panel:
    n = len(tracks)
    total_dur = sum(t.get("dur_s", 0.0) for t in tracks)

    if phase == "cutting":
        display_starts = list(confirmed_starts) + [current_pos]
        prev = current_pos
        for i in range(current_i + 1, n):
            dur = tracks[i - 1].get("dur_s")
            prev = prev + dur + last_gap if dur is not None else prev
            display_starts.append(prev)
    else:
        display_starts = list(confirmed_starts)

    show_export = export_status is not None
    show_lrc = lrc_status is not None

    table = Table(box=box.SIMPLE, show_header=True, expand=True,
                  padding=(0, 1), show_edge=False)
    table.add_column("#", width=3, justify="right")
    table.add_column("Titel", no_wrap=True, overflow="ellipsis", ratio=1)
    table.add_column("Länge", width=7, justify="right")
    table.add_column("Start", width=10, justify="right")
    table.add_column("", width=2, justify="center")
    if show_export:
        table.add_column("Export", width=7, justify="center")
    if show_lrc:
        table.add_column("LRC", width=5, justify="center")

    for i, track in enumerate(tracks):
        dur_str = fmt_dur(track["dur_s"]) if "dur_s" in track else "?:??"
        start_val = display_starts[i] if i < len(display_starts) else 0.0

        if phase != "cutting" or i < current_i:
            start_text = Text(fmt_dur(start_val))
            status = Text("✓", style="green")
            row_style = "dim"
        elif i == current_i:
            start_text = Text(fmt_dur(start_val), style="bold")
            status = Text("→", style="bold cyan")
            row_style = "bold"
        else:
            start_text = Text("~" + fmt_dur(start_val))
            status = Text("○", style="dim yellow")
            row_style = "dim"

        row = [f"{i+1:02d}", track["title"], dur_str, start_text, status]
        if show_export:
            exp = export_status[i] if i < len(export_status) else ""
            row.append(Text(exp, style="green" if exp == "✓" else "dim"))
        if show_lrc:
            lrc = lrc_status[i] if i < len(lrc_status) else ""
            row.append(Text(lrc, style="green" if lrc == "✓" else ("red" if lrc == "✗" else "dim")))

        table.add_row(*row, style=row_style)

    # Info section
    if phase == "cutting":
        track = tracks[current_i]
        est = estimate_start(current_i, tracks, confirmed_starts, last_gap)
        delta = current_pos - est
        delta_style = "green" if abs(delta) <= 1.0 else ("yellow" if abs(delta) <= 5.0 else "red")
        info = Text()
        info.append(f"Track {current_i+1:02d} · {track['title']}\n", style="bold cyan")
        info.append(f"Position: {fmt_dur(current_pos)}   Schätzung: {fmt_dur(est)}   ")
        info.append(f"Δ {delta:+.2f}s\n", style=delta_style)
        info.append("Normton: ", style="dim")
        info.append("EIN\n\n" if normton else "aus\n\n", style="green" if normton else "dim")
        info.append("[p] abspielen  [+/-] ±0.5s  [++/--] ±2s  [ok] bestätigen  "
                    "[u] rückgängig  [n] Normton  Offset: ±m:ss", style="dim")
    elif phase == "export":
        done = sum(1 for s in (export_status or []) if s == "✓")
        info = Text()
        info.append(f"Exportiere Tracks: {done}/{n}\n", style="bold")
        info.append("✓ Abgeschlossen." if done == n else "Bitte warten...", style="green" if done == n else "dim")
    elif phase == "songtext":
        found = sum(1 for s in (lrc_status or []) if s == "✓")
        missing = sum(1 for s in (lrc_status or []) if s == "✗")
        checked = found + missing
        info = Text()
        info.append(f"Suche Songtexte: {checked}/{n}\n", style="bold")
        if checked == 0:
            info.append("Bitte warten...", style="dim")
        else:
            result_style = "green" if missing == 0 else "yellow"
            info.append(f"✓ {found} gefunden, {missing} nicht gefunden.", style=result_style)
    else:
        info = Text("✓ Fertig.", style="bold green")

    total_str = fmt_dur(total_dur) if total_dur else "?:??"
    return Panel(
        Group(table, Rule(style="dim"), info),
        title=f"[bold]{artist} · {album}[/bold]",
        subtitle=f"[dim]{n} Tracks · {total_str}[/dim]",
        expand=True,
        border_style="blue dim",
    )


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(
            f"VinylCut Interactive Cutter v{__version__}\n"
            "\nNutzung:\n"
            "  python3 interactive_cutter.py \"Pfad/zur/Artist - Album.flac\"\n"
            "\nOptionen:\n"
            "  -h, --help          Diese Hilfe anzeigen\n"
            "  -V, --version       Versionsnummer ausgeben\n"
            "  --no-songtext       Songtext-Suche am Ende überspringen\n"
            "  --out <Verzeichnis> Ausgabeverzeichnis für geschnittene Tracks\n"
            "  --preview <Sek>     Snippet-Länge in Sekunden (Standard: 3)\n"
            "\nInteraktive Befehle während des Schneidens:\n"
            "  [p]         Snippet nochmal abspielen\n"
            "  [+] / [-]   Start ±0,5 s verschieben\n"
            "  [++]/[--]   Start ±2,0 s verschieben\n"
            "  [ok]        Startpunkt bestätigen, nächster Track\n"
            "  [u]         Letztes ok rückgängig machen\n"
            "  [n]         Normton (220 Hz, 0,25 s) vor Snippet aus-/einschalten (Standard: EIN)\n"
            "  Zahl/±m:ss  Start um Offset verschieben (z.B. +2:34 oder -30)"
        )
        sys.exit(0 if len(sys.argv) >= 2 else 1)

    if sys.argv[1] in ("-V", "--version"):
        print(f"interactive_cutter.py {__version__}")
        sys.exit(0)

    args = sys.argv[1:]
    no_songtext = "--no-songtext" in args
    args = [a for a in args if a != "--no-songtext"]

    out_arg = None
    if "--out" in args:
        idx = args.index("--out")
        if idx + 1 >= len(args):
            print("Fehler: --out benötigt ein Verzeichnis.")
            sys.exit(1)
        out_arg = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    preview_duration = DEFAULT_PLAY_DURATION_SEC
    if "--preview" in args:
        idx = args.index("--preview")
        if idx + 1 >= len(args):
            print("Fehler: --preview benötigt eine Sekundenangabe.")
            sys.exit(1)
        try:
            preview_duration = float(args[idx + 1])
        except ValueError:
            print("Fehler: --preview erwartet eine Zahl.")
            sys.exit(1)
        args = args[:idx] + args[idx + 2:]

    if not args:
        print("Fehler: Kein FLAC-Pfad angegeben.")
        sys.exit(1)

    flac_path = Path(args[0]).resolve()
    out_dir = flac_path.parent / flac_path.stem
    track_out_dir = Path(out_arg).resolve() if out_arg else out_dir
    track_out_dir.mkdir(parents=True, exist_ok=True)

    subprocess.run(["python3", "metadata_fetcher.py", str(flac_path)])

    with open(out_dir / "release.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    for track in data["tracks"]:
        if "dur_s" not in track and "duration" in track:
            m, s = map(int, track["duration"].split(":"))
            track["dur_s"] = m * 60 + s

    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate", "-of", "json", str(flac_path)],
        capture_output=True, text=True).stdout)
    sr = int(probe["streams"][0]["sample_rate"])

    progress_path = out_dir / "progress.json"
    history: list = []
    starts: list = []
    last_gap = 0.0

    if progress_path.exists():
        with open(progress_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        history = saved["history"]
        starts = [h["start"] for h in history]
        last_gap = history[-1]["last_gap"] if history else 0.0
        n_done, n_total = len(starts), len(data["tracks"])
        ans = input(f"\n=== Fortschritt gefunden ({n_done}/{n_total} Tracks). Fortsetzen? [j/n] ===\n> ").strip().lower()
        if ans != "j":
            history, starts, last_gap = [], [], 0.0
            progress_path.unlink()

    normton = True
    i = len(starts)

    n = len(data["tracks"])

    def panel(phase="cutting", export_status=None, lrc_status=None):
        return build_panel(data["artist"], data["album"], data["tracks"],
                           starts, i, current_start if phase == "cutting" else 0.0,
                           normton, last_gap, phase, export_status, lrc_status)

    with Live(console=console, screen=True, auto_refresh=False) as live:
        current_start = 0.0

        # --- Schneiden ---
        while i < n:
            current_start = estimate_start(i, data["tracks"], starts, last_gap)

            while True:
                live.update(panel())
                live.refresh()

                if normton:
                    play_snippet_with_tone(flac_path, current_start, preview_duration)
                else:
                    play_snippet(flac_path, current_start, preview_duration)

                live.update(panel())
                live.refresh()

                action = console.input("  > ").strip().lower()

                if action == 'p':
                    continue
                elif action == '+':
                    current_start += 0.5
                elif action == '-':
                    current_start = max(0.0, current_start - 0.5)
                elif action == '++':
                    current_start += 2.0
                elif action == '--':
                    current_start = max(0.0, current_start - 2.0)
                elif action == 'n':
                    normton = not normton
                elif action == 'u':
                    if i > 0:
                        history.pop()
                        starts = [h["start"] for h in history]
                        last_gap = history[-1]["last_gap"] if history else 0.0
                        save_progress(progress_path, history)
                        i -= 1
                        break
                elif action == 'ok':
                    starts.append(current_start)
                    if i > 0 and "dur_s" in data["tracks"][i - 1]:
                        last_gap = current_start - (starts[i - 1] + data["tracks"][i - 1]["dur_s"])
                    history.append({"start": current_start, "last_gap": last_gap})
                    save_progress(progress_path, history)
                    i += 1
                    break
                else:
                    try:
                        current_start = max(0.0, current_start + parse_offset(action))
                    except ValueError:
                        pass

        progress_path.unlink(missing_ok=True)

        # --- Export ---
        export_status = [""] * n
        for idx, track in enumerate(data["tracks"]):
            live.update(panel("export", export_status))
            live.refresh()
            start_smp = round(starts[idx] * sr)
            if idx < len(starts) - 1:
                len_smp = round((starts[idx + 1] - starts[idx]) * sr)
            else:
                total_dur_s = float(subprocess.check_output([
                    "ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", str(flac_path)]))
                len_smp = round((total_dur_s * sr) - start_smp)
            cut_and_tag(
                flac_path,
                track_out_dir / f"{idx+1:02d} - {track['title'].replace('/', '_')}.flac",
                idx + 1, track["title"], data["artist"], data["album"],
                start_smp, len_smp, out_dir / "cover.jpg")
            export_status[idx] = "✓"

        live.update(panel("export", export_status))
        live.refresh()

        # --- Songtexte ---
        lrc_status = None
        if not no_songtext:
            lrc_status = [""] * n
            live.update(panel("songtext", export_status, lrc_status))
            live.refresh()
            subprocess.run(["python3", "songtext.py", str(track_out_dir)],
                           capture_output=True, text=True)
            for idx, track in enumerate(data["tracks"]):
                safe = track["title"].replace("/", "_")
                lrc_path = track_out_dir / f"{idx+1:02d} - {safe}.lrc"
                lrc_status[idx] = "✓" if lrc_path.exists() else "✗"
            live.update(panel("songtext", export_status, lrc_status))
            live.refresh()

        console.input("\n  [Enter] zum Beenden")


if __name__ == "__main__":
    main()
