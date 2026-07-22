#!/usr/bin/env python3
"""Phase 4 der Songtexte-Pipeline: Songtexte bewerten (Konsens/Whisper).

Entscheidet je Song, welcher der in Phase 2/3 gesammelten Provider-Kandidaten
(oder gar keiner) der richtige Songtext ist -- siehe "workflow für
songexte.txt", Abschnitt ZIELARCHITEKTUR. Baut dafür auf denselben Funktionen
auf, die früher im alten fetch_songtext.fetch_lrc() (siehe Git-Historie)
genau diese Entscheidung trafen (_provider_consensus, _whisper_best,
_whisper_accept, _heuristic_best) -- die Algorithmen selbst werden NICHT neu
erfunden, nur die Datenquelle
aendert sich: Kandidaten kommen aus der Cache-DB (Phase 2/3 haben schon live
abgefragt), nicht aus einer eigenen Live-Provider-Abfrage. evaluate_song()
schreibt auch NICHTS auf die Platte (das ist Phase 5, siehe write_lrc.py) --
sie gibt (gefunden, info_str, extras) zurueck, extras["content"] enthaelt bei
gefunden=True den rohen Songtext.

Whisper-Modell und der kontrastive Hintergrund-Kontext (siehe
lyrics_core._build_contrastive_context) werden HOECHSTENS EINMAL pro Lauf
geladen, nicht pro Song -- IDF wird alle lyrics_core._idf_refresh_interval(N)
TATSAECHLICH bewerteter Songs aufgefrischt (N proportional zur Datenmenge,
nicht fest, siehe dortiger Docstring), damit neu hinzugekommene Texte/
Transkripte einfliessen (siehe Design-Dokument, Abschnitt 3, Antwort A3). "Hoechstens"
bewusst: mit zugeordneter Audiodatei prueft evaluate_all() vorher
_skip_reevaluation() (JSON-Ordner-Cache-Eintrag noch gueltig UND DB seitdem
nicht neuer, siehe dortiger Docstring) -- wird JEDER Song im Scope
uebersprungen, wird der Kontext gar nicht erst gebaut. Ohne PFAD (keine
Datei-Zuordnung) ist dieser Skip nicht moeglich, jeder Song wird wie bisher
neu bewertet.

Modellwahl nach Sprache (siehe ROADMAP.md, "Nachtrag: large-v3 ergänzt +
Entscheidung für den Produktivbetrieb" -- dort als "noch offen" markiert,
hier erstmals umgesetzt): Englischsprachige Songs nutzen `medium`
(Qualitätsunterschied zu `large-v3` laut Testlauf zu gering für dessen ~40 %
Mehrkosten pro Song). Nicht-englische Songs (Sprach-Hint != "en",
insbesondere Deutsch und gemischtsprachige Songs) nutzen `large-v3` -- dort
ist der Qualitätsgewinn laut Testlauf real und deutlich.
lyrics_core._whisper_best() selbst
kennt kein Modell-Argument -- es liest _WHISPER_MODEL immer als Modul-Global.
_select_whisper_model() wird deshalb VOR jedem _whisper_best()-Aufruf
verwendet, um dieses Global kurzzeitig auf das passende Modell zu setzen
(siehe evaluate_song) -- lyrics_core.py selbst bleibt unangetastet.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unicodedata
from pathlib import Path

import fetch_providers
import lyrics_core

_WHISPER_MODEL_EN = "medium"
_WHISPER_MODEL_OTHER = "large-v3"


def _select_whisper_model(candidates: list[Path]) -> str:
    """Modellwahl nach Sprach-Hint, siehe Moduldocstring. Ruft dieselbe
    lyrics_core._resolve_lrc_language() auf, die _whisper_best() intern
    sowieso nochmal aufruft -- billige, reine Textanalyse (kein Whisper-
    Lauf), keine doppelte Sprach-Logik.

    _resolve_lrc_language() erkennt die Sprache je Kandidat EINZELN statt
    (wie das ältere _detect_lrc_language) alle Kandidaten zu einem Textblock
    zu vermischen -- sonst kann ein einzelner falscher Kandidat (z.B. eine
    Übersetzungsseite eines Providers) die Sprache kippen (siehe Telepatía-
    Fall, ROADMAP.md). Sind sich die Kandidaten uneinig, liefert es None --
    das faellt hier automatisch unter "nicht englisch" und erzwingt damit
    bereits das grosse Modell, ganz ohne eigenen Sonderfall."""
    lang = lyrics_core._resolve_lrc_language(candidates)
    return _WHISPER_MODEL_EN if lang == "en" else _WHISPER_MODEL_OTHER


def _load_candidate_texts(
    conn: sqlite3.Connection, song_id: int
) -> list[tuple[str, str]]:
    """(provider, inhalt) je Treffer fuer song_id, in _ALL_PROVIDERS-Reihenfolge
    (wichtig fuer lyrics_core._group_candidates: "erster Treffer in
    Prioritaetsreihenfolge bleibt Gruppen-Repraesentant").

    Filtert Übersetzungsseiten (lyrics_core._looks_like_translation) trotz
    status='treffer' heraus: diese Funktion liest direkt aus der Cache-DB,
    NICHT über lyrics_core._query_provider -- Einträge, die VOR diesem Filter
    (siehe dortiger Docstring) geschrieben wurden, könnten sonst trotz
    laufendem Fix dauerhaft als gültiger Kandidat durchgehen (siehe Telepatía-
    Fall, ROADMAP.md: Genius' "(English Translation)"-Seite stand bereits als
    "treffer" in der DB, bevor der Filter existierte)."""
    rows = conn.execute(
        "SELECT e.quelle, t.inhalt FROM ergebnisse e "
        "JOIN texte t ON t.fingerabdruck = e.fingerabdruck "
        "WHERE e.song_id=? AND e.status='treffer'",
        (song_id,),
    ).fetchall()
    by_provider = {
        quelle: inhalt
        for quelle, inhalt in rows
        if inhalt and not lyrics_core._looks_like_translation(inhalt)
    }
    return [(p, by_provider[p]) for p in lyrics_core._ALL_PROVIDERS if p in by_provider]


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

    Spiegelt den Entscheidungsbaum aus dem alten fetch_songtext.fetch_lrc()
    (Konsens -> Whisper -> Dauer-Heuristik, siehe Git-Historie) -- ohne den
    dortigen Provider-Abfrage-Block (Kandidaten kommen aus der DB) und ohne
    den --fast/deferred-Sonderfall (der ist mit der Phasen-Aufteilung
    obsolet: Phase 4 laeuft per Definition erst NACHDEM Phase 2/3 alle
    Provider-Versuche ausgeschoepft haben).

    flac_path wird NUR gebraucht, wenn Whisper diesen Song noch nie gehoert
    hat (kein gecachtes Transkript, siehe lyrics_core._whisper_best) --
    ist es None oder existiert die Datei nicht, faellt die Entscheidung auf
    die reine Dauer-Heuristik zurueck (wie fetch_lrc's letzter else-Zweig).
    """
    # Direkt über den uebergebenen conn nachschlagen, NICHT ueber
    # lyrics_core._lookup_cache_song_id (haengt am Modul-Global
    # _cache_conn) -- evaluate_song muss auch funktionieren, wenn die
    # Globals (noch) nicht vorbereitet sind (siehe _prepare_lyrics_core_
    # globals), z.B. bei einem direkten Aufruf aus write_lrc.py.
    row = conn.execute(
        "SELECT id, genre FROM songs WHERE artist_key=? AND titel_key=?",
        (artist_key, titel_key),
    ).fetchone()
    song_id, genre = (row[0], row[1]) if row else (None, None)

    # Bugfix (siehe ROADMAP.md, "Big City Beats"-Fall): Skip-Genre wird
    # bisher NUR in fetch_providers.fetch_all() (Phase --abfragen) geprueft,
    # bevor live abgefragt wird -- evaluate_song() selbst kannte Genre
    # bisher gar nicht und benutzte auch schon vorhandene, evtl. aeltere
    # Provider-Treffer (aus der Zeit VOR einem Retagging) trotzdem weiter.
    # Der Kurzschluss hier ist die zweite Haelfte des SPoT-Fixes: selbst
    # wenn eine Neubewertung ausgeloest wird (siehe _current_sig), verhaelt
    # sich ein aktuell skip-genre-getaggter Song IMMER wie "kein Provider",
    # unabhaengig davon was noch an alten Ergebnis-Zeilen in der DB liegt.
    if genre and lyrics_core._is_skip_genre(genre):
        info_str = f"0/{len(lyrics_core._ALL_PROVIDERS)}: — │ kein Provider (Genre)"
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
                "existing_best": False,
            },
        )

    provider_texts = _load_candidate_texts(conn, song_id) if song_id is not None else []

    candidates: list[Path] = [_write_temp_lrc(content) for _, content in provider_texts]
    provider_hits = [p for p, _ in provider_texts]

    has_existing = bool(existing_lrc and existing_lrc.exists())
    # existing_lrc steht bewusst VORNE (nicht hinten): _whisper_best()s
    # Scoring-Schleife nutzt striktes ">" -- bei einem Score-Gleichstand
    # gewinnt der ZUERST in der Liste stehende Kandidat. existing_lrc und ein
    # frischer Kandidat mit wortgleichem (aber z.B. anders formatiertem/
    # zeitgestempeltem) Text erzeugen denselben IDF-Jaccard-Score (der
    # arbeitet auf Wortmengen, nicht auf Bytes) -- ohne diese Reihenfolge
    # würde bei so einem Gleichstand faelschlich der frische Kandidat
    # gewinnen, obwohl inhaltlich gleichwertig, und existing_lrc koennte im
    # Anschluss faelschlich geloescht werden (siehe ROADMAP.md).
    all_candidates = ([existing_lrc] if has_existing else []) + candidates

    def _cleanup() -> None:
        for p in candidates:  # nur eigene Temp-Dateien, nie existing_lrc
            p.unlink(missing_ok=True)

    n_providers = len(lyrics_core._ALL_PROVIDERS)

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
                "existing_best": False,  # has_existing hier immer False, s.o.
            },
        )

    hit_str = ", ".join(provider_hits) if provider_hits else "—"
    prov_str = f"{len(candidates)}/{n_providers}: {hit_str}"

    # Bugfix (siehe ROADMAP.md, "existing_lrc im Konsens"): frueher lief der
    # Konsens NUR ueber die 4 frischen Provider (reiner Byte-Dedup vorher,
    # existing_lrc komplett aussen vor, nur ein nachtraeglicher Veto-Check
    # gegen einen abweichenden Konsens). Jetzt: EIN wort-basierter
    # Gruppierungsschritt (_group_candidates) ueber ALLE Kandidaten
    # (existing_lrc + frische) ersetzt sowohl den alten Byte-Dedup als auch
    # den separaten Veto-Check -- existing_lrc nimmt jetzt als vollwertiger
    # Kandidat am Konsens teil, kann sich dabei aber NIE mit einem
    # inhaltsgleichen frischen Nachfolger ihres urspruenglichen Providers
    # doppelt zaehlen (beide landen in derselben Gruppe, siehe dortiger
    # Docstring zum C3-Ausreisser-Exploit).
    #
    # Bugfix (siehe ROADMAP.md, "Fernando-Fall"): die Mindestanzahl-Schwelle
    # (_CONSENSUS_MIN_PROVIDERS) muss auf der ROHEN, ungruppierten Quellenzahl
    # pruefen (raw_count=len(all_candidates)) -- nicht auf der Gruppenzahl.
    # Sonst wird starke, echte Einigkeit (z.B. 4 von 5 Quellen sagen praktisch
    # dasselbe, landen in EINER Gruppe) faelschlich als "zu wenig Kandidaten"
    # verworfen, obwohl die verbleibenden Gruppen selbst hochgradig
    # uebereinstimmen. Die eigentliche Rechnung (Durchschnitt, Ausreisser-Wurf)
    # laeuft weiterhin auf den gruppierten Repraesentanten.
    grouped = lyrics_core._group_candidates(all_candidates)
    consensus_rep, consensus_jaccard = lyrics_core._provider_consensus(
        grouped, raw_count=lyrics_core._nonempty_candidate_count(all_candidates)
    )

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
            "existing_best": False,
        }
    elif flac_path is not None and flac_path.exists():
        # Bugfix (siehe ROADMAP.md, "Big City Beats"-Fall): der kontrastive
        # Kontext-Aufbau wird erst HIER angestossen, direkt bevor Whisper
        # tatsaechlich gebraucht wird -- nicht mehr pauschal fuer jeden
        # bewerteten Song in evaluate_all()s Schleife. Ein Song, der per
        # Konsens oder Genre-Skip (0 Kandidaten, "kein-provider") gar nie
        # bis hierher kommt, loest den teuren Aufbau dadurch nicht mehr aus,
        # nur weil er zufaellig der erste bewertete Song im Lauf war.
        lyrics_core._note_contrastive_evaluation()

        # Grund, WARUM Whisper ueberhaupt noetig ist (kein Konsens moeglich)
        # -- nur fuer die transiente Statuszeile in lyrics_core._whisper_best
        # (siehe dortiger Docstring, ROADMAP.md Punkt 6: ohne Grund fuer den
        # Nutzer nicht nachvollziehbar). Zu wenige Provider (< _CONSENSUS_MIN_
        # PROVIDERS) vs. genug Provider aber zu geringe Uebereinstimmung sind
        # zwei verschiedene Gruende -- _provider_consensus gibt bei zu wenigen
        # Providern IMMER 0.0 zurueck (kein echter Score), das waere sonst
        # irrefuehrend als "Konsens 0%" dargestellt.
        if len(candidates) < lyrics_core._CONSENSUS_MIN_PROVIDERS:
            whisper_reason = f"nur {len(candidates)}/{n_providers} Provider"
        else:
            whisper_reason = (
                f"Konsens nur {consensus_jaccard:.0%} < "
                f"{lyrics_core._CONSENSUS_MIN_JACCARD:.0%}"
            )

        # Modellwahl nach Sprach-Hint (siehe Moduldocstring) -- _whisper_best
        # liest _WHISPER_MODEL immer als Modul-Global, deshalb hier kurzzeitig
        # gesetzt und danach garantiert zurückgesetzt (auch bei Exceptions).
        prev_model = lyrics_core._WHISPER_MODEL
        lyrics_core._WHISPER_MODEL = _select_whisper_model(all_candidates)
        try:
            (
                best_path,
                best_score,
                has_vocals,
                whisper_words,
                model_used,
                lrc_lang,
                contrastive_margin,
                early_stopped,
            ) = lyrics_core._whisper_best(
                flac_path,
                all_candidates,
                expected_dur,
                artist=artist_key,
                title=titel_key,
                reason=whisper_reason,
            )
        finally:
            lyrics_core._WHISPER_MODEL = prev_model
        method = f"whisper-{model_used}" if model_used else "heuristik"
        model_str = f"[{model_used}]" if model_used else ""
        lang_str = lrc_lang or ""
        words_str = f"{whisper_words}W"
        early_stop_str = "früh-gestoppt" if early_stopped else ""
        whisper_head = " ".join(
            p for p in [model_str, lang_str, "Whisper", words_str, early_stop_str] if p
        )

        if not has_vocals:
            # Frueher: bei >=2 Providern im Konsens wurde deren LRC trotzdem
            # gespeichert ("Konsens (kein Vokal)"). Abgeschafft (siehe
            # ROADMAP.md) -- Provider-Konsens allein kann nicht unterscheiden,
            # ob eine KONKRETE Aufnahme gesungen wird oder nur der Songtitel
            # offizielle Lyrics hat (z.B. Instrumental-Cover eines Songs mit
            # bekanntem Text). has_vocals=False gilt jetzt immer als
            # kein-vokal, unabhaengig vom Provider-Konsens.
            #
            # Bugfix (siehe ROADMAP.md, "Pohlmann-Fall"): existing_best hier
            # bewusst IMMER False, auch wenn existing_lrc selbst best_path
            # war -- das Whisper-Verdikt ist final, unabhaengig davon ob der
            # Text von einem Provider oder von der Platte kam. Frueher schuetzte
            # "existing_lrc war der beste Kandidat" auch dann vor dem Loeschen,
            # wenn sie schlicht der EINZIGE Kandidat war (0 Provider-Treffer) --
            # ein katastrophal schlechter Match "gewann" dann automatisch gegen
            # niemanden. Als frischer Einzel-Kandidat waere dieselbe Datei nie
            # akzeptiert worden.
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
                "existing_best": False,
            }
        elif lyrics_core._whisper_accept(
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
                "early_stopped": early_stopped,
                "existing_best": False,
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
                "existing_best": False,  # Whisper-Verdikt final, siehe kein-vokal-Zweig oben
            }
    else:
        best_content, _score = lyrics_core._heuristic_best(all_candidates, expected_dur)
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
                "existing_best": False,
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
                # Konservativ: ohne Audiodatei kein Beweis, dass existing_lrc
                # falsch ist (siehe ROADMAP.md) -- Loeschen nur mit Beleg.
                "existing_best": has_existing,
            }

    _cleanup()
    extras["content"] = best_content
    if best_content is None:
        return False, info_str, extras
    return True, info_str, extras


def _resolve_expected_dur(flac_path: Path) -> float:
    """Liest die erwartete Songdauer aus release.json (falls vorhanden) --
    exakt wie main() das im alten lyrics_core.py tut (meta_title/
    _clean_query_title/_load_release), hier moeglich weil flac_path
    vorliegt und die Original-Gross-/Kleinschreibung direkt aus den Tags
    gelesen wird (die songs-Tabelle speichert nur normalisierte Schluessel,
    siehe scan_songs.py)."""
    meta_artist, meta_title, _meta_genre = lyrics_core._read_audio_tags(flac_path)
    title = meta_title or (
        flac_path.stem.split(" - ", 1)[-1]
        if " - " in flac_path.stem
        else flac_path.stem
    )
    _artist, tracks_by_title = lyrics_core._load_release(flac_path.parent)
    return tracks_by_title.get(unicodedata.normalize("NFC", title), 0.0)


def _skip_reevaluation(
    conn: sqlite3.Connection, audio_path: Path, artist_key: str, titel_key: str
) -> bool:
    """True wenn dieser Track NICHT neu bewertet werden muss -- nutzt
    dasselbe Prädikat wie write_lrc.write_all() (lyrics_core.
    _cache_entry_up_to_date(), siehe dort -- war vorher als fast identischer
    Code dreifach unabhängig implementiert, siehe ROADMAP.md,
    Redundanz-Aufräumen).

    Rein lesend -- KEINE Ordner-Sperre wie in write_lrc.write_all (das dort
    zusätzlich schreibt). Ein Race mit einem gleichzeitig laufenden
    --schreiben ist unkritisch: hier wird nichts geschrieben, nur eine
    Lese-Entscheidung getroffen -- im schlimmsten Fall wird einmal zu viel
    statt zu wenig bewertet.

    Braucht audio_path, um dieselbe JSON-Datei wie write_lrc.write_all zu
    finden (.fetch_songtext.json im selben Ordner) -- ohne zugeordnete Datei
    (kein PFAD-Lauf) kann diese Prüfung nicht stattfinden, siehe Aufrufer.

    Sig-Backfill (siehe lyrics_core._sig_backfill-Docstring): fehlt einem
    sonst gültigen Eintrag nur die "sig" (reine Migration, kein echter
    Genre-Wechsel), gilt er hier ebenfalls als "kann übersprungen werden" --
    schreibt aber NICHTS (rein lesend, siehe oben). Das tatsächliche
    Nachtragen auf der Platte übernimmt write_lrc.write_all() für denselben
    Song im selben Durchlauf.
    """
    dir_cache = lyrics_core._load_cache(audio_path.parent)
    cache_key = unicodedata.normalize("NFC", audio_path.name)
    entry = dir_cache.get(cache_key)
    lrc_path = audio_path.with_suffix(".lrc")
    if lyrics_core._cache_entry_up_to_date(
        entry, lrc_path, conn, artist_key, titel_key
    ):
        return True
    return lyrics_core._sig_backfill(entry, conn, artist_key, titel_key) is not None


def evaluate_all(
    conn: sqlite3.Connection,
    scope: set[tuple[str, str]] | None = None,
    file_song_map: dict[tuple[str, str], Path] | None = None,
    quiet: bool = False,
) -> dict[str, int]:
    """Phase 4: bewertet Songs aus "songs" -- optional eingegrenzt auf `scope`
    (siehe fetch_providers.fetch_all fuer dasselbe Scope-Prinzip: ohne PFAD
    bewusst die GANZE DB, mit PFAD nur die Songs des aktuellen Laufs).

    Reihenfolge: mit nicht-leerem `file_song_map` wird in dessen
    Einfuegereihenfolge iteriert (Datei-/Verzeichnisreihenfolge, siehe
    songtext_pipeline.build_file_song_map) statt alphabetisch nach
    Kuenstler/Titel -- Nutzer-Feedback: die Konsolenausgabe soll sich mit
    der Tracklist im Ordner decken, nicht mit der DB-Sortierung. Ohne
    file_song_map (kein PFAD) bleibt es bei der alphabetischen DB-Reihenfolge,
    mangels jeder Datei-Information. Anzeige entsprechend: Dateiname wenn
    vorhanden, sonst "artist_key / titel_key" als Fallback.

    `file_song_map` (artist_key, titel_key) -> Audiodatei-Pfad, siehe
    songtext_pipeline.build_file_song_map -- nur fuer Songs mit Eintrag dort
    kann Whisper bei einem Transkript-Cache-Miss live transkribieren; andere
    Songs fallen auf Konsens/Dauer-Heuristik zurueck.

    Prueft vorab nur, ob das faster-whisper-PAKET ueberhaupt installiert ist
    (lyrics_core._faster_whisper_available() -- reiner Import-Check, laedt
    KEIN Modell). Welches Modell (medium/large-v3) tatsaechlich pro Song
    geladen wird, entscheidet _select_whisper_model() je nach Sprache (siehe
    Moduldocstring); beide werden dabei lazy von lyrics_core.
    _get_whisper_model() geladen und gecacht, hoechstens einmal pro
    Modellname und Lauf -- und NUR wenn ein Song im Scope das jeweilige
    Modell auch wirklich braucht (kein Konsens UND kein bereits gecachtes
    Transkript). Frueher wurde hier `medium` als Verfuegbarkeits-Sonde immer
    voll geladen, selbst wenn im gesamten Lauf kein einziger Song `medium`
    brauchte (z.B. eine rein deutschsprachige Bibliothek/Album, die nur
    `large-v3` nutzt) -- siehe ROADMAP.md.

    quiet=True unterdrückt die Kopfzeile ("Bewerte N Song(s) ...") und die
    persistente Ergebniszeile pro Song -- gesetzt vom kombinierten
    Datei-für-Datei-Lauf aus songtext_pipeline.py, wenn im selben Durchlauf
    gleich danach --schreiben für denselben Song läuft und dessen EINE
    Abschlusszeile (write_lrc.write_all, ruft evaluate_song() ohnehin ein
    zweites Mal auf) diese Zwischenzeile sonst dupliziert (Nutzer-Feedback:
    "zeig auf trackebene [...] pro track eine zeile", siehe ROADMAP.md).
    """
    if not lyrics_core._faster_whisper_available():
        print("FEHLER: faster-whisper nicht verfügbar (Paket nicht installiert).")
        return {}

    fetch_providers._prepare_lyrics_core_globals(conn)

    file_song_map = file_song_map or {}
    if file_song_map:
        # Mit PFAD: in Datei-Reihenfolge iterieren (dict-Einfügereihenfolge
        # -- file_song_map wird von songtext_pipeline.py aus derselben
        # dateibasierten Liste gebaut wie build_file_song_map() sie liefert,
        # also Verzeichnis-/Dateiname-Reihenfolge, NICHT alphabetisch nach
        # Künstler/Titel). Nutzer-Feedback: Durchläufe sollen nach
        # Dateinamen sortiert sein, nicht nach Band-/Songname, damit sich
        # die Konsolenausgabe mit der Tracklist im Ordner deckt.
        to_evaluate = list(file_song_map.keys())
    else:
        # Ohne PFAD (globaler Lauf über die ganze DB, keine Dateien bekannt)
        # bleibt die bisherige alphabetische DB-Reihenfolge einzige Option.
        rows = conn.execute(
            "SELECT artist_key, titel_key FROM songs ORDER BY artist_key, titel_key"
        ).fetchall()
        to_evaluate = [(a, t) for a, t in rows if scope is None or (a, t) in scope]
    total = len(to_evaluate)
    if total and not quiet:
        print(f"Bewerte {total} Song(s) ...")

    counts = {
        "konsens": 0,
        "whisper-akzeptiert": 0,
        "abgelehnt": 0,
        "kein-provider": 0,
        "uebersprungen": 0,
    }

    # Kontrastiver Kontext (siehe lyrics_core._build_contrastive_context) UND
    # der IDF-Refresh alle lyrics_core._idf_refresh_interval(N) Songs laufen
    # lazy anhand tatsaechlich Whisper-bedürftiger Songs (siehe
    # lyrics_core._note_contrastive_evaluation, aufgerufen aus evaluate_song()
    # direkt vor dem Whisper-Zweig, NICHT hier in der Schleife) -- ein Lauf,
    # in dem JEDER Song wegen _skip_reevaluation uebersprungen ODER per
    # Konsens/Genre-Skip ohne Whisper geloest wird, baut den Kontext dann gar
    # nicht erst auf (realer Befund: sowohl ein reiner Wiederholungslauf über
    # einen unveraenderten Pfad als auch ein Lauf, dessen erster bewerteter
    # Song zufaellig genre-geskippt war, loesten den teuren Aufbau bisher
    # trotzdem sofort aus, siehe ROADMAP.md). Der Fortschritt (wurde je
    # gebaut, wie viele Songs seit dem letzten Aufbau) ist dabei bewusst
    # modulglobal in lyrics_core.py, nicht lokal hier -- ruft
    # songtext_pipeline.py evaluate_all() mehrfach im selben Prozess auf
    # (z.B. einmal pro Ordner), bleibt der Fortschritt ueber diese Aufrufe
    # hinweg erhalten, siehe dortiger Docstring.

    for i, (artist_key, titel_key) in enumerate(to_evaluate, start=1):
        flac_path = file_song_map.get((artist_key, titel_key))
        # Anzeige: Dateiname wenn vorhanden (Nutzer-Feedback -- besser
        # nachvollziehbar als der normalisierte Cache-Schlüssel), sonst
        # Fallback auf "artist_key / titel_key" (globaler Lauf ohne PFAD).
        label = (
            flac_path.name if flac_path is not None else f"{artist_key} / {titel_key}"
        )

        # Skip nur moeglich MIT zugeordneter Audiodatei (JSON-Ordner-Cache
        # ist datei-basiert) -- ohne PFAD (file_song_map leer) wird wie
        # bisher jeder Song neu bewertet.
        if flac_path is not None and _skip_reevaluation(
            conn, flac_path, artist_key, titel_key
        ):
            counts["uebersprungen"] += 1
            continue

        # "i/total: " nur bei echten Mehrfach-Laeufen (siehe fetch_providers.py,
        # gleiche Begruendung: bei total==1 reine Redundanz ohne Info).
        counter = f"{i}/{total}: " if total > 1 else ""
        lyrics_core._print_status(f"  {counter}{label} ...")

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

        if not quiet:
            lyrics_core._tprint(f"{lyrics_core._ts()}  {label}  {info_str}")

    return counts
