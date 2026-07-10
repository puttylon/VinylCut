#!/usr/bin/env python3
"""Baut eine Stichprobe aus Tracks, bei denen Whisper Vokale übersehen hat.

Kriterium: Cache-Eintrag mit r=nf, reason=kein-vokal (Whisper hat 0 Wörter /
keinen Gesang erkannt) UND mindestens --min-providers-consensus Provider
liefern inhaltlich übereinstimmende LRCs (Jaccard ≥ Konsens-Schwelle, wie
beim normalen Provider-Konsens-Check in fetch_songtext.py). Das ist der
Ground-Truth-Indiz: die Provider sind sich einig, dass der Song Text hat —
Whisper (base) hat ihn trotzdem nicht gehört.

Fragt Provider für jeden Kandidaten aus dem Cache erneut ab (Netzwerk) —
nur für die bereits gefilterte kein-vokal-Teilmenge, nicht die ganze
Bibliothek. Setzt vorhandene Audio-Tags voraus (Artist/Title).

Dient als Testset für Schritt 2: alternative Whisper-Modelle gegen `base`
vergleichen (siehe ROADMAP.md, Abschnitt "Whisper-Modell-Stichprobe").

Verwendung:
    python3 whisper_sample.py /Musik/                        # nur anzeigen
    python3 whisper_sample.py /Musik/ --out stichprobe.json  # zusätzlich speichern
"""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from fetch_songtext import (
    _ALL_PROVIDERS,
    _CONSENSUS_MIN_JACCARD,
    _dedupe_by_content,
    _load_env,
    _provider_consensus,
    _query_provider,
    _read_audio_tags,
    _ts,
)

CACHE_FILENAME = ".fetch_songtext.json"


def _reject_reason(entry: dict) -> str:
    """Ablehnungsgrund — mit Legacy-Fallback für pre-v1.5.0-Einträge (wie lrc_recheck.py)."""
    reason = entry.get("reason")
    if reason:
        return reason
    if entry.get("providers", 0) == 0:
        return "kein-provider"
    words = entry.get("words") or 0
    score = entry.get("score")
    if score is None:
        return "kein-whisper"
    if words == 0 and score == 0.0:
        return "kein-vokal"
    return "unter-schwelle"


def _find_candidates(root: Path, min_providers: int) -> list[tuple[Path, str, dict]]:
    """Cache nach kein-vokal-Ablehnungen mit genug Provider-Treffern durchsuchen."""
    candidates: list[tuple[Path, str, dict]] = []
    for cache_file in sorted(root.rglob(CACHE_FILENAME)):
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        for track, entry in data.items():
            if not isinstance(entry, dict):
                continue
            if entry.get("r") != "nf":
                continue
            if _reject_reason(entry) != "kein-vokal":
                continue
            if entry.get("providers", 0) < min_providers:
                continue
            candidates.append((cache_file, track, entry))
    return candidates


def _check_provider_consensus(
    audio_path: Path, env: dict, min_providers: int
) -> tuple[float, list[str]] | None:
    """Fragt alle Provider erneut ab, prüft Jaccard-Konsens. None wenn kein Konsens/keine Tags."""
    artist, title, _ = _read_audio_tags(audio_path)
    if not artist and not title:
        return None
    query = f"{artist} {title}".strip()

    results: dict[str, Path | None] = {}
    with ThreadPoolExecutor(max_workers=len(_ALL_PROVIDERS)) as pool:
        futures = {
            pool.submit(_query_provider, query, p, env): p for p in _ALL_PROVIDERS
        }
        for future in as_completed(futures):
            provider, path = future.result()
            results[provider] = path

    paths: list[Path] = []
    hits: list[str] = []
    for provider in _ALL_PROVIDERS:  # Reihenfolge beibehalten
        path = results.get(provider)
        if path:
            paths.append(path)
            hits.append(provider)

    paths, hits = _dedupe_by_content(paths, hits)
    rep, jaccard = _provider_consensus(paths, min_providers=min_providers)
    for p in paths:
        p.unlink(missing_ok=True)
    if rep is None:
        return None
    return jaccard, hits


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("path", help="Wurzelverzeichnis zum Durchsuchen")
    parser.add_argument(
        "--out", metavar="DATEI", help="Ergebnis zusätzlich als JSON speichern"
    )
    parser.add_argument(
        "--min-providers-cache",
        type=int,
        default=2,
        metavar="N",
        help="Mindest-Provider-Treffer laut Cache-Eintrag, um überhaupt neu abzufragen (Standard: 2)",
    )
    parser.add_argument(
        "--min-providers-consensus",
        type=int,
        default=2,
        metavar="N",
        help="Mindest-Provider für den erneuten Jaccard-Konsens-Check (Standard: 2)",
    )
    args = parser.parse_args()

    root = Path(args.path).expanduser().resolve()
    env = _load_env()

    candidates = _find_candidates(root, args.min_providers_cache)
    if not candidates:
        print("Keine kein-vokal-Kandidaten im Cache gefunden.")
        return

    print(f"{_ts()}  {len(candidates)} Kandidaten im Cache — frage Provider erneut ab...\n")

    sample: list[dict] = []
    for i, (cache_file, track, entry) in enumerate(candidates, 1):
        audio_path = cache_file.parent / track
        rel = audio_path.relative_to(root)
        try:
            result = _check_provider_consensus(
                audio_path, env, args.min_providers_consensus
            )
        except FileNotFoundError:
            print("syncedlyrics nicht gefunden — Abbruch.")
            return
        if result is None:
            print(f"{_ts()}  [{i}/{len(candidates)}]  {rel}  kein Provider-Konsens — verworfen")
            continue
        jaccard, hits = result
        print(f"{_ts()}  [{i}/{len(candidates)}]  {rel}  {len(hits)}P {jaccard:.0%} → Stichprobe")
        sample.append(
            {
                "path": str(rel),
                "providers": len(hits),
                "provider_names": hits,
                "jaccard": round(jaccard, 3),
                "whisper_words": entry.get("words"),
            }
        )

    print(
        f"\n{len(sample)}/{len(candidates)} Kandidaten bestätigt "
        f"(Provider-Konsens ≥ {_CONSENSUS_MIN_JACCARD:.0%} Jaccard)."
    )

    if args.out and sample:
        Path(args.out).write_text(
            json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Gespeichert: {args.out}")


if __name__ == "__main__":
    main()
