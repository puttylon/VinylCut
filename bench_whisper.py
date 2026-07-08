#!/usr/bin/env python3
"""Whisper-Backend-Benchmark: Geschwindigkeit und Qualität messen.

Lauf 1 — vor dem Umbau (faster_whisper):
    python3 bench_whisper.py track1.flac track2.flac ... -o bench_ctranslate2.json

Lauf 2 — nach dem Umbau (mlx-whisper):
    python3 bench_whisper.py track1.flac track2.flac ... -o bench_mlx.json

Vergleich:
    python3 bench_whisper.py --compare bench_ctranslate2.json bench_mlx.json
"""

import argparse
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

from fetch_songtext import (
    _WHISPER_MODEL_FAST,
    _WHISPER_MODEL_FULL,
    _extract_lrc_words,
    _first_timestamp,
    _transcribe,
    _whisper_context_sec,
    _word_overlap,
    _get_whisper_model,
)


def _track_duration(path: Path) -> float:
    try:
        r = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _detect_backend() -> str:
    try:
        import mlx_whisper  # noqa: F401

        return "mlx_whisper"
    except ImportError:
        pass
    try:
        from faster_whisper import WhisperModel  # noqa: F401

        return "faster_whisper"
    except ImportError:
        return "unknown"


def run_benchmark(audio_files: list[Path], out_path: Path) -> None:
    backend = _detect_backend()
    print(f"Backend: {backend}")
    print(f"Modelle: {_WHISPER_MODEL_FAST} (Pass 1), {_WHISPER_MODEL_FULL} (Pass 2)\n")

    # Modelle vorladen
    print(f"Lade {_WHISPER_MODEL_FAST}...", end=" ", flush=True)
    _get_whisper_model(_WHISPER_MODEL_FAST)
    print("bereit.")
    print(f"Lade {_WHISPER_MODEL_FULL}...", end=" ", flush=True)
    _get_whisper_model(_WHISPER_MODEL_FULL)
    print("bereit.\n")

    results = []

    for audio in audio_files:
        lrc = audio.with_suffix(".lrc")
        if not lrc.exists():
            print(f"  {audio.name}: keine LRC — übersprungen")
            continue

        dur = _track_duration(audio)
        ctx = _whisper_context_sec(dur)
        lrc_words = _extract_lrc_words(lrc.read_text(encoding="utf-8"))
        first_ts = _first_timestamp(lrc.read_text(encoding="utf-8"))
        start = max(0.0, first_ts)

        print(
            f"{audio.name}  ({dur:.0f}s Track, {ctx:.0f}s Kontext, Start {start:.1f}s)"
        )

        row: dict = {
            "path": str(audio),
            "name": audio.name,
            "duration_s": round(dur, 1),
            "context_s": round(ctx, 1),
            "lrc_words": len(lrc_words),
        }

        for model_name, label in [
            (_WHISPER_MODEL_FAST, "base"),
            (_WHISPER_MODEL_FULL, "small"),
        ]:
            t0 = time.perf_counter()
            words = _transcribe(audio, start, ctx, model_name)
            elapsed = time.perf_counter() - t0
            jaccard = _word_overlap(words, lrc_words)
            row[label] = {
                "time_s": round(elapsed, 1),
                "words": len(words),
                "jaccard": round(jaccard, 3),
            }
            print(
                f"  {label:5s}  {elapsed:5.1f}s  {len(words):4d}W  Jaccard {jaccard:.0%}"
            )

        results.append(row)
        print()

    data = {
        "backend": backend,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "models": {"fast": _WHISPER_MODEL_FAST, "full": _WHISPER_MODEL_FULL},
        "tracks": results,
    }
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Ergebnisse gespeichert: {out_path}")


def compare(before_path: Path, after_path: Path) -> None:
    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))

    before_by_name = {t["name"]: t for t in before["tracks"]}
    after_by_name = {t["name"]: t for t in after["tracks"]}

    common = [n for n in before_by_name if n in after_by_name]
    if not common:
        print("Keine gemeinsamen Tracks.")
        return

    print(f"\nVergleich: {before['backend']} → {after['backend']}")
    print(f"Vorher:   {before['timestamp']}")
    print(f"Nachher:  {after['timestamp']}\n")

    col = "{:<45}  {:>8}  {:>8}  {:>8}  {:>8}  {:>8}  {:>8}"
    print(
        col.format(
            "Track",
            "base↑t",
            "base Δt",
            "base↑J",
            "base ΔJ",
            "sml↑t",
            "sml Δt",
        )
    )
    print("-" * 105)

    total_base_before = total_base_after = 0.0
    total_small_before = total_small_after = 0.0

    for name in common:
        b = before_by_name[name]
        a = after_by_name[name]

        bt_b = b["base"]["time_s"]
        bt_a = a["base"]["time_s"]
        bj_b = b["base"]["jaccard"]
        bj_a = a["base"]["jaccard"]
        st_b = b["small"]["time_s"]
        st_a = a["small"]["time_s"]

        total_base_before += bt_b
        total_base_after += bt_a
        total_small_before += st_b
        total_small_after += st_a

        def fmt_delta_t(before: float, after: float) -> str:
            d = after - before
            sign = "+" if d > 0 else ""
            return f"{sign}{d:.1f}s"

        def fmt_delta_j(before: float, after: float) -> str:
            d = (after - before) * 100
            sign = "+" if d > 0 else ""
            return f"{sign}{d:.0f}%"

        print(
            col.format(
                name[:45],
                f"{bt_a:.1f}s",
                fmt_delta_t(bt_b, bt_a),
                f"{bj_a:.0%}",
                fmt_delta_j(bj_b, bj_a),
                f"{st_a:.1f}s",
                fmt_delta_t(st_b, st_a),
            )
        )

    print("-" * 105)
    base_speedup = total_base_before / total_base_after if total_base_after else 0
    small_speedup = total_small_before / total_small_after if total_small_after else 0
    print(
        f"\nGesamt base:  {total_base_before:.0f}s → {total_base_after:.0f}s  ({base_speedup:.1f}× schneller)"
    )
    print(
        f"Gesamt small: {total_small_before:.0f}s → {total_small_after:.0f}s  ({small_speedup:.1f}× schneller)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", help="Audiodateien mit vorhandener .lrc")
    parser.add_argument(
        "-o", "--out", default="bench_results.json", help="Ausgabe-JSON"
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("BEFORE", "AFTER"),
        help="Zwei Ergebnis-JSONs vergleichen",
    )
    args = parser.parse_args()

    if args.compare:
        compare(Path(args.compare[0]), Path(args.compare[1]))
    elif args.files:
        run_benchmark([Path(f) for f in args.files], Path(args.out))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
