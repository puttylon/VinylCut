#!/usr/bin/env python3
"""Phase 4 der Songtexte-Pipeline: Songtexte bewerten (Konsens/Whisper).

Entscheidet je Song, welcher der in Phase 2/3 gesammelten Provider-Kandidaten
(oder gar keiner) der richtige Songtext ist -- siehe "workflow für
songexte.txt", Abschnitt ZIELARCHITEKTUR. Baut dafür auf denselben Funktionen
auf, die fetch_songtext.fetch_lrc() seit Jahren fuer genau diese Entscheidung
nutzt (_provider_consensus, _whisper_best, _whisper_accept, _heuristic_best)
-- die Algorithmen selbst werden NICHT neu erfunden, nur die Datenquelle
aendert sich: Kandidaten kommen aus der Cache-DB (Phase 2/3 haben schon live
abgefragt), nicht aus einer eigenen Live-Provider-Abfrage. evaluate_song()
schreibt auch NICHTS auf die Platte (das ist Phase 5, siehe write_lrc.py) --
sie gibt (gefunden, info_str, extras) zurueck, extras["content"] enthaelt bei
gefunden=True den rohen Songtext.

Whisper-Modell und der kontrastive Hintergrund-Kontext (siehe
fetch_songtext._build_contrastive_context) werden EINMAL pro Lauf geladen,
nicht pro Song -- IDF wird alle _IDF_REFRESH_INTERVAL Songs aufgefrischt,
damit neu hinzugekommene Texte/Transkripte einfliessen (siehe Design-
Dokument, Abschnitt 3, Antwort A3).

Modellwahl nach Sprache (siehe ROADMAP.md, "Nachtrag: large-v3 ergänzt +
Entscheidung für den Produktivbetrieb" -- dort als "noch offen" markiert,
hier erstmals umgesetzt): Englischsprachige Songs nutzen `medium`
(Qualitätsunterschied zu `large-v3` laut Testlauf zu gering für dessen ~40 %
Mehrkosten pro Song). Nicht-englische Songs (Sprach-Hint != "en",
insbesondere Deutsch und gemischtsprachige Songs) nutzen `large-v3` -- dort
ist der Qualitätsgewinn laut Testlauf real und deutlich (siehe
whisper_modellvergleich_ergebnis.md). fetch_songtext._whisper_best() selbst
kennt kein Modell-Argument -- es liest _WHISPER_MODEL immer als Modul-Global.
_select_whisper_model() wird deshalb VOR jedem _whisper_best()-Aufruf
verwendet, um dieses Global kurzzeitig auf das passende Modell zu setzen
(siehe evaluate_song) -- fetch_songtext.py selbst bleibt unangetastet.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unicodedata
from pathlib import Path

import fetch_providers
import fetch_songtext

_IDF_REFRESH_INTERVAL = 50

_WHISPER_MODEL_EN = "medium"
_WHISPER_MODEL_OTHER = "large-v3"


def _select_whisper_model(candidates: list[Path]) -> str:
    """Modellwahl nach Sprach-Hint, siehe Moduldocstring. Ruft dieselbe
    fetch_songtext._detect_lrc_language() auf, die _whisper_best() intern
    sowieso nochmal aufruft -- billige, reine Textanalyse (kein Whisper-
    Lauf), keine doppelte Sprach-Logik."""
    lang = fetch_songtext._detect_lrc_language(candidates)
    return _WHISPER_MODEL_EN if lang == "en" else _WHISPER_MODEL_OTHER


def _load_candidate_texts(
    conn: sqlite3.Connection, song_id: int
) -> list[tuple[str, str]]:
    """(provider, inhalt) je Treffer fuer song_id, in _ALL_PROVIDERS-Reihenfolge
    (wichtig fuer _dedupe_by_content: "erster Treffer in Prioritaetsreihenfolge
    bleibt")."""
    rows = conn.execute(
        "SELECT e.quelle, t.inhalt FROM ergebnisse e "
        "JOIN texte t ON t.fingerabdruck = e.fingerabdruck "
        "WHERE e.song_id=? AND e.status='treffer'",
        (song_id,),
    ).fetchall()
    by_provider = {quelle: inhalt for quelle, inhalt in rows if inhalt}
    return [
        (p, by_provider[p]) for p in fetch_songtext._ALL_PROVIDERS if p in by_provider
    ]


def _write_temp_lrc(content: str) -> Path:
    with tempfile.NamedTemporaryFile(
        suffix=".lrc", delete=False, mode="w", encoding="utf-8"
    ) as tmp:
        tmp.write(content)
        return Path(tmp.name)


def evaluate_song(
    conn: sqlite3.Connection,
    artist_key: str,
    titel_key: str,
    flac_path: Path | None = None,
    expected_dur: float = 0.0,
    existing_lrc: Path | None = None,
) -> tuple[bool, str, dict]:
    """Entscheidet fuer EINEN Song, welcher Songtext richtig ist.

    Spiegelt den Entscheidungsbaum aus fetch_songtext.fetch_lrc() (Konsens ->
    Whisper -> Dauer-Heuristik) -- ohne den dortigen Provider-Abfrage-Block
    (Kandidaten kommen aus der DB) und ohne den --fast/deferred-Sonderfall
    (der ist mit der Phasen-Aufteilung obsolet: Phase 4 laeuft per Definition
    erst NACHDEM Phase 2/3 alle Provider-Versuche ausgeschoepft haben).

    flac_path wird NUR gebraucht, wenn Whisper diesen Song noch nie gehoert
    hat (kein gecachtes Transkript, siehe fetch_songtext._whisper_best) --
    ist es None oder existiert die Datei nicht, faellt die Entscheidung auf
    die reine Dauer-Heuristik zurueck (wie fetch_lrc's letzter else-Zweig).
    """
    # Direkt über den uebergebenen conn nachschlagen, NICHT ueber
    # fetch_songtext._lookup_cache_song_id (haengt am Modul-Global
    # _cache_conn) -- evaluate_song muss auch funktionieren, wenn die
    # Globals (noch) nicht vorbereitet sind (siehe _prepare_fetch_songtext_
    # globals), z.B. bei einem direkten Aufruf aus write_lrc.py.
    row = conn.execute(
        "SELECT id FROM songs WHERE artist_key=? AND titel_key=?",
        (artist_key, titel_key),
    ).fetchone()
    song_id = row[0] if row else None
    provider_texts = _load_candidate_texts(conn, song_id) if song_id is not None else []

    candidates: list[Path] = [_write_temp_lrc(content) for _, content in provider_texts]
    provider_hits = [p for p, _ in provider_texts]
    candidates, provider_hits = fetch_songtext._dedupe_by_content(
        candidates, provider_hits
    )

    all_candidates = candidates + (
        [existing_lrc] if existing_lrc and existing_lrc.exists() else []
    )

    def _cleanup() -> None:
        for p in candidates:  # nur eigene Temp-Dateien, nie existing_lrc
            p.unlink(missing_ok=True)

    n_providers = len(fetch_songtext._ALL_PROVIDERS)

    if not all_candidates:
        _cleanup()
        info_str = f"0/{n_providers}: — │ kein Provider"
        return (
            False,
            info_str,
            {
                "providers": 0,
                "provider_names": [],
                "method": None,
                "no_vocal": False,
                "score": None,
                "reason": "kein-provider",
                "words": None,
                "language": None,
                "content": None,
            },
        )

    hit_str = ", ".join(provider_hits) if provider_hits else "—"
    prov_str = f"{len(candidates)}/{n_providers}: {hit_str}"

    consensus_rep, consensus_jaccard = fetch_songtext._provider_consensus(candidates)

    if consensus_rep is not None:
        best_content: bytes | None = consensus_rep.read_bytes()
        info_str = f"{prov_str} │ Konsens {consensus_jaccard:.0%}"
        extras: dict = {
            "providers": len(candidates),
            "provider_names": provider_hits,
            "method": "konsens",
            "no_vocal": False,
            "score": round(consensus_jaccard, 3),
            "words": None,
            "language": None,
        }
    elif flac_path is not None and flac_path.exists():
        # Modellwahl nach Sprach-Hint (siehe Moduldocstring) -- _whisper_best
        # liest _WHISPER_MODEL immer als Modul-Global, deshalb hier kurzzeitig
        # gesetzt und danach garantiert zurückgesetzt (auch bei Exceptions).
        prev_model = fetch_songtext._WHISPER_MODEL
        fetch_songtext._WHISPER_MODEL = _select_whisper_model(all_candidates)
        try:
            (
                best_path,
                best_score,
                has_vocals,
                whisper_words,
                model_used,
                lrc_lang,
                contrastive_margin,
            ) = fetch_songtext._whisper_best(
                flac_path,
                all_candidates,
                expected_dur,
                artist=artist_key,
                title=titel_key,
            )
        finally:
            fetch_songtext._WHISPER_MODEL = prev_model
        method = f"whisper-{model_used}" if model_used else "heuristik"
        model_str = f"[{model_used}]" if model_used else ""
        lang_str = lrc_lang or ""
        words_str = f"{whisper_words}W"
        whisper_head = " ".join(
            p for p in [model_str, lang_str, "Whisper", words_str] if p
        )

        if not has_vocals:
            novocal_rep, novocal_jaccard = fetch_songtext._provider_consensus(
                candidates, min_providers=2
            )
            if novocal_rep is not None:
                best_content = novocal_rep.read_bytes()
                info_str = f"{prov_str} │ Konsens {novocal_jaccard:.0%} (kein Vokal)"
                extras = {
                    "providers": len(candidates),
                    "provider_names": provider_hits,
                    "method": "konsens",
                    "no_vocal": True,
                    "score": round(novocal_jaccard, 3),
                    "words": whisper_words,
                    "language": lrc_lang,
                }
            else:
                best_content = None
                info_str = f"{prov_str} │ {whisper_head} kein Vokal"
                extras = {
                    "providers": len(candidates),
                    "provider_names": provider_hits,
                    "method": method,
                    "no_vocal": True,
                    "score": 0.0,
                    "reason": "kein-vokal",
                    "words": 0,
                    "language": lrc_lang,
                }
        elif fetch_songtext._whisper_accept(
            best_score, lrc_lang, margin=contrastive_margin
        ):
            best_content = best_path.read_bytes() if best_path else None
            info_str = f"{prov_str} │ {whisper_head} idf-jacc={best_score:.3f}"
            extras = {
                "providers": len(candidates),
                "provider_names": provider_hits,
                "method": method,
                "no_vocal": False,
                "score": round(best_score, 3),
                "words": whisper_words,
                "language": lrc_lang,
            }
        else:
            best_content = None
            info_str = (
                f"{prov_str} │ {whisper_head} unter Schwelle idf-jacc={best_score:.3f}"
            )
            extras = {
                "providers": len(candidates),
                "provider_names": provider_hits,
                "method": method,
                "no_vocal": False,
                "score": round(best_score, 3),
                "reason": "unter-schwelle",
                "words": whisper_words,
                "language": lrc_lang,
            }
    else:
        best_content, _score = fetch_songtext._heuristic_best(
            all_candidates, expected_dur
        )
        if best_content is not None:
            info_str = f"{prov_str} │ Heuristik"
            extras = {
                "providers": len(candidates),
                "provider_names": provider_hits,
                "method": "heuristik",
                "no_vocal": False,
                "score": None,
                "words": None,
                "language": None,
            }
        else:
            info_str = f"{prov_str} │ Heuristik Dauer-Abweichung"
            extras = {
                "providers": len(candidates),
                "provider_names": provider_hits,
                "method": "heuristik",
                "no_vocal": False,
                "score": None,
                "reason": "dauer-abweichung",
                "words": None,
                "language": None,
            }

    _cleanup()
    extras["content"] = best_content
    if best_content is None:
        return False, info_str, extras
    return True, info_str, extras


def _resolve_expected_dur(flac_path: Path) -> float:
    """Liest die erwartete Songdauer aus release.json (falls vorhanden) --
    exakt wie main() das im alten fetch_songtext.py tut (meta_title/
    _clean_query_title/_load_release), hier moeglich weil flac_path
    vorliegt und die Original-Gross-/Kleinschreibung direkt aus den Tags
    gelesen wird (die songs-Tabelle speichert nur normalisierte Schluessel,
    siehe scan_songs.py)."""
    meta_artist, meta_title, _meta_genre = fetch_songtext._read_audio_tags(flac_path)
    title = meta_title or (
        flac_path.stem.split(" - ", 1)[-1]
        if " - " in flac_path.stem
        else flac_path.stem
    )
    _artist, tracks_by_title = fetch_songtext._load_release(flac_path.parent)
    return tracks_by_title.get(unicodedata.normalize("NFC", title), 0.0)


def evaluate_all(
    conn: sqlite3.Connection,
    scope: set[tuple[str, str]] | None = None,
    file_song_map: dict[tuple[str, str], Path] | None = None,
) -> dict[str, int]:
    """Phase 4: bewertet Songs aus "songs" -- optional eingegrenzt auf `scope`
    (siehe fetch_providers.fetch_all fuer dasselbe Scope-Prinzip: ohne PFAD
    bewusst die GANZE DB, mit PFAD nur die Songs des aktuellen Laufs).

    `file_song_map` (artist_key, titel_key) -> Audiodatei-Pfad, siehe
    songtext_pipeline.build_file_song_map -- nur fuer Songs mit Eintrag dort
    kann Whisper bei einem Transkript-Cache-Miss live transkribieren; andere
    Songs fallen auf Konsens/Dauer-Heuristik zurueck.

    Prueft vorab nur, ob faster-whisper ueberhaupt verfuegbar ist (per
    _WHISPER_MODEL_EN als Sonde) -- welches Modell (medium/large-v3)
    tatsaechlich pro Song geladen wird, entscheidet _select_whisper_model()
    je nach Sprache (siehe Moduldocstring); beide werden dabei lazy von
    fetch_songtext._get_whisper_model() geladen und gecacht, hoechstens
    einmal pro Modellname und Lauf, nicht pro Song.
    """
    if fetch_songtext._get_whisper_model(_WHISPER_MODEL_EN) is None:
        print(
            f"FEHLER: faster-whisper nicht verfügbar — Modell "
            f"'{_WHISPER_MODEL_EN}' konnte nicht geladen werden."
        )
        return {}

    fetch_providers._prepare_fetch_songtext_globals(conn)
    fetch_songtext._build_contrastive_context()

    file_song_map = file_song_map or {}
    rows = conn.execute(
        "SELECT artist_key, titel_key FROM songs ORDER BY artist_key, titel_key"
    ).fetchall()
    to_evaluate = [(a, t) for a, t in rows if scope is None or (a, t) in scope]
    total = len(to_evaluate)
    if total:
        print(f"Bewerte {total} Song(s) ...")

    counts = {
        "konsens": 0,
        "whisper-akzeptiert": 0,
        "abgelehnt": 0,
        "kein-provider": 0,
    }

    for i, (artist_key, titel_key) in enumerate(to_evaluate, start=1):
        if i > 1 and (i - 1) % _IDF_REFRESH_INTERVAL == 0:
            fetch_songtext._build_contrastive_context()

        fetch_songtext._print_status(f"  {i}/{total}: {artist_key} / {titel_key} ...")

        flac_path = file_song_map.get((artist_key, titel_key))
        existing_lrc = flac_path.with_suffix(".lrc") if flac_path is not None else None
        expected_dur = (
            _resolve_expected_dur(flac_path) if flac_path is not None else 0.0
        )

        found, info_str, extras = evaluate_song(
            conn, artist_key, titel_key, flac_path, expected_dur, existing_lrc
        )

        if extras.get("method") == "konsens":
            counts["konsens"] += 1
        elif extras.get("reason") == "kein-provider":
            counts["kein-provider"] += 1
        elif found:
            counts["whisper-akzeptiert"] += 1
        else:
            counts["abgelehnt"] += 1

        fetch_songtext._tprint(
            f"{fetch_songtext._ts()}  {artist_key} / {titel_key}  {info_str}"
        )

    return counts
