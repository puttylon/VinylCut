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

def find_release_with_cover(releases):
    """Iteriert über Kandidaten und gibt (mbid, cover_bytes) des ersten Treffers zurück."""
    for release in releases:
        mbid = release["id"]
        url = f"https://coverartarchive.org/release/{mbid}/front"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return mbid, response.read()
        except Exception:
            time.sleep(0.5)
    return None, None

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
    search_url = f"https://musicbrainz.org/ws/2/release/?query={urllib.parse.quote(query)}&limit=5&fmt=json"

    search_data = fetch_json(search_url)
    if not search_data or not search_data.get("releases"):
        sys.exit("Fehler: Kein Release gefunden.")

    time.sleep(1.1)

    # Schritt 1: Cover unter den Top-5-Treffern suchen
    mbid, cover_bytes = find_release_with_cover(search_data["releases"])

    # Schritt 2: Fallback über Release-Group (alle Editionen)
    if mbid is None:
        print("  → Kein Cover in Top-5, suche in Release-Group …")
        rg_mbid = search_data["releases"][0].get("release-group", {}).get("id")
        if rg_mbid:
            time.sleep(1.1)
            rg_url = f"https://musicbrainz.org/ws/2/release?release-group={rg_mbid}&limit=10&fmt=json"
            rg_data = fetch_json(rg_url)
            if rg_data:
                mbid, cover_bytes = find_release_with_cover(rg_data.get("releases", []))

    # Metadaten-MBID: bester Suchtreffer (unabhängig vom Cover)
    meta_mbid = mbid if mbid else search_data["releases"][0]["id"]

    # Cover speichern
    if cover_bytes:
        (out_dir / "cover.jpg").write_bytes(cover_bytes)
        print("✓ Cover gespeichert.")
    else:
        print("⚠ Kein Cover gefunden – weiter ohne.")

    time.sleep(1.1)

    # Trackliste laden
    details_data = fetch_json(
        f"https://musicbrainz.org/ws/2/release/{meta_mbid}?inc=recordings+media&fmt=json"
    )
    tracks = []
    for medium in details_data.get("media", []):
        for track in medium.get("tracks", []):
            dur = track.get("length") or (track.get("recording") or {}).get("length")
            tracks.append({
                "title": track.get("title", "Track"),
                "dur_s": (dur / 1000.0) if dur else 180.0
            })

    with open(out_dir / "release.json", "w", encoding="utf-8") as f:
        json.dump({"artist": artist, "album": album, "mbid": meta_mbid, "tracks": tracks}, f, indent=2)

    print("✓ Fertig.")

if __name__ == "__main__":
    main()