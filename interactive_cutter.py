#!/usr/bin/env python3
"""
interactive_cutter.py — Interaktiver Terminal-Cutter (Samplegenau / Gapless)

- Liest die Sample-Rate der Quelle aus.
- Konvertiert alle Zeitangaben in exakte Sample-Werte.
- Garantiert lückenlose Schnitte (Gapless) zwischen den Tracks.
"""

import sys
import json
import subprocess
from pathlib import Path

# --- KONSTANTEN ---
PLAY_DURATION_SEC = 3.0

def play_snippet(flac_path, start_time):
    print(f"  > Spiele {PLAY_DURATION_SEC}s ab {start_time:.2f}s...")
    cmd = [
        "ffplay", "-nodisp", "-autoexit", "-v", "quiet",
        "-ss", f"{start_time:.3f}", "-t", str(PLAY_DURATION_SEC), str(flac_path)
    ]
    subprocess.run(cmd)

def cut_and_tag(flac_path, out_dir, track_num, title, artist, album, start_sample, length_sample):
    out_file = out_dir / f"{track_num:02d} - {title.replace('/', '_')}.flac"
    
    # "s" am Ende signalisiert sox, dass es sich um Samples handelt
    subprocess.run([
        "sox", str(flac_path), str(out_file), 
        "trim", f"{start_sample}s", f"{length_sample}s"
    ], capture_output=True)
    
    subprocess.run([
        "metaflac", "--remove-all-tags",
        f"--set-tag=ARTIST={artist}",
        f"--set-tag=ALBUM={album}",
        f"--set-tag=TITLE={title}",
        f"--set-tag=TRACKNUMBER={track_num}",
        str(out_file)
    ], capture_output=True)

def main():
    if len(sys.argv) < 2:
        sys.exit("Nutzung: python3 interactive_cutter.py \"Pfad/zum/Album\"")
        
    target_dir = Path(sys.argv[1])
    release_json = target_dir / "release.json"
    
    if not release_json.exists():
        sys.exit(f"Fehler: release.json in {target_dir} nicht gefunden.")

    flac_files = list(target_dir.glob("*.flac"))
    if not flac_files:
        sys.exit(f"Fehler: Keine .flac Datei in {target_dir} gefunden.")
    flac_path = flac_files[0]

    with open(release_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    artist = data.get("artist", "Unknown")
    album = data.get("album", "Unknown")
    tracks = data.get("tracks", [])
    
    # --- Sample-Rate und Dauer auslesen ---
    # -select_streams a:0 stellt sicher, dass wir nicht versehentlich das Cover-Bild analysieren
    ffprobe_cmd = [
        "ffprobe", "-v", "quiet", "-select_streams", "a:0",
        "-show_entries", "format=duration:stream=sample_rate", 
        "-of", "json", str(flac_path)
    ]
    ffprobe_out = subprocess.run(ffprobe_cmd, capture_output=True, text=True).stdout
    probe_data = json.loads(ffprobe_out)
    
    total_dur = float(probe_data["format"]["duration"])
    sample_rate = int(probe_data["streams"][0]["sample_rate"])
    total_samples = round(total_dur * sample_rate)

    print(f"\n=== INTERAKTIVER CUTTER: {artist} - {album} ===")
    print(f"-> Audio-Analyse: {sample_rate} Hz Sample-Rate erkannt.")
    
    starts = []
    last_gap = 0.0
    
    # 1. Interaktive Ermittlung
    for i, track in enumerate(tracks):
        if i == 0:
            proposed_start = 0.0
        else:
            proposed_start = starts[i-1] + tracks[i-1]["dur_s"] + last_gap
            
        current_start = proposed_start

        print(f"\n--- Track {i+1:02d}: {track['title']} ---")
        play_snippet(flac_path, current_start)
        
        while True:
            print(f"Aktueller Startpunkt: {current_start:.2f}s (Angenommene Pause: {last_gap:.2f}s)")
            action = input(f"Aktion -> [p]lay | [+] +0.5s | [-] -0.5s | [++] +2s | [--] -2s | [ok] | [Zahl] z.B. 0.1 oder -0.2: ").strip().lower()
            
            if action == 'p':
                play_snippet(flac_path, current_start)
            elif action == '+':
                current_start += 0.5
                play_snippet(flac_path, current_start)
            elif action == '-':
                current_start = max(0.0, current_start - 0.5)
                play_snippet(flac_path, current_start)
            elif action == '++':
                current_start += 2.0
                play_snippet(flac_path, current_start)
            elif action == '--':
                current_start = max(0.0, current_start - 2.0)
                play_snippet(flac_path, current_start)
            elif action == 'ok':
                starts.append(current_start)
                if i > 0:
                    last_gap = current_start - (starts[i-1] + tracks[i-1]["dur_s"])
                break
            else:
                try:
                    shift = float(action)
                    current_start = max(0.0, current_start + shift)
                    play_snippet(flac_path, current_start)
                except ValueError:
                    print("Unbekannte Eingabe.")

    # 2. Schneiden und Taggen (Samplegenau)
    out_dir = target_dir / "cut_out"
    out_dir.mkdir(exist_ok=True)
    
    print("\n--- SCHNEIDEN & TAGGEN (Samplegenau) ---")
    for i, track in enumerate(tracks):
        # Startpunkt in Samples umrechnen
        start_sample = round(starts[i] * sample_rate)
        
        # Länge aus der Differenz zum exakten nächsten Start-Sample berechnen
        if i < len(tracks) - 1:
            next_start_sample = round(starts[i+1] * sample_rate)
            length_sample = next_start_sample - start_sample
        else:
            length_sample = total_samples - start_sample
            
        # Zur Anzeige im Terminal rechnen wir die Samples nochmal in Sekunden zurück
        display_length_s = length_sample / sample_rate
        print(f"Schreibe Track {i+1:02d}: {track['title']} ({display_length_s:.2f}s | {length_sample} Samples)...")
        
        cut_and_tag(flac_path, out_dir, i+1, track["title"], artist, album, start_sample, length_sample)

    print(f"\n✓ Fertig. Dateien liegen in: {out_dir}")

if __name__ == "__main__":
    main()