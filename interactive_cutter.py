#!/usr/bin/env python3
import sys
import json
import subprocess
from pathlib import Path

PLAY_DURATION_SEC = 3.0

def play_snippet(flac_path, start_time):
    subprocess.run(["ffplay", "-nodisp", "-autoexit", "-v", "quiet", "-ss", f"{start_time:.3f}", "-t", str(PLAY_DURATION_SEC), str(flac_path)])

def cut_and_tag(flac_path, out_file, track_num, title, artist, album, start_s, length_s, cover_path):
    subprocess.run(["sox", str(flac_path), str(out_file), "trim", f"{start_s}s", f"{length_s}s"], capture_output=True)
    subprocess.run(["metaflac", "--remove-all-tags", f"--set-tag=ARTIST={artist}", f"--set-tag=ALBUM={album}", f"--set-tag=TITLE={title}", f"--set-tag=TRACKNUMBER={track_num}", str(out_file)], capture_output=True)
    if cover_path.exists():
        subprocess.run(["metaflac", f"--import-picture-from={cover_path}", str(out_file)], capture_output=True)

def main():
    flac_path = Path(sys.argv[1]).resolve()
    out_dir = flac_path.parent / flac_path.stem
    
    subprocess.run(["python3", "metadata_fetcher.py", str(flac_path)])
    
    with open(out_dir / "release.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    
    probe = json.loads(subprocess.run(["ffprobe", "-v", "quiet", "-select_streams", "a:0", "-show_entries", "stream=sample_rate", "-of", "json", str(flac_path)], capture_output=True, text=True).stdout)
    sr = int(probe["streams"][0]["sample_rate"])
    
    starts, last_gap = [], 0.0
    for i, track in enumerate(data["tracks"]):
        current_start = (starts[i-1] + data["tracks"][i-1]["dur_s"] + last_gap) if i > 0 else 0.0
        print(f"\n--- Track {i+1:02d}: {track['title']} ---")
        while True:
            # Hier spielen wir das Snippet zur Kontrolle
            play_snippet(flac_path, current_start)
            
            action = input(f"Start: {current_start:.2f}s | [p]lay | [+] +0.5 | [-] -0.5 | [++] +2 | [--] -2 | [ok] | [Zahl]: ").strip().lower()
            
            if action == 'p':
                continue # play_snippet wird am Anfang der Schleife aufgerufen
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
                break
            else:
                try:
                    # Direkte Zahleneingabe für Feinkorrektur
                    current_start = max(0.0, current_start + float(action))
                except ValueError:
                    print("Ungültige Eingabe.")      
                          
    for i, track in enumerate(data["tracks"]):
        start_smp = round(starts[i] * sr)
        len_smp = round((starts[i+1] - starts[i]) * sr) if i < len(data["tracks"])-1 else round((float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(flac_path)]))*sr) - start_smp)
        cut_and_tag(flac_path, out_dir / f"{i+1:02d} - {track['title']}.flac", i+1, track["title"], data["artist"], data["album"], start_smp, len_smp, out_dir / "cover.jpg")

    subprocess.run(["python3", "songtext.py", str(out_dir)])
    print("✓ Fertig.")

if __name__ == "__main__":
    main()