#!/usr/bin/env python3
import sys
import re
import json
import subprocess
from pathlib import Path

from rich.console import Console
from rich.live import Live

from assemble_ui import (
    build_analysis_panel,
    build_crossfade_panel,
    build_export_panel,
    build_normalize_panel,
    build_points_panel,
)
from cut_ui import fmt_dur, live_input

__version__ = "1.1.2"

console = Console()


def suggest_clean_name(stem: str) -> str:
    return re.sub(r"[-_]raw$", "", stem, flags=re.IGNORECASE).strip()


SILENCE_NOISE_DB = -50
SILENCE_MIN_DURATION = 5.0
TRIM_NOISE_DB = -40
TRIM_MIN_DURATION = 0.5
DEFAULT_PLAY_DURATION = 3.0
DEFAULT_CROSSFADE_PREVIEW_SEC = 8.0
CROSSFADE_DURATION = 0.5


def detect_silences(
    flac_path: Path,
    noise_db: int = SILENCE_NOISE_DB,
    min_duration: float = SILENCE_MIN_DURATION,
) -> list:
    cmd = [
        "ffmpeg",
        "-i",
        str(flac_path),
        "-af",
        f"silencedetect=noise={noise_db}dB:duration={min_duration}",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = result.stderr

    silences = []
    current_start = None
    for line in output.splitlines():
        m = re.search(r"silence_start: ([0-9.]+)", line)
        if m:
            current_start = float(m.group(1))
        m = re.search(r"silence_end: ([0-9.]+) \| silence_duration: ([0-9.]+)", line)
        if m and current_start is not None:
            silences.append(
                {
                    "start": current_start,
                    "end": float(m.group(1)),
                    "duration": float(m.group(2)),
                }
            )
            current_start = None
    return silences


def detect_trim_points(flac_path: Path, total_duration: float) -> tuple:
    silences = detect_silences(
        flac_path, noise_db=TRIM_NOISE_DB, min_duration=TRIM_MIN_DURATION
    )
    music_start = silences[0]["end"] if silences and silences[0]["start"] < 1.0 else 0.0
    music_end = (
        silences[-1]["start"]
        if silences and silences[-1]["end"] > total_duration - 10.0
        else total_duration
    )
    return music_start, music_end


def fmt_time(seconds: float) -> str:
    total = abs(seconds)
    m = int(total) // 60
    s = total - m * 60
    return f"{m}:{s:05.2f}"


def parse_offset(s: str) -> float:
    s = s.strip()
    sign = 1.0
    if s.startswith("+"):
        s, sign = s[1:], 1.0
    elif s.startswith("-"):
        s, sign = s[1:], -1.0
    if ":" in s:
        m, sec = s.split(":", 1)
        return sign * (int(m) * 60 + float(sec))
    return sign * float(s)


def play_snippet(
    flac_path: Path, start_time: float, duration: float = DEFAULT_PLAY_DURATION
) -> None:
    subprocess.run(
        [
            "ffplay",
            "-nodisp",
            "-autoexit",
            "-v",
            "quiet",
            "-ss",
            f"{start_time:.3f}",
            "-t",
            str(duration),
            str(flac_path),
        ]
    )


def play_snippet_with_tone(
    flac_path: Path, start_time: float, duration: float = DEFAULT_PLAY_DURATION
) -> None:
    filter_complex = (
        "[0:a]aformat=channel_layouts=stereo[tone];"
        "[1:a]aformat=channel_layouts=stereo[audio];"
        "[tone][audio]concat=n=2:v=0:a=1[out]"
    )
    cmd = [
        "ffmpeg",
        "-v",
        "quiet",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=220:duration=0.25",
        "-ss",
        f"{start_time:.3f}",
        "-t",
        str(duration),
        "-i",
        str(flac_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[out]",
        "-f",
        "wav",
        "pipe:1",
    ]
    ffmpeg = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    subprocess.run(
        ["ffplay", "-nodisp", "-autoexit", "-v", "quiet", "-"],
        stdin=ffmpeg.stdout,
        stderr=subprocess.DEVNULL,
    )
    ffmpeg.wait()


def play_crossfade_preview(
    flac_path: Path,
    a_pos: float,
    b_pos: float,
    preview_sec: float = DEFAULT_CROSSFADE_PREVIEW_SEC,
    crossfade_sec: float = CROSSFADE_DURATION,
    normton: bool = False,
) -> None:
    """Spielt [Ton +] Ende Seite N + Crossfade + [Ton +] Anfang Seite N+1."""
    if normton:
        filter_complex = (
            "[0:a]aformat=channel_layouts=stereo[t1];"
            "[1:a]aformat=channel_layouts=stereo[end];"
            "[2:a]aformat=channel_layouts=stereo[start];"
            "[3:a]aformat=channel_layouts=stereo[t2];"
            f"[end][start]acrossfade=d={crossfade_sec}[cf];"
            "[t1][cf][t2]concat=n=3:v=0:a=1[out]"
        )
        cmd = [
            "ffmpeg",
            "-v",
            "quiet",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=0.25",
            "-ss",
            f"{max(0.0, a_pos - preview_sec):.3f}",
            "-t",
            f"{preview_sec:.3f}",
            "-i",
            str(flac_path),
            "-ss",
            f"{b_pos:.3f}",
            "-t",
            f"{preview_sec:.3f}",
            "-i",
            str(flac_path),
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=0.25",
            "-filter_complex",
            filter_complex,
            "-map",
            "[out]",
            "-f",
            "wav",
            "pipe:1",
        ]
    else:
        cmd = [
            "ffmpeg",
            "-v",
            "quiet",
            "-ss",
            f"{max(0.0, a_pos - preview_sec):.3f}",
            "-t",
            f"{preview_sec:.3f}",
            "-i",
            str(flac_path),
            "-ss",
            f"{b_pos:.3f}",
            "-t",
            f"{preview_sec:.3f}",
            "-i",
            str(flac_path),
            "-filter_complex",
            f"[0:a][1:a]acrossfade=d={crossfade_sec}[out]",
            "-map",
            "[out]",
            "-f",
            "wav",
            "pipe:1",
        ]
    ffmpeg = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    subprocess.run(
        ["ffplay", "-nodisp", "-autoexit", "-v", "quiet", "-"],
        stdin=ffmpeg.stdout,
        stderr=subprocess.DEVNULL,
    )
    ffmpeg.wait()


def save_progress(
    progress_path: Path, flac_path: Path, history: list, cf_done: list
) -> None:
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "flac": flac_path.name,
                "history": history,
                "crossfade_confirmed": cf_done,
            },
            f,
            indent=2,
        )


def side_letter(index: int) -> str:
    return chr(ord("A") + index)


def build_steps(music_start: float, music_end: float, silences: list) -> list:
    last = side_letter(len(silences))
    steps = [
        {
            "label": "trim_start",
            "desc": f"Anfang Seite {side_letter(0)} — Musik beginnt hier",
            "suggested": music_start,
        }
    ]
    for i, s in enumerate(silences):
        steps.append(
            {
                "label": f"boundary_{i}_a",
                "desc": f"Übergang {side_letter(i)}→{side_letter(i + 1)} — Ende Seite {side_letter(i)}",
                "suggested": s["start"],
            }
        )
        steps.append(
            {
                "label": f"boundary_{i}_b",
                "desc": f"Übergang {side_letter(i)}→{side_letter(i + 1)} — Anfang Seite {side_letter(i + 1)}",
                "suggested": s["end"],
            }
        )
    steps.append(
        {
            "label": "trim_end",
            "desc": f"Ende Seite {last} — Musik endet hier",
            "suggested": music_end,
        }
    )
    return steps


def get_segments(history: list, n_boundaries: int) -> list:
    """Gibt Liste von (start, end) Tupeln für alle Segmente zurück."""
    trim_start = history[0]["pos"]
    trim_end = history[-1]["pos"]
    if n_boundaries == 0:
        return [(trim_start, trim_end)]
    segments = []
    segments.append((trim_start, history[1]["pos"]))
    for j in range(n_boundaries - 1):
        segments.append((history[2 + j * 2]["pos"], history[3 + j * 2]["pos"]))
    segments.append((history[2 + (n_boundaries - 1) * 2]["pos"], trim_end))
    return segments


def cut_segment(flac_path: Path, out_path: Path, start_s: float, end_s: float) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "quiet",
            "-y",
            "-ss",
            f"{start_s:.3f}",
            "-t",
            f"{end_s - start_s:.3f}",
            "-i",
            str(flac_path),
            str(out_path),
        ],
        check=True,
    )


def measure_channel_peaks(flac_path: Path) -> tuple:
    """Gibt (left_db, right_db) Peak-Pegel zurück."""
    result = subprocess.run(
        ["sox", str(flac_path), "-n", "stats"], capture_output=True, text=True
    )
    for line in result.stderr.splitlines():
        if line.strip().startswith("Pk lev dB"):
            parts = line.split()
            return float(parts[-2]), float(parts[-1])
    return 0.0, 0.0


def normalize(
    in_path: Path, out_path: Path, left_gain: float = 1.0, right_gain: float = 1.0
) -> None:
    """DC-Offset entfernen, optionaler Kanalausgleich, auf -1.0 dBTP normalisieren (ffmpeg loudnorm, 2 Pässe)."""
    base_filters = ["highpass=f=5"]
    if left_gain != 1.0 or right_gain != 1.0:
        base_filters.append(f"pan=stereo|c0={left_gain:.6f}*c0|c1={right_gain:.6f}*c1")

    # Pass 1: Pegel messen
    r = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(in_path),
            "-af",
            ",".join(base_filters) + ",loudnorm=I=-23:LRA=11:TP=-1.0:print_format=json",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    j_start = r.stderr.rfind("{")
    j_end = r.stderr.rfind("}") + 1
    stats = json.loads(r.stderr[j_start:j_end])

    # Pass 2: Normalisierung anwenden
    apply_af = ",".join(base_filters) + (
        f",loudnorm=I=-23:LRA=11:TP=-1.0"
        f":measured_I={stats['input_i']}"
        f":measured_LRA={stats['input_lra']}"
        f":measured_TP={stats['input_tp']}"
        f":measured_thresh={stats['input_thresh']}"
        f":offset={stats['target_offset']}"
        f":linear=true"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-v",
            "error",
            "-y",
            "-i",
            str(in_path),
            "-af",
            apply_af,
            str(out_path),
        ],
        check=True,
    )


def join_with_crossfade(
    seg1: Path, seg2: Path, out_path: Path, crossfade_sec: float = CROSSFADE_DURATION
) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "quiet",
            "-y",
            "-i",
            str(seg1),
            "-i",
            str(seg2),
            "-filter_complex",
            f"[0:a][1:a]acrossfade=d={crossfade_sec}[out]",
            "-map",
            "[out]",
            str(out_path),
        ],
        check=True,
    )


def _cf_boundaries(
    history: list, n_boundaries: int, current_j: int, a_pos: float, b_pos: float
) -> list:
    """Baut die boundaries-Liste für build_crossfade_panel."""
    bds = []
    for j in range(n_boundaries):
        if j == current_j:
            bds.append(
                {
                    "left": side_letter(j),
                    "right": side_letter(j + 1),
                    "a_pos": a_pos,
                    "b_pos": b_pos,
                }
            )
        else:
            bds.append(
                {
                    "left": side_letter(j),
                    "right": side_letter(j + 1),
                    "a_pos": history[1 + j * 2]["pos"],
                    "b_pos": history[2 + j * 2]["pos"],
                }
            )
    return bds


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(
            f"VinylCut Assemble v{__version__}\n"
            "\nNutzung:\n"
            '  python3 assemble.py "Pfad/zur/Aufnahme.flac"\n'
            "\nOptionen:\n"
            "  -h, --help          Diese Hilfe anzeigen\n"
            "  -V, --version       Versionsnummer ausgeben\n"
            "  --preview <Sek>     Crossfade-Vorschau-Länge in Sekunden (Standard: 8)\n"
            "\nPhase 1: Schnitt-/Trim-Punkte interaktiv setzen.\n"
            "Phase 2: Crossfade-Vorschau je Seitengrenze, Feinschneiden.\n"
            "Ergebnis non-destruktiv in assemble.json gespeichert.\n"
            "\nPhase-1-Steuerung:\n"
            "  [p]         Snippet abspielen\n"
            "  [+] / [-]   Punkt ±0,5 s verschieben\n"
            "  [++]/[--]   Punkt ±2,0 s verschieben\n"
            "  [ok]        Punkt bestätigen, weiter\n"
            "  [u]         Letzten Schritt rückgängig\n"
            "  [n]         Normton (220 Hz) ein-/ausschalten\n"
            "  Zahl/±m:ss  Offset eingeben\n"
            "\nPhase-2-Steuerung (Crossfade):\n"
            "  [p]         Crossfade nochmal abspielen\n"
            "  [a]         Fokus auf A (Ende Musik)\n"
            "  [b]         Fokus auf B (Anfang Musik)\n"
            "  [+]/[-]     Aktiven Punkt ±0,5 s verschieben\n"
            "  [++]/[--]   Aktiven Punkt ±2,0 s verschieben\n"
            "  [ok]        Grenze bestätigen, weiter\n"
            "  [u]         Vorherige Grenze nochmal\n"
            "  [n]         Normton ein-/ausschalten\n"
            "  Zahl/±m:ss  Aktiven Punkt um Offset verschieben"
        )
        sys.exit(0 if len(sys.argv) >= 2 else 1)

    if sys.argv[1] in ("-V", "--version"):
        print(f"assemble.py {__version__}")
        sys.exit(0)

    args = sys.argv[1:]
    cf_preview_sec = DEFAULT_CROSSFADE_PREVIEW_SEC
    if "--preview" in args:
        idx = args.index("--preview")
        if idx + 1 >= len(args):
            print("Fehler: --preview benötigt eine Sekundenangabe.")
            sys.exit(1)
        try:
            cf_preview_sec = float(args[idx + 1])
        except ValueError:
            print("Fehler: --preview erwartet eine Zahl.")
            sys.exit(1)
        args = args[:idx] + args[idx + 2 :]

    if not args:
        print("Fehler: Kein FLAC-Pfad angegeben.")
        sys.exit(1)

    flac_path = Path(args[0]).resolve()
    if not flac_path.exists():
        print(f"Fehler: Datei nicht gefunden: {flac_path}")
        sys.exit(1)

    out_dir = flac_path.parent / flac_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    progress_path = out_dir / "assemble.json"
    stem = flac_path.stem

    with Live(console=console, screen=True, auto_refresh=False) as live:

        def refresh_analysis(status):
            live.update(build_analysis_panel(stem, status))
            live.refresh()

        # --- Phase 0: Analyse ---
        status: list[str] = [f"Datei: {flac_path.name}", ""]

        status[-1] = "Bestimme Gesamtdauer..."
        refresh_analysis(status)
        total_duration = float(
            subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(flac_path),
                ]
            )
        )
        status[-1] = f"Gesamtdauer: {fmt_dur(total_duration)}"

        status.append("Erkenne Trim-Punkte...")
        refresh_analysis(status)
        music_start, music_end = detect_trim_points(flac_path, total_duration)
        status[-1] = f"Musik: {fmt_dur(music_start)} – {fmt_dur(music_end)}"

        status.append("Erkenne Seitengrenzen...")
        refresh_analysis(status)
        all_silences = detect_silences(flac_path)
        n_detected = len(all_silences)
        status[-1] = f"Erkannte Grenzen: {n_detected}  (→ {n_detected + 1} Seite(n))"
        refresh_analysis(status)

        # Seitenanzahl bestätigen
        n_sides = None
        while n_sides is None:
            ans = live_input(
                live,
                build_analysis_panel(stem, status),
                f"Wie viele Seiten hat die Vinyl? [{n_detected + 1}]: ",
            )
            if not ans:
                n_sides = n_detected + 1
            else:
                try:
                    val = int(ans)
                    if val >= 1:
                        n_sides = val
                    else:
                        status.append("  Bitte eine Zahl ≥ 1 eingeben.")
                except ValueError:
                    status.append("  Bitte eine ganze Zahl eingeben.")

        n_boundaries = n_sides - 1
        silences = sorted(
            sorted(all_silences, key=lambda s: s["duration"], reverse=True)[
                :n_boundaries
            ],
            key=lambda s: s["start"],
        )
        if len(silences) < n_boundaries:
            n_missing = n_boundaries - len(silences)
            status.append(
                f"  ⚠ {n_missing} Grenze(n) geschätzt — bitte manuell anpassen."
            )
            span = music_end - music_start
            for k in range(1, n_missing + 1):
                est = music_start + span * k / (n_missing + 1)
                silences.append({"start": est, "end": est + 5.0, "duration": 0.0})
            silences.sort(key=lambda s: s["start"])

        steps = build_steps(music_start, music_end, silences)
        status.append(f"  {n_boundaries} Grenze(n), {len(steps)} Punkte zu setzen.")
        refresh_analysis(status)

        history: list = []
        cf_done: list = []

        if progress_path.exists():
            with open(progress_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if saved.get("flac") == flac_path.name:
                history = saved.get("history", [])
                cf_done = saved.get("crossfade_confirmed", [])
                n_done = len(history)
                cf_count = len(cf_done)
                if n_done >= len(steps) and cf_count >= n_boundaries:
                    status.append("Alle Punkte + Crossfades bestätigt.")
                    ans = live_input(
                        live,
                        build_analysis_panel(stem, status),
                        "Neu beginnen? [j/n]: ",
                    )
                    if ans.lower() == "j":
                        history, cf_done = [], []
                        progress_path.unlink()
                elif n_done > 0 or cf_count > 0:
                    p_info = (
                        f"Phase 1: {n_done}/{len(steps)}"
                        if n_done < len(steps)
                        else f"Phase 2: {cf_count}/{n_boundaries} Crossfades"
                    )
                    status.append(f"Fortschritt gefunden ({p_info}).")
                    ans = live_input(
                        live, build_analysis_panel(stem, status), "Fortsetzen? [j/n]: "
                    )
                    if ans.lower() != "j":
                        history, cf_done = [], []
                        progress_path.unlink()

        # --- Phase 1: Punkte setzen ---
        normton = True
        i = len(history)
        while i < len(steps):
            step = steps[i]
            current_pos = history[i]["pos"] if i < len(history) else step["suggested"]

            while True:
                panel = build_points_panel(
                    stem, steps, history, i, current_pos, normton
                )
                live.update(panel)
                live.refresh()

                if normton:
                    play_snippet_with_tone(flac_path, current_pos)
                else:
                    play_snippet(flac_path, current_pos)

                action = live_input(
                    live,
                    build_points_panel(stem, steps, history, i, current_pos, normton),
                    "> ",
                )

                if action == "p":
                    continue
                elif action == "n":
                    normton = not normton
                elif action == "+":
                    current_pos += 0.5
                elif action == "-":
                    current_pos = max(0.0, current_pos - 0.5)
                elif action == "++":
                    current_pos += 2.0
                elif action == "--":
                    current_pos = max(0.0, current_pos - 2.0)
                elif action == "u":
                    if i > 0:
                        history.pop()
                        save_progress(progress_path, flac_path, history, cf_done)
                        i -= 1
                        break
                elif action == "ok":
                    if i < len(history):
                        history[i] = {"label": step["label"], "pos": current_pos}
                    else:
                        history.append({"label": step["label"], "pos": current_pos})
                    save_progress(progress_path, flac_path, history, cf_done)
                    i += 1
                    break
                else:
                    try:
                        current_pos = max(0.0, current_pos + parse_offset(action))
                    except ValueError:
                        pass

        # --- Phase 2: Crossfade-Vorschau ---
        normton_cf = True
        active = "a"
        j = len(cf_done)
        while j < n_boundaries:
            a_idx = 1 + j * 2
            b_idx = 2 + j * 2
            a_pos = history[a_idx]["pos"]
            b_pos = history[b_idx]["pos"]

            while True:
                bds = _cf_boundaries(history, n_boundaries, j, a_pos, b_pos)
                panel = build_crossfade_panel(
                    stem, bds, len(cf_done), j, active, normton_cf
                )
                live.update(panel)
                live.refresh()

                play_crossfade_preview(
                    flac_path,
                    a_pos,
                    b_pos,
                    preview_sec=cf_preview_sec,
                    normton=normton_cf,
                )

                action = live_input(
                    live,
                    build_crossfade_panel(
                        stem, bds, len(cf_done), j, active, normton_cf
                    ),
                    "> ",
                )

                if action == "p":
                    continue
                elif action == "n":
                    normton_cf = not normton_cf
                elif action == "a":
                    active = "a"
                elif action == "b":
                    active = "b"
                elif action == "u":
                    if j > 0:
                        cf_done.pop()
                        save_progress(progress_path, flac_path, history, cf_done)
                        j -= 1
                        a_pos = history[1 + j * 2]["pos"]
                        b_pos = history[2 + j * 2]["pos"]
                    break
                elif action == "ok":
                    history[a_idx]["pos"] = a_pos
                    history[b_idx]["pos"] = b_pos
                    cf_done.append(j)
                    save_progress(progress_path, flac_path, history, cf_done)
                    j += 1
                    break
                else:
                    delta = {"+": 0.5, "-": -0.5, "++": 2.0, "--": -2.0}.get(action)
                    if delta is None:
                        try:
                            delta = parse_offset(action)
                        except ValueError:
                            continue
                    if active == "a":
                        a_pos = max(0.0, a_pos + delta)
                    else:
                        b_pos = max(0.0, b_pos + delta)
                    save_progress(progress_path, flac_path, history, cf_done)

        # --- Phase 3: Schneiden + Zusammenfügen ---
        raw_segs = get_segments(history, n_boundaries)
        segments = [
            (s, e, f"Seite {side_letter(k)}") for k, (s, e) in enumerate(raw_segs)
        ]
        n_seg = len(segments)
        export_status = [""] * n_seg
        temp_files: list[Path] = []
        to_cleanup: list[Path] = []

        for k, (start, end, label) in enumerate(segments):
            export_status[k] = "…"
            live.update(
                build_export_panel(
                    stem, segments, export_status, crossfade_sec=CROSSFADE_DURATION
                )
            )
            live.refresh()
            tmp = out_dir / f"_seg_{k:02d}.wav"
            cut_segment(flac_path, tmp, start, end)
            temp_files.append(tmp)
            to_cleanup.append(tmp)
            export_status[k] = "✓"

        live.update(
            build_export_panel(
                stem,
                segments,
                export_status,
                joining=True,
                crossfade_sec=CROSSFADE_DURATION,
            )
        )
        live.refresh()
        current_seg = temp_files[0]
        for k in range(1, n_seg):
            joined = out_dir / f"_joined_{k:02d}.wav"
            join_with_crossfade(current_seg, temp_files[k], joined)
            to_cleanup.append(joined)
            current_seg = joined

        out_flac = flac_path.parent / f"{flac_path.stem}_prepared.flac"
        subprocess.run(
            ["ffmpeg", "-v", "quiet", "-y", "-i", str(current_seg), str(out_flac)],
            check=True,
        )
        for f in to_cleanup:
            f.unlink(missing_ok=True)

        live.update(
            build_export_panel(
                stem, segments, export_status, crossfade_sec=CROSSFADE_DURATION
            )
        )
        live.refresh()

        # --- Phase 4: Normalisierung + Umbenennen ---
        final_flac = flac_path.parent / f"{flac_path.stem}_final.flac"
        norm_status: list[str] = []

        left_db, right_db = measure_channel_peaks(out_flac)

        left_gain = right_gain = 1.0
        diff = right_db - left_db
        if abs(diff) >= 0.1:
            ans = live_input(
                live,
                build_normalize_panel(stem, left_db, right_db, norm_status),
                "Kanalausgleich anwenden? [j/n]: ",
            )
            if ans.lower() == "j":
                if diff < 0:
                    right_gain = 10 ** (-diff / 20)
                else:
                    left_gain = 10 ** (diff / 20)
                norm_status.append(
                    f"Korrektur: Links ×{left_gain:.4f}  Rechts ×{right_gain:.4f}"
                )

        norm_status.append("DC-Offset + Peak-Normalisierung auf -0.1 dBFS...")
        live.update(build_normalize_panel(stem, left_db, right_db, norm_status))
        live.refresh()
        normalize(out_flac, final_flac, left_gain, right_gain)
        norm_status[-1] = "✓ Normalisierung abgeschlossen."

        # Umbenennen
        suggested = suggest_clean_name(flac_path.stem)
        norm_status.append(f"Vorschlag: {suggested}.flac")
        ans = live_input(
            live,
            build_normalize_panel(stem, left_db, right_db, norm_status),
            "[Enter] übernehmen oder neuen Namen eingeben: ",
        )
        clean_name = ans if ans else suggested
        clean_flac = flac_path.parent / f"{clean_name}.flac"

        if clean_flac.exists():
            ans = live_input(
                live,
                build_normalize_panel(stem, left_db, right_db, norm_status),
                f"{clean_flac.name} existiert. Überschreiben? [j/n]: ",
            )
            if ans.lower() != "j":
                clean_flac = final_flac
                norm_status.append(f"Beibehalten als: {final_flac.name}")
            else:
                clean_flac.unlink()

        if clean_flac != final_flac:
            final_flac.rename(clean_flac)
            norm_status.append(f"✓ {final_flac.name} → {clean_flac.name}")

        norm_status.append("")
        norm_status.append(f"Vorbereitet: {out_flac.name}")
        norm_status.append(f"Ausgabe:     {clean_flac.name}")
        norm_status.append(f"Original:    {flac_path.name}  (unverändert)")
        live_input(
            live,
            build_normalize_panel(stem, left_db, right_db, norm_status),
            "[Enter] zum Beenden",
        )


if __name__ == "__main__":
    main()
