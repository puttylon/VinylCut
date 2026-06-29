#!/usr/bin/env python3
import sys
import json
import subprocess
from pathlib import Path

__version__ = "1.1.0"

PLAY_DURATION_SEC = 3.0


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
    total = int(round(abs(seconds)))
    sign = "-" if seconds < 0 else ""
    return f"{sign}{total // 60}:{total % 60:02d}"


def show_status(i: int, data: dict, current_start: float, starts: list, last_gap: float) -> None:
    tracks = data["tracks"]
    n = len(tracks)
    print()
    if i > 0:
        prev = tracks[i - 1]
        laenge = current_start - starts[i - 1]
        soll = f"  (Soll: {fmt_dur(prev['dur_s'])})" if "dur_s" in prev else ""
        print(f"  Tracklänge [{i:02d}/{n:02d}] \"{prev['title']}\": {fmt_dur(laenge)}{soll}")
    print(f"  Startpunkt [{i+1:02d}/{n:02d}] \"{tracks[i]['title']}\": {fmt_dur(current_start)}")
    print("  [p]lay | [+] +0.5s | [-] -0.5s | [++] +2s | [--] -2s | [ok] bestätigen | Offset: Zahl in s oder ±m:ss")

def play_snippet(flac_path, start_time):
    subprocess.run(["ffplay", "-nodisp", "-autoexit", "-v", "quiet", "-ss", f"{start_time:.3f}", "-t", str(PLAY_DURATION_SEC), str(flac_path)])

def cut_and_tag(flac_path, out_file, track_num, title, artist, album, start_s, length_s, cover_path):
    comment = f"interactive_cutter.py v{__version__}"
    subprocess.run(["sox", str(flac_path), str(out_file), "trim", f"{start_s}s", f"{length_s}s"], capture_output=True)
    subprocess.run(["metaflac", "--remove-all-tags", f"--set-tag=ARTIST={artist}", f"--set-tag=ALBUM={album}", f"--set-tag=TITLE={title}", f"--set-tag=TRACKNUMBER={track_num}", f"--set-tag=COMMENT={comment}", str(out_file)], capture_output=True)
    if cover_path.exists():
        subprocess.run(["metaflac", f"--import-picture-from={cover_path}", str(out_file)], capture_output=True)

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(
            f"VinylCut Interactive Cutter v{__version__}\n"
            "\nNutzung:\n"
            "  python3 interactive_cutter.py \"Pfad/zur/Artist - Album.flac\"\n"
            "\nOptionen:\n"
            "  -h, --help       Diese Hilfe anzeigen\n"
            "  -V, --version    Versionsnummer ausgeben\n"
            "  --no-songtext    Songtext-Suche am Ende überspringen\n"
            "\nInteraktive Befehle während des Schneidens:\n"
            "  [p]        Snippet nochmal abspielen\n"
            "  [+] / [-]  Start ±0.5 s verschieben\n"
            "  [++]/[--]  Start ±2.0 s verschieben\n"
            "  [ok]       Startpunkt bestätigen\n"
            "  [Zahl]     Start um den angegebenen Wert (in s) verschieben"
        )
        sys.exit(0 if len(sys.argv) >= 2 else 1)

    if sys.argv[1] in ("-V", "--version"):
        print(f"interactive_cutter.py {__version__}")
        sys.exit(0)

    args = sys.argv[1:]
    no_songtext = "--no-songtext" in args
    args = [a for a in args if a != "--no-songtext"]
    if not args:
        print("Fehler: Kein FLAC-Pfad angegeben.")
        sys.exit(1)

    flac_path = Path(args[0]).resolve()
    out_dir = flac_path.parent / flac_path.stem
    
    # Metadaten holen
    subprocess.run(["python3", "metadata_fetcher.py", str(flac_path)])
    
    with open(out_dir / "release.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        
    # Robustes Laden der Längen
    for track in data["tracks"]:
        if "dur_s" not in track and "duration" in track:
            m, s = map(int, track["duration"].split(":"))
            track["dur_s"] = m * 60 + s
    
    probe = json.loads(subprocess.run(["ffprobe", "-v", "quiet", "-select_streams", "a:0", "-show_entries", "stream=sample_rate", "-of", "json", str(flac_path)], capture_output=True, text=True).stdout)
    sr = int(probe["streams"][0]["sample_rate"])
    
    starts, last_gap = [], 0.0
    for i, track in enumerate(data["tracks"]):
        # Berechnung des nächsten Starts basierend auf vorherigem Ende + gelernter Pause
        if i == 0:
            current_start = 0.0
        elif "dur_s" in data["tracks"][i-1]:
            current_start = starts[i-1] + data["tracks"][i-1]["dur_s"] + last_gap
        else:
            current_start = starts[i-1]
        
        while True:
            show_status(i, data, current_start, starts, last_gap)
            play_snippet(flac_path, current_start)
            action = input("  > ").strip().lower()
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
            elif action == 'ok':
                starts.append(current_start)
                if i > 0 and "dur_s" in data["tracks"][i-1]:
                    last_gap = current_start - (starts[i-1] + data["tracks"][i-1]["dur_s"])
                break
            else:
                try:
                    current_start = max(0.0, current_start + parse_offset(action))
                except ValueError:
                    print("Ungültige Eingabe.")
                                               
    n = len(data["tracks"])
    print("\n=== TRACKS EXPORTIEREN ===")
    for i, track in enumerate(data["tracks"]):
        print(f"  [{i+1:02d}/{n:02d}] {track['title']}...")
        start_smp = round(starts[i] * sr)
        if i < len(starts) - 1:
            len_smp = round((starts[i+1] - starts[i]) * sr)
        else:
            total_dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(flac_path)]))
            len_smp = round((total_dur * sr) - start_smp)
        cut_and_tag(flac_path, out_dir / f"{i+1:02d} - {track['title'].replace('/', '_')}.flac", i+1, track["title"], data["artist"], data["album"], start_smp, len_smp, out_dir / "cover.jpg")

    if no_songtext:
        print("\n(Songtexte übersprungen: --no-songtext)")
    else:
        print("\n=== SONGTEXTE LADEN ===")
        print(f"=== Suche in: {out_dir} ===")
        result = subprocess.run(["python3", "songtext.py", str(out_dir)], capture_output=True, text=True)
        if result.returncode == 0:
            print("✓ Songtexte erfolgreich verarbeitet.")
            if result.stdout:
                print(result.stdout)
        else:
            print("✗ Fehler beim Suchen der Songtexte:")
            print(result.stderr)
            print("\nBitte prüfe 'songtext.py' auf Import-Fehler.")

    print(f"\n=== ALLES FERTIG! ===")
    print(f"Dein fertiges Album liegt in: {out_dir}")

if __name__ == "__main__":
    main()