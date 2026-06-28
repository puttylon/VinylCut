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
    if len(sys.argv) < 2:
        print("Nutzung: python3 interactive_cutter.py \"Pfad/zur/Artist - Album.flac\"")
        sys.exit(1)

    flac_path = Path(sys.argv[1]).resolve()
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
        prev_dur = data["tracks"][i-1].get("dur_s", 180.0) if i > 0 else 0.0
        current_start = (starts[i-1] + prev_dur + last_gap) if i > 0 else 0.0
        
        print(f"\n--- Track {i+1:02d}: {track['title']} ---")
        while True:
             play_snippet(flac_path, current_start)
            
             # Hier wurde die Anzeige um "Pause: {last_gap:.2f}s" erweitert
             prompt = (f"Track {i+1} | Start: {current_start:.2f}s | Pause: {last_gap:.2f}s | "
                       f"[p]lay | [+] +0.5 | [-] -0.5 | [++] +2 | [--] -2 | [ok] | [Zahl]: ")
            
             action = input(prompt).strip().lower()
            
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
                 # Berechnung der neuen Pause für den nächsten Track
                 if i > 0:
                     prev_dur = data["tracks"][i-1].get("dur_s", 180.0)
                     last_gap = current_start - (starts[i-1] + prev_dur)
                 break
             else:
                 try:
                     current_start = max(0.0, current_start + float(action))
                 except ValueError:
                     print("Ungültige Eingabe.")
                                               
    for i, track in enumerate(data["tracks"]):
        start_smp = round(starts[i] * sr)
        # Länge bis zum nächsten Track oder bis Ende der Datei
        if i < len(starts) - 1:
            len_smp = round((starts[i+1] - starts[i]) * sr)
        else:
            total_dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(flac_path)]))
            len_smp = round((total_dur * sr) - start_smp)
            
        cut_and_tag(flac_path, out_dir / f"{i+1:02d} - {track['title'].replace('/', '_')}.flac", i+1, track["title"], data["artist"], data["album"], start_smp, len_smp, out_dir / "cover.jpg")

    subprocess.run(["python3", "songtext.py", str(out_dir)])
    print("\n✓ Fertig.")

if __name__ == "__main__":
    main()