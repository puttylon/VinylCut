#!/usr/bin/env python3
"""Gemeinsame Bausteine der Songtexte-Pipeline: Tag-Lesen, Provider-Abfrage
mit Rate-Limit-Handling, Konsens-Scoring, Whisper-Transkription + kontrastive
Marge, JSON-Ordner-Cache. Ehemals Teil des monolithischen fetch_songtext.py
(bis v1.13.2) -- seit dem Umbau auf die Phasen-Pipeline (scan_songs.py,
fetch_providers.py, evaluate_lyrics.py, write_lrc.py, songtext_pipeline.py,
cut.py) hierher ausgelagert, damit kein einzelnes Modul mehr alles kennt.
Die Versionsgeschichte bis v1.13.2 steht in der Git-Historie von
fetch_songtext.py, nicht mehr hier.

Enthält bewusst NICHT: main()/CLI (siehe songtext_pipeline.py),
fetch_lrc() (das alte All-in-eins aus Live-Abfrage+Entscheidung+Schreiben --
abgelöst durch evaluate_lyrics.evaluate_song() + write_lrc.write_all(), bzw.
für cut.py durch fetch_one_song() dort), _whisper_rerun_needed() (nur vom
alten --no-whisper-Flag gebraucht, das die neue Pipeline nicht hat).
"""

import errno
import fcntl
import hashlib
import math
import random
import re
import os
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import IO

try:
    import cache_store
except ImportError:
    cache_store = None

# Versionsgeschichte bis hier: siehe Git-Historie von fetch_songtext.py.
# Weiterhin nur für den JSON-Ordner-Cache-Eintrag ("v"-Feld, siehe
# _cache_entry_valid) gebraucht -- kein eigenständiges CLI-Tool mehr.
__version__ = "1.13.24"

_ALL_PROVIDERS = ["lrclib", "musixmatch", "netease", "genius"]
_PROVIDER_TIMEOUT = 20  # Sekunden pro Provider-Abfrage
_AUDIO_EXTENSIONS = {".flac", ".mp3", ".ogg", ".opus", ".m4a", ".aac", ".wav"}

# Rate-Limit-Backoff pro Provider (siehe _rate_limit_report()). Recherchiert im
# syncedlyrics-Quellcode: Musixmatch meldet Rate-Limits über einen im JSON
# eingebetteten status_code (402 = Kontingent, 401 = Captcha/Anti-Bot), NetEase
# nur über eine generische Exception, Genius und lrclib geben laut Quellcode
# GAR KEIN Signal — dort greift nur der proaktive Mindestabstand.
_RATE_LIMIT_FLOOR_SEC = 1.5  # proaktiver Mindestabstand zwischen Anfragen pro Provider
_RATE_LIMIT_BASE_SEC = (
    10.0  # reaktive Basis-Strafe bei 402/generischem Fehler — verankert an
)
# syncedlyrics' eigenem time.sleep(10) beim Musixmatch-Token-Refresh nach 401
_RATE_LIMIT_CAPTCHA_SEC = (
    30.0  # längerer Cooldown bei 401/Captcha (Anti-Bot, kurzer Retry hilft nicht)
)
_RATE_LIMIT_MAX_SEC = 60.0  # Eskalations-Obergrenze bei wiederholten Treffern
# v1.9.4: Musixmatch blockiert in der Praxis oft dauerhaft (captcha bei JEDEM
# Song) — die Eskalation allein (gedeckelt bei _RATE_LIMIT_MAX_SEC) wartet dann
# bei JEDEM folgenden Song erneut ~60s, ohne je zum Ziel zu kommen. Ab
# _RATE_LIMIT_STUCK_THRESHOLD Treffern IN FOLGE wechselt der Provider in eine
# lange Ruhephase (_RATE_LIMIT_LONG_PAUSE_SEC), in der JEDER Versuch instant
# (ohne sleep, ohne Live-Abfrage) als Fehlschlag gilt — siehe _rate_limit_wait.
_RATE_LIMIT_STUCK_THRESHOLD = (
    5  # so viele Treffer IN FOLGE lösen die lange Ruhephase aus
)
_RATE_LIMIT_LONG_PAUSE_SEC = 900.0  # 15 Minuten Ruhephase, danach EIN frischer Versuch

_rate_limit_lock = threading.Lock()
_rate_limit_state: dict[
    str, dict
] = {}  # provider -> {"next_allowed": float, "consecutive_hits": int}

# LRC-Timestamps enden oft vor dem Track-Ende (Instrumental-Outro → kein Text).
# Asymmetrische Toleranz: zu kurz ist normal, zu lang bedeutet falscher Song.
_LRC_TOO_SHORT_TOLERANCE = 0.40  # last_ts darf bis zu 40 % kürzer als der Track sein
_LRC_TOO_LONG_TOLERANCE = 0.10  # last_ts darf höchstens 10 % länger als der Track sein

# Whisper: einstufige Verifikation mit small. base wurde entfernt (v1.7.0) —
# unzuverlässig bei nicht-englischen Songs (falsch-negative "kein Vokal").
# v1.7.7: Containment (Anteil Transkript-Wörter in LRC) durch IDF-gewichtetes
# Jaccard ersetzt (_idf_jaccard) — Containment akzeptierte zu oft falsche Songs,
# wenn wenige generische Wörter (Stopwords) zufällig übereinstimmten. IDF-Jaccard
# gewichtet seltene (inhaltstragende) Wörter stark, häufige kaum. Schwelle 0,065
# an 20 gelabelten Songs (5 Sprachen) validiert: niedrigster korrekter Wert 0,089,
# höchster falscher Wert 0,053 — Reserve nach beiden Seiten (siehe metric_bakeoff).
_WHISPER_MIN_OVERLAP = 0.065  # Schwellwert: ab hier wird eine LRC akzeptiert

# Sprachspezifische Schwellen: (schwelle, kalibriert_bei_n_docs). Diese
# ABSOLUTEN Schwellen greifen nur noch als Fallback, wenn die kontrastive
# Marge mangels ausreichendem gleichsprachigen Hintergrund-Pool None ist
# (siehe _CONTRASTIVE_MIN_BACKGROUND/_whisper_accept). Default
# (_WHISPER_MIN_OVERLAP) gilt fuer alle Sprachen ohne eigenen Eintrag hier
# (u.a. Englisch: dessen eigene Tabelle ist kaum kleiner als die alte
# globale, keine Neukalibrierung noetig -- empirisch bestaetigt in v1.9.13,
# siehe ROADMAP). Kalibriert an 8 Testfaellen (4 akzeptieren/4 ablehnen,
# echte Whisper-Transkriptionen), siehe ROADMAP v1.9.13 fuer Details.
_WHISPER_MIN_OVERLAP_BY_LANG: dict[str, tuple[float, int]] = {
    "de": (0.043, 2212),
}
_WHISPER_MODEL = "small"
_WHISPER_PRE_ROLL = 0.0  # direkt beim ersten LRC-Timestamp starten

# Provider-Konsens: wenn genug Provider übereinstimmen, wird Whisper-Threshold überstimmt.
_CONSENSUS_MIN_PROVIDERS = (
    3  # mindestens N Provider müssen einen Treffer geliefert haben
)
_CONSENSUS_MIN_JACCARD = (
    0.40  # mindest-Übereinstimmung zwischen den Provider-LRCs untereinander
)

_VOCALS_MIN_WORDS = 5  # Fallback: weniger Wörter → als instrumental behandelt
_VOCALS_NO_SPEECH_THOLD = (
    0.65  # faster_whisper: avg no_speech_prob > dies → instrumental
)
_HALLUCINATION_MIN_WORDS = 20  # ab hier Wiederholungsrate prüfen
_HALLUCINATION_MAX_UNIQUE_RATIO = 0.25  # < 25 % einzigartige Wörter → Halluzination
# _HALLUCINATION_AVG_LOGPROB entfernt: sprachbiased (Deutsch < Englisch), _is_hallucination reicht

_CACHE_FILENAME = ".fetch_songtext.json"
_CACHE_LOCKFILE = ".fetch_songtext.lock"  # schützt _save_cache + Ordner-Claim (siehe _try_claim_folder) vor parallel laufenden Instanzen
_CACHE_MIN_VERSION = "1.7.1"  # v1.7.1: Abbruch-Check bei fehlendem Whisper-Modell — alle Einträge neu prüfen

# Genres die keinen Songtext haben — Substring-Matching (Kleinschreibung)
_SKIP_GENRE_KEYWORDS = {
    # Hörbuch / Audiobook
    "hörbuch",
    "hoerbuch",
    "audiobook",
    "audio book",
    # Hörspiel / Audio Drama
    "hörspiel",
    "hoerspiel",
    "audio play",
    "audioplay",
    "radio play",
    "radioplay",
    "radio drama",
    "radio show",
    # Instrumental
    "instrumental",
    # Gesprochenes Wort
    "podcast",
    "speech",
    "spoken word",
    "spoken",
    "interview",
    "lesung",
    "vortrag",
    "reading",
    # Soundeffekte / Ambient ohne Text
    "sound effects",
    "sound effect",
    "sfx",
    "noise",
    "field recording",
    "nature sounds",
}


_whisper_models: dict = {}  # name → WhisperModel

# Cache-Modul (siehe CACHE_DESIGN.md). _cache_conn wird einmal pro Lauf in main()
# gesetzt (None = Cache inaktiv: cache_store fehlt, --no-cache, oder DB-Open
# fehlgeschlagen). _cache_lock schützt die eine Connection gegen gleichzeitigen
# Zugriff aus den Provider-Worker-Threads (_query_provider läuft im ThreadPoolExecutor).
# _cache_only (--cache-only) verbietet JEDE Live-Provider-Abfrage, auch für
# Provider ohne gültigen Cache-Treffer (kein Eintrag, abgelaufen, oder
# status="fehlschlag") — siehe Guard in _query_provider.
_cache_conn = None
_cache_ttl_days = 30
_cache_refresh = False
_cache_only = False
_cache_lock = threading.Lock()

# _retry_missing() setzt _cache_refresh für die Dauer seines Laufs auf True --
# aber NUR, um den EIGENEN Cache-Lookup in _query_provider zu zwingen, ein
# gecachtes "nichts" nicht als gültigen (nicht abgelaufenen) Treffer zu werten
# (siehe _retry_missing-Docstring). Das ist eine andere Absicht als bei
# --force/--refresh-cache, wo _cache_refresh="wirklich alles frisch, auch den
# Dump umgehen" bedeutet. Ohne diese Unterscheidung würde der lrclib-Dump-
# Lookup in _query_provider (der ebenfalls an "not _cache_refresh" hängt)
# während JEDES --retry-missing-Laufs übersprungen -- ein Dump-Treffer könnte
# dann nie einen Live-Versuch ersparen, obwohl genau das der Sinn ist. Siehe
# Guard in _query_provider.
_retry_missing_active = False

# Lokaler LRCLib-Datenbank-Abzug (SMB-Netzlaufwerk, Original-LRCLib-Schema,
# aktuell nicht mehr aktiv befüllt): wird in _query_provider bei der lrclib-
# Quelle VOR einer echten Live-Abfrage durchsucht (cache_store.lookup_lrclib_dump),
# um wiederholte Live-Anfragen zu sparen. _lrclib_dump_conn wird EINMAL pro Lauf
# in main() geöffnet (None = nicht verfügbar: Mount fehlt, Datei fehlt, sonstiger
# Fehler beim Öffnen -- still degradieren, kein Absturz, wie beim regulären
# Cache). Denselben _cache_lock wie _cache_conn mitbenutzen (kurze Lookups,
# kein eigenes Lock-Objekt nötig).
_LRCLIB_DUMP_PATH = Path("/Volumes/music/db.sqlite3")
_lrclib_dump_conn = None

# Kontrastive Marge (seit v1.10.0 Standardverfahren der Whisper-Verifikation,
# vormals --contrastive-experiment). Ersetzt die ABSOLUTE Whisper-
# Verifikationsschwelle (_whisper_threshold_for) durch eine KONTRASTIVE Marge:
# statt "IDF-Jaccard >= feste sprachabhängige Schwelle" wird gefragt "hebt
# sich der beste Kandidat deutlich vom Zufall ab?" (Marge = best_score − bester
# Score von K=20 zufälligen ANDEREN Songs gleicher Sprache aus dem Cache, als
# Hintergrund). Betrifft NUR die Whisper-Verifikation, NICHT den Konsens-Pfad
# (der bleibt Jaccard, hat mit IDF nichts zu tun). Nutzt eine GLOBALE IDF-
# Tabelle aus der Cache-DB (siehe _build_contrastive_context) — keine Datei-
# basierte, sprachspezifische Tabelle mehr — ein DB-Test hat validiert, dass
# eine sprachunabhängige globale IDF für die kontrastive Entscheidung
# ausreicht (AUC-Differenz global vs. sprachrichtig DE: −0,0007). Siehe
# scratch_contrastive_test_ergebnis.md (Nachbar-Worktree) für die volle
# Analyse.
# Marge-Schwelle: an 8200 Cache-Texten / 680 Eval-Songs (EN+DE gemeinsam)
# optimiert — 95,0% Genauigkeit ggü. 90,6% der heutigen zwei sprachspezifischen
# absoluten Schwellen (siehe scratch_contrastive_test_ergebnis.md Abschnitt 3).
# PROVISORISCH aus synthetischer Kalibrierung, nicht an einem echten
# Produktionslauf verifiziert.
_CONTRASTIVE_MARGIN = 0.0115
# Hybrid-Boden (v1.9.14): ein hoher absoluter Score allein reicht schon zur
# Akzeptanz, unabhängig vom Hintergrund-Vergleich -- faengt Faelle ab, in denen
# der Hintergrund-Pool durch einen einzelnen fehlerhaften Kandidaten (Provider-
# Fehltreffer bei einem ANDEREN, zufaellig gezogenen Song) kontaminiert ist und
# dadurch die Marge eines eigentlich korrekten Songtexts unter die Schwelle
# drueckt (siehe Garth-Brooks-"White-Christmas"-Fall in ROADMAP.md).
_CONTRASTIVE_ABSOLUTE_FLOOR = 0.3
_CONTRASTIVE_BACKGROUND_K = (
    20  # Größe des Hintergrund-Pools (zufällige andere Songs gleicher Sprache)
)
_CONTRASTIVE_MIN_BACKGROUND = 5  # darunter: Hintergrund zu klein für eine sinnvolle Marge -> Fallback auf alte absolute Schwelle
_CONTRASTIVE_SEED = 20260714  # fester Seed (identisch zum Validierungsskript) für reproduzierbare Hintergrund-Ziehung

# In-memory-Kontext für die kontrastive Marge, einmal pro Lauf gebaut (siehe
# _build_contrastive_context, in main() aufgerufen). None solange nicht gebaut.
_contrastive_idf: "tuple[int, dict] | None" = None  # (n_docs, df) -- globale Cache-IDF
_contrastive_lang_pools: "dict[str, list[int]] | None" = (
    None  # Sprache -> [song_id, ...]
)
_contrastive_song_texts: "dict[int, list[str]] | None" = (
    None  # song_id -> Kandidatentexte (roh)
)
_contrastive_song_words_cache: dict = {}  # song_id -> tokenisierte Kandidatentexte, memoisiert

# Verfolgt "wurde der Kontext in diesem Prozess je gebaut" + "wie viele Songs
# seit dem letzten Aufbau bewertet" -- bewusst NICHT als lokale Variablen in
# evaluate_lyrics.evaluate_all() (siehe _note_contrastive_evaluation unten):
# ruft songtext_pipeline.py evaluate_all() mehrfach im selben Prozess auf
# (z.B. einmal pro Ordner bei einer Ordner-für-Ordner-Schleife), müssen
# beide Zähler ÜBER diese Aufrufe hinweg erhalten bleiben, sonst würde der
# Kontext bei jedem Aufruf erneut als "noch nie gebaut" gelten und viel
# öfter als beabsichtigt (alle _IDF_REFRESH_INTERVAL Songs) neu aufgebaut.
_contrastive_context_built_ever = False
_contrastive_context_evaluations_since_refresh = 0
_contrastive_context_last_data_signature: int | None = None


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _read_audio_tags(audio_path: Path) -> tuple[str, str, str]:
    """Liest ARTIST, TITLE und GENRE via mutagen (FLAC, MP3, OGG, M4A …). Gibt ('', '', '') bei Fehler."""
    try:
        from mutagen import File as MutagenFile

        tags = MutagenFile(audio_path, easy=True)
        if tags is None:
            return "", "", ""
        artist = str(tags.get("artist", [""])[0])
        title = str(tags.get("title", [""])[0])
        genre = str(tags.get("genre", [""])[0])
        return artist, title, genre
    except Exception:
        return "", "", ""


def _clean_query_title(title: str) -> str:
    """Entfernt Klammer-Zusätze (Live/Remix/Remaster/Edit/…) für die Provider-Suche.

    Lyrics-Provider indizieren i.d.R. nur den Kern-Songtitel — lange Zusätze wie
    "(Live In Osaka Japan 16th August 1972) (2014 Remix)" führen zu 0 Treffern,
    obwohl der Songtext (identisch zur Studio-Version) längst vorhanden wäre.
    Nur für den Suchbegriff verwendet — Title-Tag/Dateiname/.lrc bleiben unberührt.
    """
    cleaned = re.sub(r"\s*[\(\[][^\(\)\[\]]*[\)\]]", "", title).strip()
    return cleaned or title


def _is_skip_genre(genre: str) -> bool:
    g = genre.lower()
    return any(kw in g for kw in _SKIP_GENRE_KEYWORDS)


def _song_keys(artist: str, title: str) -> tuple[str, str]:
    """Die EINE gemeinsame Stelle für (artist_key, titel_key) -- Vorgabe:
    mehrfach genutzte Funktionen gehören in ein zentrales Modul, nicht
    redundant in jeden Aufrufer kopiert (siehe ROADMAP.md). scan_songs.py
    und songtext_pipeline.build_file_song_map() bauten diese drei Zeilen
    (_clean_query_title + zweimal cache_store.normalize_key) bisher
    unabhängig voneinander -- laufen sie auseinander, findet die
    Datei-zu-Song-Zuordnung den passenden Eintrag in "songs" nicht mehr.

    Titel wird VOR der Normalisierung über _clean_query_title bereinigt --
    exakt dieselbe Reihenfolge, die beim Anlegen der songs-Zeile gilt (siehe
    CACHE_DESIGN.md, "Normalisierung")."""
    clean_title = _clean_query_title(title) if title else title
    return cache_store.normalize_key(artist), cache_store.normalize_key(clean_title)


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


def _heuristic_best(
    candidates: list[Path], expected_dur: float = 0.0
) -> tuple[bytes | None, tuple[int, int, int]]:
    """Wählt per Dauer-Heuristik den besten Kandidaten (ohne Whisper).

    Gibt (Inhalt, score) zurück. Inhalt ist None wenn der beste Kandidat die
    Dauer-Toleranz überschreitet (valid=0) — kein blindes Schreiben eines
    offensichtlich falschen Songs.
    """
    best = max(candidates, key=lambda p: _score_lrc(p, expected_dur))
    score = _score_lrc(best, expected_dur)
    if not score[0]:
        return None, score
    return best.read_bytes(), score


def _rate_limit_wait(provider: str) -> bool:
    """Wartet, falls für `provider` noch eine proaktive/reaktive Sperre besteht.

    Gibt True zurück, wenn der Provider GERADE in der langen Ruhephase steckt
    (>= _RATE_LIMIT_STUCK_THRESHOLD Treffer in Folge, Sperre noch nicht
    abgelaufen) — der Aufrufer (_query_provider) soll dann den kompletten
    Live-Versuch überspringen, OHNE zu schlafen. Grund: fetch_lrc() wartet
    synchron (ThreadPoolExecutor + as_completed) auf alle 4 Provider-Threads,
    bevor der nächste Track drankommt — ein echter time.sleep() über die volle
    Ruhephase (15 Min) würde den GESAMTEN Lauf einfrieren, nicht nur diesen
    einen Provider.

    Gibt False zurück in allen anderen Fällen: keine Sperre aktiv, normale
    kurze Backoff-Wartezeit (dann wird hier wie bisher via time.sleep(wait)
    gewartet), oder die lange Ruhephase ist gerade abgelaufen (dann ist kein
    sleep mehr nötig — ein frischer Live-Versuch ist fällig, dessen Ergebnis
    _rate_limit_report auswertet)."""
    with _rate_limit_lock:
        state = _rate_limit_state.get(provider, {})
        next_allowed = state.get("next_allowed", 0.0)
        consecutive_hits = state.get("consecutive_hits", 0)
    wait = next_allowed - time.monotonic()
    if wait <= 0:
        return False
    if consecutive_hits >= _RATE_LIMIT_STUCK_THRESHOLD:
        return True  # lange Ruhephase aktiv — kein sleep, Aufrufer überspringt
    time.sleep(wait)
    return False


def _rate_limit_report(provider: str, stderr: str) -> str | None:
    """Wertet stderr auf Rate-Limit-Signale aus und setzt ggf. eine Sperre.

    Musixmatch meldet Rate-Limits über einen im JSON eingebetteten status_code
    ("Got status code N for ..." auf stderr, siehe syncedlyrics/providers/
    musixmatch.py): 402 = Kontingent/Rate-Limit, 401 = Captcha/Anti-Bot (kein
    kurzer Retry sinnvoll). NetEase liefert nur eine generische Exception-
    Meldung ("An error occurred while searching for an LRC on ..."). Genius
    und lrclib geben laut Quellcode bei HTTP-Fehlern (inkl. 429) KEIN Signal
    — sie liefern still None zurück, ununterscheidbar von "nicht gefunden".
    Dort greift ausschließlich der proaktive Mindestabstand (Fallback-Zweig
    unten, auch bei sauberem Erfolg — das ist der proaktive Floor).

    Gibt bei einem transienten Rate-Limit/Captcha/Fehler-Signal den erkannten
    Grund zurück ("captcha" oder "rate_limit"), sonst None. Der Cache hält
    einen transienten Fehlschlag IMMER unter status="fehlschlag" fest (siehe
    CACHE_DESIGN.md) — er zählt aber nie als gültiger Cache-Treffer.

    Erreicht consecutive_hits NACH dem Hochzählen _RATE_LIMIT_STUCK_THRESHOLD,
    wird statt der normalen (bei _RATE_LIMIT_MAX_SEC gedeckelten) Eskalation
    die lange Ruhephase (_RATE_LIMIT_LONG_PAUSE_SEC) gesetzt — siehe
    _rate_limit_wait für die Begründung (kein blockierender Lauf-weiter-Sleep).
    Unterhalb des Schwellwerts bleibt die bisherige Formel unverändert.
    """
    with _rate_limit_lock:
        state = _rate_limit_state.setdefault(
            provider, {"next_allowed": 0.0, "consecutive_hits": 0}
        )
        if re.search(r"[Gg]ot status code 401", stderr) or "captcha" in stderr.lower():
            hits_before = state["consecutive_hits"]
            state["consecutive_hits"] += 1
            if state["consecutive_hits"] >= _RATE_LIMIT_STUCK_THRESHOLD:
                delay = _RATE_LIMIT_LONG_PAUSE_SEC
            else:
                delay = min(
                    _RATE_LIMIT_CAPTCHA_SEC * (2**hits_before), _RATE_LIMIT_MAX_SEC
                )
            grund = "captcha"
        elif re.search(r"[Gg]ot status code 402", stderr) or (
            "An error occurred while searching for an LRC on" in stderr
        ):
            hits_before = state["consecutive_hits"]
            state["consecutive_hits"] += 1
            if state["consecutive_hits"] >= _RATE_LIMIT_STUCK_THRESHOLD:
                delay = _RATE_LIMIT_LONG_PAUSE_SEC
            else:
                delay = min(
                    _RATE_LIMIT_BASE_SEC * (2**hits_before), _RATE_LIMIT_MAX_SEC
                )
            grund = "rate_limit"
        else:
            state["consecutive_hits"] = 0
            delay = _RATE_LIMIT_FLOOR_SEC
            grund = None
        state["next_allowed"] = time.monotonic() + delay
        return grund


# Provider-unabhängig: prüft nur den rohen Fetch-Text, egal von welchem
# Provider er stammt (siehe _looks_like_translation). Bislang nur bei Genius
# beobachtet (Genius indiziert Übersetzungsseiten als eigene Song-Objekte,
# die syncedlyrics' Genius-Provider ungeprüft als ersten Suchtreffer übernimmt
# -- siehe Telepatía-Fall, ROADMAP.md), Musixmatch/Netease/lrclib/Megalobiz
# matchen dagegen über Titel-/Künstler-Ähnlichkeit und liefern strukturell
# keine separaten Sprachvarianten. Der Filter ist trotzdem bewusst nicht auf
# Genius beschränkt, falls ein anderer Provider künftig doch mal eine
# Übersetzungsseite liefert.
# Braucht BEIDES im selben Klammer-Zusatz: ein Übersetzungs-Schlüsselwort UND
# einen Sprachnamen (in beliebiger Reihenfolge -- Genius mischt Konventionen:
# "(English Translation)" keyword-LETZT, aber "(Traducción al Español)"/
# "(Traduction Française)"/"(Tradução em Português)" keyword-ERST). Nur das
# Schlüsselwort allein reicht NICHT: live gegen die echte Cache-DB geprüft,
# ergab 2 echte False Positives, die beide lediglich "Translation" enthalten
# OHNE Sprachnamen -- "(Translation in brackets)" (Prince -- Girls & Boys,
# Musixmatch: Hinweis auf eine im Text eingeklammerte Übersetzung EINER
# Songzeile) und "(Translation:)" (1 Giant Leap -- The Way You Dream,
# lrclib/musixmatch: derselbe Fall) -- beides legitime Songtext-Anmerkungen,
# keine Übersetzungsseiten-Titel (siehe ROADMAP.md).
_TRANSLATION_KEYWORD_RE = (
    r"(?:Translation|Übersetzung|Uebersetzung|Traducci[oó]n|Traduction|Tradu[cç][aã]o)"
)
_TRANSLATION_LANGUAGE_RE = (
    r"(?:English|Deutsch\w*|Espa[nñ]ol|Fran[cç]ais\w*|Italiano|Portugu[eê]s|"
    r"Türkçe|Turkish|R?ussian|Русский|Polski|Polish|Arabic|العربية|"
    r"Japanese|日本語|Korean|한국어|Chinese|中文|Greek|Ελληνικά)"
)
_TRANSLATION_MARKER_RE = re.compile(
    r"\((?=[^()]*" + _TRANSLATION_KEYWORD_RE + r")"
    r"(?=[^()]*" + _TRANSLATION_LANGUAGE_RE + r")"
    r"[^()]*\)",
    re.IGNORECASE,
)


def _looks_like_translation(content: str) -> bool:
    """Erkennt Übersetzungsseiten am Fetch-Text, unabhängig vom Provider.

    NUR EIN Signal: ein Klammer-Zusatz wie "(English Translation)" oder
    "(Traducción al Español)" irgendwo im Text (typischerweise im Genius-
    Seitentitel, der mit in den Lyrics-Container gescrapt wird).

    Bugfix (siehe ROADMAP.md): ein zweites Signal -- die Zeile "Translations"
    in den ersten 15 Zeilen -- wurde probeweise ergaenzt und live gegen die
    echte Cache-DB geprueft. Ergebnis: 1191 von 23504 Treffern schlugen an,
    weit mehr als plausibel. Ursache: Genius zeigt dieses "Translations"-
    Sprachauswahl-Menü auf JEDER Seite eines Songs, der IRGENDEINE Übersetzung
    hat -- auch auf der Original-Seite selbst (Beleg: song_id 5, "The Hollies
    -- Long Cool Woman in a Black Dress", ganz normale korrekte Original-
    Lyrics mit "Translations\\nTürkçe\\nLong Cool Woman (In a Black Dress)
    Lyrics" im Kopf -- keine Übersetzung, nur ein Song mit türkischer
    Übersetzung verfügbar). Das Signal unterscheidet nicht zwischen "diese
    Seite IST eine Übersetzung" und "dieser Song HAT Übersetzungen" -- damit
    ungeeignet, wieder entfernt.
    """
    return bool(_TRANSLATION_MARKER_RE.search(content))


def _query_provider(
    query: str, provider: str, env: dict, artist: str = "", title: str = ""
) -> tuple[str, Path | None]:
    """Fragt syncedlyrics für einen Anbieter ab, gibt (Anbieter, Temp-LRC-Pfad|None) zurück.

    Wartet vorab auf eine ggf. bestehende Rate-Limit-Sperre (_rate_limit_wait)
    und wertet stderr danach auf Rate-Limit-Signale aus (_rate_limit_report).

    Cache (siehe CACHE_DESIGN.md), nur aktiv wenn cache_store importiert werden
    konnte UND _cache_conn offen ist: vor der Live-Abfrage wird `get_provider`
    geprüft (übersprungen bei --refresh-cache ODER --force — beide erzwingen
    eine frische Live-Abfrage). Jedes Ergebnis wird danach IMMER festgehalten:
    Treffer, "wirklich nichts" UND transiente Fehler (Timeout/Rate-Limit/
    Captcha) — Fehlschläge mit Grund (status="fehlschlag", fehlergrund), damit
    kein Versuch stillschweigend spurlos bleibt. Ein Fehlschlag zählt aber nie
    als gültiger Cache-Treffer (get_provider gibt dafür immer None zurück) —
    sonst würden gedrosselte Läufe Songs fälschlich 30 Tage lang als "hat
    keinen Text" abstempeln.

    Steckt der Provider in der langen Ruhephase (siehe _rate_limit_wait),
    wird HIER kein Live-Versuch gestartet (kein subprocess.run, kein sleep) —
    das Ergebnis ist sofort (provider, None). Der Cache hält diesen
    übersprungenen Fall trotzdem als Fehlschlag fest, mit fehlergrund="gesperrt"
    (bewusst kein Rückgriff auf den ursprünglichen Grund wie "captcha" —
    pragmatischer, eigener Wert, der anzeigt: "wurde wegen aktiver Ruhephase
    übersprungen, kein echter Versuch"). Dieser Fall ruft NIE _rate_limit_report
    auf und verändert `consecutive_hits`/`next_allowed` NICHT — es gab kein
    neues Signal, die laufende Ruhephase läuft unangetastet von selbst ab.

    --cache-only (_cache_only) geht noch einen Schritt weiter als der reguläre
    Cache-Lookup: der wertet einen gecachten "fehlschlag" bewusst NIE als
    Treffer (s.o.), sodass ohne diesen Guard direkt im Anschluss live
    nachgefragt würde. Mit --cache-only wird stattdessen sofort (provider,
    None) zurückgegeben, ohne subprocess.run, ohne _rate_limit_wait und ohne
    neuen Cache-Eintrag (es fand ja kein echter Versuch statt — ein
    "fehlschlag"-Eintrag wäre hier fachlich falsch). Der Guard greift auch
    ohne offene Cache-Verbindung (use_cache=False), damit --cache-only
    garantiert nie live fragt, egal ob die Cache-DB verfügbar ist.

    Lokaler LRCLib-Dump (nur provider == "lrclib", nur wenn not _cache_refresh
    ODER _retry_missing_active — siehe _retry_missing_active-Modulkommentar:
    _cache_refresh bedeutet bei --force/--refresh-cache "wirklich alles frisch,
    auch den Dump umgehen", bei --retry-missing dagegen nur "den EIGENEN
    Cache-Lookup oben nicht als gültig werten" — ein Dump-Treffer soll dort
    weiterhin einen Live-Versuch ersparen dürfen): zwischen dem eigenen Cache-
    Lookup und dem --cache-only-Guard wird zuerst cache_store.lookup_lrclib_dump
    gegen _lrclib_dump_conn geprüft (Beschleuniger, spart eine echte Live-
    Abfrage). Ist der Dump nicht verfügbar (_lrclib_dump_conn None, z.B. Mount
    fehlt) oder liefert er 0 Treffer zu Künstler+Titel, läuft der Ablauf
    unverändert weiter (Schritt 2/3 unten). Liefert der Dump einen Treffer
    (mit oder ohne Songtext), wird das Ergebnis GENAU WIE ein Live-Treffer im
    eigenen Cache abgelegt (sofern use_cache) und sofort zurückgegeben — kein
    subprocess.run mehr nötig. --cache-only ist hier irrelevant: der Dump ist
    keine Live-Abfrage, sein Ergebnis darf also auch unter --cache-only
    verwendet werden.
    """
    use_cache = cache_store is not None and _cache_conn is not None
    artist_key = title_key = None
    if cache_store is not None:
        artist_key = cache_store.normalize_key(artist)
        title_key = cache_store.normalize_key(title)

    if use_cache and not _cache_refresh:
        cached = None
        try:
            with _cache_lock:
                cached = cache_store.get_provider(
                    _cache_conn,
                    provider,
                    artist_key,
                    title_key,
                    ttl_days=_cache_ttl_days,
                )
        except Exception:
            cached = (
                None  # Cache-Fehler dürfen den Lauf nie stören — einfach live abfragen
            )
        if cached is not None:
            if (
                cached["status"] == "treffer"
                and cached["content"]
                and not _looks_like_translation(cached["content"])
            ):
                with tempfile.NamedTemporaryFile(
                    suffix=".lrc", delete=False, mode="w", encoding="utf-8"
                ) as tmp:
                    tmp.write(cached["content"])
                    tmp_path = Path(tmp.name)
                return provider, tmp_path
            # "nichts" gecacht, oder nachträglich als Übersetzungsseite erkannt
            # (self-healing für ältere Cache-Einträge von vor diesem Filter)
            return provider, None

    if (
        provider == "lrclib"
        and (not _cache_refresh or _retry_missing_active)
        and cache_store is not None
        and _lrclib_dump_conn is not None
    ):
        try:
            with _cache_lock:
                dump_result = cache_store.lookup_lrclib_dump(
                    _lrclib_dump_conn, artist_key, title_key
                )
        except Exception:
            dump_result = (
                None  # Dump-Fehler dürfen den Lauf nie stören — weiter wie bisher
            )
        if dump_result is not None:
            if (
                dump_result["status"] == "treffer"
                and dump_result["content"]
                and not _looks_like_translation(dump_result["content"])
            ):
                if use_cache:
                    try:
                        with _cache_lock:
                            cache_store.put_provider(
                                _cache_conn,
                                provider,
                                artist_key,
                                title_key,
                                "treffer",
                                dump_result["content"],
                            )
                    except Exception:
                        pass  # Cache-Schreibfehler dürfen den Lauf nie stören
                with tempfile.NamedTemporaryFile(
                    suffix=".lrc", delete=False, mode="w", encoding="utf-8"
                ) as tmp:
                    tmp.write(dump_result["content"])
                    tmp_path = Path(tmp.name)
                return provider, tmp_path
            # Track im Dump gefunden, aber ohne Songtext ODER als Übersetzung
            # verworfen -- beides "nichts"
            if use_cache:
                try:
                    with _cache_lock:
                        cache_store.put_provider(
                            _cache_conn, provider, artist_key, title_key, "nichts", None
                        )
                except Exception:
                    pass  # Cache-Schreibfehler dürfen den Lauf nie stören
            return provider, None

    if _cache_only:
        return provider, None

    if _rate_limit_wait(provider):
        if use_cache:
            try:
                with _cache_lock:
                    cache_store.put_provider(
                        _cache_conn,
                        provider,
                        artist_key,
                        title_key,
                        "fehlschlag",
                        None,
                        fehlergrund="gesperrt",
                    )
            except Exception:
                pass  # Cache-Schreibfehler dürfen den Lauf nie stören
        return provider, None

    with tempfile.NamedTemporaryFile(suffix=".lrc", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    tmp_path.unlink()
    try:
        result = subprocess.run(
            ["syncedlyrics", query, "-o", str(tmp_path), "-p", provider],
            capture_output=True,
            text=True,
            env=env,
            timeout=_PROVIDER_TIMEOUT,
        )
        fehlergrund = _rate_limit_report(provider, result.stderr)
    except subprocess.TimeoutExpired:
        tmp_path.unlink(missing_ok=True)
        if use_cache:
            try:
                with _cache_lock:
                    cache_store.put_provider(
                        _cache_conn,
                        provider,
                        artist_key,
                        title_key,
                        "fehlschlag",
                        None,
                        fehlergrund="timeout",
                    )
            except Exception:
                pass  # Cache-Schreibfehler dürfen den Lauf nie stören
        return provider, None

    found_path = tmp_path if tmp_path.exists() else None
    if found_path is not None:
        try:
            if _looks_like_translation(found_path.read_text(encoding="utf-8")):
                found_path.unlink(missing_ok=True)
                found_path = None  # zaehlt unten als "nichts", genau wie kein Treffer
        except Exception:
            pass
    if use_cache:
        try:
            if fehlergrund is not None:
                with _cache_lock:
                    cache_store.put_provider(
                        _cache_conn,
                        provider,
                        artist_key,
                        title_key,
                        "fehlschlag",
                        None,
                        fehlergrund=fehlergrund,
                    )
            else:
                content = found_path.read_text(encoding="utf-8") if found_path else None
                status = "treffer" if content else "nichts"
                with _cache_lock:
                    cache_store.put_provider(
                        _cache_conn, provider, artist_key, title_key, status, content
                    )
        except Exception:
            pass  # Cache-Schreibfehler dürfen den Lauf nie stören

    return provider, found_path


def _dedupe_by_content(
    paths: list[Path], provider_hits: list[str]
) -> tuple[list[Path], list[str]]:
    """Entfernt inhaltlich identische Kandidaten (gespiegelte Provider-Datenbanken).

    Erster Treffer in Prioritätsreihenfolge bleibt, Duplikat-Dateien werden gelöscht.
    """
    seen_hashes: set[bytes] = set()
    deduped: list[Path] = []
    deduped_hits: list[str] = []
    for path, provider in zip(paths, provider_hits):
        h = hashlib.md5(path.read_bytes()).digest()
        if h not in seen_hashes:
            seen_hashes.add(h)
            deduped.append(path)
            deduped_hits.append(provider)
        else:
            path.unlink(missing_ok=True)  # Duplikat-Temp-Datei sofort löschen
    return deduped, deduped_hits


def _faster_whisper_available() -> bool:
    """Prüft NUR, ob das faster-whisper-Paket importierbar ist -- lädt dabei
    KEIN Modell in den Speicher (reiner Import-Check, kein Instanziieren
    von WhisperModel).

    Dient als billige Alternative zu `_get_whisper_model(name) is not None`
    für einen reinen Verfügbarkeits-Check (z.B. evaluate_all()s Sonde vor
    dem Bewerten): ein Song, der am Ende gar kein Whisper braucht (Konsens
    reicht, oder alle Songs im Scope sind z.B. rein englisch/deutsch und
    brauchen nur eines der beiden Modelle), soll nicht ungefragt das jeweils
    ANDERE Modell laden, nur um "ist Whisper überhaupt installiert" zu
    beantworten (realer Befund: ein reines Deutsch-Album lud bei jedem Lauf
    unnötig `medium`, obwohl kein einziger Song dieses Modell brauchte --
    siehe ROADMAP.md)."""
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def _get_whisper_model(name: str):
    """Lädt ein faster_whisper-Modell (gecacht). Gibt None zurück wenn nicht installiert."""
    if name not in _whisper_models:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            return None
        # _clear_status() vor dem ersten print(): löscht eine noch stehende
        # transiente Statuszeile (z.B. "i/N: ..." aus fetch_providers.py oder
        # "Whisper transkribiert..." aus _whisper_best), sonst "beißt" sich
        # die Ausgabe auf derselben Terminalzeile (siehe ROADMAP.md, gleiche
        # Ursache wie bei der Ordner-Kopfzeile).
        _clear_status()
        print(f"   {_ts()}  Lade Whisper-Modell ({name})...", end=" ", flush=True)
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        # int8: schneller auf CPU, vermeidet float16-Warnung von ctranslate2
        _whisper_models[name] = WhisperModel(name, device="auto", compute_type="int8")
        print(f"bereit.  {_ts()}")
    return _whisper_models[name]


def _whisper_context_sec(dur_s: float) -> float:
    """Transkriptionsdauer: vollständig, max 8 Minuten."""
    if dur_s <= 0:
        return 480.0  # Fallback ohne bekannte Dauer
    return min(dur_s, 480.0)  # immer 100 %, max 8 min


def _extract_lrc_words(content: str) -> list[str]:
    """Alle Textzeilen einer LRC als Wortliste (Unicode-Buchstaben)."""
    words: list[str] = []
    for line in content.splitlines():
        if re.match(r"\[[a-z]+:", line.lower()):
            continue  # Metadaten-Tags überspringen
        text = re.sub(r"\[\d+:\d+\.\d+\]", "", line)  # LRC-Timestamps entfernen
        text = re.sub(
            r"\[[^\]]*\]", "", text
        ).strip()  # C1: Sektion-Labels wie [Chorus], [Verse 1]
        if text:
            words.extend(re.findall(r"[^\W\d_]+", text.lower()))
    return words


def _detect_lrc_language(candidates: list[Path]) -> str | None:
    """Erkennt Sprache aus LRC-Inhalt via langdetect (ISO 639-1, z.B. 'de').

    Gibt None zurück wenn langdetect nicht installiert ist, zu wenig Text
    vorhanden ist oder die Konfidenz unter 80 % liegt.
    """
    try:
        from langdetect import DetectorFactory, detect_langs

        DetectorFactory.seed = 0  # deterministisches Ergebnis
    except ImportError:
        return None

    texts: list[str] = []
    for p in candidates:
        try:
            content = p.read_text(encoding="utf-8")
            # Nur Liedtext, keine LRC-Timestamps
            for line in content.splitlines():
                text = re.sub(r"\[\d+:\d+\.\d+\]", "", line).strip()
                if text and not re.match(r"\[[a-z]+:", line.lower()):
                    texts.append(text)
        except Exception:
            pass

    if not texts:
        return None

    combined = " ".join(texts)
    if len(combined.split()) < 10:
        return None

    try:
        langs = detect_langs(combined)
        if langs and langs[0].prob >= 0.80:
            return langs[0].lang
    except Exception:
        pass
    return None


def _resolve_lrc_language(candidates: list[Path]) -> str | None:
    """Erkennt die Song-Sprache je Kandidat EINZELN (nicht als Textmix wie
    _detect_lrc_language) und vergleicht die Ergebnisse. Drop-in-Ersatz für
    _detect_lrc_language mit exakt derselben Signatur.

    Grund: ein einzelner falscher Kandidat (z.B. eine Übersetzungsseite eines
    Providers) darf die Spracherkennung nicht kippen, wenn er mit den übrigen
    Kandidaten zu einem Textblock vermischt wird -- siehe Telepatía-Fall
    (ROADMAP.md): Genius' englische Übersetzung + Netease-Original zusammen
    detektierten "en", einzeln detektierten sie "en" bzw. "es".

    Sind sich alle Kandidaten mit erkannter Sprache einig (oder erkennt
    höchstens einer eine Sprache), wird diese Sprache zurückgegeben. Bei
    Widerspruch wird None zurückgegeben -- Whisper bekommt dann keinen
    Sprach-Hinweis (erkennt selbst anhand des Audios) UND evaluate_lyrics.
    _select_whisper_model() behandelt None wie jede nicht-englische Sprache,
    erzwingt also automatisch das große Modell (siehe dortiger Docstring) --
    kein eigenes Escalate-Flag hier nötig.
    """
    langs = {
        lang for p in candidates if (lang := _detect_lrc_language([p])) is not None
    }
    return next(iter(langs)) if len(langs) == 1 else None


def _word_overlap(a: list[str], b: list[str]) -> float:
    """Jaccard-Ähnlichkeit zweier Wortmengen (für Provider-Konsens)."""
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb)


def _whisper_threshold_for(lang: str | None) -> float:
    """Waehlt die Whisper-Akzeptanzschwelle: sprachspezifisch falls kalibriert, sonst Default."""
    if lang is not None and lang in _WHISPER_MIN_OVERLAP_BY_LANG:
        return _WHISPER_MIN_OVERLAP_BY_LANG[lang][0]
    return _WHISPER_MIN_OVERLAP


def _whisper_accept(
    score: float, lang: str | None, margin: float | None = None
) -> bool:
    """Akzeptanz-Check fuer den Whisper-Score aus fetch_lrc(). margin ist die
    kontrastive Marge (siehe _contrastive_margin_and_decision) -- Akzeptanz
    per Hybrid-Regel (v1.9.14): score >= _CONTRASTIVE_ABSOLUTE_FLOOR ODER
    margin >= _CONTRASTIVE_MARGIN. Der absolute Boden greift unabhaengig vom
    Hintergrund-Vergleich und faengt Faelle ab, in denen der Hintergrund-Pool
    durch einen einzelnen fehlerhaften Kandidaten kontaminiert ist (siehe
    _CONTRASTIVE_ABSOLUTE_FLOOR-Kommentar). margin=None (kein/zu kleiner
    gleichsprachiger Hintergrund-Pool, siehe _CONTRASTIVE_MIN_BACKGROUND)
    faellt auf die alte absolute Schwelle (_whisper_threshold_for) zurueck."""
    if margin is not None:
        return score >= _CONTRASTIVE_ABSOLUTE_FLOOR or margin >= _CONTRASTIVE_MARGIN
    return score >= _whisper_threshold_for(lang)


def _idf(word: str, n_docs: int, df: dict) -> float:
    """Inverse Dokumentfrequenz mit Laplace-Glättung (unbekannte Wörter → hohe, aber endliche IDF)."""
    return math.log((n_docs + 1) / (df.get(word, 0) + 1))


def _idf_jaccard(transcript_words: set, lrc_words: set, n_docs: int, df: dict) -> float:
    """IDF-gewichtetes Jaccard zwischen Transkript- und LRC-Wortmenge.

    Ersetzt die frühere Containment-Metrik (v1.7.7): seltene, inhaltstragende
    Wörter zählen stark, häufige Stopwords kaum — verhindert Fehlmatches durch
    zufällig übereinstimmende generische Wörter. Siehe _WHISPER_MIN_OVERLAP-
    Kommentar für die Validierung.
    """
    if not transcript_words or not lrc_words:
        return 0.0
    inter = transcript_words & lrc_words
    union = transcript_words | lrc_words
    denom = sum(_idf(w, n_docs, df) for w in union)
    if not denom:
        return 0.0
    return sum(_idf(w, n_docs, df) for w in inter) / denom


# --- Kontrastive Marge (Whisper-Verifikation) — Standardverfahren ----------


def _global_cache_idf(conn) -> tuple[int, dict]:
    """Baut df/n_docs aus ALLEN texte.inhalt der Cache-DB (ein Zaehlschritt pro
    Text, Tokenisierung wie _extract_lrc_words) -- die globale IDF-Tabelle fuer
    die kontrastive Marge (siehe Modul-Kommentar _CONTRASTIVE_MARGIN). Keine
    Sprach-Teiltabellen, keine Datei -- die Tabelle lebt ausschliesslich in der
    Cache-DB.
    """
    from collections import Counter

    df: Counter = Counter()
    n_docs = 0
    for (inhalt,) in conn.execute("SELECT inhalt FROM texte").fetchall():
        if not inhalt:
            continue
        words = set(_extract_lrc_words(inhalt))
        if not words:
            continue
        n_docs += 1
        df.update(words)
    return n_docs, dict(df)


def _song_candidate_words(song_id: int) -> list[list[str]]:
    """Tokenisierte Kandidatentexte eines Cache-Songs (memoisiert) -- nur fuer
    den Hintergrund-Pool der kontrastiven Marge gebraucht (siehe
    _contrastive_margin_and_decision). Erwartet, dass _contrastive_song_texts
    bereits gebaut ist (siehe _build_contrastive_context)."""
    cached = _contrastive_song_words_cache.get(song_id)
    if cached is not None:
        return cached
    texts = (_contrastive_song_texts or {}).get(song_id, [])
    words = [_extract_lrc_words(t) for t in texts]
    _contrastive_song_words_cache[song_id] = words
    return words


def _build_contrastive_context() -> None:
    """Baut einmal pro Lauf (vor der Whisper-Verifikation, siehe main()) den
    Kontext fuer die kontrastive Marge: globale Cache-IDF (_global_cache_idf) +
    eine song_id -> Sprache-Map (je ein Provider-Treffer-Text pro Song via
    _detect_lrc_language) fuer die gleichsprachigen Hintergrund-Pools.

    Braucht eine offene Cache-Verbindung -- ohne Cache ist kein Hintergrund-
    Pool moeglich. main() verhindert die Kombination --no-cache + aktive
    Whisper-Verifikation bereits per parser.error(), diese Fehlermeldung hier
    ist nur ein zusaetzliches Sicherheitsnetz."""
    global _contrastive_idf, _contrastive_lang_pools, _contrastive_song_texts
    global _contrastive_song_words_cache
    if _cache_conn is None:
        print(
            "FEHLER: Die Whisper-Verifikation braucht eine offene Cache-DB "
            "(fetch_songtext_cache.db) -- ohne Cache ist kein Hintergrund-Pool "
            "fuer die kontrastive Marge moeglich. Nicht mit --no-cache kombinierbar "
            "(--no-whisper oder --fast umgehen die Whisper-Verifikation und "
            "funktionieren weiterhin ohne Cache)."
        )
        sys.exit(1)

    _contrastive_song_words_cache = {}
    _contrastive_idf = _global_cache_idf(_cache_conn)

    cur = _cache_conn.execute("SELECT fingerabdruck, inhalt FROM texte")
    texte_map = {fp: inhalt for fp, inhalt in cur.fetchall() if inhalt}

    cur = _cache_conn.execute(
        "SELECT song_id, fingerabdruck FROM ergebnisse WHERE status='treffer'"
    )
    song_texts: dict[int, list[str]] = {}
    for song_id, fp in cur.fetchall():
        inhalt = texte_map.get(fp)
        if inhalt:
            song_texts.setdefault(song_id, []).append(inhalt)
    _contrastive_song_texts = song_texts

    pools: dict[str, list[int]] = {}
    for song_id, texts in song_texts.items():
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".lrc", delete=False, encoding="utf-8"
        ) as f:
            f.write(texts[0])
            tmp_path = Path(f.name)
        try:
            lang = _detect_lrc_language([tmp_path])
        finally:
            tmp_path.unlink(missing_ok=True)
        if lang is not None:
            pools.setdefault(lang, []).append(song_id)
    _contrastive_lang_pools = pools

    n_docs = _contrastive_idf[0]
    # _clear_status() vor dem print(): loescht eine noch stehende transiente
    # Statuszeile (z.B. "i/N: ..." aus fetch_providers.py), sonst "beisst"
    # sich die Ausgabe auf derselben Terminalzeile -- gleiche Ursache wie bei
    # der Ordner-Kopfzeile und der Whisper-Modell-Ladung (siehe ROADMAP.md).
    _clear_status()
    print(
        f"Kontrastiver Hintergrund-Kontext gebaut: {n_docs} IDF-Dokumente, "
        f"{len(song_texts)} Cache-Songs, {len(pools)} Sprachen mit Hintergrund-Pool."
    )


def _contrastive_data_signature() -> int | None:
    """Billiges Signal, ob sich die Datengrundlage des kontrastiven Kontexts
    (`texte`/`transkripte`, siehe _build_contrastive_context) seit dem
    letzten Aufbau überhaupt verändert hat -- zwei simple COUNT(*)-Abfragen
    statt des vollen Aufbaus (Scan über alle Provider-Texte + Whisper-
    Transkripte, Sprach-Erkennung je Song für die Hintergrund-Pools).
    None ohne offene Cache-Verbindung (kein Signal möglich)."""
    if _cache_conn is None:
        return None
    row = _cache_conn.execute(
        "SELECT (SELECT COUNT(*) FROM texte) + (SELECT COUNT(*) FROM transkripte)"
    ).fetchone()
    return row[0] if row else None


def _note_contrastive_evaluation(refresh_interval: int) -> None:
    """Von evaluate_lyrics.evaluate_all() vor JEDEM tatsächlich bewerteten
    Song aufgerufen (nicht bei übersprungenen, siehe dortiger Skip) --
    prüft frühestens alle `refresh_interval` tatsächlich bewertete Songs, ob
    der kontrastive Kontext neu gebaut werden muss.

    Der Zähler alleine reicht als Auslöser NICHT mehr (Nutzer-Feedback: bei
    einem Lauf mit vielen übersprungenen/bereits gecachten Songs kamen keine
    neuen Provider-Texte/Transkripte dazu -- ein Neuaufbau hätte exakt
    dasselbe Ergebnis wie vorher geliefert, nur die teure Arbeit wiederholt).
    Der Zähler bestimmt daher nur noch, wie oft überhaupt ein billiger
    Veränderungs-Check (_contrastive_data_signature) läuft -- der eigentliche
    Neuaufbau passiert NUR, wenn sich diese Signatur seit dem letzten Aufbau
    tatsächlich geändert hat.

    Der Zustand (_contrastive_context_built_ever/_evaluations_since_refresh/
    _last_data_signature) ist bewusst modulglobal, nicht lokal in
    evaluate_all() -- ruft songtext_pipeline.py evaluate_all() mehrfach im
    selben Prozess auf (z.B. einmal pro Ordner), bleibt der Fortschritt über
    diese Aufrufe hinweg erhalten. "Built ever" wird dabei UNABHÄNGIG von
    _contrastive_idfs tatsächlichem Wert verfolgt (nicht
    `_contrastive_idf is None` als Signal) -- sonst würde ein in Tests
    gemocktes _build_contrastive_context (das _contrastive_idf nicht setzt)
    bei jedem Song erneut als "nie gebaut" gelten.
    """
    global \
        _contrastive_context_built_ever, \
        _contrastive_context_evaluations_since_refresh, \
        _contrastive_context_last_data_signature
    if (
        not _contrastive_context_built_ever
        or _contrastive_context_evaluations_since_refresh >= refresh_interval
    ):
        signature = _contrastive_data_signature()
        if not _contrastive_context_built_ever or signature != (
            _contrastive_context_last_data_signature
        ):
            _build_contrastive_context()
            _contrastive_context_last_data_signature = signature
        _contrastive_context_built_ever = True
        _contrastive_context_evaluations_since_refresh = 0
    _contrastive_context_evaluations_since_refresh += 1


def _lookup_cache_song_id(artist_key: str, titel_key: str) -> int | None:
    """song_id des aktuellen Songs in der Cache-DB (falls vorhanden) -- wird
    gebraucht, um den aktuellen Song aus seinem eigenen Hintergrund-Pool
    auszuschliessen (siehe _contrastive_margin_and_decision)."""
    if _cache_conn is None:
        return None
    try:
        row = _cache_conn.execute(
            "SELECT id FROM songs WHERE artist_key=? AND titel_key=?",
            (artist_key, titel_key),
        ).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _contrastive_margin_and_decision(
    transcript_words: list[str],
    best_score: float,
    lrc_lang: str | None,
    exclude_song_id: int | None,
    n_docs: int,
    df: dict,
) -> tuple[float | None, float | None, bool]:
    """Kontrastive Marge (siehe _CONTRASTIVE_MARGIN):
    Marge = best_score - bester Score von K=20 zufaelligen ANDEREN Songs
    gleicher Sprache aus dem Cache (Hintergrund, via _song_candidate_words).
    Fester, songspezifischer Seed (_CONTRASTIVE_SEED + Sprache + song_id) fuer
    reproduzierbare Ziehung unabhaengig von der Verarbeitungsreihenfolge.

    Gibt (max_hintergrund, marge, fallback) zurueck. fallback=True (beide
    Werte dann None): kein oder zu kleiner (< _CONTRASTIVE_MIN_BACKGROUND)
    gleichsprachiger Hintergrund-Pool -- Aufrufer (_whisper_accept) faellt dann
    auf die alte absolute Schwelle zurueck."""
    pools = _contrastive_lang_pools or {}
    pool = pools.get(lrc_lang, []) if lrc_lang is not None else []
    others = [sid for sid in pool if sid != exclude_song_id]
    if len(others) < _CONTRASTIVE_MIN_BACKGROUND:
        return None, None, True

    rng = random.Random(f"{_CONTRASTIVE_SEED}:{lrc_lang}:{exclude_song_id}")
    k = min(_CONTRASTIVE_BACKGROUND_K, len(others))
    background_ids = rng.sample(others, k)

    tw = set(transcript_words)
    bg_scores = []
    for sid in background_ids:
        cand_words = _song_candidate_words(sid)
        s = max(
            (_idf_jaccard(tw, set(cw), n_docs, df) for cw in cand_words),
            default=0.0,
        )
        bg_scores.append(s)
    bg_max = max(bg_scores) if bg_scores else 0.0
    margin = best_score - bg_max
    return bg_max, margin, False


def _contrastive_result_for(
    best_score: float,
    transcript_words: list[str],
    lrc_lang: str | None,
    artist_key: str | None,
    titel_key: str | None,
    n_docs: int,
    df: dict,
) -> tuple[float | None, float | None, bool]:
    """Wrapper um _contrastive_margin_and_decision: loest zuerst die song_id
    des aktuellen Songs auf (fuer den Hintergrund-Ausschluss), siehe
    _whisper_best."""
    exclude_id = (
        _lookup_cache_song_id(artist_key, titel_key) if artist_key is not None else None
    )
    return _contrastive_margin_and_decision(
        transcript_words, best_score, lrc_lang, exclude_id, n_docs, df
    )


def _is_hallucination(words: list[str]) -> bool:
    """Erkennt Whisper-Halluzinationsschleifen (z.B. 'let's go' ×20).

    Viele Wörter, aber kaum einzigartige → Wiederholungsschleife statt Lyrik.
    """
    if len(words) < _HALLUCINATION_MIN_WORDS:
        return False
    if len(set(words)) / len(words) >= _HALLUCINATION_MAX_UNIQUE_RATIO:
        return False
    # Niedrige Wortvielfalt allein reicht nicht — repetitive Songs haben das auch.
    # Zusätzlich muss ein einzelnes Wort ≥ MAX_UNIQUE_RATIO aller Wörter ausmachen.
    most_common = max(words.count(w) for w in set(words))
    return most_common / len(words) >= _HALLUCINATION_MAX_UNIQUE_RATIO


def _transcribe(
    flac_path: Path,
    start: float,
    context_sec: float,
    model_name: str,
    language: str | None = None,
) -> tuple[list[str], float, float]:
    """Transkribiert context_sec Sekunden ab start, gibt (words, no_speech_prob, avg_logprob) zurück.

    condition_on_previous_text=False (temperature bleibt Standard-Fallback-
    Liste [0.0..1.0] — unverändert). Mit isoliertem Test gegen beide bekannten
    Problem-Tracks verifiziert: der frühere ~21-Minuten-Hänger (Yazoo) läuft
    damit in 160s durch, ohne die Temperatur-Liste anzufassen. Ein zuvor
    fälschlich als "kein Vokal" eingestufter Track (Wiederholungsschleife bei
    temperature=0.0 ohne Fallback) besteht jetzt stabil über mehrere Läufe.
    Die eigentliche Ursache des Hängers war offenbar, dass ein einzelnes
    schlechtes Segment sich über condition_on_previous_text=True auf alle
    folgenden Segmente fortpflanzt — nicht die Temperatur-Fallback-Liste
    selbst. Frühere Versuche mit reduzierter/fixer temperature (0.0 bzw.
    [0.0, 0.4]) wurden verworfen: ersteres führte zu echten Fehlklassi-
    fikationen, letzteres war nicht-deterministisch (Sampling bei Temperatur
    >0 macht Wiederholungen desselben Tracks unterschiedlich).
    """
    if _get_whisper_model(model_name) is None:
        return [], 1.0, 0.0
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_wav = Path(tmp.name)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(flac_path),
                "-ss",
                str(start),
                "-t",
                str(context_sec),
                "-ar",
                "16000",
                "-ac",
                "1",
                str(tmp_wav),
            ],
            capture_output=True,
        )
        model = _whisper_models[model_name]
        kwargs: dict = {"beam_size": 1, "condition_on_previous_text": False}
        if language:
            kwargs["language"] = language
        segs = list(model.transcribe(str(tmp_wav), **kwargs)[0])
        if not segs:
            return [], 1.0, 0.0
        no_speech = sum(s.no_speech_prob for s in segs) / len(segs)
        avg_logprob = sum(s.avg_logprob for s in segs) / len(segs)
        text = " ".join(s.text for s in segs)
        return re.findall(r"[^\W\d_]+", text.lower()), no_speech, avg_logprob
    except Exception:
        return [], 1.0, 0.0
    finally:
        tmp_wav.unlink(missing_ok=True)


def _provider_consensus(
    candidates: list[Path],
    min_providers: int = _CONSENSUS_MIN_PROVIDERS,
) -> tuple[Path | None, float]:
    """Prüft ob ≥ min_providers Provider inhaltlich übereinstimmen.

    Gibt (repräsentativsten Kandidaten, avg_score) zurück, oder (None, avg_score)
    wenn kein Konsens erreicht wird. avg_score ist die durchschnittliche
    paarweise Jaccard-Ähnlichkeit (hoch = ähnlich). C3: Bei initialem
    Scheitern wird der stärkste Ausreißer herausgeworfen und der Check auf
    den verbleibenden Kandidaten wiederholt.
    """
    if len(candidates) < min_providers:
        return None, 0.0
    path_words: list[tuple[Path, set]] = []
    for p in candidates:
        try:
            ws = set(_extract_lrc_words(p.read_text(encoding="utf-8")))
            if ws:
                path_words.append((p, ws))
        except Exception:
            pass
    if len(path_words) < min_providers:
        return None, 0.0

    def _jaccard(a: set, b: set) -> float:
        u = a | b
        return len(a & b) / len(u) if u else 0.0

    def _eval(pw: list[tuple[Path, set]]) -> tuple[Path | None, float]:
        n = len(pw)
        pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
        avg = sum(_jaccard(pw[i][1], pw[j][1]) for i, j in pairs) / len(pairs)
        if avg < _CONSENSUS_MIN_JACCARD:
            return None, avg

        best_rep: Path | None = None
        best_avg = -1.0
        for i, (p, ws_i) in enumerate(pw):
            others = [pw[j][1] for j in range(n) if j != i]
            a = sum(_jaccard(ws_i, o) for o in others) / len(others)
            if a > best_avg:
                best_avg = a
                best_rep = p
        return best_rep, avg

    rep, avg = _eval(path_words)
    if rep is not None:
        return rep, avg

    # C3: Ausreißer herauswerfen und erneut prüfen (braucht ≥ 3 Kandidaten)
    if len(path_words) >= 3:
        n = len(path_words)
        avg_to_others = [
            sum(
                _jaccard(path_words[i][1], path_words[j][1]) for j in range(n) if j != i
            )
            / (n - 1)
            for i in range(n)
        ]
        worst = avg_to_others.index(min(avg_to_others))
        filtered = [pw for k, pw in enumerate(path_words) if k != worst]
        rep2, avg2 = _eval(filtered)
        if rep2 is not None:
            return rep2, avg2

    return None, avg


def _whisper_best(
    flac_path: Path,
    candidates: list[Path],
    expected_dur: float = 0.0,
    artist: str = "",
    title: str = "",
    reason: str = "",
) -> tuple[Path | None, float, bool, int, str, str | None, float | None]:
    """Verifikation via small: bester Kandidat nach IDF-Jaccard-Score (_idf_jaccard).

    Gibt (bester Kandidat, score, has_vocals, words, model_used, language,
    contrastive_margin) zurück. contrastive_margin ist die kontrastive Marge
    (siehe _contrastive_margin_and_decision) -- None falls der gleichsprachige
    Hintergrund-Pool fehlt oder zu klein ist (siehe
    _CONTRASTIVE_MIN_BACKGROUND). Die eigentliche Akzeptanz-Entscheidung
    trifft weiterhin der Aufrufer (fetch_lrc) über
    _whisper_accept(score, lang, margin=contrastive_margin).

    Song-Transkript-Cache (siehe CACHE_DESIGN.md, Künstler+Titel-Identität wie
    beim Provider-Cache): existiert bereits ein gecachtes Transkript für
    (artist, title), wird der Whisper-Aufruf für diesen Lauf komplett
    übersprungen — das gecachte Transkript entscheidet direkt über
    has_vocals/total_words, die Vergleichslogik (idf-Jaccard je LRC-Kandidat)
    läuft unverändert weiter. Bei einem Cache-Miss läuft GENAU EIN
    Whisper-Lauf: der Start-Offset ist der früheste erste Zeitstempel über
    ALLE übergebenen Kandidaten (minus Pre-Roll), da alle Kandidaten dieselbe
    Audiodatei beschreiben und ein einziges Transkript für den Vergleich mit
    allen genügt (die Vergleichslogik ist ein reiner Wort-/Score-Vergleich,
    keine Zeit-Ausrichtung je Kandidat). Am Ende wird dieses eine Transkript
    persistent gecacht, damit derselbe Song beim nächsten Lauf nicht erneut
    transkribiert werden muss.

    Kandidaten-Auswahl (bester IDF-Jaccard-Score) nutzt die GLOBALE Cache-IDF
    (_contrastive_idf, siehe _build_contrastive_context) — keine Datei-basierte
    Tabelle. --cache-only betrifft NUR Live-Provider-Abfragen (siehe
    _cache_only-Docstring weiter oben), NICHT Whisper (ein v1.10.0-Refactor
    hatte das faelschlich gekoppelt, seit v1.10.1 wieder korrigiert): ein
    Cache-Miss transkribiert immer live, unabhängig von --cache-only.

    Modell-Load bewusst NICHT hier am Funktionsanfang (siehe unten, direkt
    vor dem eigentlichen Live-Transkriptions-Aufruf): _get_whisper_model()
    lädt bei einem Cache-Miss im _whisper_models-Dict das volle Modell in
    den Speicher -- bei einem Song-Transkript-Cache-TREFFER (siehe
    cached_transcript unten) wird das geladene Modell-Objekt gar nie
    benutzt, nur der Modell-NAME als String zur Anzeige. Ein zu früher Load
    hier würde also bei jedem Cache-Treffer unnötig ein Whisper-Modell laden
    (realer Befund: ein zweiter/dritter Lauf über denselben Ordner lud
    weiterhin medium/large-v3 neu, obwohl die Transkripte längst gecacht
    waren -- siehe ROADMAP.md).

    reason: optionaler Klartext, WARUM Whisper für diesen Song überhaupt
    läuft (z.B. "nur 1/4 Provider" oder "Konsens nur 32% < 40%") -- wird nur
    in die transiente "Whisper transkribiert..."-Statuszeile eingeblendet
    (siehe ROADMAP.md, Nutzer-Feedback: ohne Grund nicht nachvollziehbar,
    warum ein bestimmter Track überhaupt per Whisper geprüft wird). Ohne
    Angabe (Standard "") bleibt die Zeile wie bisher.
    """
    ctx = _whisper_context_sec(expected_dur)

    # EIN Start-Offset fuer den EINEN Whisper-Lauf (frueheste Kandidaten-
    # Zeitmarke -- verpasst keine echten frühen Vokale). Alle Kandidaten
    # beschreiben dieselbe Audiodatei, daher genuegt ein Transkript fuer
    # den Vergleich mit allen (statt einer pro Kandidat/Start).
    starts: list[float] = []
    for p in candidates:
        try:
            ft = _first_timestamp(p.read_text(encoding="utf-8"))
            starts.append(max(0.0, (ft if ft > 0 else 0.0) - _WHISPER_PRE_ROLL))
        except Exception:
            starts.append(0.0)
    start = min(starts) if starts else 0.0

    lrc_lang = _resolve_lrc_language(candidates)
    n_docs, df = _contrastive_idf or (0, {})

    use_cache = cache_store is not None and _cache_conn is not None
    artist_key = titel_key = None
    cached_transcript: dict | None = None
    if use_cache:
        artist_key = cache_store.normalize_key(artist)
        titel_key = cache_store.normalize_key(title)
        if not _cache_refresh:
            try:
                with _cache_lock:
                    cached_transcript = cache_store.get_transcript(
                        _cache_conn, artist_key, titel_key
                    )
            except Exception:
                cached_transcript = None  # Cache-Fehler dürfen den Lauf nie stören

    def _score_against_idf(words: list[str], p: Path) -> float:
        """IDF-gewichtetes Jaccard (hoch = ähnlich)."""
        if not words:
            return 0.0
        try:
            return _idf_jaccard(
                set(words),
                set(_extract_lrc_words(p.read_text(encoding="utf-8"))),
                n_docs,
                df,
            )
        except Exception:
            return 0.0

    if cached_transcript is not None:
        # Song-Cache-Treffer: kein einziger Whisper-Aufruf für diesen Lauf.
        words = (
            cached_transcript["transcript"].split()
            if cached_transcript["transcript"]
            else []
        )
        if _is_hallucination(words):
            words = []
        no_speech = cached_transcript["no_speech_prob"]
        logprob = cached_transcript["avg_logprob"]
        total_words = len(words)
        has_vocals = (
            no_speech < _VOCALS_NO_SPEECH_THOLD or total_words >= _VOCALS_MIN_WORDS
        )

        best_path: Path | None = None
        best_score = 0.0
        for p in candidates:
            s = _score_against_idf(words, p)
            if s > best_score:
                best_score = s
                best_path = p

        _, margin, _fallback = _contrastive_result_for(
            best_score, words, lrc_lang, artist_key, titel_key, n_docs, df
        )

        return (
            best_path,
            best_score,
            has_vocals,
            total_words,
            _WHISPER_MODEL,
            lrc_lang,
            margin,
        )

    # BUGFIX (war in v1.10.0 faelschlich an _cache_only gekoppelt):
    # --cache-only betrifft nur Live-PROVIDER-Abfragen (siehe Docstring bei
    # _cache_only weiter oben), nicht Whisper. Ein Cache-Miss transkribiert
    # daher immer live -- auch unter --cache-only, sonst wuerde kein neuer
    # Song je zum ersten Mal verifiziert.

    # Modell-Load HIER, erst unmittelbar vor dem echten Live-Transkriptions-
    # Aufruf (siehe Docstring oben) -- ein Cache-Treffer weiter oben hat
    # diese Zeile nie erreicht, also auch nie ein Modell geladen.
    if _get_whisper_model(_WHISPER_MODEL) is None:
        return (None, 0.0, False, 0, "", None, None)

    # Cache-Miss: EIN einziger Whisper-Lauf (Start-Offset s.o.), gegen ALLE
    # Kandidaten gescort -- alle Kandidaten beschreiben dieselbe Audiodatei,
    # ein Transkript genuegt fuer den Vergleich mit allen.
    reason_suffix = f" ({reason})" if reason else ""
    _print_status(f"  {flac_path.name}  Whisper transkribiert...{reason_suffix}")
    raw_words, no_speech, logprob = _transcribe(
        flac_path, start, ctx, _WHISPER_MODEL, language=lrc_lang
    )
    words = [] if _is_hallucination(raw_words) else raw_words

    # has_vocals: primär no_speech_prob, sekundär Wortzahl
    total_words = len(words)
    has_vocals = no_speech < _VOCALS_NO_SPEECH_THOLD or total_words >= _VOCALS_MIN_WORDS

    best_path = None
    best_score = 0.0
    for p in candidates:
        score = _score_against_idf(words, p)
        if score > best_score:
            best_score = score
            best_path = p

    _, margin, _fallback = _contrastive_result_for(
        best_score, words, lrc_lang, artist_key, titel_key, n_docs, df
    )

    # GENAU EINMAL persistent cachen (das eine Transkript dieses Laufs).
    if use_cache:
        try:
            with _cache_lock:
                cache_store.put_transcript(
                    _cache_conn,
                    artist_key,
                    titel_key,
                    " ".join(raw_words),
                    no_speech,
                    logprob,
                    modell=_WHISPER_MODEL,
                )
        except Exception:
            pass

    return (
        best_path,
        best_score,
        has_vocals,
        total_words,
        _WHISPER_MODEL,
        lrc_lang,
        margin,
    )


def _load_release(folder: Path) -> tuple[str, dict]:
    """Artist und {Titel: dur_s} aus release.json lesen.

    Titel werden auf NFC normalisiert — derselbe Grund wie bei _load_cache():
    der Titel aus release.json (JSON-Text) und der Dateiname/-stem (kann über
    SMB anders normalisiert ankommen) müssen für den Lookup byte-gleich sein.
    """
    try:
        with open(folder / "release.json", encoding="utf-8") as f:
            data = json.load(f)
        artist = data.get("artist", "")
        tracks = {
            unicodedata.normalize("NFC", t["title"]): t.get("dur_s", 0.0)
            for t in data.get("tracks", [])
        }
        return artist, tracks
    except Exception:
        return "", {}


def _parse_version(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.split("."))
    except Exception:
        return (0,)


def _parse_cache_ts(ts: str) -> datetime:
    """Vergleichbarer Zeitpunkt für einen Cache-Eintrag-"ts"-Wert -- NIE als
    reinen String vergleichen (siehe _load_cache/_save_cache, beide mergen
    per "neuerer ts gewinnt"). Zwei Formate sind im Umlauf: das ältere,
    naive Lokalzeit-Format ohne Zeitzone/Mikrosekunden
    (`datetime.now().isoformat(timespec="seconds")`, z.B. "...T20:02:03")
    und das neuere, UTC-aware DB-Format mit Mikrosekunden (siehe
    write_lrc.py, "ts" = `cache_store.latest_result_timestamp()`, z.B.
    "...T18:02:03.719825+00:00"). Ein reiner Stringvergleich zwischen beiden
    ist FALSCH: "18:02" (UTC) < "20:02" (Lokalzeit) lexikographisch, obwohl
    beides praktisch derselbe Moment sein kann (Real-Bug, live gegen die
    Produktions-DB gefunden -- siehe ROADMAP.md: ein frisch korrekt
    berechneter DB-Zeitstempel wurde dadurch beim Schreiben in _save_cache
    STILLSCHWEIGEND wieder verworfen, der alte, fehlerhafte Eintrag blieb
    liegen. Fix: beide Formen auf denselben timezone-aware Vergleichspunkt
    normalisieren -- ein naiver Wert wird als Lokalzeit interpretiert
    (`astimezone()`), ein bereits aware Wert (endet auf "+00:00" o.ä.)
    bleibt unverändert. Fehlt "ts" oder ist er nicht parsbar, gilt er als
    minimal alt (verliert jeden Vergleich)."""
    if not ts:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return dt.astimezone() if dt.tzinfo is None else dt


def _load_cache(folder: Path) -> dict:
    try:
        raw = json.loads((folder / _CACHE_FILENAME).read_text(encoding="utf-8"))
    except Exception:
        return {}
    # Dateinamen (v.a. ä/ö/ü) können je nach Zugriffsweg unterschiedlich
    # Unicode-normalisiert sein (NFC vs. NFD) — z.B. lokal geschrieben, dann
    # über SMB gelesen. Ohne Normalisierung verpasst der Cache-Lookup
    # vorhandene Einträge und legt Duplikate an. Beim Laden auf NFC
    # vereinheitlichen, bei Kollision den neueren Eintrag (per "ts",
    # siehe _parse_cache_ts) behalten.
    cache: dict = {}
    for key, entry in raw.items():
        norm_key = unicodedata.normalize("NFC", key)
        if norm_key not in cache or _parse_cache_ts(
            entry.get("ts", "")
        ) > _parse_cache_ts(cache[norm_key].get("ts", "")):
            cache[norm_key] = entry
    return cache


_FOLDER_BUSY = object()  # Sentinel: Ordner wird gerade von anderer Instanz bearbeitet


def _try_claim_folder(folder: Path) -> "IO | None | object":
    """Versucht, `folder` exklusiv zu sperren (non-blocking) — für bewusst
    parallele Instanzen: hält eine Instanz die Sperre bereits (sie bearbeitet
    den Ordner gerade), scheitert der Versuch sofort statt zu warten, und die
    andere Instanz überspringt den ganzen Ordner.

    Rückgabe:
    - `_FOLDER_BUSY`: Sperre ist von einer anderen Instanz gehalten (EAGAIN/
      EWOULDBLOCK) — Aufrufer soll den Ordner überspringen.
    - `None`: Locking hier nicht möglich (z.B. Netzwerk-Mount ohne flock-
      Unterstützung, ENOTSUP/ENOLCK, oder open() schlägt fehl) — kein
      Hinweis auf eine andere Instanz, also trotzdem unkoordiniert
      weiterarbeiten statt fälschlich zu überspringen. Sonst würden zwei
      Instanzen bei jedem Locking-Fehler beide denselben Ordner überspringen
      und im Extremfall die ganze Bibliothek still auslassen.
    - offenes Filehandle: Sperre erfolgreich gehalten. Muss vom Aufrufer
      offen gehalten werden, solange der Ordner bearbeitet wird, und danach
      mit `_release_folder()` gelöst werden.
    """
    lock_path = folder / _CACHE_LOCKFILE
    try:
        lockfile = open(lock_path, "w")
    except OSError:
        return None
    try:
        fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        lockfile.close()
        if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
            return _FOLDER_BUSY
        return None
    return lockfile


def _release_folder(lockfile: "IO | None | object") -> None:
    if lockfile is None or lockfile is _FOLDER_BUSY:
        return
    # Robust gegen einen bereits ungültigen Deskriptor: Während ein Ordner
    # bearbeitet wird, laufen nebenläufig etliche Subprozesse (syncedlyrics,
    # ffmpeg) und C-Bibliotheken (ctranslate2/faster-whisper zum Audio-Dekodieren).
    # Schließt eine davon versehentlich den rohen fd der Lock-Datei quer weg,
    # steht das Python-Objekt noch offen, aber flock(LOCK_UN) scheitert dann mit
    # OSError EBADF ("Bad file descriptor") — das riss zuvor den ganzen
    # (rekursiven) Lauf ab. flock-Sperren sind an die offene Dateibeschreibung
    # gebunden: Sobald deren fd schließt, gibt der Kernel die Sperre automatisch
    # frei. Ist der fd hier also schon weg, ist die Sperre bereits aufgehoben und
    # LOCK_UN nur noch redundant — das Schlucken verletzt die Parallel-Instanz-
    # Semantik nicht, sondern verhindert nur den Absturz. (ValueError deckt den
    # Fall ab, dass das Python-Objekt selbst bereits geschlossen wurde.)
    try:
        fcntl.flock(lockfile, fcntl.LOCK_UN)
    except (OSError, ValueError):
        pass
    try:
        lockfile.close()
    except (OSError, ValueError):
        pass


def _save_cache(folder: Path, cache: dict, lockfile: "IO | None" = None) -> None:
    """Schreibt den Cache-Ordnerstand — sicher gegen parallel laufende
    fetch_songtext-Instanzen im selben Ordner: Lock halten, aktuellen
    Diskstand frisch laden, mit `cache` mergen (neuerer "ts" gewinnt je
    Schlüssel), erst dann schreiben. Ohne das würde ein zweiter Prozess,
    der vor unserem letzten Schreibvorgang geladen hat, unsere Einträge
    beim eigenen Schreiben stillschweigend überschreiben (Lost-Update).

    `lockfile`: falls der Aufrufer die Ordner-Sperre bereits hält (siehe
    _try_claim_folder), wird sie hier weiterverwendet statt erneut gesperrt —
    ein zweiter flock()-Versuch auf denselben Ordner im selben Prozess würde
    sich sonst selbst blockieren (Deadlock).
    """
    own_lock = lockfile is None
    if own_lock:
        try:
            lockfile = open(folder / _CACHE_LOCKFILE, "w")
        except OSError:
            lockfile = None

    try:
        if lockfile is not None and own_lock:
            fcntl.flock(lockfile, fcntl.LOCK_EX)
        disk_cache = _load_cache(folder)
        for key, entry in cache.items():
            if key not in disk_cache or _parse_cache_ts(
                entry.get("ts", "")
            ) >= _parse_cache_ts(disk_cache[key].get("ts", "")):
                disk_cache[key] = entry
        (folder / _CACHE_FILENAME).write_text(
            json.dumps(disk_cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass  # nicht kritisch — Track wird beim nächsten Lauf erneut geprüft
    finally:
        if lockfile is not None and own_lock:
            fcntl.flock(lockfile, fcntl.LOCK_UN)
            lockfile.close()


def _build_cache_entry(
    conn: sqlite3.Connection,
    artist_key: str,
    titel_key: str,
    result: str,
    extras: dict,
) -> dict:
    """Baut einen JSON-Ordner-Cache-Eintrag ("v"/"r"/"ts" + extras) -- die
    EINE gemeinsame Stelle für write_lrc.py UND cut.py, statt zwei getrennter
    Kopien derselben Logik (siehe ROADMAP.md, "JSON-Zeitstempel vs.
    DB-Zeitstempel": genau diese Redundanz sorgte dafür, dass cut.py den
    Zeitstempel-Fix nicht automatisch mitbekam, als er nur in write_lrc.py
    gemacht wurde -- Nutzer-Feedback: "ich erwarte, dass solche Dinge in
    einem Modul zentral liegen").

    "ts" ist bewusst NICHT die Wanduhr-Zeit: zwei unabhängige Uhren (Wanduhr
    beim JSON-Schreiben, DB-Zeitstempel beim Anbieter-/Whisper-Schreiben)
    können bei einem schnellen Durchlauf in dieselbe Sekunde fallen -- die
    sekundengenaue Wanduhr-Zeit wäre dann fälschlich "früher" als der
    mikrosekundengenaue DB-Wert. Übernimmt stattdessen
    `cache_store.latest_result_timestamp()` direkt; ohne DB-Zeile (z.B.
    Skip-Genre-Track ganz ohne Anbieter-Versuch) fällt es auf die Wanduhr
    zurück -- für diese Tracks fällt ohnehin nie Live-Arbeit an, ein
    falsches "veraltet" kostet dort nichts."""
    db_ts = (
        cache_store.latest_result_timestamp(conn, artist_key, titel_key)
        if cache_store is not None
        else None
    )
    return {
        "v": __version__,
        "r": result,
        "ts": db_ts or datetime.now().isoformat(timespec="seconds"),
        **extras,
    }


def _cache_entry_valid(entry: dict) -> bool:
    return _parse_version(entry.get("v", "0")) >= _parse_version(_CACHE_MIN_VERSION)


def _db_newer_than_json_entry(
    conn: sqlite3.Connection, artist_key: str, titel_key: str, entry_ts: str | None
) -> bool:
    """True wenn die Cache-DB einen jüngeren Provider- oder Whisper-Datensatz
    für diesen Song hat als entry_ts (der "ts"-Wert eines JSON-Ordner-Cache-
    Eintrags, lokale Zeit ohne Zeitzone -- siehe write_lrc.write_all).

    Gemeinsam genutzt von write_lrc.py (Skip beim Schreiben) und
    evaluate_lyrics.py (Skip beim Bewerten, siehe ROADMAP.md, Songtexte-
    Pipeline-Umbau, "'bewerten' hat keinen Skip für unveränderte Songs") --
    beide vergleichen denselben JSON-Zeitstempel gegen denselben DB-Stand,
    um zu entscheiden, ob ein gültiger JSON-Eintrag noch aktuell ist.

    entry_ts fehlt oder ist nicht parsbar -> konservativ True (nicht
    überspringen, lieber einmal zu oft neu bewerten als für immer eine
    veraltete Entscheidung stehen lassen). Kein DB-Datensatz für diesen Song
    -> False (nichts Neues, der bisherige Skip bleibt gültig).
    """
    if not entry_ts:
        return True
    try:
        entry_dt = datetime.fromisoformat(entry_ts).astimezone()
    except ValueError:
        return True

    if cache_store is None:
        return True
    db_ts = cache_store.latest_result_timestamp(conn, artist_key, titel_key)
    if db_ts is None:
        return False
    try:
        db_dt = datetime.fromisoformat(db_ts)
    except ValueError:
        return True

    return db_dt > entry_dt


def _cache_entry_up_to_date(
    entry: dict | None,
    lrc_path: Path,
    conn: sqlite3.Connection | None = None,
    artist_key: str | None = None,
    titel_key: str | None = None,
) -> bool:
    """True wenn ein JSON-Ordner-Cache-Eintrag noch gültig ist -- der Track
    muss dann NICHT neu bewertet/geschrieben werden.

    War als fast identisches Prädikat dreifach unabhängig implementiert:
    inline in write_lrc.write_all(), inline in cut.py (OHNE den DB-
    Aktualitäts-Check), als eigene Funktion evaluate_lyrics.
    _skip_reevaluation() (siehe ROADMAP.md, Redundanz-Aufräumen -- genau die
    Art Drift, die schon beim Zeitstempel-Bug zugeschlagen hat).

    conn=None (Standard) überspringt den DB-Aktualitäts-Check
    (_db_newer_than_json_entry) komplett -- cut.py verarbeitet frisch
    geschnittene Tracks, bei denen dieser Check bisher bewusst fehlte (siehe
    ROADMAP.md, Redundanz-Audit: "cut.py bewusst ohne
    _db_newer_than_json_entry-Teil"). Mit conn+artist_key+titel_key gesetzt
    (write_lrc.py, evaluate_lyrics.py) läuft die volle Prüfung inkl.
    DB-Aktualität.
    """
    if not entry:
        return False
    if not _cache_entry_valid(entry):
        return False
    if entry.get("r") == "ok" and not lrc_path.exists():
        return False
    if conn is None:
        return True
    return not _db_newer_than_json_entry(conn, artist_key, titel_key, entry.get("ts"))


def _clear_status() -> None:
    """Löscht eine per _print_status() geschriebene Statuszeile."""
    print(f"\r{' ' * 100}\r", end="", flush=True)


def _tprint(msg: str) -> None:
    """Löscht Statuszeile und gibt eine Track-Zeile aus."""
    _clear_status()
    print(msg)


def _print_status(msg: str) -> None:
    """Überschreibbare Statuszeile (kein Zeilenumbruch, max. 100 Zeichen)."""
    print(f"\r{msg[:98]:<98}", end="", flush=True)


def _iter_audio_dfs(root: Path) -> Iterator[Path]:
    """Liefert Audiodateien depth-first, innerhalb jeder Ebene alphabetisch.

    Geht sofort in die Tiefe: A/ → A/ABBA/ → A/ABBA/Gold/ → erste Files.
    Zeigt per _print_status() welches Verzeichnis gerade betreten wird.
    """

    def _recurse(current: Path) -> Iterator[Path]:
        try:
            entries = sorted(current.iterdir())
        except PermissionError:
            return
        try:
            _print_status(f"  Scanne: {current.relative_to(root)}")
        except ValueError:
            pass
        for entry in entries:
            if not entry.is_dir() and entry.suffix.lower() in _AUDIO_EXTENSIONS:
                yield entry
        for entry in entries:
            if entry.is_dir():
                yield from _recurse(entry)

    yield from _recurse(root)


def _retry_missing(
    providers: list[str],
    artist: str | None,
    title: str | None,
    song_ids: list[int] | None = None,
) -> None:
    """--retry-missing: fragt `providers` live erneut ab, wo die Cache-DB
    aktuell status='nichts' ODER status='fehlschlag' zeigt (Motivation:
    lrclib steckte einmal stundenlang fälschlich in der "gesperrt"-
    Ruhephase, siehe ROADMAP.md).

    Reine Cache-DB-Operation: kein Whisper, keine .lrc-Datei wird gelesen
    oder geschrieben. Nutzt _query_provider unverändert wieder (inkl.
    Rate-Limit-Handling und Cache-Schreiblogik) -- dafür wird _cache_refresh
    für die Dauer dieses Laufs auf True gesetzt, sonst würde ein gecachtes
    "nichts" von _query_provider als gültiger (nicht abgelaufener) Cache-
    Treffer behandelt und NIE live nachgefragt (siehe get_provider: nur
    status='fehlschlag' erzwingt dort von sich aus einen Live-Versuch).
    Zusätzlich wird _retry_missing_active für dieselbe Dauer auf True gesetzt
    (siehe Modulkommentar dort): _cache_refresh allein würde in
    _query_provider auch den lrclib-Datenbank-Abzug überspringen (der genauso
    an "not _cache_refresh" hängt, siehe dortiger Guard) -- hier soll ein
    Dump-Treffer aber ausdrücklich weiterhin einen Live-Versuch ersparen
    dürfen, _cache_refresh=True bedeutet in diesem Kontext nur "den EIGENEN
    Cache nicht als gültig werten", nicht "auch den Dump umgehen".

    Query-String: da die Cache-DB nur normalisierte artist_key/titel_key
    speichert (NFC, gestrippt, kleingeschrieben -- siehe cache_store.py),
    nicht die Original-Schreibweise, wird die Suchanfrage aus genau diesen
    normalisierten Schlüsseln gebaut. Bekannte Einschränkung: geht Groß-/
    Kleinschreibung für einen Provider verloren, kann das die Trefferquote
    gegenüber der ursprünglichen Live-Abfrage (mit Original-Schreibweise)
    leicht verschlechtern.

    Rate-Limit-Ruhephase (_rate_limit_state): rein In-Memory, pro Prozess-
    lauf neu (kein Cache-DB-Bezug) -- ein separat gestarteter
    --retry-missing-Lauf beginnt daher automatisch mit einem leeren
    Zustand, unabhängig davon, ob ein früherer (anderer) Lauf gerade
    "gesperrt" war. Das eigentliche Stuck-Bug-Verhalten selbst wird hier
    NICHT behoben.

    Eingrenzung über artist/title: beide zusammen -> genau ein Song. Nur
    artist (title=None) -> alle Songs dieses Künstlers in der Cache-DB.
    Weder artist noch title -> keine Eingrenzung, ganze Cache-DB.

    song_ids: alternative Eingrenzung direkt über Song-IDs (siehe
    fetch_providers.retry_missing, PFAD-Scope) -- hat Vorrang vor
    artist/title, wenn nicht None. Eine LEERE Liste bedeutet bewusst "nichts
    zu tun" (anders als None = keine Eingrenzung) -- ein PFAD ohne passende
    Songs soll still nichts abfragen, nicht auf die ganze DB zurückfallen.
    Wird separat behandelt statt einfach in die SQL-IN-Klausel zu geben, weil
    "IN ()" mit leerer Liste ungültiges SQL wäre.

    Ergebniszeilen werden immer nach Artist, Titel (Cache-DB-Schlüssel)
    sortiert abgearbeitet -- unabhängig von der Eingrenzung.
    """
    assert _cache_conn is not None  # von main() vor dem Aufruf sichergestellt

    scope_song_ids: list[int] | None = None
    if song_ids is not None:
        scope_song_ids = song_ids
    elif artist is not None:
        artist_key = cache_store.normalize_key(artist)
        if title is not None:
            title_key = cache_store.normalize_key(title)
            row = _cache_conn.execute(
                "SELECT id FROM songs WHERE artist_key=? AND titel_key=?",
                (artist_key, title_key),
            ).fetchone()
            if row is None:
                print(
                    f"FEHLER: Song nicht in der Cache-Datenbank gefunden: "
                    f"Artist={artist!r}, Titel={title!r}"
                )
                sys.exit(1)
            scope_song_ids = [row[0]]
        else:
            song_rows = _cache_conn.execute(
                "SELECT id FROM songs WHERE artist_key=?", (artist_key,)
            ).fetchall()
            if not song_rows:
                print(
                    f"FEHLER: Kein Song von Artist={artist!r} in der Cache-Datenbank gefunden."
                )
                sys.exit(1)
            scope_song_ids = [r[0] for r in song_rows]

    if scope_song_ids is not None and not scope_song_ids:
        rows = []
    else:
        placeholders = ",".join("?" for _ in providers)
        sql = (
            "SELECT e.song_id, e.quelle, s.artist_key, s.titel_key "
            "FROM ergebnisse e JOIN songs s ON s.id = e.song_id "
            f"WHERE e.status IN ('nichts', 'fehlschlag') AND e.quelle IN ({placeholders})"
        )
        params: list = list(providers)
        if scope_song_ids is not None:
            id_placeholders = ",".join("?" for _ in scope_song_ids)
            sql += f" AND e.song_id IN ({id_placeholders})"
            params.extend(scope_song_ids)
        sql += " ORDER BY s.artist_key, s.titel_key, e.quelle"

        rows = _cache_conn.execute(sql, params).fetchall()

    if not rows:
        print(
            "Keine passenden Cache-Einträge gefunden (status='nichts'/'fehlschlag' "
            f"für {', '.join(providers)})."
        )
        return

    env = _load_env()
    global _cache_refresh, _retry_missing_active
    prev_refresh = _cache_refresh
    # Erzwingt bei _query_provider den Live-Versuch, statt ein gecachtes
    # "nichts" als gültigen (nicht abgelaufenen) Cache-Treffer zu werten.
    _cache_refresh = True
    # Signalisiert _query_provider, dass dieses _cache_refresh=True NICHT
    # "auch den lrclib-Dump umgehen" bedeutet (siehe Modulkommentar bei
    # _retry_missing_active und Docstring oben) -- ein Dump-Treffer soll
    # weiterhin einen Live-Versuch ersparen dürfen.
    _retry_missing_active = True
    checked = now_found = still_missing = still_failing = 0
    try:
        for song_id, provider, song_artist_key, song_title_key in rows:
            query = f"{song_artist_key} {song_title_key}".strip()
            checked += 1
            _print_status(
                f"  Retry {provider}: {song_artist_key} / {song_title_key} ..."
            )
            _, path = _query_provider(
                query, provider, env, artist=song_artist_key, title=song_title_key
            )
            if path is not None:
                now_found += 1
                path.unlink(missing_ok=True)
                _tprint(
                    f"{_ts()}  {provider}: {song_artist_key} / {song_title_key}  "
                    "✓ jetzt gefunden"
                )
                continue

            # path is None heißt NICHT zwangsläufig "wirklich nichts gefunden"
            # -- _query_provider schreibt bei einem transienten Fehler
            # (Timeout/Rate-Limit/Captcha) genauso status="fehlschlag" und
            # gibt ebenfalls None zurück. Ohne diese Unterscheidung sähe ein
            # erneuter transienter Fehler (der beim nächsten --retry-missing
            # wieder aufgegriffen würde) genauso aus wie ein bestätigtes
            # "gibt es nicht" -- irreführend, gerade in dem Moment, wo ein
            # weiterer Versuch am ehesten lohnt.
            row = _cache_conn.execute(
                "SELECT status, fehlergrund FROM ergebnisse WHERE song_id=? AND quelle=?",
                (song_id, provider),
            ).fetchone()
            status, fehlergrund = row if row else (None, None)
            if status == "fehlschlag":
                still_failing += 1
                _tprint(
                    f"{_ts()}  {provider}: {song_artist_key} / {song_title_key}  "
                    f"weiterhin Fehler ({fehlergrund}) — später erneut versuchen"
                )
            else:
                still_missing += 1
                _tprint(
                    f"{_ts()}  {provider}: {song_artist_key} / {song_title_key}  "
                    "weiterhin kein Treffer"
                )
    finally:
        _cache_refresh = prev_refresh
        _retry_missing_active = False

    print(
        f"\n--retry-missing fertig — {checked} (Song, Provider)-Kombinationen geprüft, "
        f"{now_found} jetzt gefunden, {still_missing} weiterhin ohne Treffer, "
        f"{still_failing} weiterhin mit Fehler (später erneut versuchen)."
    )


def _open_lrclib_dump_conn(no_cache: bool) -> sqlite3.Connection | None:
    """Öffnet den lokalen LRCLib-Datenbank-Abzug (siehe _query_provider) oder
    gibt None zurück -- still degradieren bei jedem Fehler (Mount fehlt,
    Datei fehlt, sonstiger Öffnungsfehler, oder no_cache=True) -- kein
    Absturz, keine Meldung, die den Lauf stört (reiner Beschleuniger).
    immutable=1 ist auf dem SMB-Mount nötig (siehe cache_store.
    lookup_lrclib_dump-Docstring): SMB unterstützt kein SQLite-Locking,
    mode=ro allein scheitert.

    no_cache schaltet den Abzug ebenso ab wie den eigenen Cache
    (CACHE_DESIGN.md: "--no-cache ignoriert den Cache komplett") -- als
    expliziter Parameter statt eines globalen Zugriffs auf args.no_cache,
    damit diese Funktion von JEDEM main()-Zweig aufgerufen werden kann
    (auch dem --retry-missing-Zweig, wo --no-cache zwar laut Argparse-Check
    ohnehin ausgeschlossen ist, aber die Funktion soll sich nicht
    stillschweigend auf diese Regel verlassen).

    Muss aus JEDEM main()-Zweig aufgerufen werden, der live gegen lrclib
    fragen könnte (regulärer Lauf UND --retry-missing) -- ein früher
    `return` vor dieser Stelle lässt _lrclib_dump_conn sonst beim
    Modul-Default None stehen (Bug: --retry-missing ging deshalb bislang
    IMMER live, ohne den Abzug je zu konsultieren)."""
    if cache_store is None or no_cache:
        return None
    try:
        return sqlite3.connect(
            f"file:{_LRCLIB_DUMP_PATH}?mode=ro&immutable=1",
            uri=True,
            check_same_thread=False,
        )
    except Exception:
        return None
