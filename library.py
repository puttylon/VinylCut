#!/usr/bin/env python3
"""Zentrale Bibliothek für Funktionen, die von mehreren Kern-Skripten
(aktuell cut.py + assemble.py) unverändert gebraucht werden -- Vorgabe:
mehrfach genutzte Funktionen werden hier implementiert und von den
Aufrufern importiert, statt redundant in jedem Skript neu geschrieben zu
werden (siehe ROADMAP.md).

Reine Funktionen ohne Seiteneffekte -- kein Rich/tty (das bleibt in
cut_ui.py/assemble_ui.py), kein subprocess, kein Dateisystem.
"""

from __future__ import annotations

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
