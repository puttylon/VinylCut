#!/usr/bin/env python3
"""Analysiert .fetch_songtext.json-Caches und gibt Statistiken aus."""

import argparse
import json
from pathlib import Path

from library import method_from_cache_entry as _method
from library import reject_reason_from_cache_entry as _reject_reason


def analyse(root: Path) -> None:
    cache_files = sorted(root.rglob(".fetch_songtext.json"))
    if not cache_files:
        print(f"Keine Cache-Dateien gefunden in: {root}")
        return

    ok_entries: list[dict] = []
    nf_entries: list[dict] = []
    skip_entries: list[dict] = []
    albums = set()

    for cf in cache_files:
        albums.add(cf.parent)
        try:
            data = json.loads(cf.read_text(encoding="utf-8"))
        except Exception:
            continue
        for entry in data.values():
            if not isinstance(entry, dict):
                continue
            if entry.get("r") == "ok":
                ok_entries.append(entry)
            elif entry.get("r") == "nf":
                nf_entries.append(entry)
            elif entry.get("r") == "skip":
                skip_entries.append(entry)

    total = len(ok_entries) + len(nf_entries) + len(skip_entries)
    if total == 0:
        print("Keine Einträge gefunden.")
        return

    print(f"\n=== Analyse: {root} ===")
    print(f"Alben: {len(albums)}   Tracks gesamt: {total}\n")

    # ── Ergebnis ────────────────────────────────────────────────────────────
    print("ERGEBNIS")
    print(
        f"  LRC gefunden    (ok):  {len(ok_entries):4d}  ({len(ok_entries) / total:.1%})"
    )
    print(
        f"  Nicht gefunden  (nf):  {len(nf_entries):4d}  ({len(nf_entries) / total:.1%})"
    )
    if skip_entries:
        print(
            f"  Übersprungen  (skip): {len(skip_entries):4d}  ({len(skip_entries) / total:.1%})"
        )

    # ── Methode (ok) ─────────────────────────────────────────────────────────
    print("\nMETHODE (ok-Tracks)")
    method_counts: dict[str, int] = {}
    for e in ok_entries:
        m = _method(e)
        method_counts[m] = method_counts.get(m, 0) + 1
    order = [
        "konsens",
        "konsens-kein-vokal",
        "whisper-base",
        "whisper-small",
        "fallback",
        "heuristik",
    ]
    labels = {
        "konsens": "Provider-Konsens         ",
        "konsens-kein-vokal": "Konsens (kein Vokal)     ",
        "whisper-base": "Whisper base ≥40%        ",
        "whisper-small": "Whisper small ≥40%       ",
        "fallback": "Fallback alt (kein Vokal)",
        "heuristik": "Heuristik (kein Whisper) ",
    }
    for key in order:
        n = method_counts.get(key, 0)
        if n:
            print(f"  {labels[key]}  {n:4d}  ({n / len(ok_entries):.1%})")

    # ── Ablehnungsgrund (nf) ─────────────────────────────────────────────────
    print("\nABLEHNUNGSGRUND (nf-Tracks)")
    reject_counts: dict[str, int] = {}
    for e in nf_entries:
        r = _reject_reason(e)
        reject_counts[r] = reject_counts.get(r, 0) + 1
    reject_order = [
        "kein-provider",
        "kein-vokal",
        "unter-schwelle",
        "kein-whisper",
        "dauer-abweichung",
    ]
    reject_labels = {
        "kein-provider": "Kein Provider gefunden",
        "kein-vokal": "Kein Vokal erkannt    ",
        "unter-schwelle": "Whisper unter Schwelle",
        "kein-whisper": "Kein Whisper/Score    ",
        "dauer-abweichung": "--no-whisper: Dauer passt nicht",
    }
    for key in reject_order:
        n = reject_counts.get(key, 0)
        if n:
            print(f"  {reject_labels[key]}  {n:4d}  ({n / len(nf_entries):.1%})")

    # ── Score-Verteilung (ok, Whisper) ───────────────────────────────────────
    whisper_ok = [
        e for e in ok_entries if e.get("score") is not None and e.get("model")
    ]
    if whisper_ok:
        print(f"\nSCORE-VERTEILUNG (ok, {len(whisper_ok)} Whisper-Tracks)")
        buckets = [(i, i + 10) for i in range(40, 100, 10)]
        for lo, hi in buckets:
            n = sum(1 for e in whisper_ok if lo <= (e["score"] or 0) * 100 < hi)
            bar = "█" * (n // max(1, len(whisper_ok) // 30))
            print(f"  {lo:3d}–{hi:3d}%  {n:4d}  {bar}")

    # ── Risiko-Tracks ────────────────────────────────────────────────────────
    risky = [
        e
        for e in ok_entries
        if e.get("model")
        and not e.get("consensus")
        and not e.get("fallback")
        and (e.get("score") or 0) < 0.50
        and e.get("providers", 0) == 1
    ]
    if risky:
        print(
            f"\nRISIKO-TRACKS (ok, Score 40–50%, nur 1 Provider) — {len(risky)} Stück"
        )
        for cf in cache_files:
            try:
                data = json.loads(cf.read_text(encoding="utf-8"))
            except Exception:
                continue
            for fname, entry in data.items():
                if not isinstance(entry, dict):
                    continue
                if (
                    entry.get("r") == "ok"
                    and entry.get("model")
                    and not entry.get("consensus")
                    and not entry.get("fallback")
                    and (entry.get("score") or 0) < 0.50
                    and entry.get("providers", 0) == 1
                ):
                    score_pct = f"{entry['score']:.0%}"
                    print(f"  {score_pct}  {cf.parent.name} / {fname}")
    else:
        print("\nKeine Risiko-Tracks gefunden (ok, Score 40–50%, 1 Provider).")

    # ── Konsens (kein Vokal): Provider einig, Whisper hat nichts gehört ─────
    # 2 Provider ist die schwächstmögliche Stufe (Minimum für diesen Fallback) —
    # separat ausweisen, damit die riskantesten Fälle zuerst auffallen.
    novocal_entries: list[tuple] = []
    for cf in cache_files:
        try:
            data = json.loads(cf.read_text(encoding="utf-8"))
        except Exception:
            continue
        for fname, entry in data.items():
            if not isinstance(entry, dict):
                continue
            if entry.get("r") == "ok" and _method(entry) == "konsens-kein-vokal":
                novocal_entries.append((cf, fname, entry))

    if novocal_entries:
        novocal_entries.sort(key=lambda t: t[2].get("providers", 0))
        print(
            f"\nKONSENS KEIN VOKAL (Provider einig, Whisper hat nichts gehört) — {len(novocal_entries)} Stück"
        )

        def _print_novocal(rows: list[tuple]) -> None:
            for cf, fname, entry in rows:
                prov = entry.get("providers", "?")
                score = entry.get("score", 0.0) or 0.0
                names = ", ".join(entry.get("provider_names") or [])
                print(f"    {prov}P {score:.0%}  {names}  {cf.parent.name} / {fname}")

        risky = [t for t in novocal_entries if t[2].get("providers", 0) <= 2]
        solid = [t for t in novocal_entries if t[2].get("providers", 0) > 2]
        if risky:
            print(
                f"  ⚠ 2 Provider — schwächste Stufe, Review empfohlen ({len(risky)}):"
            )
            _print_novocal(risky)
        if solid:
            print(f"  ≥3 Provider — solider ({len(solid)}):")
            _print_novocal(solid)
    else:
        print("\nKeine Konsens-kein-Vokal-Tracks (alle ok-Tracks Whisper-verifiziert).")

    # ── nf-Tracks unter Schwelle mit mehreren Providern ──────────────────────
    near_miss = [
        e
        for e in nf_entries
        if _reject_reason(e) == "unter-schwelle"
        and e.get("providers", 0) >= 2
        and (e.get("score") or 0) >= 0.20
    ]
    if near_miss:
        print(f"\nNAHE TREFFER (nf, Score ≥20%, ≥2 Provider) — {len(near_miss)} Stück")
        for cf in cache_files:
            try:
                data = json.loads(cf.read_text(encoding="utf-8"))
            except Exception:
                continue
            for fname, entry in data.items():
                if not isinstance(entry, dict):
                    continue
                if (
                    entry.get("r") == "nf"
                    and _reject_reason(entry) == "unter-schwelle"
                    and entry.get("providers", 0) >= 2
                    and (entry.get("score") or 0) >= 0.20
                ):
                    score_pct = f"{entry['score']:.0%}"
                    prov = entry.get("providers", "?")
                    print(f"  {score_pct}  {prov}P  {cf.parent.name} / {fname}")

    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="LRC-Cache-Analyse")
    parser.add_argument(
        "path", nargs="?", default=".", help="Wurzelverzeichnis (Standard: .)"
    )
    args = parser.parse_args()
    analyse(Path(args.path).expanduser().resolve())


if __name__ == "__main__":
    main()
