#!/usr/bin/env python3
import hashlib
import re
import os
import json
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

__version__ = "1.5.1"

_ALL_PROVIDERS = ["lrclib", "musixmatch", "netease", "genius"]
_PROVIDER_TIMEOUT = 20  # Sekunden pro Provider-Abfrage
_AUDIO_EXTENSIONS = {".flac", ".mp3", ".ogg", ".opus", ".m4a", ".aac", ".wav"}

# LRC-Timestamps enden oft vor dem Track-Ende (Instrumental-Outro → kein Text).
# Asymmetrische Toleranz: zu kurz ist normal, zu lang bedeutet falscher Song.
_LRC_TOO_SHORT_TOLERANCE = 0.40  # last_ts darf bis zu 40 % kürzer als der Track sein
_LRC_TOO_LONG_TOLERANCE = 0.10  # last_ts darf höchstens 10 % länger als der Track sein

# Whisper: zweistufige Verifikation — base zuerst, small nur im Grenzbereich.
_WHISPER_MIN_OVERLAP = 0.40  # Schwellwert: ab hier wird eine LRC akzeptiert
_WHISPER_RETRY_MIN = 0.20  # Untergrenze: base-Score ab hier → small-Pass
_WHISPER_MODEL_FAST = "base"  # erster Pass — immer
_WHISPER_MODEL_FULL = "small"  # zweiter Pass — nur im Grenzbereich [0.20, 0.40)
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
_WHISPER_VAD_PROBE_SEC = 15.0  # Kurzprobe vor vollständigem Pass
_VAD_PEAK_SCAN_SEC = 10.0  # Analysefenster je Position beim Peak-Scan
_HALLUCINATION_MIN_WORDS = 20  # ab hier Wiederholungsrate prüfen
_HALLUCINATION_MAX_UNIQUE_RATIO = 0.25  # < 25 % einzigartige Wörter → Halluzination
# _HALLUCINATION_AVG_LOGPROB entfernt: sprachbiased (Deutsch < Englisch), _is_hallucination reicht

_CACHE_FILENAME = ".fetch_songtext.json"
_CACHE_MIN_VERSION = (
    "1.4.0"  # Einträge dieser oder neuerer Version = gültig, kein Neulauf
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


def _get_whisper_model(name: str):
    """Lädt ein faster_whisper-Modell (gecacht). Gibt None zurück wenn nicht installiert."""
    if name not in _whisper_models:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            return None
        print(f"   Lade Whisper-Modell ({name})...", end=" ", flush=True)
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        # int8: schneller auf CPU, vermeidet float16-Warnung von ctranslate2
        _whisper_models[name] = WhisperModel(name, device="auto", compute_type="int8")
        print("bereit.")
    return _whisper_models[name]


def _vad_peak_start(flac_path: Path, dur_s: float) -> float:
    """Findet die lauteste Position via ffmpeg volumedetect an 5 Stellen.

    Scannt je _VAD_PEAK_SCAN_SEC Sekunden an 5 Positionen zwischen 10 % und
    90 % der Trackdauer und gibt die lauteste zurück. Fallback: 10 % der Dauer.
    """
    if dur_s < 60:
        return 0.0
    scan_start = dur_s * 0.10
    scan_end = dur_s * 0.90
    positions = [scan_start + (scan_end - scan_start) * i / 4 for i in range(5)]
    best_pos = scan_start
    best_rms = float("-inf")
    for pos in positions:
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-ss",
                    f"{pos:.1f}",
                    "-t",
                    f"{_VAD_PEAK_SCAN_SEC:.0f}",
                    "-i",
                    str(flac_path),
                    "-af",
                    "volumedetect",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            for line in result.stderr.splitlines():
                if "mean_volume:" in line:
                    val = float(line.split("mean_volume:")[1].split("dB")[0].strip())
                    if val > best_rms:
                        best_rms = val
                        best_pos = pos
                    break
        except Exception:
            continue
    return best_pos


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
        text = re.sub(r"\[\d+:\d+\.\d+\]", "", line).strip()
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
    n = len(path_words)
    pair_scores = [
        len(path_words[i][1] & path_words[j][1])
        / len(path_words[i][1] | path_words[j][1])
        if len(path_words[i][1] | path_words[j][1]) > 0
        else 0.0
        for i in range(n)
        for j in range(i + 1, n)
    ]
    avg = sum(pair_scores) / len(pair_scores)
    if avg < _CONSENSUS_MIN_JACCARD:
        return None, avg
    best_rep: Path | None = None
    best_avg = -1.0
    for i, (p, ws_i) in enumerate(path_words):
        others = [path_words[j][1] for j in range(n) if j != i]
        a = sum(
            len(ws_i & o) / len(ws_i | o) if len(ws_i | o) > 0 else 0.0 for o in others
        ) / len(others)
        if a > best_avg:
            best_avg = a
            best_rep = p
    return best_rep, avg


def _whisper_best(
    flac_path: Path, candidates: list[Path], expected_dur: float = 0.0
) -> tuple[Path | None, float, bool, int, str, str | None]:
    """Zweistufige Verifikation: base zuerst, small nur im Grenzbereich.

    Gibt (bester Kandidat, score, has_vocals, words, model_used, language) zurück.
    """
    if _get_whisper_model(_WHISPER_MODEL_FAST) is None:
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

    def _score_from_cache(
        cache: dict[int, _CacheVal],
    ) -> tuple[Path | None, float, int]:
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
        total = sum(len(v[0]) for v in cache.values())
        return best_path, best_score, total

    lrc_lang = _detect_lrc_language(candidates)

    def _build_cache(model_name: str) -> dict[int, _CacheVal]:
        cache: dict[int, _CacheVal] = {}
        for _, start in candidate_starts:
            key = round(start / 3)
            if key not in cache:
                words, no_speech, logprob = _transcribe(
                    flac_path, start, ctx, model_name, language=lrc_lang
                )
                if _is_hallucination(words):
                    words = []
                cache[key] = (words, no_speech, logprob)
        return cache

    # VAD-Probe: 15s an der lautesten Stelle. Schlägt sie an, zwei Fallback-
    # Positionen testen (30%/50% der Dauer) — Energie-Peak ≠ Vokal-Peak.
    if ctx > _WHISPER_VAD_PROBE_SEC * 2:
        probe_start = _vad_peak_start(flac_path, expected_dur)
        _, probe_no_speech, _ = _transcribe(
            flac_path, probe_start, _WHISPER_VAD_PROBE_SEC, _WHISPER_MODEL_FAST
        )
        if probe_no_speech > _VOCALS_NO_SPEECH_THOLD and expected_dur > 60:
            for frac in (0.30, 0.50):
                _, ns, _ = _transcribe(
                    flac_path,
                    expected_dur * frac,
                    _WHISPER_VAD_PROBE_SEC,
                    _WHISPER_MODEL_FAST,
                )
                if ns <= _VOCALS_NO_SPEECH_THOLD:
                    probe_no_speech = ns
                    break
        if probe_no_speech > _VOCALS_NO_SPEECH_THOLD:
            return (None, 0.0, False, 0, _WHISPER_MODEL_FAST, lrc_lang)

    # Pass 1: base
    fast_cache = _build_cache(_WHISPER_MODEL_FAST)
    # has_vocals: primär no_speech_prob, sekundär Wortzahl
    vals = list(fast_cache.values())
    avg_no_speech = sum(v[1] for v in vals) / len(vals) if vals else 1.0
    total_words_base = sum(len(v[0]) for v in vals)
    has_vocals = (
        avg_no_speech < _VOCALS_NO_SPEECH_THOLD or total_words_base >= _VOCALS_MIN_WORDS
    )
    best_path, best_score, total_words = _score_from_cache(fast_cache)
    model_used = _WHISPER_MODEL_FAST

    # Pass 2: small — nur wenn Vokale erkannt und Score im Grenzbereich
    if has_vocals and _WHISPER_RETRY_MIN <= best_score < _WHISPER_MIN_OVERLAP:
        full_cache = _build_cache(_WHISPER_MODEL_FULL)
        full_path, full_score, full_words = _score_from_cache(full_cache)
        if full_score > best_score:
            best_path, best_score, total_words = full_path, full_score, full_words
            model_used = _WHISPER_MODEL_FULL

    return (best_path, best_score, has_vocals, total_words, model_used, lrc_lang)


def fetch_lrc(
    query: str,
    lrc_path: Path,
    env: dict,
    expected_dur: float = 0.0,
    flac_path: Path | None = None,
    existing_lrc: Path | None = None,
) -> tuple[bool, str, dict]:
    """Alle Provider befragen, bestes Ergebnis via Whisper oder Dauer-Scoring wählen.

    Gibt (gefunden, info_str, extras) zurück.
    extras enthält score, providers, words, model (und ggf. fallback=True, consensus=True).
    """

    def _query_provider(provider: str) -> tuple[str, Path | None]:
        with tempfile.NamedTemporaryFile(suffix=".lrc", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        tmp_path.unlink()
        try:
            subprocess.run(
                ["syncedlyrics", query, "-o", str(tmp_path), "-p", provider],
                capture_output=True,
                text=True,
                env=env,
                timeout=_PROVIDER_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            tmp_path.unlink(missing_ok=True)
            return provider, None
        return provider, tmp_path if tmp_path.exists() else None

    candidates: list[Path] = []
    provider_hits: list[str] = []
    results: dict[str, Path | None] = {}
    with ThreadPoolExecutor(max_workers=len(_ALL_PROVIDERS)) as pool:
        futures = {pool.submit(_query_provider, p): p for p in _ALL_PROVIDERS}
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
    # Content-Hash deduplizieren — erster Treffer (Prioritätsreihenfolge) bleibt.
    seen_hashes: set[bytes] = set()
    deduped: list[Path] = []
    deduped_hits: list[str] = []
    for path, provider in zip(candidates, provider_hits):
        h = hashlib.md5(path.read_bytes()).digest()
        if h not in seen_hashes:
            seen_hashes.add(h)
            deduped.append(path)
            deduped_hits.append(provider)
        else:
            path.unlink(missing_ok=True)  # Duplikat-Temp-Datei sofort löschen
    candidates, provider_hits = deduped, deduped_hits

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
    """Artist und {Titel: dur_s} aus release.json lesen."""
    try:
        with open(folder / "release.json", encoding="utf-8") as f:
            data = json.load(f)
        artist = data.get("artist", "")
        tracks = {t["title"]: t.get("dur_s", 0.0) for t in data.get("tracks", [])}
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
        return json.loads((folder / _CACHE_FILENAME).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(folder: Path, cache: dict) -> None:
    try:
        (folder / _CACHE_FILENAME).write_text(
            json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass  # nicht kritisch — Track wird beim nächsten Lauf erneut geprüft


def _cache_entry_valid(entry: dict) -> bool:
    return _parse_version(entry.get("v", "0")) >= _parse_version(_CACHE_MIN_VERSION)


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
        "-V", "--version", action="version", version=f"fetch_songtext {__version__}"
    )
    args = parser.parse_args()

    root = Path(args.path).resolve()
    if root.is_file() and root.suffix.lower() in _AUDIO_EXTENSIONS:
        audio_files = [root]
    else:
        audio_files = sorted(
            p
            for p in (root.rglob("*") if args.recursive else root.glob("*"))
            if p.suffix.lower() in _AUDIO_EXTENSIONS
        )

    if not audio_files:
        print("Keine Audiodateien gefunden.")
        return

    mode = "rekursiv" if args.recursive else ("Datei" if root.is_file() else "Album")
    print(f"\n=== SONGTEXTE ({mode}, {len(audio_files)} Dateien) ===\n")

    env = _load_env()
    _get_whisper_model(_WHISPER_MODEL_FAST)  # vorladen — Meldung vor Track-Liste
    updated = skipped = not_found = errors = genre_skipped = no_tags = 0

    current_parent: Path | None = None
    dir_cache: dict = {}
    artist = ""
    tracks_by_title: dict = {}

    for audio in audio_files:
        lrc_path = audio.with_suffix(".lrc")

        if audio.parent != current_parent:
            current_parent = audio.parent
            artist, tracks_by_title = _load_release(audio.parent)
            dir_cache = _load_cache(audio.parent)

        # Cache-Check: Track bereits verarbeitet?
        if not args.force:
            entry = dir_cache.get(audio.name)
            if entry and _cache_entry_valid(entry):
                if entry.get("r") != "ok" or lrc_path.exists():
                    skipped += 1
                    continue

        meta_artist, meta_title, meta_genre = _read_audio_tags(audio)

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
            print(f"{_ts()}  {rel}  0/0: │ Genre={genre_label}  {symbol}")
            genre_skipped += 1
            dir_cache[audio.name] = {
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
        expected_dur = tracks_by_title.get(title, 0.0)

        rel = str(audio.relative_to(root))

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
                query, dest, env, expected_dur, flac_path=audio, existing_lrc=lrc_path
            )
        except FileNotFoundError:
            print(f"{_ts()}  {rel}  syncedlyrics nicht gefunden — Abbruch.")
            dest.unlink(missing_ok=True)
            errors += 1
            break

        if use_compare:
            if not found:
                dest.unlink(missing_ok=True)
                had_lrc = lrc_path.exists()
                lrc_path.unlink(missing_ok=True)
                extras["outcome"] = "delete" if had_lrc else "none"
                print(f"{_ts()}  {rel}  {info}  {'–' if had_lrc else '='}")
                not_found += 1
                cache_result = "nf"
            else:
                try:
                    new_content = dest.read_bytes()
                    old_content = lrc_path.read_bytes() if lrc_path.exists() else None
                    dest.unlink(missing_ok=True)
                    if old_content == new_content:
                        extras["outcome"] = "none"
                        print(f"{_ts()}  {rel}  {info}  =")
                        skipped += 1
                    else:
                        lrc_path.write_bytes(new_content)
                        extras["outcome"] = "write"
                        print(f"{_ts()}  {rel}  {info}  ✓")
                        updated += 1
                    cache_result = "ok"
                except OSError as e:
                    dest.unlink(missing_ok=True)
                    print(f"{_ts()}  {rel}  Schreibfehler: {e} — Abbruch.")
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
            print(f"{_ts()}  {rel}  {info}  {'✓' if found else '='}")

        if cache_result is not None:
            dir_cache[audio.name] = {
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
