#!/usr/bin/env python3
"""Analysiert die SQLite-Cache-DB (cache.db) und gibt
Aggregat-Statistiken aus -- Gegenstück zu lrc_analyse.py, das nur die
.fetch_songtext.json-Ordner-Caches auswertet (siehe ROADMAP.md, Songtexte-
Pipeline-Umbau, "Weiterhin offen": bislang gab es keine Statistik-Sicht auf
die eigentliche Cache-DB selbst).

Verwendung:
    python3 db_analyse.py                         # Standard-DB neben dem Skript
    python3 db_analyse.py --db /pfad/zu/anderer.db
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import cache_store

_ALL_PROVIDERS = ["lrclib", "musixmatch", "netease", "genius"]


def collect_stats(conn: sqlite3.Connection) -> dict:
    """Liest alle Aggregat-Statistiken aus der Cache-DB und gibt sie als
    reines Datenobjekt zurück (getrennt von der Ausgabe, siehe print_stats)
    -- macht die Zahlen ohne stdout-Capturing testbar."""
    stats: dict = {}

    stats["songs_gesamt"] = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    stats["texte_gesamt"] = conn.execute("SELECT COUNT(*) FROM texte").fetchone()[0]

    # Pro Provider: Anzahl je Status.
    provider_status: dict[str, dict[str, int]] = {
        p: {"treffer": 0, "nichts": 0, "fehlschlag": 0} for p in _ALL_PROVIDERS
    }
    for quelle, status, n in conn.execute(
        "SELECT quelle, status, COUNT(*) FROM ergebnisse GROUP BY quelle, status"
    ):
        if quelle in provider_status:
            provider_status[quelle][status] = n
    stats["provider_status"] = provider_status

    # Fehlschlag-Gründe pro Provider.
    fehlschlag_gruende: dict[str, dict[str, int]] = {p: {} for p in _ALL_PROVIDERS}
    for quelle, grund, n in conn.execute(
        "SELECT quelle, fehlergrund, COUNT(*) FROM ergebnisse "
        "WHERE status='fehlschlag' GROUP BY quelle, fehlergrund"
    ):
        if quelle in fehlschlag_gruende:
            fehlschlag_gruende[quelle][grund or "unbekannt"] = n
    stats["fehlschlag_gruende"] = fehlschlag_gruende

    # Songs ohne jeden Provider-Treffer (0/4) bzw. mit allen 4 Providern als
    # Fehlschlag (Kandidaten für --nachholen, siehe ROADMAP.md).
    stats["songs_ohne_treffer"] = conn.execute(
        "SELECT COUNT(*) FROM songs s WHERE NOT EXISTS ("
        "  SELECT 1 FROM ergebnisse e WHERE e.song_id = s.id AND e.status = 'treffer'"
        ")"
    ).fetchone()[0]
    stats["songs_alle_fehlgeschlagen"] = conn.execute(
        "SELECT COUNT(*) FROM songs s WHERE ("
        "  SELECT COUNT(*) FROM ergebnisse e "
        "  WHERE e.song_id = s.id AND e.status = 'fehlschlag'"
        f") = {len(_ALL_PROVIDERS)}"
    ).fetchone()[0]

    # Whisper-Transkript-Abdeckung + Modell-Aufschlüsselung.
    stats["transkripte_gesamt"] = conn.execute(
        "SELECT COUNT(*) FROM transkripte"
    ).fetchone()[0]
    stats["transkripte_je_modell"] = dict(
        conn.execute(
            "SELECT COALESCE(modell, 'unbekannt'), COUNT(*) FROM transkripte "
            "GROUP BY modell"
        ).fetchall()
    )

    # Zeitliche Aktivität: Provider-Ergebnisse der letzten 24h/7 Tage.
    # datum ist ISO-8601 (siehe cache_store._now_iso) -- String-Vergleich
    # funktioniert dafür ohne Zeitzonen-Umrechnung.
    stats["ergebnisse_letzte_24h"] = conn.execute(
        "SELECT COUNT(*) FROM ergebnisse WHERE datum >= datetime('now', '-1 day')"
    ).fetchone()[0]
    stats["ergebnisse_letzte_7d"] = conn.execute(
        "SELECT COUNT(*) FROM ergebnisse WHERE datum >= datetime('now', '-7 days')"
    ).fetchone()[0]

    return stats


def print_stats(stats: dict) -> None:
    songs = stats["songs_gesamt"]
    print("=== DB-Analyse: cache.db ===\n")
    print(f"Songs gesamt: {songs}")
    print(f"Eindeutige Songtexte (dedupliziert): {stats['texte_gesamt']}\n")

    if songs == 0:
        print("Keine Songs in der DB -- keine weiteren Statistiken.")
        return

    print("PROVIDER-STATUS")
    for p in _ALL_PROVIDERS:
        s = stats["provider_status"][p]
        total = s["treffer"] + s["nichts"] + s["fehlschlag"]
        if total == 0:
            print(f"  {p:12s}  noch nie abgefragt")
            continue
        print(
            f"  {p:12s}  Treffer {s['treffer']:5d} ({s['treffer'] / total:.1%})  "
            f"Nichts {s['nichts']:5d} ({s['nichts'] / total:.1%})  "
            f"Fehlschlag {s['fehlschlag']:5d} ({s['fehlschlag'] / total:.1%})"
        )

    any_fehlschlag = any(stats["fehlschlag_gruende"][p] for p in _ALL_PROVIDERS)
    if any_fehlschlag:
        print("\nFEHLSCHLAG-GRÜNDE JE PROVIDER")
        for p in _ALL_PROVIDERS:
            gruende = stats["fehlschlag_gruende"][p]
            if not gruende:
                continue
            grund_str = ", ".join(
                f"{grund}={n}" for grund, n in sorted(gruende.items())
            )
            print(f"  {p:12s}  {grund_str}")

    print(
        f"\nSongs ganz ohne Provider-Treffer: {stats['songs_ohne_treffer']} "
        f"({stats['songs_ohne_treffer'] / songs:.1%})"
    )
    print(
        f"Songs mit allen {len(_ALL_PROVIDERS)} Providern fehlgeschlagen: "
        f"{stats['songs_alle_fehlgeschlagen']} "
        "(Kandidaten für --nachholen)"
    )

    print(f"\nWHISPER-TRANSKRIPTE: {stats['transkripte_gesamt']} von {songs} Songs")
    for modell, n in sorted(stats["transkripte_je_modell"].items()):
        print(f"  {modell:12s}  {n}")

    print("\nAKTIVITÄT")
    print(f"  Provider-Ergebnisse letzte 24h: {stats['ergebnisse_letzte_24h']}")
    print(f"  Provider-Ergebnisse letzte 7 Tage: {stats['ergebnisse_letzte_7d']}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        metavar="PFAD",
        help="Pfad zur Cache-DB (Standard: cache.db neben dem Skript)",
    )
    args = parser.parse_args()

    db_path = args.db or cache_store.default_cache_path()
    if not db_path.exists():
        print(f"Keine Cache-DB gefunden unter: {db_path}")
        return

    conn = cache_store.open_cache(db_path)
    try:
        print_stats(collect_stats(conn))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
