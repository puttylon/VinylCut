#!/usr/bin/env python3
import re
import sys
import os
import json
import subprocess
import tempfile
from pathlib import Path

_ALL_PROVIDERS = ["lrclib", "musixmatch", "netease", "megalobiz", "genius"]


def _load_env() -> dict:
    token_path = Path(__file__).parent / "genius_token"
    env = os.environ.copy()
    if token_path.exists():
        token = token_path.read_text().strip()
        if token:
            env["GENIUS_ACCESS_TOKEN"] = token
    return env


def _score_lrc(path: Path) -> tuple[int, int]:
    """(synchronisiert, Zeilenanzahl) — höher ist besser."""
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return (0, 0)
    synced = 1 if re.search(r"\[\d+:\d+\.\d+\]", content) else 0
    lines = sum(1 for ln in content.splitlines() if ln.strip())
    return (synced, lines)


def fetch_lrc(query: str, lrc_path: Path, env: dict) -> bool:
    """Alle Provider befragen, bestes Ergebnis (synchronisiert + meiste Zeilen) speichern."""
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

    best = max(candidates, key=_score_lrc)
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
