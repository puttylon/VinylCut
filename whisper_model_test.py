#!/usr/bin/env python3
"""Testet alternative Whisper-Modelle (small, medium) gegen die Stichprobe
aus whisper_sample.py — Tracks, bei denen `base` fälschlich 0 Wörter/kein
Vokal gemeldet hat.

Für jeden Track wird der Provider-Text erneut geholt (Referenz — steht nicht
in whisper_stichprobe.json, wurde dort nach dem Konsens-Check gelöscht),
dann mit jedem Modell transkribiert und der Containment-Score gegen den
Referenztext berechnet (gleiche Metrik wie in fetch_songtext.py).

Resumable: Ergebnis wird nach jedem einzelnen Modell-Durchlauf gespeichert
(nicht erst am Ende). Bricht das Netz oder die Transkription mittendrin ab,
macht ein erneuter Lauf nur bei den fehlenden Track/Modell-Kombinationen
weiter — bereits fertige werden übersprungen.

Verwendung:
    python3 whisper_model_test.py --root /Musik/                              # Standard: whisper_stichprobe.json
    python3 whisper_model_test.py --root /Musik/ --models small,medium,large-v3
    python3 whisper_model_test.py --root /Musik/ --limit 3                    # zum Testen
"""

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from fetch_songtext import (
    _ALL_PROVIDERS,
    _WHISPER_MIN_OVERLAP,
    _containment,
    _dedupe_by_content,
    _detect_lrc_language,
    _extract_lrc_words,
    _first_timestamp,
    _is_hallucination,
    _load_env,
    _provider_consensus,
    _query_provider,
    _read_audio_tags,
    _transcribe,
    _ts,
    _whisper_context_sec,
)

SAMPLE_FILENAME = "whisper_stichprobe.json"
RESULT_FILENAME = "whisper_model_test.json"


def _get_duration(audio_path: Path) -> float:
    """Trackdauer in Sekunden via ffprobe. 0.0 bei Fehler."""
    try:
        return float(
            subprocess.check_output(
                [
                    "ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path),
                ],
                text=True,
            )
        )
    except Exception:
        return 0.0


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_reference(
    audio_path: Path, env: dict, min_providers: int
) -> tuple[str, list[str], str | None, bool] | None:
    """Provider erneut abfragen, repräsentativen Text (roh + Wörter) + Sprache zurückgeben.

    Gibt zusätzlich zurück, ob der Text durch Provider-Konsens bestätigt ist
    (>=min_providers stimmen überein) oder nur von einem einzelnen Provider
    stammt (schwächeres Signal, keine Gegenprüfung möglich).
    """
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
    for provider in _ALL_PROVIDERS:
        path = results.get(provider)
        if path:
            paths.append(path)
            hits.append(provider)
    paths, hits = _dedupe_by_content(paths, hits)

    rep, _jaccard = _provider_consensus(paths, min_providers=min_providers)
    confirmed = rep is not None
    if rep is None and len(paths) == 1:
        rep = paths[0]  # einzelner Provider, keine Gegenprüfung möglich — schwächeres Signal
    lang = _detect_lrc_language(paths) if paths else None
    text = rep.read_text(encoding="utf-8") if rep is not None else None

    for p in paths:
        p.unlink(missing_ok=True)

    if text is None:
        return None
    return text, _extract_lrc_words(text), lang, confirmed


def _test_model(
    audio_path: Path, start: float, ctx: float, model_name: str,
    language: str | None, ref_words: list[str],
) -> tuple[float, int]:
    """Transkribiert mit einem Modell, gibt (Containment-Score, Wortanzahl) zurück."""
    words, _no_speech, _logprob = _transcribe(
        audio_path, start, ctx, model_name, language=language
    )
    if _is_hallucination(words):
        words = []
    score = _containment(words, ref_words)
    return score, len(words)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "sample", nargs="?", default=SAMPLE_FILENAME,
        help=f"Stichprobe-JSON von whisper_sample.py (Standard: {SAMPLE_FILENAME})",
    )
    parser.add_argument(
        "--out", default=RESULT_FILENAME,
        help=f"Ergebnis-Cache, resumable (Standard: {RESULT_FILENAME})",
    )
    parser.add_argument(
        "--models", default="small,medium",
        help="Kommagetrennte Modell-Liste, in dieser Reihenfolge getestet (Standard: small,medium)",
    )
    parser.add_argument(
        "--root", required=True,
        help="Wurzelverzeichnis der Bibliothek — die Pfade in der Stichprobe sind relativ dazu (wie beim whisper_sample.py-Lauf, der sie erzeugt hat)",
    )
    parser.add_argument(
        "--min-providers-consensus", type=int, default=2, metavar="N",
        help="Mindest-Provider für den Referenz-Konsens-Check (Standard: 2)",
    )
    parser.add_argument(
        "--limit", type=int, metavar="N",
        help="Nur die ersten N Stichproben-Tracks verarbeiten",
    )
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    root = Path(args.root).expanduser().resolve()
    sample_path = Path(args.sample)
    sample = _load_json(sample_path, None)
    if not sample:
        print(f"Keine Stichprobe gefunden: {sample_path}")
        return
    if args.limit:
        sample = sample[: args.limit]

    out_path = Path(args.out)
    results: dict = _load_json(out_path, {})
    env = _load_env()

    print(f"{_ts()}  {len(sample)} Tracks, Modelle: {', '.join(models)}\n")

    for i, item in enumerate(sample, 1):
        rel = item["path"]
        audio_path = Path(rel) if Path(rel).is_absolute() else root / rel
        if not audio_path.exists():
            print(f"{_ts()}  [{i}/{len(sample)}]  {rel}  Datei nicht gefunden — übersprungen")
            continue

        entry = results.get(rel, {})
        missing = [m for m in models if m not in entry]
        if not missing:
            print(f"{_ts()}  [{i}/{len(sample)}]  {rel}  bereits vollständig — übersprungen")
            continue

        ref = _get_reference(audio_path, env, args.min_providers_consensus)
        if ref is None:
            print(f"{_ts()}  [{i}/{len(sample)}]  {rel}  kein Referenztext (mehr) — übersprungen")
            continue
        ref_text, ref_words, lang, confirmed = ref
        if not confirmed:
            print(
                f"{_ts()}  [{i}/{len(sample)}]  {rel}  ⚠ nur 1 Provider, unbestätigt — schwächere Referenz"
            )

        dur = _get_duration(audio_path)
        ctx = _whisper_context_sec(dur)
        start = max(0.0, _first_timestamp(ref_text))
        entry["language"] = lang
        entry["reference_confirmed"] = confirmed

        for model_name in missing:
            # Liveness-Check direkt vor dem (teuren) Modell-Lauf: SMB-Freigaben können
            # zwischendurch wegbrechen. _transcribe() verschluckt das intern still und
            # gäbe sonst ein falsches 0W/0%-Ergebnis, das dauerhaft gecacht würde.
            if _get_duration(audio_path) <= 0.0:
                print(
                    f"{_ts()}  [{i}/{len(sample)}]  {rel}  [{model_name}] "
                    "Datei gerade nicht lesbar (Netzwerk?) — übersprungen, nächster Lauf holt es nach"
                )
                continue
            score, words = _test_model(audio_path, start, ctx, model_name, lang, ref_words)
            entry[model_name] = {"score": round(score, 3), "words": words}
            results[rel] = entry
            _save_json(out_path, results)
            symbol = "✓" if score >= _WHISPER_MIN_OVERLAP else "="
            print(
                f"{_ts()}  [{i}/{len(sample)}]  {rel}  [{model_name}] {words}W {score:.0%}  {symbol}"
            )

    print(f"\n{'Track':<70}" + "".join(f"{m:>10}" for m in models))
    hits = {m: 0 for m in models}
    total = 0
    for item in sample:
        entry = results.get(item["path"])
        if not entry or not all(m in entry for m in models):
            continue
        total += 1
        row = f"{item['path'][:68]:<70}"
        for m in models:
            score = entry[m]["score"]
            if score >= _WHISPER_MIN_OVERLAP:
                hits[m] += 1
            row += f"{score:>9.0%} "
        print(row)

    print(f"\nVon {total} vollständig getesteten Tracks:")
    for m in models:
        print(f"  {m:<10} {hits[m]}/{total} ≥ {_WHISPER_MIN_OVERLAP:.0%}")


if __name__ == "__main__":
    main()
