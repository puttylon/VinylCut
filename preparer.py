#!/usr/bin/env python3
import sys
import re
import json
import subprocess
from pathlib import Path

__version__ = "0.4.0"

SILENCE_NOISE_DB = -50
SILENCE_MIN_DURATION = 5.0
TRIM_NOISE_DB = -40
TRIM_MIN_DURATION = 0.5
DEFAULT_PLAY_DURATION = 3.0
DEFAULT_CROSSFADE_PREVIEW_SEC = 8.0
CROSSFADE_DURATION = 0.5


def detect_silences(flac_path: Path, noise_db: int = SILENCE_NOISE_DB, min_duration: float = SILENCE_MIN_DURATION) -> list:
    cmd = [
        "ffmpeg", "-i", str(flac_path),
        "-af", f"silencedetect=noise={noise_db}dB:duration={min_duration}",
        "-f", "null", "-",
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
            silences.append({
                "start": current_start,
                "end": float(m.group(1)),
                "duration": float(m.group(2)),
            })
            current_start = None
    return silences


def detect_trim_points(flac_path: Path, total_duration: float) -> tuple:
    silences = detect_silences(flac_path, noise_db=TRIM_NOISE_DB, min_duration=TRIM_MIN_DURATION)
    music_start = silences[0]["end"] if silences and silences[0]["start"] < 1.0 else 0.0
    music_end = silences[-1]["start"] if silences and silences[-1]["end"] > total_duration - 10.0 else total_duration
    return music_start, music_end


def fmt_time(seconds: float) -> str:
    total = abs(seconds)
    m = int(total) // 60
    s = total - m * 60
    return f"{m}:{s:05.2f}"


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


def play_snippet(flac_path: Path, start_time: float, duration: float = DEFAULT_PLAY_DURATION) -> None:
    subprocess.run(["ffplay", "-nodisp", "-autoexit", "-v", "quiet",
                    "-ss", f"{start_time:.3f}", "-t", str(duration), str(flac_path)])


def play_snippet_with_tone(flac_path: Path, start_time: float, duration: float = DEFAULT_PLAY_DURATION) -> None:
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
                   stdin=ffmpeg.stdout, stderr=subprocess.DEVNULL)
    ffmpeg.wait()


def play_crossfade_preview(flac_path: Path, a_pos: float, b_pos: float,
                           preview_sec: float = DEFAULT_CROSSFADE_PREVIEW_SEC,
                           crossfade_sec: float = CROSSFADE_DURATION,
                           normton: bool = False) -> None:
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
            "ffmpeg", "-v", "quiet",
            "-f", "lavfi", "-i", "sine=frequency=220:duration=0.25",
            "-ss", f"{max(0.0, a_pos - preview_sec):.3f}", "-t", f"{preview_sec:.3f}", "-i", str(flac_path),
            "-ss", f"{b_pos:.3f}", "-t", f"{preview_sec:.3f}", "-i", str(flac_path),
            "-f", "lavfi", "-i", "sine=frequency=220:duration=0.25",
            "-filter_complex", filter_complex,
            "-map", "[out]", "-f", "wav", "pipe:1",
        ]
    else:
        cmd = [
            "ffmpeg", "-v", "quiet",
            "-ss", f"{max(0.0, a_pos - preview_sec):.3f}", "-t", f"{preview_sec:.3f}", "-i", str(flac_path),
            "-ss", f"{b_pos:.3f}", "-t", f"{preview_sec:.3f}", "-i", str(flac_path),
            "-filter_complex", f"[0:a][1:a]acrossfade=d={crossfade_sec}[out]",
            "-map", "[out]", "-f", "wav", "pipe:1",
        ]
    ffmpeg = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    subprocess.run(["ffplay", "-nodisp", "-autoexit", "-v", "quiet", "-"],
                   stdin=ffmpeg.stdout, stderr=subprocess.DEVNULL)
    ffmpeg.wait()


def save_progress(progress_path: Path, flac_path: Path, history: list, cf_done: list) -> None:
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump({"flac": str(flac_path), "history": history, "crossfade_confirmed": cf_done}, f, indent=2)


def show_status(step: dict, current_pos: float, i: int, n_steps: int, normton: bool = False) -> None:
    print()
    print(f"  [{i+1:02d}/{n_steps:02d}] {step['desc']}")
    print(f"  Aktuell: {fmt_time(current_pos)}  ({current_pos:.1f}s)  |  Vorschlag: {fmt_time(step['suggested'])}  ({step['suggested']:.1f}s)")
    normton_str = "EIN" if normton else "aus"
    print(f"  [p]lay | [+] +0.5s | [-] -0.5s | [++] +2s | [--] -2s | [ok] bestätigen | [u]ndo | [n]ormton: {normton_str} | Offset: Zahl oder ±m:ss")


def build_steps(music_start: float, music_end: float, silences: list) -> list:
    steps = [{"label": "trim_start", "desc": "Anfang — Musik beginnt hier", "suggested": music_start}]
    for i, s in enumerate(silences):
        steps.append({"label": f"boundary_{i}_a", "desc": f"Grenze {i+1}/{len(silences)} — A (Ende Musik Seite {i+1})", "suggested": s["start"]})
        steps.append({"label": f"boundary_{i}_b", "desc": f"Grenze {i+1}/{len(silences)} — B (Anfang Musik Seite {i+2})", "suggested": s["end"]})
    steps.append({"label": "trim_end", "desc": "Ende — Musik endet hier", "suggested": music_end})
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
    subprocess.run([
        "ffmpeg", "-v", "quiet", "-y",
        "-ss", f"{start_s:.3f}", "-t", f"{end_s - start_s:.3f}",
        "-i", str(flac_path), str(out_path),
    ], check=True)


def join_with_crossfade(seg1: Path, seg2: Path, out_path: Path, crossfade_sec: float = CROSSFADE_DURATION) -> None:
    subprocess.run([
        "ffmpeg", "-v", "quiet", "-y",
        "-i", str(seg1), "-i", str(seg2),
        "-filter_complex", f"[0:a][1:a]acrossfade=d={crossfade_sec}[out]",
        "-map", "[out]", str(out_path),
    ], check=True)


def show_crossfade_status(j: int, n: int, a_pos: float, b_pos: float, active: str, normton: bool) -> None:
    a_marker = " ←" if active == 'a' else ""
    b_marker = " ←" if active == 'b' else ""
    normton_str = "EIN" if normton else "aus"
    print()
    print(f"  === Crossfade-Vorschau: Grenze {j+1}/{n} ===")
    print(f"  A (Ende Musik):    {fmt_time(a_pos)}  ({a_pos:.1f}s){a_marker}")
    print(f"  B (Anfang Musik):  {fmt_time(b_pos)}  ({b_pos:.1f}s){b_marker}")
    print(f"  Herausgeschnitten: {fmt_time(b_pos - a_pos)}")
    print(f"  [a]/[b] Fokus | [+] +0.5s | [-] -0.5s | [++] +2s | [--] -2s | [ok] bestätigen | [u]ndo | [n]ormton: {normton_str} | Offset: Zahl oder ±m:ss")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(
            f"VinylCut Preparer v{__version__}\n"
            "\nNutzung:\n"
            "  python3 preparer.py \"Pfad/zur/Aufnahme.flac\"\n"
            "\nOptionen:\n"
            "  -h, --help          Diese Hilfe anzeigen\n"
            "  -V, --version       Versionsnummer ausgeben\n"
            "  --preview <Sek>     Crossfade-Vorschau-Länge in Sekunden (Standard: 8)\n"
            "\nPhase 1: Schnitt-/Trim-Punkte interaktiv setzen.\n"
            "Phase 2: Crossfade-Vorschau je Seitengrenze, Feinschneiden.\n"
            "Ergebnis non-destruktiv in preparer.json gespeichert.\n"
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
        print(f"preparer.py {__version__}")
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
        args = args[:idx] + args[idx + 2:]

    if not args:
        print("Fehler: Kein FLAC-Pfad angegeben.")
        sys.exit(1)

    flac_path = Path(args[0]).resolve()
    if not flac_path.exists():
        print(f"Fehler: Datei nicht gefunden: {flac_path}")
        sys.exit(1)

    out_dir = flac_path.parent / flac_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    progress_path = out_dir / "preparer.json"

    print(f"\n=== VinylCut Preparer v{__version__} ===")
    print(f"Datei: {flac_path.name}")
    print("\nAnalysiere...")

    total_duration = float(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(flac_path)
    ]))

    music_start, music_end = detect_trim_points(flac_path, total_duration)
    all_silences = detect_silences(flac_path)

    # Anzahl Seiten vom Nutzer bestätigen
    n_detected = len(all_silences)
    print(f"  Automatisch erkannte Grenzen: {n_detected} (= {n_detected + 1} Seite(n))")
    while True:
        ans = input(f"  Wie viele Seiten hat die Vinyl? [{n_detected + 1}]: ").strip()
        if not ans:
            n_sides = n_detected + 1
            break
        try:
            n_sides = int(ans)
            if n_sides >= 1:
                break
        except ValueError:
            pass
        print("  Bitte eine ganze Zahl eingeben.")

    n_boundaries = n_sides - 1
    # Beste Kandidaten nach Stillelänge auswählen, zeitlich sortiert
    silences = sorted(
        sorted(all_silences, key=lambda s: s["duration"], reverse=True)[:n_boundaries],
        key=lambda s: s["start"]
    )
    if len(silences) < n_boundaries:
        print(f"  Warnung: Nur {len(silences)} Grenze(n) gefunden, {n_boundaries} erwartet.")
        n_boundaries = len(silences)

    steps = build_steps(music_start, music_end, silences)
    print(f"  Verwende {n_boundaries} Grenze(n), {len(steps)} Punkte zu setzen.")

    history: list = []
    cf_done: list = []

    if progress_path.exists():
        with open(progress_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if saved.get("flac") == str(flac_path):
            history = saved.get("history", [])
            cf_done = saved.get("crossfade_confirmed", [])
            n_done = len(history)
            cf_count = len(cf_done)
            if n_done >= len(steps) and cf_count >= n_boundaries:
                ans = input(f"\n=== Alle Punkte + Crossfades bestätigt. Neu beginnen? [j/n] ===\n> ").strip().lower()
                if ans == "j":
                    history, cf_done = [], []
                    progress_path.unlink()
            elif n_done > 0 or cf_count > 0:
                status = f"Phase 1: {n_done}/{len(steps)}" if n_done < len(steps) else f"Phase 2: {cf_count}/{n_boundaries} Crossfades"
                ans = input(f"\n=== Fortschritt gefunden ({status}). Fortsetzen? [j/n] ===\n> ").strip().lower()
                if ans != "j":
                    history, cf_done = [], []
                    progress_path.unlink()

    # --- Phase 1: Punkte setzen ---
    if len(history) < len(steps):
        normton = False
        i = len(history)
        while i < len(steps):
            step = steps[i]
            current_pos = history[i]["pos"] if i < len(history) else step["suggested"]

            while True:
                show_status(step, current_pos, i, len(steps), normton)
                if normton:
                    play_snippet_with_tone(flac_path, current_pos)
                else:
                    play_snippet(flac_path, current_pos)
                action = input("  > ").strip().lower()

                if action == 'p':
                    continue
                elif action == 'n':
                    normton = not normton
                elif action == '+':
                    current_pos += 0.5
                elif action == '-':
                    current_pos = max(0.0, current_pos - 0.5)
                elif action == '++':
                    current_pos += 2.0
                elif action == '--':
                    current_pos = max(0.0, current_pos - 2.0)
                elif action == 'u':
                    if i == 0:
                        print("  Kein vorheriger Schritt.")
                    else:
                        history.pop()
                        save_progress(progress_path, flac_path, history, cf_done)
                        i -= 1
                        break
                elif action == 'ok':
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
                        print("  Ungültige Eingabe.")

        print("\n=== PHASE 1 ABGESCHLOSSEN ===")

    # --- Phase 2: Crossfade-Vorschau ---
    if n_boundaries > 0 and len(cf_done) < n_boundaries:
        print("\n=== PHASE 2: CROSSFADE-VORSCHAU ===")
        print(f"Für jede der {n_boundaries} Grenzen: Übergang abhören, A/B mit [a]/[b] fokussieren und verschieben.\n")

        normton_cf = False
        active = 'a'
        j = len(cf_done)
        while j < n_boundaries:
            a_idx = 1 + j * 2
            b_idx = 2 + j * 2
            a_pos = history[a_idx]["pos"]
            b_pos = history[b_idx]["pos"]

            while True:
                show_crossfade_status(j, n_boundaries, a_pos, b_pos, active, normton_cf)
                play_crossfade_preview(flac_path, a_pos, b_pos, preview_sec=cf_preview_sec, normton=normton_cf)
                action = input("  > ").strip().lower()

                if action == 'p':
                    continue
                elif action == 'n':
                    normton_cf = not normton_cf
                elif action == 'a':
                    active = 'a'
                elif action == 'b':
                    active = 'b'
                elif action == 'u':
                    if j == 0:
                        print("  Keine vorherige Grenze.")
                    else:
                        cf_done.pop()
                        save_progress(progress_path, flac_path, history, cf_done)
                        j -= 1
                        a_pos = history[1 + j * 2]["pos"]
                        b_pos = history[2 + j * 2]["pos"]
                    break
                elif action == 'ok':
                    cf_done.append(j)
                    save_progress(progress_path, flac_path, history, cf_done)
                    j += 1
                    break
                else:
                    delta = {'+': 0.5, '-': -0.5, '++': 2.0, '--': -2.0}.get(action)
                    if delta is None:
                        try:
                            delta = parse_offset(action)
                        except ValueError:
                            print("  Ungültige Eingabe.")
                            continue
                    if active == 'a':
                        a_pos = max(0.0, a_pos + delta)
                        history[a_idx]["pos"] = a_pos
                    else:
                        b_pos = max(0.0, b_pos + delta)
                        history[b_idx]["pos"] = b_pos
                    save_progress(progress_path, flac_path, history, cf_done)

    # --- Phase 3: Schneiden + Zusammenfügen ---
    print("\n=== PHASE 3: SCHNEIDEN + ZUSAMMENFÜGEN ===")
    segments = get_segments(history, n_boundaries)
    n_seg = len(segments)
    temp_files: list[Path] = []
    to_cleanup: list[Path] = []

    print(f"  {n_seg} Segment(e) werden geschnitten...")
    for i, (start, end) in enumerate(segments):
        tmp = out_dir / f"_seg_{i:02d}.wav"
        print(f"  Segment {i+1}/{n_seg}: {fmt_time(start)} → {fmt_time(end)}")
        cut_segment(flac_path, tmp, start, end)
        temp_files.append(tmp)
        to_cleanup.append(tmp)

    print(f"  Verbinde {n_seg} Segment(e) mit {CROSSFADE_DURATION}s Crossfade...")
    current = temp_files[0]
    for i in range(1, n_seg):
        joined = out_dir / f"_joined_{i:02d}.wav"
        join_with_crossfade(current, temp_files[i], joined)
        to_cleanup.append(joined)
        current = joined

    out_flac = flac_path.parent / f"{flac_path.stem}_prepared.flac"
    print(f"  Speichere als FLAC: {out_flac.name}")
    subprocess.run([
        "ffmpeg", "-v", "quiet", "-y", "-i", str(current), str(out_flac)
    ], check=True)

    for f in to_cleanup:
        f.unlink(missing_ok=True)

    print(f"\n=== FERTIG ===")
    print(f"  Ausgabe: {out_flac}")
    print(f"  Original unverändert: {flac_path.name}")
    print(f"\nWeiter mit: python3 interactive_cutter.py \"{out_flac}\"")


if __name__ == "__main__":
    main()
