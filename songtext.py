#!/usr/bin/env python3
"""
songtext.py — LRC-Dateien für Audio-Dateien suchen und im selben Verzeichnis speichern.

Verwendung:
    songtext.py [--force] DATEI_ODER_VERZEICHNIS [...]

--force   Bereits vorhandene LRC-Dateien überschreiben.
"""

import argparse
import os
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import lyricsgenius
import requests
import syncedlyrics
from mutagen import File 

# <-- NEU: Unterstützte Formate
SUPPORTED_EXTS = {".flac", ".mp3", ".ogg", ".opus", ".m4a"}

# ---------------------------------------------------------------------------
# Lyrics-Provider (Waterfall)
# ---------------------------------------------------------------------------

def _fetch_lrclib(artist: str, title: str) -> str | None:
    try:
        url = "https://lrclib.net/api/get"
        params = {"artist_name": artist, "track_name": title}
        r = requests.get(url, params=params, timeout=5)
        if r.status_code == 200:
            data = r.json()
            text = data.get("syncedLyrics") or data.get("plainLyrics", "")
            return text.strip() or None
    except Exception as e:
        return None

_lyrics_ovh_disabled = False

def _fetch_lyrics_ovh(artist: str, title: str) -> str | None:
    global _lyrics_ovh_disabled
    if _lyrics_ovh_disabled:
        return None
    try:
        a = urllib.parse.quote(artist)
        t = urllib.parse.quote(title)
        r = requests.get(f"https://api.lyrics.ovh/v1/{a}/{t}", timeout=5)
        if r.status_code == 200:
            text = r.json().get("lyrics", "").strip()
            return text or None
    except requests.exceptions.Timeout as e:
        _lyrics_ovh_disabled = True
    except Exception as e:
        return None

def _fetch_syncedlyrics(artist: str, title: str) -> str | None:
    import io
    try:
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            lrc = syncedlyrics.search(f"{title} {artist}")
        finally:
            sys.stdout = old_stdout
        if lrc:
            return lrc.strip() or None
    except Exception as e:
        return None

_genius_disabled = False

def _fetch_genius(artist: str, title: str) -> str | None:
    global _genius_disabled
    if _genius_disabled:
        return None
    token = _load_genius_token()
    if not token:
        return None
    try:
        genius = lyricsgenius.Genius(token, remove_section_headers=True)
        song = genius.search_song(title, artist)
        if song and song.lyrics:
            return song.lyrics.strip()
    except Exception as e:
        if "invalid_token" in str(e) or "401" in str(e):
            _genius_disabled = True
        else:
            return None

PROVIDERS = [
    ("lrclib", _fetch_lrclib),
    ("lyrics.ovh", _fetch_lyrics_ovh),
    ("syncedlyrics", _fetch_syncedlyrics),
    ("genius", _fetch_genius),
]

def find_lyrics(artist: str, title: str) -> tuple[str, str] | tuple[None, None]:
    for name, fn in PROVIDERS:
        result = fn(artist, title)
        if result:
            return result, name
    return None, None

# ---------------------------------------------------------------------------
# Dateiverarbeitung
# ---------------------------------------------------------------------------

def process_file(path: str, force: bool, out_dir: str | None) -> str:
    """Verarbeitet eine Datei und gibt die fertige Statuszeile zurück.

    Es wird hier NICHT gedruckt – das übernimmt der Aufrufer gesammelt,
    damit die Ausgabe bei paralleler Verarbeitung nicht durcheinander gerät.
    """
    rel = os.path.relpath(path)
    name = os.path.basename(path)

    try:
        audio = File(path, easy=True)
        if audio is None:
            raise ValueError("Dateiformat nicht erkannt")
    except Exception as e:
        msg = f"FEHLER beim Öffnen der Audio-Datei: {e}"
        return f"{name}\n  {msg}"

    _SKIP_GENRES = {"speech", "podcast", "audiobook", "hörbuch", "spoken word",
                    "interview", "comedy", "radio", "noise", "soundscape"}

    genre = (audio.get("genre") or [""])[0].strip().lower()
    if any(skip in genre for skip in _SKIP_GENRES):
        msg = f"übersprungen (Genre: {genre})"
        return f"{name}\n  – {msg}"

    artist = (audio.get("artist") or [""])[0].strip()
    title  = (audio.get("title")  or [""])[0].strip()

    if not artist or not title:
        msg = "übersprungen (kein ARTIST/TITLE-Tag gefunden)"
        return f"{name}\n  – {msg}"

    stem = os.path.splitext(os.path.basename(path))[0]
    if out_dir:
        lrc_path = os.path.join(out_dir, stem + ".lrc")
    else:
        lrc_path = os.path.splitext(path)[0] + ".lrc"

    if os.path.exists(lrc_path) and not force:
        msg = f"übersprungen (LRC vorhanden)  ({artist} – {title})"
        return f"{name}\n  – {msg}"

    lyrics, provider = find_lyrics(artist, title)
    if not lyrics:
        msg = f"nicht gefunden  ({artist} – {title})"
        return f"{name}\n  ✗ {msg}"

    existing = os.path.exists(lrc_path)
    try:
        with open(lrc_path, "w", encoding="utf-8") as f:
            f.write(lyrics)
        action = "überschrieben" if existing else "gespeichert"
        msg = f"{action} via {provider}  ({artist} – {title})"
        return f"{name}\n  ✓ {msg}"
    except Exception as e:
        msg = f"FEHLER beim Speichern der LRC-Datei: {e}"
        return f"{name}\n  ✗ {msg}"

def collect_audio_files(paths: list[str]) -> list[str]:
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, fns in os.walk(p):
                dirs.sort()
                for fn in sorted(fns):
                    if os.path.splitext(fn)[1].lower() in SUPPORTED_EXTS:
                        files.append(os.path.join(root, fn))
        elif os.path.splitext(p)[1].lower() in SUPPORTED_EXTS:
            files.append(p)
        else:
            print(f"Ignoriert (nicht unterstützt/Verzeichnis): {p}")
    return files

# ---------------------------------------------------------------------------
# Einstiegspunkt
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="LRC-Dateien für Audio-Dateien aus dem Web herunterladen."
    )
    parser.add_argument("paths", nargs="+", metavar="PFAD",
                        help="Audio-Datei(en) oder Verzeichnis(se)")
    parser.add_argument("--force", action="store_true",
                        help="Bestehende LRC-Dateien überschreiben")
    parser.add_argument("--out-dir", metavar="VERZEICHNIS",
                        help="LRC-Dateien in dieses Verzeichnis schreiben statt neben die Quelldatei")
    parser.add_argument("--jobs", "-j", type=int, default=8, metavar="N",
                        help="Wie viele Dateien gleichzeitig verarbeiten (Standard: 8, 1 = nacheinander)")
    args = parser.parse_args()

    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)

    files = collect_audio_files(args.paths)
    if not files:
        print("Keine unterstützten Audio-Dateien gefunden.")
        sys.exit(1)

    jobs = max(1, args.jobs)
    print(f"{len(files)} Datei(en) verarbeiten (parallel: {jobs}) ...\n")

    done = 0
    total = len(files)
    if jobs == 1:
        for f in files:
            done += 1
            print(f"[{done}/{total}] {process_file(f, args.force, args.out_dir)}")
    else:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(process_file, f, args.force, args.out_dir): f
                       for f in files}
            for fut in as_completed(futures):
                done += 1
                try:
                    line = fut.result()
                except Exception as e:
                    # Sollte nicht passieren – process_file fängt selbst ab.
                    name = os.path.basename(futures[fut])
                    line = f"{name}\n  ✗ UNERWARTETER FEHLER: {e}"
                print(f"[{done}/{total}] {line}")

    print(f"\nFertig.")

if __name__ == "__main__":
    main()