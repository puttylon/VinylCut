#!/usr/bin/env python3
import sys
import os
import json
import subprocess
from pathlib import Path

_LRC_PROVIDERS = [["lrclib"], ["musixmatch"], []]


def _load_env() -> dict:
    token_path = Path(__file__).parent / "genius_token"
    env = os.environ.copy()
    if token_path.exists():
        token = token_path.read_text().strip()
        if token:
            env["GENIUS_ACCESS_TOKEN"] = token
    return env


def fetch_lrc(query: str, lrc_path: Path, env: dict) -> bool:
    """Waterfall: lrclib → musixmatch → alle Provider. Gibt True zurück wenn gefunden."""
    for providers in _LRC_PROVIDERS:
        cmd = ["syncedlyrics", query, "-o", str(lrc_path)]
        if providers:
            cmd += ["-p"] + providers
        subprocess.run(cmd, capture_output=True, text=True, env=env)
        if lrc_path.exists():
            return True
    return False


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
    try:
        with open(target_dir / "release.json", "r", encoding="utf-8") as f:
            artist = json.load(f).get("artist", "")
    except Exception:
        pass

    env = _load_env()

    for flac in flac_files:
        lrc_file = flac.with_suffix(".lrc")
        title = flac.stem.split(" - ", 1)[-1] if " - " in flac.stem else flac.stem
        query = f"{artist} {title}".strip()
        print(f"Suche: {query}")
        try:
            found = fetch_lrc(query, lrc_file, env)
            print(f" {'✓' if found else '✗'} {flac.stem}.lrc")
        except FileNotFoundError:
            print("\n✗ Fehler: 'syncedlyrics' nicht gefunden.")
            break


if __name__ == "__main__":
    main()
