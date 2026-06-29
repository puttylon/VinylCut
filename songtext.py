#!/usr/bin/env python3
import sys
import os
import json
import subprocess
from pathlib import Path

def main():
    if len(sys.argv) < 2:
        sys.exit("Nutzung: python3 songtext.py \"/Pfad/zum/Album/\"")

    target_dir = Path(sys.argv[1]).resolve()
    flac_files = sorted(list(target_dir.glob("*.flac")))
    
    if not flac_files:
        print("Keine FLAC-Dateien gefunden.")
        return

    print(f"\n=== SONGTEXTE LADEN (Suche in {len(flac_files)} Dateien) ===")
    
    # 1. Künstlername aus der release.json laden (zuverlässiger als Dateipfade)
    artist = ""
    try:
        with open(target_dir / "release.json", "r", encoding="utf-8") as f:
            artist = json.load(f).get("artist", "")
    except Exception:
        pass

    # 2. Genius Token einlesen
    token_path = Path(__file__).parent / "genius_token"
    env = os.environ.copy()
    if token_path.exists():
        token = token_path.read_text().strip()
        if token:
            env["GENIUS_ACCESS_TOKEN"] = token
            print("✓ Genius Token geladen.")
    else:
        print("ℹ Datei 'genius_token' nicht gefunden.")

    for flac in flac_files:
        lrc_file = flac.with_suffix(".lrc")
        
        # Titel aus Dateinamen extrahieren (aus "01 - Goodbye" wird "Goodbye")
        title = flac.stem.split(" - ", 1)[-1] if " - " in flac.stem else flac.stem
        
        # Expliziter Suchbegriff für maximale Präzision
        search_query = f"{artist} {title}".strip()
        
        print(f"Suche Lyrics für: {search_query}")
        
        try:
            # Suchbegriff statt Dateipfad übergeben
            subprocess.run(
                ["syncedlyrics", search_query, "-o", str(lrc_file)], 
                capture_output=True, 
                text=True, 
                env=env
            )
            
            if lrc_file.exists():
                print(f" ✓ {flac.stem}.lrc gespeichert.")
            else:
                print(f" ✗ Keine Lyrics gefunden.")
                
        except FileNotFoundError:
            print("\n✗ Fehler: 'syncedlyrics' nicht gefunden.")
            break

if __name__ == "__main__":
    main()