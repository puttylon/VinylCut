#!/usr/bin/env python3
import sys
import json
import urllib.request
import urllib.parse
from pathlib import Path
import time

USER_AGENT = "VinylCutter/2.0 ( private@localhost )"

def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read())
    except Exception as e:
        print(f"  [Netzwerk-Fehler] {e}")
        return None

def download_cover(mbid, dest_path):
    url = f"https://coverartarchive.org/release/{mbid}/front"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            dest_path.write_bytes(response.read())
            return True
    except Exception:
        return False

def main():
    if len(sys.argv) < 2:
        sys.exit("Nutzung: python3 metadata_fetcher.py \"Pfad/zur/Artist - Album.flac\"")
    
    flac_path = Path(sys.argv[1]).resolve()
    if not flac_path.exists():
        sys.exit(f"Fehler: Datei nicht gefunden: {flac_path}")
        
    stem = flac_path.stem
    artist, album = stem.split(" - ", 1) if " - " in stem else ("Unknown", stem)
    out_dir = flac_path.parent / stem
    out_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"\n=== METADATEN FETCHER: {artist} - {album} ===")
    
    query = f'artist:"{artist}" AND release:"{album}"'
    search_url = f"https://musicbrainz.org/ws/2/release/?query={urllib.parse.quote(query)}&limit=1&fmt=json"
    
    search_data = fetch_json(search_url)
    if not search_data or not search_data.get("releases"):
        sys.exit("Fehler: Kein Release gefunden.")
        
    mbid = search_data["releases"][0]["id"]
    time.sleep(1.1)
    
    details_data = fetch_json(f"https://musicbrainz.org/ws/2/release/{mbid}?inc=recordings+media&fmt=json")
    tracks = []
    for medium in details_data.get("media", []):
        for track in medium.get("tracks", []):
            dur = track.get("length") or (track.get("recording") or {}).get("length")
            tracks.append({"title": track.get("title", "Track"), "dur_s": (dur/1000.0) if dur else 180.0})
            
    with open(out_dir / "release.json", "w", encoding="utf-8") as f:
        json.dump({"artist": artist, "album": album, "mbid": mbid, "tracks": tracks}, f, indent=2)
    
    download_cover(mbid, out_dir / "cover.jpg")
    print("✓ Fertig.")

if __name__ == "__main__":
    main()