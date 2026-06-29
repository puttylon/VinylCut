#!/usr/bin/env python3
import sys
import json
import urllib.request
import urllib.parse
import urllib.error
import time
import subprocess
import difflib
import unicodedata
import os
from pathlib import Path

DISCOGS_API = "https://api.discogs.com"
DISCOGS_UA  = "VinylCutter/2.0 (+https://localhost)"
DEFAULT_MAX_RELEASES = 25
DEFAULT_TRACK_LENGTH_S = 120.0

def _get_json(url, token=None, retries=3):
    headers = {"User-Agent": DISCOGS_UA}
    if token:
        url += ("&" if "?" in url else "?") + f"token={token}"
        
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                time.sleep(1.2)
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("Retry-After", 5)) + 1
                print(f"  [http] 429 Rate Limit. Warte {wait}s...")
                time.sleep(wait)
            elif e.code >= 500:
                print(f"  [http] {e.code} Server Error. Warte 3s...")
                time.sleep(3)
            else:
                return None
        except Exception:
            time.sleep(2)
    return None

def get_flac_duration(flac_path):
    try:
        return float(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(flac_path)
        ]))
    except Exception:
        return 0.0

def _norm_title(s):
    s = s or ""
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b).replace(a.upper(), b)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    for ch in "?!.,;:\"'`´’…/\\-–—()[]{}": s = s.replace(ch, " ")
    return " ".join(s.split())

def _similar(a, b):
    return difflib.SequenceMatcher(None, _norm_title(a), _norm_title(b)).ratio()

def _name_matches(a, b):
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb: return False
    return na == nb or na in nb or nb in na or _similar(a, b) >= 0.72

def _parse_discogs_duration(s):
    if not s: return None
    try:
        return float(sum(int(x) * 60 ** i for i, x in enumerate(reversed(s.strip().split(":")))))
    except ValueError:
        return None

def fmt_dur(sec):
    if not sec: return "?:??"
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"

def fetch_discogs_by_id(rel_id, token):
    full = _get_json(f"{DISCOGS_API}/releases/{rel_id}", token)
    if not full: return None
    tracks = [{"title": (t.get("title") or "Track").strip(), "dur_s": _parse_discogs_duration(t.get("duration", ""))} 
              for t in full.get("tracklist", []) if t.get("type_") in (None, "track")]
    if not tracks: return None
    fmts = ", ".join(f.get("name", "") for f in full.get("formats", []))
    return {
        "id": str(rel_id),
        "title": full.get("title", ""),
        "format": fmts,
        "is_vinyl": "vinyl" in fmts.lower(),
        "tracks": tracks,
        "cover_url": (full.get("images") or [{}])[0].get("uri"),
        "community_have": 0,
    }

def score_release(cand, flac_total, album):
    cand_durs = [t["dur_s"] for t in cand["tracks"]]
    have_durs = [d for d in cand_durs if d]
    
    title_pen = 0.0 if _norm_title(cand.get("title", "")) == _norm_title(album) else 100.0
    vinyl_pen = 0.0 if cand.get("is_vinyl") else 25.0
    missing_pen = 8.0 * (len(cand["tracks"]) - len(have_durs))
    
    cat_total = sum(have_durs)
    dur_pen = 0.0
    if flac_total and cat_total:
        ratio = cat_total / flac_total
        dev = (ratio - 1.0) if ratio >= 1.0 else (1.0 - ratio)
        tol = 0.05 if ratio >= 1.0 else 0.12
        if dev > tol:
            dur_pen = (dev - tol) * 400.0
            
    return title_pen + vinyl_pen + dur_pen + missing_pen

def main():
    if len(sys.argv) < 2:
        sys.exit("Nutzung: python3 metadata_fetcher.py \"Pfad/zur/Artist - Album.flac\"")

    flac_path = Path(sys.argv[1]).resolve()
    stem = flac_path.stem
    artist, album = stem.split(" - ", 1) if " - " in stem else ("Unknown", stem)
    out_dir = flac_path.parent / stem
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== ROBUSTER METADATEN FETCHER: {artist} - {album} ===")
    token = os.environ.get("DISCOGS_TOKEN", "")
    flac_total = get_flac_duration(flac_path)
    print(f"Dateidauer gemessen: {flac_total/60:.1f} min")

    results = []
    for page in range(1, 3):
        query = urllib.parse.quote(f"{artist} {album}")
        data = _get_json(f"{DISCOGS_API}/database/search?type=release&q={query}&per_page=50&page={page}", token)
        if not data: break
        results += data.get("results", [])
        if page >= data.get("pagination", {}).get("pages", 1): break

    plausible = [r for r in results if _name_matches(r.get("title", "").split(" - ")[-1], album)]
    plausible.sort(key=lambda r: (0 if "vinyl" in " ".join(r.get("format", [])).lower() else 1, -r.get("community", {}).get("have", 0)))

    if not plausible: sys.exit("Fehler: Kein passendes Release gefunden.")

    best_cand = None
    best_score = 9999.0
    all_cands = []

    for i, res in enumerate(plausible[:DEFAULT_MAX_RELEASES], 1):
        rel_id = res.get("id")
        print(f"  > Prüfe Pressung {i}/{min(len(plausible), DEFAULT_MAX_RELEASES)} (ID: {rel_id})...")

        full = _get_json(f"{DISCOGS_API}/releases/{rel_id}", token)
        if not full: continue

        tracks = [{"title": (t.get("title") or "Track").strip(), "dur_s": _parse_discogs_duration(t.get("duration", ""))}
                  for t in full.get("tracklist", []) if t.get("type_") in (None, "track")]

        if not tracks: continue

        fmts = ", ".join(f.get("name", "") for f in full.get("formats", []))
        cand = {
            "id": str(rel_id),
            "title": full.get("title", ""),
            "format": fmts,
            "is_vinyl": "vinyl" in fmts.lower(),
            "tracks": tracks,
            "cover_url": (full.get("images") or [{}])[0].get("uri"),
            "community_have": res.get("community", {}).get("have", 0) if isinstance(res.get("community"), dict) else 0,
        }
        all_cands.append(cand)

        score = score_release(cand, flac_total, album)
        
        if score < best_score:
            best_score = score
            best_cand = cand
            
        if score <= 5.0 and cand["is_vinyl"]:
            print(f"  ✓ Perfekter Match gefunden (Score: {score:.1f}). Breche weitere Suche ab.")
            break

    if not best_cand: sys.exit("Fehler: Konnte keine validen Tracks laden.")

    # --- INTERAKTIVE SCHLEIFE ---
    current_cand = best_cand
    while True:
        print(f"\n--- VORSCHLAG: {current_cand['title']} ---")
        print(f"Format: {current_cand['format']}")
        print(f"Quelle: https://www.discogs.com/release/{current_cand['id']}")
        print("Tracks:")
        for idx, t in enumerate(current_cand["tracks"], 1):
            print(f"  {idx:02d}. {t['title']} ({fmt_dur(t.get('dur_s'))})")
        
        ans = input("\n[Enter] Akzeptieren, oder Discogs-ID eingeben für Override: ").strip()
        if not ans:
            break
        
        print(f"Lade Discogs-ID {ans}...")
        new_cand = fetch_discogs_by_id(ans, token)
        if new_cand:
            current_cand = new_cand
            if not any(c["id"] == new_cand["id"] for c in all_cands):
                all_cands.append(new_cand)
        else:
            print("✗ Fehler: ID nicht gefunden oder enthält keine validen Tracks. Bitte erneut versuchen.")

    best_cand = current_cand
    # ----------------------------

    for t in best_cand["tracks"]:
        if not t["dur_s"]: t["dur_s"] = DEFAULT_TRACK_LENGTH_S

    with open(out_dir / "release.json", "w", encoding="utf-8") as f:
        json.dump({"artist": artist, "album": album, "release_id": best_cand["id"], "tracks": best_cand["tracks"]}, f, indent=2, ensure_ascii=False)

    cover_sorted = sorted(all_cands, key=lambda c: (0 if c["is_vinyl"] else 1, -c["community_have"]))
    for c in cover_sorted:
        if not c.get("cover_url"):
            continue
        try:
            with urllib.request.urlopen(urllib.request.Request(c["cover_url"], headers={"User-Agent": DISCOGS_UA}), timeout=20) as r:
                (out_dir / "cover.jpg").write_bytes(r.read())
                print(f"✓ Cover gespeichert (von '{c['title']}', {c['community_have']} Besitzer).")
            break
        except Exception:
            continue
    else:
        print("⚠ Cover-Download fehlgeschlagen.")

if __name__ == "__main__":
    main()