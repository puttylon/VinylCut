#!/usr/bin/env python3
"""Liest vorhandene .lrc-Dateien einer Bibliothek als Quelle "lokal" in den
fetch_songtext-Cache ein (siehe CACHE_DESIGN.md, Abschnitt "Lokale LRCs
einlesen"). Einmaliger Befehl, damit schon der erste Neuaufbau der Bibliothek
vom Cache profitiert — Dubletten zu Anbieter-Texten werden von cache_store
automatisch über den Fingerabdruck erkannt. Eingelesen wird nur, wenn GENAU
DIESER Track (per Audiodateiname als Schlüssel) in der .fetch_songtext.json
des Ordners verzeichnet ist UND dort als `"r": "ok"` (Text gefunden und
akzeptiert) markiert wurde — nicht schon, wenn irgendeine .fetch_songtext.json
im Ordner liegt.

Verwendung:
    python3 cache_seed.py /Musik/
    python3 cache_seed.py /Musik/ --db /pfad/zu/fetch_songtext_cache.db
"""

import argparse
import unicodedata
from pathlib import Path

from fetch_songtext import _clean_query_title, _load_cache, _read_audio_tags

_AUDIO_EXTENSIONS = (".flac", ".mp3", ".ogg", ".opus", ".m4a", ".aac", ".wav")

try:
    import cache_store
except ImportError:  # cache_store.py liegt evtl. (noch) nicht in diesem Worktree
    cache_store = None  # type: ignore


def _find_sibling_audio(lrc_path: Path) -> Path | None:
    """Sucht neben der .lrc eine gleichnamige Audiodatei (gleicher Stem)."""
    for ext in _AUDIO_EXTENSIONS:
        candidate = lrc_path.with_suffix(ext)
        if candidate.exists():
            return candidate
    return None


def _release_artist(folder: Path) -> str:
    """Artist aus release.json im Ordner, leer bei Fehler/Fehlen."""
    try:
        import json

        with open(folder / "release.json", encoding="utf-8") as f:
            data = json.load(f)
        return str(data.get("artist", "") or "")
    except Exception:
        return ""


def _resolve_keys(lrc_path: Path) -> tuple[str, str]:
    """Bestimmt (kuenstler_key, titel_key) für eine .lrc-Datei — exakt nach
    demselben Muster wie die Live-Abfrage in fetch_songtext.main(): Tags der
    gleichnamigen Audiodatei bevorzugt, sonst Dateiname/release.json/Ordner
    als Fallback. Titel wird wie bei der Live-Suche via _clean_query_title
    bereinigt, bevor normalize_key() zuschlägt.
    """
    audio = _find_sibling_audio(lrc_path)
    meta_artist, meta_title = "", ""
    if audio is not None:
        meta_artist, meta_title, _genre = _read_audio_tags(audio)

    title = meta_title or lrc_path.stem
    artist = meta_artist or _release_artist(lrc_path.parent) or lrc_path.parent.name

    cleaned_title = _clean_query_title(title)
    return cache_store.normalize_key(artist), cache_store.normalize_key(cleaned_title)


def seed(root: Path, db_path: Path) -> tuple[int, int]:
    """Liest alle *.lrc unter `root` (rekursiv) als Quelle "lokal" in den
    Cache ein. Gibt (eingelesen, übersprungen) zurück. Eine kaputte/leere
    Datei bricht den Lauf nicht ab, sie wird nur mitgezählt und übersprungen.
    Qualitätsfilter: pro Track geprüft, nicht pro Ordner — eine .lrc wird nur
    eingelesen, wenn GENAU DIESER Track (Schlüssel = Audiodateiname) in der
    .fetch_songtext.json des Ordners verzeichnet ist UND dort `"r" == "ok"`
    trägt (Text gefunden und akzeptiert). Ohne zugehörige Audiodatei fehlt
    die verlässliche Track-Identität für diesen Abgleich — dann wird
    übersprungen. Nur Tracks, die nachweislich durch fetch_songtext liefen
    und dort als verifiziert gelten, sind eine vertrauenswürdige
    "lokal"-Quelle.
    """
    if cache_store is None:
        raise RuntimeError(
            "cache_store-Modul nicht gefunden — cache_store.py muss neben "
            "cache_seed.py liegen."
        )

    conn = cache_store.open_cache(db_path)
    eingelesen = 0
    uebersprungen = 0

    for lrc_path in sorted(root.rglob("*.lrc")):
        audio = _find_sibling_audio(lrc_path)
        if audio is None:
            uebersprungen += 1
            continue

        cache = _load_cache(lrc_path.parent)
        cache_key = unicodedata.normalize("NFC", audio.name)
        entry = cache.get(cache_key)
        if entry is None or entry.get("r") != "ok":
            uebersprungen += 1
            continue

        try:
            content = lrc_path.read_text(encoding="utf-8")
        except Exception:
            uebersprungen += 1
            continue

        if not content.strip():
            uebersprungen += 1
            continue

        try:
            kuenstler_key, titel_key = _resolve_keys(lrc_path)
            cache_store.put_provider(
                conn, "lokal", kuenstler_key, titel_key, "treffer", content
            )
        except Exception as e:
            print(f"  Übersprungen ({lrc_path}): {e}")
            uebersprungen += 1
            continue

        eingelesen += 1
        print(f"\r  {eingelesen} eingelesen...", end="", flush=True)

    print()
    return eingelesen, uebersprungen


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Vorhandene .lrc-Dateien als Quelle 'lokal' in den fetch_songtext-Cache einlesen."
    )
    parser.add_argument(
        "path", help="Wurzelordner mit .lrc-Dateien (rekursiv durchsucht)"
    )
    parser.add_argument(
        "--db",
        default=str(Path(__file__).parent / "fetch_songtext_cache.db"),
        help="Pfad zur Cache-Datenbank (Default: fetch_songtext_cache.db neben diesem Skript)",
    )
    args = parser.parse_args()

    root = Path(args.path).expanduser().resolve()
    db_path = Path(args.db)

    eingelesen, uebersprungen = seed(root, db_path)
    print(f"Fertig: {eingelesen} LRCs eingelesen, {uebersprungen} übersprungen.")


if __name__ == "__main__":
    main()
