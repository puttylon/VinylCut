#!/usr/bin/env python3
import fcntl
import hashlib
import re
import os
import json
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

__version__ = "1.7.3"

_ALL_PROVIDERS = ["lrclib", "musixmatch", "netease", "genius"]
_PROVIDER_TIMEOUT = 20  # Sekunden pro Provider-Abfrage
_AUDIO_EXTENSIONS = {".flac", ".mp3", ".ogg", ".opus", ".m4a", ".aac", ".wav"}

# Rate-Limit-Backoff pro Provider (siehe _rate_limit_report()). Recherchiert im
# syncedlyrics-Quellcode: Musixmatch meldet Rate-Limits über einen im JSON
# eingebetteten status_code (402 = Kontingent, 401 = Captcha/Anti-Bot), NetEase
# nur über eine generische Exception, Genius und lrclib geben laut Quellcode
# GAR KEIN Signal — dort greift nur der proaktive Mindestabstand.
_RATE_LIMIT_FLOOR_SEC = 1.5  # proaktiver Mindestabstand zwischen Anfragen pro Provider
_RATE_LIMIT_BASE_SEC = 10.0  # reaktive Basis-Strafe bei 402/generischem Fehler — verankert an
# syncedlyrics' eigenem time.sleep(10) beim Musixmatch-Token-Refresh nach 401
_RATE_LIMIT_CAPTCHA_SEC = 30.0  # längerer Cooldown bei 401/Captcha (Anti-Bot, kurzer Retry hilft nicht)
_RATE_LIMIT_MAX_SEC = 60.0  # Eskalations-Obergrenze bei wiederholten Treffern

_rate_limit_lock = threading.Lock()
_rate_limit_state: dict[str, dict] = {}  # provider -> {"next_allowed": float, "consecutive_hits": int}

# LRC-Timestamps enden oft vor dem Track-Ende (Instrumental-Outro → kein Text).
# Asymmetrische Toleranz: zu kurz ist normal, zu lang bedeutet falscher Song.
_LRC_TOO_SHORT_TOLERANCE = 0.40  # last_ts darf bis zu 40 % kürzer als der Track sein
_LRC_TOO_LONG_TOLERANCE = 0.10  # last_ts darf höchstens 10 % länger als der Track sein

# Whisper: einstufige Verifikation mit small. base wurde entfernt (v1.7.0) —
# unzuverlässig bei nicht-englischen Songs (falsch-negative "kein Vokal").
_WHISPER_MIN_OVERLAP = 0.40  # Schwellwert: ab hier wird eine LRC akzeptiert
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
_CACHE_LOCKFILE = ".fetch_songtext.lock"  # schützt _save_cache vor parallel laufenden Instanzen
_CACHE_MIN_VERSION = (
    "1.7.1"  # v1.7.1: Abbruch-Check bei fehlendem Whisper-Modell — alle Einträge neu prüfen
)

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


def _is_skip_genre(genre: str) -> bool:
    g = genre.lower()
    return any(kw in g for kw in _SKIP_GENRE_KEYWORDS)


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


def _rate_limit_wait(provider: str) -> None:
    """Wartet, falls für `provider` noch eine proaktive/reaktive Sperre besteht."""
    with _rate_limit_lock:
        next_allowed = _rate_limit_state.get(provider, {}).get("next_allowed", 0.0)
    wait = next_allowed - time.monotonic()
    if wait > 0:
        time.sleep(wait)


def _rate_limit_report(provider: str, stderr: str) -> None:
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
    """
    with _rate_limit_lock:
        state = _rate_limit_state.setdefault(provider, {"next_allowed": 0.0, "consecutive_hits": 0})
        if re.search(r"[Gg]ot status code 401", stderr) or "captcha" in stderr.lower():
            delay = min(_RATE_LIMIT_CAPTCHA_SEC * (2**state["consecutive_hits"]), _RATE_LIMIT_MAX_SEC)
            state["consecutive_hits"] += 1
        elif re.search(r"[Gg]ot status code 402", stderr) or (
            "An error occurred while searching for an LRC on" in stderr
        ):
            delay = min(_RATE_LIMIT_BASE_SEC * (2**state["consecutive_hits"]), _RATE_LIMIT_MAX_SEC)
            state["consecutive_hits"] += 1
        else:
            state["consecutive_hits"] = 0
            delay = _RATE_LIMIT_FLOOR_SEC
        state["next_allowed"] = time.monotonic() + delay


def _query_provider(query: str, provider: str, env: dict) -> tuple[str, Path | None]:
    """Fragt syncedlyrics für einen Anbieter ab, gibt (Anbieter, Temp-LRC-Pfad|None) zurück.

    Wartet vorab auf eine ggf. bestehende Rate-Limit-Sperre (_rate_limit_wait)
    und wertet stderr danach auf Rate-Limit-Signale aus (_rate_limit_report).
    """
    _rate_limit_wait(provider)
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
        _rate_limit_report(provider, result.stderr)
    except subprocess.TimeoutExpired:
        tmp_path.unlink(missing_ok=True)
        return provider, None
    return provider, tmp_path if tmp_path.exists() else None


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


def _get_whisper_model(name: str):
    """Lädt ein faster_whisper-Modell (gecacht). Gibt None zurück wenn nicht installiert."""
    if name not in _whisper_models:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            return None
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
        text = re.sub(r"\[[^\]]*\]", "", text).strip()  # C1: Sektion-Labels wie [Chorus], [Verse 1]
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


def _word_overlap(a: list[str], b: list[str]) -> float:
    """Jaccard-Ähnlichkeit zweier Wortmengen (für Provider-Konsens)."""
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb)


def _containment(transcript: list[str], lrc: list[str]) -> float:
    """Anteil der Whisper-Wörter die in der LRC vorkommen (Containment).

    Asymmetrisch: Nenner ist nur das Transkript, nicht die Vereinigung.
    Dadurch spielt die LRC-Länge keine Rolle — nur ob das Gehörte passt.
    """
    if not transcript or not lrc:
        return 0.0
    st = set(transcript)
    sl = set(lrc)
    return len(st & sl) / len(st)


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
    """Transkribiert context_sec Sekunden ab start, gibt (words, no_speech_prob, avg_logprob) zurück."""
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
        kwargs: dict = {"beam_size": 1}
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
    candidates: list[Path], min_providers: int = _CONSENSUS_MIN_PROVIDERS
) -> tuple[Path | None, float]:
    """Prüft ob ≥ min_providers Provider inhaltlich übereinstimmen.

    Gibt (repräsentativsten Kandidaten, avg_inter_jaccard) zurück,
    oder (None, 0.0) wenn kein Konsens erreicht wird.
    C3: Bei initialem Scheitern wird der stärkste Ausreißer herausgeworfen
    und der Check auf den verbleibenden Kandidaten wiederholt.
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

    def _eval(pw: list[tuple[Path, set]]) -> tuple[Path | None, float]:
        n = len(pw)
        pair_scores = [
            len(pw[i][1] & pw[j][1]) / len(pw[i][1] | pw[j][1])
            if pw[i][1] | pw[j][1] else 0.0
            for i in range(n) for j in range(i + 1, n)
        ]
        avg = sum(pair_scores) / len(pair_scores)
        if avg < _CONSENSUS_MIN_JACCARD:
            return None, avg
        best_rep: Path | None = None
        best_avg = -1.0
        for i, (p, ws_i) in enumerate(pw):
            others = [pw[j][1] for j in range(n) if j != i]
            a = sum(
                len(ws_i & o) / len(ws_i | o) if ws_i | o else 0.0 for o in others
            ) / len(others)
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
                len(path_words[i][1] & path_words[j][1]) / len(path_words[i][1] | path_words[j][1])
                if path_words[i][1] | path_words[j][1] else 0.0
                for j in range(n) if j != i
            ) / (n - 1)
            for i in range(n)
        ]
        worst = avg_to_others.index(min(avg_to_others))
        filtered = [pw for k, pw in enumerate(path_words) if k != worst]
        rep2, avg2 = _eval(filtered)
        if rep2 is not None:
            return rep2, avg2

    return None, avg


def _whisper_best(
    flac_path: Path, candidates: list[Path], expected_dur: float = 0.0
) -> tuple[Path | None, float, bool, int, str, str | None]:
    """Verifikation via small: bester Kandidat nach Containment-Score.

    Gibt (bester Kandidat, score, has_vocals, words, model_used, language) zurück.
    """
    if _get_whisper_model(_WHISPER_MODEL) is None:
        return (None, 0.0, False, 0, "", None)

    ctx = _whisper_context_sec(expected_dur)

    # Start-Offset pro Kandidat bestimmen
    candidate_starts: list[tuple[Path, float]] = []
    for p in candidates:
        try:
            ft = _first_timestamp(p.read_text(encoding="utf-8"))
            start = max(0.0, (ft if ft > 0 else 0.0) - _WHISPER_PRE_ROLL)
        except Exception:
            start = 0.0
        candidate_starts.append((p, start))

    # cache: start_key → (words, no_speech_prob, avg_logprob)
    _CacheVal = tuple[list[str], float, float]

    lrc_lang = _detect_lrc_language(candidates)

    cache: dict[int, _CacheVal] = {}
    for _, start in candidate_starts:
        key = round(start / 3)
        if key not in cache:
            words, no_speech, logprob = _transcribe(
                flac_path, start, ctx, _WHISPER_MODEL, language=lrc_lang
            )
            if _is_hallucination(words):
                words = []
            cache[key] = (words, no_speech, logprob)

    # has_vocals: primär no_speech_prob, sekundär Wortzahl
    vals = list(cache.values())
    avg_no_speech = sum(v[1] for v in vals) / len(vals) if vals else 1.0
    total_words = sum(len(v[0]) for v in vals)
    has_vocals = (
        avg_no_speech < _VOCALS_NO_SPEECH_THOLD or total_words >= _VOCALS_MIN_WORDS
    )

    best_path: Path | None = None
    best_score = 0.0
    for p, start in candidate_starts:
        words, _, _ = cache.get(round(start / 3), ([], 1.0, 0.0))
        if not words:
            continue
        try:
            score = _containment(
                words, _extract_lrc_words(p.read_text(encoding="utf-8"))
            )
        except Exception:
            score = 0.0
        if score > best_score:
            best_score = score
            best_path = p

    return (best_path, best_score, has_vocals, total_words, _WHISPER_MODEL, lrc_lang)


def fetch_lrc(
    query: str,
    lrc_path: Path,
    env: dict,
    expected_dur: float = 0.0,
    flac_path: Path | None = None,
    existing_lrc: Path | None = None,
    no_whisper: bool = False,
) -> tuple[bool, str, dict]:
    """Alle Provider befragen, bestes Ergebnis via Whisper oder Dauer-Scoring wählen.

    Gibt (gefunden, info_str, extras) zurück.
    extras enthält score, providers, words, model (und ggf. fallback=True, consensus=True).
    """

    candidates: list[Path] = []
    provider_hits: list[str] = []
    results: dict[str, Path | None] = {}
    with ThreadPoolExecutor(max_workers=len(_ALL_PROVIDERS)) as pool:
        futures = {
            pool.submit(_query_provider, query, p, env): p for p in _ALL_PROVIDERS
        }
        for future in as_completed(futures):
            try:
                provider, path = future.result()
                results[provider] = path
            except FileNotFoundError:
                for path in results.values():
                    if path:
                        path.unlink(missing_ok=True)
                raise
    for provider in _ALL_PROVIDERS:  # Reihenfolge beibehalten
        path = results.get(provider)
        if path:
            candidates.append(path)
            provider_hits.append(provider)

    # Duplikate entfernen: gespiegelte Provider liefern oft identischen Inhalt.
    candidates, provider_hits = _dedupe_by_content(candidates, provider_hits)

    # Vorhandene LRC als Kandidat einbeziehen (wird nicht gelöscht)
    all_candidates = candidates + (
        [existing_lrc] if existing_lrc and existing_lrc.exists() else []
    )

    if not all_candidates:
        for p in candidates:
            p.unlink(missing_ok=True)
        info_str = f"0/{len(_ALL_PROVIDERS)}: — │ kein Provider"
        return False, info_str, {
            "providers": 0,
            "provider_names": [],
            "method": None,
            "no_vocal": False,
            "score": None,
            "reason": "kein-provider",
            "words": None,
            "language": None,
        }

    hit_str = ", ".join(provider_hits) if provider_hits else "—"
    prov_str = f"{len(candidates)}/{len(_ALL_PROVIDERS)}: {hit_str}"

    # Konsens-Check zuerst: stimmen ≥ 3 deduplizierte Provider überein?
    # Wenn ja → Whisper wird gespart, direkter Treffer.
    consensus_rep, consensus_jaccard = _provider_consensus(candidates)

    if consensus_rep is not None:
        best_content = consensus_rep.read_bytes()
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
    elif no_whisper:
        # Whisper deaktiviert: 2-Provider-Konsens versuchen, sonst Dauer-Heuristik
        # mit Reject-Schwelle (kein blindes Schreiben eines falschen Songs).
        novocal_rep, novocal_jaccard = _provider_consensus(candidates, min_providers=2)
        if novocal_rep is not None:
            best_content = novocal_rep.read_bytes()
            info_str = f"{prov_str} │ Konsens {novocal_jaccard:.0%} (2P)"
            extras = {
                "providers": len(candidates),
                "provider_names": provider_hits,
                "method": "konsens",
                "no_vocal": False,
                "score": round(novocal_jaccard, 3),
                "words": None,
                "language": None,
            }
        else:
            best_content, _ = _heuristic_best(all_candidates, expected_dur)
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
    elif flac_path and flac_path.exists():
        # Kein Konsens → Whisper als primärer Entscheider.
        (
            best_path,
            best_score,
            has_vocals,
            whisper_words,
            model_used,
            lrc_lang,
        ) = _whisper_best(flac_path, all_candidates, expected_dur)

        method = f"whisper-{model_used}" if model_used else "heuristik"
        model_str = f"[{model_used}]" if model_used else ""
        lang_str = lrc_lang or ""
        words_str = f"{whisper_words}W"
        whisper_head = " ".join(p for p in [model_str, lang_str, "Whisper", words_str] if p)

        if not has_vocals:
            # kein Vokal: Prüfe ob ≥ 2 Provider inhaltlich übereinstimmen.
            novocal_rep, novocal_jaccard = _provider_consensus(candidates, min_providers=2)
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
        elif best_score >= _WHISPER_MIN_OVERLAP:
            best_content = best_path.read_bytes() if best_path else None
            info_str = f"{prov_str} │ {whisper_head} {best_score:.0%}"
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
            info_str = f"{prov_str} │ {whisper_head} unter Schwelle {best_score:.0%}"
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
        best = max(all_candidates, key=lambda p: _score_lrc(p, expected_dur))
        best_content = best.read_bytes()
        info_str = f"{prov_str} │ —"
        extras = {
            "providers": len(candidates),
            "provider_names": provider_hits,
            "method": "heuristik",
            "no_vocal": False,
            "score": None,
            "words": None,
            "language": None,
        }

    for p in candidates:  # nur temp-Dateien löschen, nie existing_lrc
        p.unlink(missing_ok=True)

    if best_content is None:
        return False, info_str, extras
    lrc_path.write_bytes(best_content)
    return True, info_str, extras


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


def _load_cache(folder: Path) -> dict:
    try:
        raw = json.loads((folder / _CACHE_FILENAME).read_text(encoding="utf-8"))
    except Exception:
        return {}
    # Dateinamen (v.a. ä/ö/ü) können je nach Zugriffsweg unterschiedlich
    # Unicode-normalisiert sein (NFC vs. NFD) — z.B. lokal geschrieben, dann
    # über SMB gelesen. Ohne Normalisierung verpasst der Cache-Lookup
    # vorhandene Einträge und legt Duplikate an. Beim Laden auf NFC
    # vereinheitlichen, bei Kollision den neueren Eintrag (per "ts") behalten.
    cache: dict = {}
    for key, entry in raw.items():
        norm_key = unicodedata.normalize("NFC", key)
        if norm_key not in cache or entry.get("ts", "") > cache[norm_key].get("ts", ""):
            cache[norm_key] = entry
    return cache


def _save_cache(folder: Path, cache: dict) -> None:
    """Schreibt den Cache-Ordnerstand — sicher gegen parallel laufende
    fetch_songtext-Instanzen im selben Ordner: Lock halten, aktuellen
    Diskstand frisch laden, mit `cache` mergen (neuerer "ts" gewinnt je
    Schlüssel), erst dann schreiben. Ohne das würde ein zweiter Prozess,
    der vor unserem letzten Schreibvorgang geladen hat, unsere Einträge
    beim eigenen Schreiben stillschweigend überschreiben (Lost-Update).
    """
    lock_path = folder / _CACHE_LOCKFILE
    try:
        lockfile = open(lock_path, "w")
    except OSError:
        lockfile = None

    try:
        if lockfile is not None:
            fcntl.flock(lockfile, fcntl.LOCK_EX)
        disk_cache = _load_cache(folder)
        for key, entry in cache.items():
            if key not in disk_cache or entry.get("ts", "") >= disk_cache[key].get("ts", ""):
                disk_cache[key] = entry
        (folder / _CACHE_FILENAME).write_text(
            json.dumps(disk_cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass  # nicht kritisch — Track wird beim nächsten Lauf erneut geprüft
    finally:
        if lockfile is not None:
            fcntl.flock(lockfile, fcntl.LOCK_UN)
            lockfile.close()


def _cache_entry_valid(entry: dict) -> bool:
    return _parse_version(entry.get("v", "0")) >= _parse_version(_CACHE_MIN_VERSION)


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


def _iter_audio_dfs(root: Path) -> "Iterator[Path]":
    """Liefert Audiodateien depth-first, innerhalb jeder Ebene alphabetisch.

    Geht sofort in die Tiefe: A/ → A/ABBA/ → A/ABBA/Gold/ → erste Files.
    Zeigt per _print_status() welches Verzeichnis gerade betreten wird.
    """
    from typing import Iterator

    def _recurse(current: Path) -> "Iterator[Path]":
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
        "--force",
        "-f",
        action="store_true",
        help="Cache ignorieren, alle Tracks neu prüfen",
    )
    parser.add_argument(
        "--no-whisper",
        action="store_true",
        help=(
            "Whisper-Verifikation überspringen (Konsens/Dauer-Heuristik statt "
            "Content-Check). Cache-Einträge mit reason=kein-vokal/unter-schwelle "
            "werden dabei automatisch neu geprüft, auch ohne --force."
        ),
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"fetch_songtext {__version__}"
    )
    args = parser.parse_args()

    root = Path(args.path).resolve()
    if root.is_file() and root.suffix.lower() in _AUDIO_EXTENSIONS:
        audio_files: "Iterable[Path]" = [root]
        mode = "Datei"
    elif args.recursive:
        audio_files = _iter_audio_dfs(root)  # Generator: geht sofort in die Tiefe
        mode = "rekursiv"
    else:
        audio_files = sorted(
            p for p in root.glob("*") if p.suffix.lower() in _AUDIO_EXTENSIONS
        )
        mode = "Album"

    if mode != "rekursiv" and not audio_files:  # type: ignore[truthy-iterable]
        print("Keine Audiodateien gefunden.")
        return

    if mode == "rekursiv":
        print(f"\n=== SONGTEXTE ({mode}) — {_ts()} ===\n")
    else:
        print(f"\n=== SONGTEXTE ({mode}, {len(audio_files)} Dateien) — {_ts()} ===\n")  # type: ignore[arg-type]

    env = _load_env()
    if not args.no_whisper:
        if _get_whisper_model(_WHISPER_MODEL) is None:  # vorladen — Meldung vor Track-Liste
            print(
                f"FEHLER: faster-whisper nicht verfügbar — Modell '{_WHISPER_MODEL}' konnte "
                "nicht geladen werden.\n"
                "Läuft dieses Python in der .venv? ('which python3' sollte auf "
                ".venv/bin/python3 zeigen, source .venv/bin/activate falls nicht.)\n"
                "Ohne Whisper würden alle Nicht-Konsens-Tracks fälschlich als "
                "'kein Vokal' verworfen. Abbruch — mit --no-whisper lässt sich ohne "
                "Whisper-Verifikation fortfahren."
            )
            sys.exit(1)
    updated = skipped = not_found = errors = genre_skipped = no_tags = 0

    current_parent: Path | None = None
    dir_cache: dict = {}
    artist = ""
    tracks_by_title: dict = {}

    for audio in audio_files:
        lrc_path = audio.with_suffix(".lrc")
        cache_key = unicodedata.normalize("NFC", audio.name)

        if audio.parent != current_parent:
            current_parent = audio.parent
            artist, tracks_by_title = _load_release(audio.parent)
            dir_cache = _load_cache(audio.parent)
            if args.recursive:
                try:
                    rel_dir = audio.parent.relative_to(root)
                except ValueError:
                    rel_dir = audio.parent
                _clear_status()
                print(f"{_ts()}  ── {rel_dir}")

        # Cache-Check: Track bereits verarbeitet?
        if not args.force:
            entry = dir_cache.get(cache_key)
            if entry and _cache_entry_valid(entry):
                # --no-whisper: frühere Whisper-Ablehnungen automatisch neu prüfen
                whisper_reject_rerun = args.no_whisper and entry.get("r") == "nf" and entry.get(
                    "reason"
                ) in ("kein-vokal", "unter-schwelle")
                if not whisper_reject_rerun and (entry.get("r") != "ok" or lrc_path.exists()):
                    skipped += 1
                    continue

        meta_artist, meta_title, meta_genre = _read_audio_tags(audio)
        rel = str(audio.relative_to(root))

        # Keine Tags → Suche unzuverlässig, überspringen (kein Cache-Eintrag)
        if not meta_artist and not meta_title:
            lrc_path.unlink(missing_ok=True)
            no_tags += 1
            continue

        # Genre-Check: kein Songtext erwartet → überspringen
        if _is_skip_genre(meta_genre):
            had_lrc = lrc_path.exists()
            lrc_path.unlink(missing_ok=True)
            outcome = "delete" if had_lrc else "none"
            symbol = "–" if had_lrc else "="
            genre_label = meta_genre.strip() if meta_genre else "Instrumental"
            _tprint(f"{_ts()}  {rel}  0/0: │ Genre={genre_label}  {symbol}")
            genre_skipped += 1
            dir_cache[cache_key] = {
                "v": __version__,
                "r": "skip",
                "outcome": outcome,
                "providers": 0,
                "provider_names": [],
                "method": None,
                "no_vocal": False,
                "score": None,
                "reason": "genre",
                "words": None,
                "language": None,
                "ts": datetime.now().isoformat(timespec="seconds"),
            }
            _save_cache(audio.parent, dir_cache)
            continue

        title = meta_title or (
            audio.stem.split(" - ", 1)[-1] if " - " in audio.stem else audio.stem
        )
        query_artist = meta_artist or artist
        query = f"{query_artist} {title}".strip()
        expected_dur = tracks_by_title.get(unicodedata.normalize("NFC", title), 0.0)

        use_compare = args.recursive or lrc_path.exists()
        if use_compare:
            with tempfile.NamedTemporaryFile(suffix=".lrc", delete=False) as tmp:
                dest = Path(tmp.name)
            dest.unlink()
        else:
            dest = lrc_path

        cache_result: str | None = None

        try:
            found, info, extras = fetch_lrc(
                query,
                dest,
                env,
                expected_dur,
                flac_path=audio,
                existing_lrc=lrc_path,
                no_whisper=args.no_whisper,
            )
        except FileNotFoundError:
            _tprint(f"{_ts()}  {rel}  syncedlyrics nicht gefunden — Abbruch.")
            dest.unlink(missing_ok=True)
            errors += 1
            break

        if use_compare:
            if not found:
                dest.unlink(missing_ok=True)
                had_lrc = lrc_path.exists()
                lrc_path.unlink(missing_ok=True)
                extras["outcome"] = "delete" if had_lrc else "none"
                _tprint(f"{_ts()}  {rel}  {info}  {'–' if had_lrc else '='}")
                not_found += 1
                cache_result = "nf"
            else:
                try:
                    new_content = dest.read_bytes()
                    old_content = lrc_path.read_bytes() if lrc_path.exists() else None
                    dest.unlink(missing_ok=True)
                    if old_content == new_content:
                        extras["outcome"] = "none"
                        _tprint(f"{_ts()}  {rel}  {info}  =")
                        skipped += 1
                    else:
                        lrc_path.write_bytes(new_content)
                        extras["outcome"] = "write"
                        _tprint(f"{_ts()}  {rel}  {info}  ✓")
                        updated += 1
                    cache_result = "ok"
                except OSError as e:
                    dest.unlink(missing_ok=True)
                    _tprint(f"{_ts()}  {rel}  Schreibfehler: {e} — Abbruch.")
                    errors += 1
                    break
        else:
            if found:
                extras["outcome"] = "write"
                updated += 1
                cache_result = "ok"
            else:
                extras["outcome"] = "none"
                not_found += 1
                cache_result = "nf"
            _tprint(f"{_ts()}  {rel}  {info}  {'✓' if found else '='}")

        if cache_result is not None:
            dir_cache[cache_key] = {
                "v": __version__,
                "r": cache_result,
                "ts": datetime.now().isoformat(timespec="seconds"),
                **extras,
            }
            _save_cache(audio.parent, dir_cache)

    summary = f"Fertig — {updated} geladen, {skipped} übersprungen, {not_found} nicht gefunden"
    if genre_skipped:
        summary += f", {genre_skipped} Genre übersprungen"
    if no_tags:
        summary += f", {no_tags} ohne Tags"
    if errors:
        summary += f", {errors} Fehler"
    print(f"\n{summary}.")


if __name__ == "__main__":
    main()
