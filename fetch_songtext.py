#!/usr/bin/env python3
import re
import sys
import os
import json
import subprocess
import tempfile
from pathlib import Path

_ALL_PROVIDERS = ["lrclib", "musixmatch", "netease", "genius"]
# LRC-Timestamps enden oft vor dem Track-Ende (Instrumental-Outro → kein Text).
# Deshalb asymmetrische Toleranz: zu kurz ist normal, zu lang bedeutet falscher Song.
_LRC_TOO_SHORT_TOLERANCE = 0.40  # last_ts darf bis zu 40 % kürzer als der Track sein
_LRC_TOO_LONG_TOLERANCE = 0.10   # last_ts darf höchstens 10 % länger als der Track sein


def _load_env() -> dict:
    token_path = Path(__file__).parent / "genius_token"
    env = os.environ.copy()
    if token_path.exists():
        token = token_path.read_text().strip()
        if token:
            env["GENIUS_ACCESS_TOKEN"] = token
    return env


def _last_timestamp(content: str) -> float:
    """Letzten LRC-Timestamp in Sekunden auslesen, 0.0 wenn keiner gefunden."""
    matches = re.findall(r"\[(\d+):(\d+\.\d+)\]", content)
    if not matches:
        return 0.0
    m, s = matches[-1]
    return int(m) * 60 + float(s)


def _score_lrc(path: Path, expected_dur: float = 0.0) -> tuple[int, int, int]:
    """(nicht_disqualifiziert, synchronisiert, Zeilenanzahl) — höher ist besser."""
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return (0, 0, 0)
    synced = 1 if re.search(r"\[\d+:\d+\.\d+\]", content) else 0
    lines = sum(1 for ln in content.splitlines() if ln.strip())
    valid = 1
    if expected_dur > 0 and synced:
        last_ts = _last_timestamp(content)
        if last_ts > 0:
            ratio = (last_ts - expected_dur) / expected_dur
            if ratio > _LRC_TOO_LONG_TOLERANCE or ratio < -_LRC_TOO_SHORT_TOLERANCE:
                valid = 0
    return (valid, synced, lines)


def fetch_lrc(query: str, lrc_path: Path, env: dict, expected_dur: float = 0.0) -> bool:
    """Alle Provider befragen, bestes Ergebnis (Dauer-geprüft, synchronisiert, meiste Zeilen) speichern."""
    candidates: list[Path] = []
    for provider in _ALL_PROVIDERS:
        with tempfile.NamedTemporaryFile(suffix=".lrc", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        tmp_path.unlink()
        try:
            subprocess.run(
                ["syncedlyrics", query, "-o", str(tmp_path), "-p", provider],
                capture_output=True,
                text=True,
                env=env,
            )
        except FileNotFoundError:
            for p in candidates:
                p.unlink(missing_ok=True)
            raise
        if tmp_path.exists():
            candidates.append(tmp_path)

    if not candidates:
        return False

    best = max(candidates, key=lambda p: _score_lrc(p, expected_dur))
    lrc_path.write_bytes(best.read_bytes())
    for p in candidates:
        p.unlink(missing_ok=True)
    return True


def main():
    if len(sys.argv) < 2:
        sys.exit('Nutzung: python3 fetch_songtext.py "/Pfad/zum/Album/"')

    target_dir = Path(sys.argv[1]).resolve()
    flac_files = sorted(target_dir.glob("*.flac"))

    if not flac_files:
        print("Keine FLAC-Dateien gefunden.")
        return

    print(f"\n=== SONGTEXTE LADEN ({len(flac_files)} Dateien) ===")

    artist = ""
    tracks_by_title: dict = {}
    try:
        with open(target_dir / "release.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            artist = data.get("artist", "")
            for t in data.get("tracks", []):
                tracks_by_title[t["title"]] = t.get("dur_s", 0.0)
    except Exception:
        pass

    env = _load_env()

    for flac in flac_files:
        lrc_file = flac.with_suffix(".lrc")
        title = flac.stem.split(" - ", 1)[-1] if " - " in flac.stem else flac.stem
        query = f"{artist} {title}".strip()
        expected_dur = tracks_by_title.get(title, 0.0)
        print(f"Suche: {query}")
        try:
            found = fetch_lrc(query, lrc_file, env, expected_dur)
            print(f" {'✓' if found else '✗'} {flac.stem}.lrc")
        except FileNotFoundError:
            print("\n✗ Fehler: 'syncedlyrics' nicht gefunden.")
            break


if __name__ == "__main__":
    main()
