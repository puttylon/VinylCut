#!/usr/bin/env python3
"""Zentrale Bibliothek für Funktionen, die von mehreren Kern-Skripten
unverändert gebraucht werden -- Vorgabe: mehrfach genutzte Funktionen werden
hier implementiert und von den Aufrufern importiert, statt redundant in
jedem Skript neu geschrieben zu werden (siehe ROADMAP.md).

Kein Rich/tty (das bleibt in cut_ui.py/assemble_ui.py). Externe Programme
(ffprobe, sox, ...) UND Abhängigkeiten dazu sind dagegen ausdrücklich
erlaubt -- Nutzer-Feedback: "ohne ffprobe und sox etc geht es nicht, library
darf externe Programme benutzen und Abhängigkeiten dazu haben".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_MIN_PREVIEW_SEC = 2.0  # Untergrenze für "p<Sek>" (Bedienfehler-Schutz)
_MAX_PREVIEW_SEC = 30.0  # Obergrenze für "p<Sek>"


def parse_offset(s: str) -> float:
    s = s.strip()
    sign = 1.0
    if s.startswith("+"):
        s, sign = s[1:], 1.0
    elif s.startswith("-"):
        s, sign = s[1:], -1.0
    if ":" in s:
        m, sec = s.split(":", 1)
        return sign * (int(m) * 60 + float(sec))
    return sign * float(s)


def parse_preview_duration(action: str) -> float | None:
    """Parst 'p<Sek>' (z.B. 'p18') zur Änderung der Vorschau-/Preview-Dauer.

    Gibt None zurück wenn kein p<Zahl>-Muster vorliegt oder der Wert
    außerhalb [_MIN_PREVIEW_SEC, _MAX_PREVIEW_SEC] liegt — die Eingabe wird
    dann komplett ignoriert (Bedienfehler-Schutz), nicht auf die Grenze
    geklemmt.
    """
    if not (action.startswith("p") and action[1:]):
        return None
    try:
        new_dur = float(action[1:])
    except ValueError:
        return None
    if _MIN_PREVIEW_SEC <= new_dur <= _MAX_PREVIEW_SEC:
        return new_dur
    return None


def method_from_cache_entry(entry: dict) -> str:
    """Bestimmt die Methode, die zu einem JSON-Ordner-Cache-Eintrag geführt
    hat -- inkl. Legacy-Fallback für Einträge von vor v1.5.0 (kein "method"-
    Feld). War wortgleich in lrc_analyse.py UND whisper_analyse.py dupliziert
    (siehe ROADMAP.md)."""
    method = entry.get("method")
    if method:
        if method == "konsens" and entry.get("no_vocal"):
            return "konsens-kein-vokal"
        return method
    # Legacy: Cache-Einträge vor v1.5.0
    if entry.get("consensus") and entry.get("no_vocal"):
        return "konsens-kein-vokal"
    if entry.get("consensus"):
        return "konsens"
    if entry.get("fallback"):
        return "konsens-kein-vokal"
    model = entry.get("model")
    if model == "small":
        return "whisper-small"
    if model == "base":
        return "whisper-base"
    if entry.get("score") is not None:
        return "whisper-base"
    return "heuristik"


def reject_reason_from_cache_entry(entry: dict) -> str:
    """Bestimmt den Ablehnungsgrund für einen "nf"-JSON-Ordner-Cache-
    Eintrag -- inkl. Legacy-Fallback für Einträge von vor v1.5.0 (kein
    "reason"-Feld). War wortgleich in lrc_analyse.py, lrc_recheck.py UND
    whisper_analyse.py dupliziert (siehe ROADMAP.md)."""
    reason = entry.get("reason")
    if reason:
        return reason
    # Legacy: Cache-Einträge vor v1.5.0 hatten kein 'reason'-Feld
    if entry.get("providers", 0) == 0:
        return "kein-provider"
    words = entry.get("words") or 0
    score = entry.get("score")
    if score is None:
        return "kein-whisper"
    if words == 0 and score == 0.0:
        return "kein-vokal"
    return "unter-schwelle"


def get_audio_duration(audio_path: Path) -> float:
    """Gesamtdauer einer Audiodatei in Sekunden via ffprobe -- war fast
    wortgleich in assemble.py (inline) UND fetch_metadata.get_flac_duration()
    dupliziert (siehe ROADMAP.md).

    Wirft bei Fehlern (ffprobe fehlt, Datei kaputt) weiter -- reine,
    ehrliche Funktion ohne Fehler-Schlucken. fetch_metadata.
    get_flac_duration() bleibt als dünner Wrapper bestehen, der bei Fehlern
    weiterhin 0.0 zurückgibt (ihre bisherigen Aufrufer in cut.py verlassen
    sich darauf); assemble.py ruft diese Funktion direkt auf und lässt einen
    Fehler wie bisher durchschlagen (dort war es nie ein stiller Fallback)."""
    return float(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ]
        )
    )
