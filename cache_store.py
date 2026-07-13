"""SQLite-Speicherschicht für den fetch_songtext-Cache.

Der Cache ist nur ein Beschleuniger, kein Fundament (siehe CACHE_DESIGN.md):
fehlt die Datenbank oder ist sie leer, liefern alle get_*-Funktionen None und
das Programm fragt live nach. Dieses Modul kennt nur die Speicherschicht,
keine Anbieter- oder Whisper-Logik.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_TTL_DAYS = 30

_SCHEMA = """
CREATE TABLE IF NOT EXISTS texte (
    fingerabdruck TEXT PRIMARY KEY,
    inhalt TEXT
);

CREATE TABLE IF NOT EXISTS quelle (
    quelle TEXT,
    kuenstler_key TEXT,
    titel_key TEXT,
    status TEXT,
    fingerabdruck TEXT,
    datum TEXT,
    PRIMARY KEY (quelle, kuenstler_key, titel_key)
);

CREATE TABLE IF NOT EXISTS gehoert (
    datei_kennung TEXT,
    modell TEXT,
    parameter_key TEXT,
    transkript TEXT,
    no_speech_prob REAL,
    avg_logprob REAL,
    datum TEXT,
    PRIMARY KEY (datei_kennung, modell, parameter_key)
);
"""


def open_cache(db_path: Path) -> sqlite3.Connection:
    """Öffnet (und legt bei Bedarf an) die Cache-Datenbank unter db_path.

    Setzt WAL-Modus und einen busy_timeout, damit parallele --fast-Läufe
    gleichzeitig schreiben können, ohne sich gegenseitig zu blockieren.

    check_same_thread=False: die Provider-Abfragen laufen in fetch_songtext.py
    über einen ThreadPoolExecutor in Worker-Threads, während die Verbindung im
    Hauptthread geöffnet wird. fetch_songtext._cache_lock serialisiert alle
    Zugriffe bereits vollständig — ohne dieses Flag lehnt sqlite3 jeden Zugriff
    aus einem anderen Thread mit "SQLite objects created in a thread can only
    be used in that same thread" ab (wurde von der bewusst großzügigen
    except-Exception-Absicherung um jeden Cache-Aufruf bislang stillschweigend
    verschluckt — der Cache blieb dadurch trotz laufender Läufe leer).
    """
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def normalize_key(text: str) -> str:
    """Normalisiert einen Künstler-/Titel-String zu einem Vergleichsschlüssel."""
    return unicodedata.normalize("NFC", text).strip().lower()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def get_provider(
    conn: sqlite3.Connection,
    provider: str,
    artist_key: str,
    title_key: str,
    ttl_days: int = DEFAULT_TTL_DAYS,
) -> dict | None:
    """Liefert einen gecachten Anbieter-Eintrag, falls vorhanden und nicht abgelaufen.

    Gibt {"status": "treffer"|"nichts", "content": str|None} zurück,
    oder None, wenn kein (gültiger) Eintrag existiert.
    """
    row = conn.execute(
        "SELECT status, fingerabdruck, datum FROM quelle "
        "WHERE quelle=? AND kuenstler_key=? AND titel_key=?",
        (provider, artist_key, title_key),
    ).fetchone()
    if row is None:
        return None

    status, fingerabdruck, datum = row
    try:
        eintrag_datum = datetime.fromisoformat(datum)
    except ValueError:
        return None
    if eintrag_datum.tzinfo is None:
        eintrag_datum = eintrag_datum.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - eintrag_datum >= timedelta(days=ttl_days):
        return None

    content = None
    if status == "treffer" and fingerabdruck is not None:
        content_row = conn.execute(
            "SELECT inhalt FROM texte WHERE fingerabdruck=?", (fingerabdruck,)
        ).fetchone()
        content = content_row[0] if content_row else None

    return {"status": status, "content": content}


def put_provider(
    conn: sqlite3.Connection,
    provider: str,
    artist_key: str,
    title_key: str,
    status: str,
    content: str | None,
) -> None:
    """Speichert (Upsert) das Ergebnis einer Anbieter-Abfrage.

    Bei status="treffer" wird content über seinen SHA-256-Fingerabdruck in
    `texte` dedupliziert; bei status="nichts" bleibt content None/leer.
    """
    fingerabdruck = None
    if status == "treffer" and content is not None:
        fingerabdruck = _fingerprint(content)
        conn.execute(
            "INSERT INTO texte (fingerabdruck, inhalt) VALUES (?, ?) "
            "ON CONFLICT(fingerabdruck) DO NOTHING",
            (fingerabdruck, content),
        )

    conn.execute(
        "INSERT INTO quelle (quelle, kuenstler_key, titel_key, status, fingerabdruck, datum) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(quelle, kuenstler_key, titel_key) DO UPDATE SET "
        "status=excluded.status, fingerabdruck=excluded.fingerabdruck, datum=excluded.datum",
        (provider, artist_key, title_key, status, fingerabdruck, _now_iso()),
    )
    conn.commit()


def get_transcript(
    conn: sqlite3.Connection, audio_key: str, model: str, params_key: str
) -> dict | None:
    """Liefert ein gecachtes Whisper-Transkript oder None."""
    row = conn.execute(
        "SELECT transkript, no_speech_prob, avg_logprob FROM gehoert "
        "WHERE datei_kennung=? AND modell=? AND parameter_key=?",
        (audio_key, model, params_key),
    ).fetchone()
    if row is None:
        return None
    transkript, no_speech_prob, avg_logprob = row
    return {
        "transcript": transkript,
        "no_speech_prob": no_speech_prob,
        "avg_logprob": avg_logprob,
    }


def put_transcript(
    conn: sqlite3.Connection,
    audio_key: str,
    model: str,
    params_key: str,
    transcript: str,
    no_speech_prob: float,
    avg_logprob: float,
) -> None:
    """Speichert (Upsert) ein Whisper-Transkript mitsamt Kennzahlen."""
    conn.execute(
        "INSERT INTO gehoert "
        "(datei_kennung, modell, parameter_key, transkript, no_speech_prob, avg_logprob, datum) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(datei_kennung, modell, parameter_key) DO UPDATE SET "
        "transkript=excluded.transkript, no_speech_prob=excluded.no_speech_prob, "
        "avg_logprob=excluded.avg_logprob, datum=excluded.datum",
        (
            audio_key,
            model,
            params_key,
            transcript,
            no_speech_prob,
            avg_logprob,
            _now_iso(),
        ),
    )
    conn.commit()


def audio_key_for(path: Path) -> str:
    """Bildet einen Dateikennungs-Schlüssel aus Pfad, Größe und Änderungsdatum."""
    resolved = path.resolve()
    stat = resolved.stat()
    return f"{resolved}|{stat.st_size}|{int(stat.st_mtime)}"


def params_key_for(**kwargs) -> str:
    """Bildet einen kanonischen, deterministischen Schlüssel aus Parametern."""
    return json.dumps(kwargs, sort_keys=True, ensure_ascii=False)
