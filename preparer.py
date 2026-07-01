#!/usr/bin/env python3
import sys
import re
import json
import subprocess
from pathlib import Path

__version__ = "0.2.0"

SILENCE_NOISE_DB = -50
SILENCE_MIN_DURATION = 5.0
TRIM_NOISE_DB = -40
TRIM_MIN_DURATION = 0.5
DEFAULT_PLAY_DURATION = 3.0


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
    total = int(round(abs(seconds)))
    return f"{total // 60}:{total % 60:02d}"


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


def save_progress(progress_path: Path, flac_path: Path, history: list) -> None:
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump({"flac": str(flac_path), "history": history}, f, indent=2)


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


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(
            f"VinylCut Preparer v{__version__}\n"
            "\nNutzung:\n"
            "  python3 preparer.py \"Pfad/zur/Aufnahme.flac\"\n"
            "\nOptionen:\n"
            "  -h, --help     Diese Hilfe anzeigen\n"
            "  -V, --version  Versionsnummer ausgeben\n"
            "\nErkenne Seitengrenzen, setze Schnitt- und Trim-Punkte interaktiv.\n"
            "Ergebnis wird in preparer.json gespeichert (non-destruktiv).\n"
            "\nSteuerung:\n"
            "  [p]         Snippet nochmal abspielen\n"
            "  [+] / [-]   Punkt ±0,5 s verschieben\n"
            "  [++]/[--]   Punkt ±2,0 s verschieben\n"
            "  [ok]        Punkt bestätigen, weiter\n"
            "  [u]         Letzten Schritt rückgängig machen\n"
            "  [n]         Normton (220 Hz, 0,25 s) vor Snippet ein-/ausschalten\n"
            "  Zahl/±m:ss  Punkt um Offset verschieben"
        )
        sys.exit(0 if len(sys.argv) >= 2 else 1)

    if sys.argv[1] in ("-V", "--version"):
        print(f"preparer.py {__version__}")
        sys.exit(0)

    flac_path = Path(sys.argv[1]).resolve()
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
    silences = detect_silences(flac_path)
    steps = build_steps(music_start, music_end, silences)

    print(f"  Erkannt: {len(silences)} Seitengrenze(n), {len(steps)} Punkte zu setzen.")

    history: list = []

    if progress_path.exists():
        with open(progress_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if saved.get("flac") == str(flac_path):
            history = saved.get("history", [])
            n_done = len(history)
            ans = input(f"\n=== Fortschritt gefunden ({n_done}/{len(steps)} Punkte). Fortsetzen? [j/n] ===\n> ").strip().lower()
            if ans != "j":
                history = []
                progress_path.unlink()

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
                    save_progress(progress_path, flac_path, history)
                    i -= 1
                    break
            elif action == 'ok':
                if i < len(history):
                    history[i] = {"label": step["label"], "pos": current_pos}
                else:
                    history.append({"label": step["label"], "pos": current_pos})
                save_progress(progress_path, flac_path, history)
                i += 1
                break
            else:
                try:
                    current_pos = max(0.0, current_pos + parse_offset(action))
                except ValueError:
                    print("  Ungültige Eingabe.")

    print("\n=== ALLE PUNKTE GESETZT ===\n")
    trim_start = history[0]["pos"]
    trim_end = history[-1]["pos"]
    print(f"  Anfang:  {fmt_time(trim_start)}  ({trim_start:.1f}s)")
    for j, s in enumerate(silences):
        a = history[1 + j * 2]["pos"]
        b = history[2 + j * 2]["pos"]
        print(f"  Grenze {j+1}: A={fmt_time(a)} ({a:.1f}s)  →  B={fmt_time(b)} ({b:.1f}s)  |  Herausgeschnitten: {fmt_time(b - a)}")
    print(f"  Ende:    {fmt_time(trim_end)}  ({trim_end:.1f}s)")
    print(f"\nGespeichert in: {progress_path}")
    print("Weiter mit: preparer.py v0.3 — Crossfade-Vorschau")


if __name__ == "__main__":
    main()
