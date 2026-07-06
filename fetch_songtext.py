#!/usr/bin/env python3
import re
import os
import json
import subprocess
import tempfile
from pathlib import Path

__version__ = "1.2.5"

_ALL_PROVIDERS = ["lrclib", "musixmatch", "netease", "genius"]

# LRC-Timestamps enden oft vor dem Track-Ende (Instrumental-Outro → kein Text).
# Asymmetrische Toleranz: zu kurz ist normal, zu lang bedeutet falscher Song.
_LRC_TOO_SHORT_TOLERANCE = 0.40  # last_ts darf bis zu 40 % kürzer als der Track sein
_LRC_TOO_LONG_TOLERANCE = 0.10  # last_ts darf höchstens 10 % länger als der Track sein

# Whisper: erst ab Mindest-Overlap gilt eine LRC als verifiziert.
_WHISPER_MIN_OVERLAP = 0.12
_WHISPER_MODEL = "base"
_WHISPER_CONTEXT_SEC = 30  # Sekunden Audio die transkribiert werden
_WHISPER_PRE_ROLL = 0.0  # direkt beim ersten LRC-Timestamp starten

_whisper_model = None  # lazy singleton — einmal laden, für alle Tracks wiederverwenden
_last_whisper_score: float = 0.0  # letzter Overlap-Score, für Ausgabe in main()


def _load_env() -> dict:
    token_path = Path(__file__).parent / "genius_token"
    env = os.environ.copy()
    if token_path.exists():
        token = token_path.read_text().strip()
        if token:
            env["GENIUS_ACCESS_TOKEN"] = token
    return env


def _last_timestamp(content: str) -> float:
    """Letzten LRC-Timestamp in Sekunden, 0.0 wenn keiner gefunden."""
    matches = re.findall(r"\[(\d+):(\d+\.\d+)\]", content)
    if not matches:
        return 0.0
    m, s = matches[-1]
    return int(m) * 60 + float(s)


def _first_timestamp(content: str) -> float:
    """Ersten Lyric-Timestamp in Sekunden; überspringt Metadaten-Zeilen (z.B. '作词 : …')."""
    for line in content.splitlines():
        match = re.match(r"\[(\d+):(\d+\.\d+)\](.*)", line)
        if not match:
            continue
        text = match.group(3).strip()
        if not text or " : " in text:
            continue
        return int(match.group(1)) * 60 + float(match.group(2))
    return 0.0


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


def _get_whisper_model():
    """Lädt das Whisper-Modell beim ersten Aufruf, gibt None zurück wenn nicht installiert."""
    global _whisper_model
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            return None
        print(f"   Lade Whisper-Modell ({_WHISPER_MODEL})...", end=" ", flush=True)
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        # int8: schneller auf CPU, vermeidet float16-Warnung von ctranslate2
        _whisper_model = WhisperModel(_WHISPER_MODEL, device="auto", compute_type="int8")
        print("bereit.")
    return _whisper_model


def _extract_lrc_words(content: str, max_lines: int = 15) -> list[str]:
    """Ersten max_lines Textzeilen einer LRC als Wortliste (Unicode-Buchstaben)."""
    words: list[str] = []
    for line in content.splitlines():
        if re.match(r"\[[a-z]+:", line.lower()):
            continue  # Metadaten-Tags überspringen
        text = re.sub(r"\[\d+:\d+\.\d+\]", "", line).strip()
        if text:
            words.extend(re.findall(r"[^\W\d_]+", text.lower()))
            if len(words) >= max_lines * 8:
                break
    return words


def _word_overlap(a: list[str], b: list[str]) -> float:
    """Jaccard-Ähnlichkeit zweier Wortmengen."""
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb)


def _transcribe(flac_path: Path, start: float) -> list[str]:
    """Transkribiert _WHISPER_CONTEXT_SEC Sekunden ab start, gibt Wortliste zurück."""
    model = _get_whisper_model()
    if model is None:
        return []
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_wav = Path(tmp.name)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(flac_path), "-ss", str(start),
             "-t", str(_WHISPER_CONTEXT_SEC), "-ar", "16000", "-ac", "1", str(tmp_wav)],
            capture_output=True,
        )
        segments, _ = model.transcribe(str(tmp_wav), beam_size=1)
        return re.findall(r"[^\W\d_]+", " ".join(s.text for s in segments).lower())
    except Exception:
        return []
    finally:
        tmp_wav.unlink(missing_ok=True)


def _whisper_best(flac_path: Path, candidates: list[Path]) -> tuple[Path, float] | None:
    """Gibt (bester Kandidat, Overlap) zurück.

    Jeder Kandidat wird gegen die Transkription seines eigenen Start-Offsets
    gescored. Gleiche Offsets (±3 s) teilen sich eine Transkription.
    """
    if _get_whisper_model() is None:
        return None

    # Start-Offset pro Kandidat bestimmen
    candidate_starts: list[tuple[Path, float]] = []
    for p in candidates:
        try:
            ft = _first_timestamp(p.read_text(encoding="utf-8"))
            start = max(0.0, (ft if ft > 0 else 0.0) - _WHISPER_PRE_ROLL)
        except Exception:
            start = 0.0
        candidate_starts.append((p, start))

    # Transkriptionen cachen: key = start auf 3 s gerundet
    transcript_cache: dict[int, list[str]] = {}
    for _, start in candidate_starts:
        key = round(start / 3)
        if key not in transcript_cache:
            transcript_cache[key] = _transcribe(flac_path, start)

    best_path: Path | None = None
    best_score = 0.0
    for p, start in candidate_starts:
        words = transcript_cache.get(round(start / 3), [])
        if not words:
            continue
        try:
            score = _word_overlap(words, _extract_lrc_words(p.read_text(encoding="utf-8")))
        except Exception:
            score = 0.0
        if score > best_score:
            best_score = score
            best_path = p

    global _last_whisper_score
    _last_whisper_score = best_score
    return (best_path, best_score) if best_path else None


def fetch_lrc(
    query: str,
    lrc_path: Path,
    env: dict,
    expected_dur: float = 0.0,
    flac_path: Path | None = None,
    existing_lrc: Path | None = None,
) -> bool:
    """Alle Provider befragen, bestes Ergebnis via Whisper oder Dauer-Scoring wählen.

    Mit flac_path: Whisper bewertet alle Kandidaten inkl. existing_lrc (falls vorhanden).
    Die vorhandene LRC tritt gleichberechtigt an — nur wer den höchsten Overlap hat gewinnt.
    Liegt der beste Overlap unter _WHISPER_MIN_OVERLAP wird nichts gespeichert.
    Ohne flac_path (oder faster-whisper nicht installiert): Fallback auf Dauer-Scoring.
    """
    global _last_whisper_score
    _last_whisper_score = 0.0  # zurücksetzen, damit kein alter Score vom Vorgänger-Track angezeigt wird
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

    # Vorhandene LRC als Kandidat einbeziehen (wird nicht gelöscht)
    all_candidates = candidates + ([existing_lrc] if existing_lrc and existing_lrc.exists() else [])

    if not all_candidates:
        return False

    if flac_path and flac_path.exists():
        result = _whisper_best(flac_path, all_candidates)
        best_content = (
            result[0].read_bytes()
            if result and result[1] >= _WHISPER_MIN_OVERLAP
            else None
        )
    else:
        best = max(all_candidates, key=lambda p: _score_lrc(p, expected_dur))
        best_content = best.read_bytes()

    for p in candidates:  # nur temp-Dateien löschen, nie existing_lrc
        p.unlink(missing_ok=True)

    if best_content is None:
        return False
    lrc_path.write_bytes(best_content)
    return True


def _load_release(folder: Path) -> tuple[str, dict]:
    """Artist und {Titel: dur_s} aus release.json lesen."""
    try:
        with open(folder / "release.json", encoding="utf-8") as f:
            data = json.load(f)
        artist = data.get("artist", "")
        tracks = {t["title"]: t.get("dur_s", 0.0) for t in data.get("tracks", [])}
        return artist, tracks
    except Exception:
        return "", {}


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Songtexte laden via syncedlyrics + Whisper-Verifikation"
    )
    parser.add_argument("path", help="Albumordner (oder Wurzelordner mit --recursive)")
    parser.add_argument(
        "--recursive",
        "-r",
        action="store_true",
        help="Alle Unterordner rekursiv durchsuchen und LRCs erneuern",
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"fetch_songtext {__version__}"
    )
    args = parser.parse_args()

    root = Path(args.path).resolve()
    flac_files = sorted(root.rglob("*.flac") if args.recursive else root.glob("*.flac"))

    if not flac_files:
        print("Keine FLAC-Dateien gefunden.")
        return

    mode = "rekursiv" if args.recursive else "Album"
    print(f"\n=== SONGTEXTE ({mode}, {len(flac_files)} Dateien) ===\n")

    env = _load_env()
    updated = skipped = not_found = errors = 0

    current_parent: Path | None = None
    artist = ""
    tracks_by_title: dict = {}

    for flac in flac_files:
        lrc_path = flac.with_suffix(".lrc")

        # Im normalen Modus: vorhandene LRCs nicht anfassen
        if not args.recursive and lrc_path.exists():
            skipped += 1
            continue

        # release.json nur neu laden wenn Albumordner wechselt
        if flac.parent != current_parent:
            current_parent = flac.parent
            artist, tracks_by_title = _load_release(flac.parent)

        title = flac.stem.split(" - ", 1)[-1] if " - " in flac.stem else flac.stem
        query = f"{artist} {title}".strip()
        expected_dur = tracks_by_title.get(title, 0.0)

        if args.recursive:
            print(f"── {flac.parent.name} / {flac.stem}")
            # In temp-Datei schreiben, danach mit vorhandener LRC vergleichen
            with tempfile.NamedTemporaryFile(suffix=".lrc", delete=False) as tmp:
                dest = Path(tmp.name)
            dest.unlink()
        else:
            print(f"Suche: {query}")
            dest = lrc_path

        try:
            found = fetch_lrc(query, dest, env, expected_dur, flac_path=flac, existing_lrc=lrc_path)
        except FileNotFoundError:
            print("   ✗ syncedlyrics nicht gefunden — Abbruch.")
            dest.unlink(missing_ok=True)
            errors += 1
            break

        if args.recursive:
            score_str = f"  ({_last_whisper_score:.0%})" if _last_whisper_score > 0 else ""
            if not found:
                dest.unlink(missing_ok=True)
                if lrc_path.exists():
                    print(f"   = vorhandene LRC behalten.{score_str}")
                    skipped += 1
                else:
                    print(f"   ✗ Kein Treffer.{score_str}")
                    not_found += 1
            else:
                new_content = dest.read_bytes()
                old_content = lrc_path.read_bytes() if lrc_path.exists() else None
                dest.unlink(missing_ok=True)
                if old_content == new_content:
                    print(f"   = unverändert.{score_str}")
                    skipped += 1
                else:
                    lrc_path.write_bytes(new_content)
                    print(f"   ✓ gespeichert.{score_str}")
                    updated += 1
        else:
            if found:
                print(f"  ✓ {flac.stem}.lrc")
                updated += 1
            else:
                print(f"  ✗ {flac.stem} — kein Treffer")
                not_found += 1

    if args.recursive:
        print(
            f"\nFertig — {updated} aktualisiert, {skipped} unverändert, {not_found} nicht gefunden",
            end="",
        )
        if errors:
            print(f", {errors} Fehler", end="")
        print(".")


if __name__ == "__main__":
    main()
