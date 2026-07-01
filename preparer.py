#!/usr/bin/env python3
import sys
import re
import json
import subprocess
from pathlib import Path

__version__ = "0.3.0"

SILENCE_NOISE_DB = -50
SILENCE_MIN_DURATION = 5.0
TRIM_NOISE_DB = -40
TRIM_MIN_DURATION = 0.5
DEFAULT_PLAY_DURATION = 3.0
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


def play_crossfade_preview(flac_path: Path, a_pos: float, b_pos: float,
                           preview_sec: float = DEFAULT_PLAY_DURATION,
                           crossfade_sec: float = CROSSFADE_DURATION) -> None:
    """Spielt Ende von Seite N + Crossfade + Anfang von Seite N+1 ab."""
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


def adjust_point_loop(flac_path: Path, label: str, pos: float, normton: bool, play_from_end: bool = False) -> float:
    """Sub-Loop zum Feinjustieren eines einzelnen Punktes (A oder B)."""
    play_dur = DEFAULT_PLAY_DURATION
    while True:
        print()
        print(f"  {label}: {fmt_time(pos)}  ({pos:.1f}s)")
        print(f"  [p]lay | [+] +0.5s | [-] -0.5s | [++] +2s | [--] -2s | [ok] fertig | Offset: Zahl oder ±m:ss")
        start = max(0.0, pos - play_dur) if play_from_end else pos
        if normton:
            play_snippet_with_tone(flac_path, start, play_dur)
        else:
            play_snippet(flac_path, start, play_dur)
        action = input("  > ").strip().lower()
        if action == 'p':
            continue
        elif action == '+':
            pos += 0.5
        elif action == '-':
            pos = max(0.0, pos - 0.5)
        elif action == '++':
            pos += 2.0
        elif action == '--':
            pos = max(0.0, pos - 2.0)
        elif action == 'ok':
            return pos
        else:
            try:
                pos = max(0.0, pos + parse_offset(action))
            except ValueError:
                print("  Ungültige Eingabe.")


def crossfade_review_loop(flac_path: Path, history: list, n_boundaries: int,
                          progress_path: Path, cf_done: list) -> list:
    """Phase 2: Crossfade-Vorschau und Feinschneiden für jede Grenze."""
    normton = False
    j = len(cf_done)

    while j < n_boundaries:
        a_idx = 1 + j * 2
        b_idx = 2 + j * 2
        a_pos = history[a_idx]["pos"]
        b_pos = history[b_idx]["pos"]

        while True:
            print()
            print(f"  === Crossfade-Vorschau: Grenze {j+1}/{n_boundaries} ===")
            print(f"  A (Ende Musik):   {fmt_time(a_pos)}  ({a_pos:.1f}s)")
            print(f"  B (Anfang Musik): {fmt_time(b_pos)}  ({b_pos:.1f}s)")
            print(f"  Herausgeschnitten: {fmt_time(b_pos - a_pos)}")
            normton_str = "EIN" if normton else "aus"
            print(f"  [p]lay | [a] A anpassen | [b] B anpassen | [ok] bestätigen | [u]ndo | [n]ormton: {normton_str}")
            play_crossfade_preview(flac_path, a_pos, b_pos)
            action = input("  > ").strip().lower()

            if action == 'p':
                continue
            elif action == 'n':
                normton = not normton
            elif action == 'a':
                a_pos = adjust_point_loop(flac_path, f"A — Grenze {j+1}", a_pos, normton, play_from_end=True)
                history[a_idx]["pos"] = a_pos
                save_progress(progress_path, Path(history[0]["pos"] if False else ""), history, cf_done)
            elif action == 'b':
                b_pos = adjust_point_loop(flac_path, f"B — Grenze {j+1}", b_pos, normton, play_from_end=False)
                history[b_idx]["pos"] = b_pos
                save_progress(progress_path, Path(history[0]["pos"] if False else ""), history, cf_done)
            elif action == 'u':
                if j == 0:
                    print("  Keine vorherige Grenze.")
                else:
                    cf_done.pop()
                    j -= 1
                    a_pos = history[1 + j * 2]["pos"]
                    b_pos = history[2 + j * 2]["pos"]
                break
            elif action == 'ok':
                history[a_idx]["pos"] = a_pos
                history[b_idx]["pos"] = b_pos
                cf_done.append(j)
                j += 1
                break

    return cf_done


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(
            f"VinylCut Preparer v{__version__}\n"
            "\nNutzung:\n"
            "  python3 preparer.py \"Pfad/zur/Aufnahme.flac\"\n"
            "\nOptionen:\n"
            "  -h, --help     Diese Hilfe anzeigen\n"
            "  -V, --version  Versionsnummer ausgeben\n"
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
            "  [a]         Punkt A feinjustieren\n"
            "  [b]         Punkt B feinjustieren\n"
            "  [ok]        Grenze bestätigen, weiter\n"
            "  [u]         Vorherige Grenze nochmal\n"
            "  [n]         Normton ein-/ausschalten"
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
    n_boundaries = len(silences)

    print(f"  Erkannt: {n_boundaries} Seitengrenze(n), {len(steps)} Punkte zu setzen.")

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
        print(f"Für jede der {n_boundaries} Grenzen: Übergang abhören, bei Bedarf A/B feinjustieren.\n")

        # save_progress braucht flac_path — wir wrappen es
        def _save(h, cf):
            save_progress(progress_path, flac_path, h, cf)

        # Undo in Phase 2 muss auch history speichern können
        normton_cf = False
        j = len(cf_done)
        while j < n_boundaries:
            a_idx = 1 + j * 2
            b_idx = 2 + j * 2
            a_pos = history[a_idx]["pos"]
            b_pos = history[b_idx]["pos"]

            while True:
                print()
                print(f"  === Crossfade-Vorschau: Grenze {j+1}/{n_boundaries} ===")
                print(f"  A (Ende Musik):    {fmt_time(a_pos)}  ({a_pos:.1f}s)")
                print(f"  B (Anfang Musik):  {fmt_time(b_pos)}  ({b_pos:.1f}s)")
                print(f"  Herausgeschnitten: {fmt_time(b_pos - a_pos)}")
                normton_str = "EIN" if normton_cf else "aus"
                print(f"  [p]lay | [a] A anpassen | [b] B anpassen | [ok] bestätigen | [u]ndo | [n]ormton: {normton_str}")
                play_crossfade_preview(flac_path, a_pos, b_pos)
                action = input("  > ").strip().lower()

                if action == 'p':
                    continue
                elif action == 'n':
                    normton_cf = not normton_cf
                elif action == 'a':
                    a_pos = adjust_point_loop(flac_path, f"A — Grenze {j+1}", a_pos, normton_cf, play_from_end=True)
                    history[a_idx]["pos"] = a_pos
                    _save(history, cf_done)
                elif action == 'b':
                    b_pos = adjust_point_loop(flac_path, f"B — Grenze {j+1}", b_pos, normton_cf, play_from_end=False)
                    history[b_idx]["pos"] = b_pos
                    _save(history, cf_done)
                elif action == 'u':
                    if j == 0:
                        print("  Keine vorherige Grenze.")
                    else:
                        cf_done.pop()
                        _save(history, cf_done)
                        j -= 1
                        a_pos = history[1 + j * 2]["pos"]
                        b_pos = history[2 + j * 2]["pos"]
                    break
                elif action == 'ok':
                    history[a_idx]["pos"] = a_pos
                    history[b_idx]["pos"] = b_pos
                    cf_done.append(j)
                    _save(history, cf_done)
                    j += 1
                    break

    # --- Zusammenfassung ---
    print("\n=== ALLE PUNKTE BESTÄTIGT ===\n")
    trim_start = history[0]["pos"]
    trim_end = history[-1]["pos"]
    print(f"  Anfang:  {fmt_time(trim_start)}  ({trim_start:.1f}s)")
    for j in range(n_boundaries):
        a = history[1 + j * 2]["pos"]
        b = history[2 + j * 2]["pos"]
        print(f"  Grenze {j+1}: A={fmt_time(a)} ({a:.1f}s)  →  B={fmt_time(b)} ({b:.1f}s)  |  Herausgeschnitten: {fmt_time(b - a)}")
    print(f"  Ende:    {fmt_time(trim_end)}  ({trim_end:.1f}s)")
    print(f"\nGespeichert in: {progress_path}")
    print("Weiter mit: preparer.py v0.4 — Schneiden + Zusammenfügen")


if __name__ == "__main__":
    main()
