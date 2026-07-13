"""SQLite-Speicherschicht für den fetch_songtext-Cache.

Der Cache ist nur ein Beschleuniger, kein Fundament (siehe CACHE_DESIGN.md):
fehlt die Datenbank oder ist sie leer, liefern alle get_*-Funktionen None und
das Programm fragt live nach. Dieses Modul kennt nur die Speicherschicht,
keine Anbieter- oder Whisper-Logik.

Schema (normalisiert):
    songs       — eine Zeile pro Künstler/Titel (+ optional Genre)
    ergebnisse  — eine Zeile pro (Song, Provider): Treffer/Nichts/Fehlschlag,
                  bei Fehlschlag mit Grund; verweist bei Treffer auf `texte`
    texte       — jeder Liedtext-Inhalt genau einmal (SHA-256-Fingerabdruck)
    transkripte — Whisper-Ergebnisse je Audiodatei+Modell+Parameter

Ein Fehlschlag (Timeout/Rate-Limit/Captcha) wird IMMER festgehalten (Status
"fehlschlag" + Grund) — nie stillschweigend verworfen. Er zählt aber nie als
gültiger Cache-Treffer: get_provider() liefert für "fehlschlag" immer None,
damit der Aufrufer beim nächsten Lauf erneut live fragt.
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
CREATE TABLE IF NOT EXISTS songs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artist_key TEXT NOT NULL,
    titel_key TEXT NOT NULL,
    genre TEXT,
    UNIQUE (artist_key, titel_key)
);

CREATE TABLE IF NOT EXISTS texte (
    fingerabdruck TEXT PRIMARY KEY,
    inhalt TEXT
);

CREATE TABLE IF NOT EXISTS ergebnisse (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    song_id INTEGER NOT NULL REFERENCES songs(id),
    quelle TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('treffer', 'nichts', 'fehlschlag')),
    fehlergrund TEXT,
    fingerabdruck TEXT REFERENCES texte(fingerabdruck),
    datum TEXT NOT NULL,
    UNIQUE (song_id, quelle)
);

CREATE TABLE IF NOT EXISTS transkripte (
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
    be used in that same thread" ab.
    """
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
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


def _get_or_create_song(
    conn: sqlite3.Connection, artist_key: str, titel_key: str, genre: str | None = None
) -> int:
    """Liefert die song_id für (artist_key, titel_key); legt die Zeile bei Bedarf an.

    Ein mitgegebenes genre wird nur beim erstmaligen Anlegen gesetzt bzw. bei
    einer bestehenden Zeile ergänzt, wenn dort noch kein Genre steht.
    """
    conn.execute(
        "INSERT INTO songs (artist_key, titel_key, genre) VALUES (?, ?, ?) "
        "ON CONFLICT(artist_key, titel_key) DO UPDATE SET "
        "genre=COALESCE(songs.genre, excluded.genre)",
        (artist_key, titel_key, genre),
    )
    row = conn.execute(
        "SELECT id FROM songs WHERE artist_key=? AND titel_key=?",
        (artist_key, titel_key),
    ).fetchone()
    return row[0]


def get_provider(
    conn: sqlite3.Connection,
    provider: str,
    artist_key: str,
    title_key: str,
    ttl_days: int = DEFAULT_TTL_DAYS,
) -> dict | None:
    """Liefert einen gecachten Anbieter-Eintrag, falls vorhanden, gültig und kein Fehlschlag.

    Gibt {"status": "treffer"|"nichts", "content": str|None} zurück, oder None
    wenn kein Eintrag existiert, der Eintrag abgelaufen ist, oder der letzte
    Versuch ein Fehlschlag war (dann soll der Aufrufer immer live neu fragen).
    """
    row = conn.execute(
        "SELECT e.status, e.fingerabdruck, e.datum "
        "FROM ergebnisse e JOIN songs s ON s.id = e.song_id "
        "WHERE e.quelle=? AND s.artist_key=? AND s.titel_key=?",
        (provider, artist_key, title_key),
    ).fetchone()
    if row is None:
        return None

    status, fingerabdruck, datum = row
    if status == "fehlschlag":
        return None  # nie als Cache-Treffer werten — immer erneut live fragen

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
    fehlergrund: str | None = None,
    genre: str | None = None,
) -> None:
    """Speichert (Upsert) das Ergebnis einer Anbieter-Abfrage — IMMER, auch bei Fehlschlag.

    status ∈ {"treffer", "nichts", "fehlschlag"}. Bei "treffer" wird content
    über seinen SHA-256-Fingerabdruck in `texte` dedupliziert. Bei "fehlschlag"
    sollte `fehlergrund` gesetzt sein (z.B. "rate_limit", "timeout", "captcha").
    Jeder Provider-Versuch für einen Song hinterlässt eine Zeile — ein
    Fehlschlag wird nie stillschweigend übersprungen.
    """
    if status not in ("treffer", "nichts", "fehlschlag"):
        raise ValueError(f"Ungültiger status: {status!r}")

    song_id = _get_or_create_song(conn, artist_key, title_key, genre)

    fingerabdruck = None
    if status == "treffer" and content is not None:
        fingerabdruck = _fingerprint(content)
        conn.execute(
            "INSERT INTO texte (fingerabdruck, inhalt) VALUES (?, ?) "
            "ON CONFLICT(fingerabdruck) DO NOTHING",
            (fingerabdruck, content),
        )

    conn.execute(
        "INSERT INTO ergebnisse (song_id, quelle, status, fehlergrund, fingerabdruck, datum) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(song_id, quelle) DO UPDATE SET "
        "status=excluded.status, fehlergrund=excluded.fehlergrund, "
        "fingerabdruck=excluded.fingerabdruck, datum=excluded.datum",
        (song_id, provider, status, fehlergrund, fingerabdruck, _now_iso()),
    )
    conn.commit()


def get_transcript(
    conn: sqlite3.Connection, audio_key: str, model: str, params_key: str
) -> dict | None:
    """Liefert ein gecachtes Whisper-Transkript oder None."""
    row = conn.execute(
        "SELECT transkript, no_speech_prob, avg_logprob FROM transkripte "
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
        "INSERT INTO transkripte "
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
