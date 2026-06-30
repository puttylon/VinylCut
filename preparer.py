#!/usr/bin/env python3
import sys
import re
import subprocess
from pathlib import Path

__version__ = "0.1.0"

SILENCE_NOISE_DB = -50
SILENCE_MIN_DURATION = 5.0
TRIM_NOISE_DB = -40
TRIM_MIN_DURATION = 0.5


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
    """Erkennt Rauschen am Anfang und Ende mit lockerem Schwellwert."""
    silences = detect_silences(flac_path, noise_db=TRIM_NOISE_DB, min_duration=TRIM_MIN_DURATION)
    music_start = silences[0]["end"] if silences and silences[0]["start"] < 1.0 else 0.0
    music_end = silences[-1]["start"] if silences and silences[-1]["end"] > total_duration - 10.0 else total_duration
    return music_start, music_end


def fmt_time(seconds: float) -> str:
    total = int(round(abs(seconds)))
    return f"{total // 60}:{total % 60:02d}"


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(
            f"VinylCut Preparer v{__version__}\n"
            "\nNutzung:\n"
            "  python3 preparer.py \"Pfad/zur/Aufnahme.flac\"\n"
            "\nOptionen:\n"
            "  -h, --help     Diese Hilfe anzeigen\n"
            "  -V, --version  Versionsnummer ausgeben\n"
            "\nErkennt lange Stillepausen (potenzielle Seitengrenzen) in einer Vinyl-Aufnahme\n"
            "und gibt vorgeschlagene Schnittbereiche (A=Ende Musik, B=Anfang Musik) aus.\n"
            f"Standard-Schwelle: {SILENCE_NOISE_DB} dB, Mindestdauer: {SILENCE_MIN_DURATION} s"
        )
        sys.exit(0 if len(sys.argv) >= 2 else 1)

    if sys.argv[1] in ("-V", "--version"):
        print(f"preparer.py {__version__}")
        sys.exit(0)

    flac_path = Path(sys.argv[1]).resolve()
    if not flac_path.exists():
        print(f"Fehler: Datei nicht gefunden: {flac_path}")
        sys.exit(1)

    print(f"\n=== VinylCut Preparer v{__version__} ===")
    print(f"Datei: {flac_path.name}")
    print("\nAnalysiere...")

    total_duration = float(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(flac_path)
    ]))

    music_start, music_end = detect_trim_points(flac_path, total_duration)
    silences = detect_silences(flac_path)

    print()
    print(f"  Anfang:  Rauschen bis  {fmt_time(music_start)}  ({music_start:.1f}s)  → Musik ab {fmt_time(music_start)}")
    print(f"  Ende:    Musik bis     {fmt_time(music_end)}  ({music_end:.1f}s)  → Rauschen ab {fmt_time(music_end)}")

    if not silences:
        print("\nKeine Seitengrenzen gefunden.")
        print(f"Tipp: Vinyl hat oft Oberflächenrauschen — evtl. Schwelle auf -45 dB anheben.")
        sys.exit(0)

    print(f"\n  {len(silences)} Seitengrenze(n) gefunden:\n")
    for i, s in enumerate(silences, 1):
        print(f"  Grenze {i}:")
        print(f"    A (Ende Musik):   {fmt_time(s['start'])}  ({s['start']:.1f}s)")
        print(f"    B (Anfang Musik): {fmt_time(s['end'])}  ({s['end']:.1f}s)")
        print(f"    Stille:           {fmt_time(s['duration'])}  ({s['duration']:.1f}s)")
        print()


if __name__ == "__main__":
    main()
