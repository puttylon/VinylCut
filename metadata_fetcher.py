#!/usr/bin/env python3
import sys
import json
import urllib.request
import urllib.parse
from pathlib import Path
import time
import re

USER_AGENT = "VinylCutter/2.0 ( private@localhost )"

def fetch_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [Netzwerk-Fehler] {e}")
        return None

def fetch_json(url):
    txt = fetch_text(url)
    if not txt:
        return None
    try:
        return json.loads(txt)
    except Exception as e:
        print(f"  [JSON-Fehler] {e}")
        return None

def download_file(url, dest_path):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            dest_path.write_bytes(response.read())
        return True
    except Exception as e:
        print(f"  [Cover-Fehler] {e}")
        return False

def pick_best_release(results, artist, album):
    artist_l = artist.strip().lower()
    album_l = album.strip().lower()

    def score(r):
        s = 0
        title = (r.get("title") or "").strip().lower()
        fmt = " ".join(r.get("format") or []).lower()
        community = r.get("community", {}) or {}
        have = community.get("have", 0)
        want = community.get("want", 0)

        if title == album_l:
            s += 50
        if artist_l in (r.get("artist") or "").strip().lower():
            s += 25
        if "album" in fmt:
            s += 10
        if have:
            s += min(have // 5, 20)
        if want:
            s += min(want // 20, 10)
        if any(x in title for x in ["deluxe", "remaster", "expanded", "live", "compilation", "best of"]):
            s -= 20
        return s

    return sorted(results, key=score, reverse=True)[0] if results else None

def extract_og_image(html):
    m = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', html, re.I)
    return m.group(1) if m else None

def main():
    if len(sys.argv) < 2:
        sys.exit('Nutzung: python3 metadata_fetcher.py "Pfad/zur/Artist - Album.flac"')

    flac_path = Path(sys.argv[1]).resolve()
    if not flac_path.exists():
        sys.exit(f"Fehler: Datei nicht gefunden: {flac_path}")

    stem = flac_path.stem
    artist, album = stem.split(" - ", 1) if " - " in stem else ("Unknown", stem)
    out_dir = flac_path.parent / stem
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== METADATEN FETCHER: {artist} - {album} ===")

    query = urllib.parse.quote(f"{artist} {album}")
    search_url = f"https://api.discogs.com/database/search?type=release&q={query}&per_page=10&page=1"
    search_data = fetch_json(search_url)
    if not search_data or not search_data.get("results"):
        sys.exit("Fehler: Kein Release gefunden.")

    best = pick_best_release(search_data["results"], artist, album)
    if not best:
        sys.exit("Fehler: Kein passender Release-Kandidat gefunden.")

    release_url = best.get("resource_url")
    if not release_url:
        sys.exit("Fehler: Kein resource_url im Treffer.")

    time.sleep(1.0)
    release_data = fetch_json(release_url)
    if not release_data:
        sys.exit("Fehler: Release-Details konnten nicht geladen werden.")

    tracks = []
    for track in release_data.get("tracklist", []):
        if track.get("type_") != "track":
            continue
        tracks.append({
            "title": track.get("title", "Track"),
            "duration": track.get("duration") or ""
        })

    cover_url = None
    images = release_data.get("images") or []
    for img in images:
        if img.get("type") == "primary" and img.get("uri"):
            cover_url = img["uri"]
            break
    if not cover_url and images:
        cover_url = images[0].get("uri")

    if not cover_url:
        html = fetch_text(best.get("uri")) if best.get("uri") else None
        if html:
            cover_url = extract_og_image(html)

    with open(out_dir / "release.json", "w", encoding="utf-8") as f:
        json.dump({
            "artist": artist,
            "album": album,
            "release_id": release_data.get("id"),
            "title": release_data.get("title"),
            "year": release_data.get("year"),
            "country": release_data.get("country"),
            "tracks": tracks
        }, f, indent=2, ensure_ascii=False)

    if cover_url:
        if download_file(cover_url, out_dir / "cover.jpg"):
            print("✓ Cover gespeichert.")
        else:
            print("⚠ Cover-Download fehlgeschlagen.")
    else:
        print("⚠ Kein Cover gefunden.")

    print("✓ Fertig.")

if __name__ == "__main__":
    main()