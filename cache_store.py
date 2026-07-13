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
    transkripte — EIN Whisper-Transkript je Song (Künstler+Titel-Identität,
                  wie `songs` — nicht mehr an Datei/Modell/Parameter gebunden)

Ein Fehlschlag (Timeout/Rate-Limit/Captcha) wird IMMER festgehalten (Status
"fehlschlag" + Grund) — nie stillschweigend verworfen. Er zählt aber nie als
gültiger Cache-Treffer: get_provider() liefert für "fehlschlag" immer None,
damit der Aufrufer beim nächsten Lauf erneut live fragt.
"""

from __future__ import annotations

import hashlib
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_TTL_DAYS = 30

_TRANSKRIPTE_SCHEMA = """
CREATE TABLE IF NOT EXISTS transkripte (
    song_id INTEGER PRIMARY KEY REFERENCES songs(id),
    transkript TEXT,
    no_speech_prob REAL,
    avg_logprob REAL,
    modell TEXT,
    datum TEXT
);
"""

_SCHEMA = f"""
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

{_TRANSKRIPTE_SCHEMA}
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
    _migrate_transkripte_v1_to_v2(conn)
    return conn


def _migrate_transkripte_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Migriert `transkripte` vom alten Datei-Schlüssel (v1) auf Song-Identität (v2).

    Läuft bei jedem open_cache() automatisch mit, greift aber nur einmal: eine
    PRAGMA table_info-Prüfung erkennt die alte Spalte `datei_kennung` — fehlt
    sie (frische DB oder schon migriert), passiert nichts (idempotent).

    Alte Zeilen werden aus dem im Pfad enthaltenen Datei-Tag (Künstler/Titel)
    rekonstruiert — NICHT neu transkribiert. Mehrere alte Zeilen zur selben
    Datei (unterschiedliche Fenster-Parameter) werden auf die jüngste (nach
    `datum`) reduziert, weil das neue Schema nur EIN Transkript pro Song kennt.
    Nicht migrierbare Zeilen (Datei fehlt / keine Tags lesbar) werden NIE
    stillschweigend verworfen, sondern nur nicht übernommen — mit sichtbarer
    Warnung inkl. Grund. Die alte Tabelle bleibt als `transkripte_alt_v1`
    Backup erhalten (kein DROP).
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(transkripte)").fetchall()}
    if "datei_kennung" not in cols:
        return  # schon im neuen Format oder frische DB — nichts zu tun

    import fetch_songtext  # lazy: fetch_songtext importiert seinerseits cache_store

    old_rows = conn.execute(
        "SELECT datei_kennung, modell, transkript, no_speech_prob, avg_logprob, datum "
        "FROM transkripte"
    ).fetchall()

    # Pro Pfad nur die juengste Zeile behalten (alte Zeilen: Datei+Modell+
    # Parameter-Schluessel, oft mehrere Fenster-Starts je Datei — neues Schema
    # kennt nur genau EIN Transkript je Song).
    by_path: dict[str, tuple] = {}
    for (
        datei_kennung,
        modell,
        transkript,
        no_speech_prob,
        avg_logprob,
        datum,
    ) in old_rows:
        path_str = datei_kennung.rsplit("|", 2)[0]
        existing = by_path.get(path_str)
        if existing is None or datum > existing[-1]:
            by_path[path_str] = (modell, transkript, no_speech_prob, avg_logprob, datum)

    migrated: list[tuple] = []
    failed: list[tuple[str, str]] = []
    for path_str, (
        modell,
        transkript,
        no_speech_prob,
        avg_logprob,
        datum,
    ) in by_path.items():
        path = Path(path_str)
        if not path.exists():
            failed.append((path_str, "Datei fehlt"))
            continue
        try:
            artist, title, _genre = fetch_songtext._read_audio_tags(path)
        except Exception:
            artist, title = "", ""
        if not artist and not title:
            failed.append((path_str, "keine Tags lesbar"))
            continue
        clean_title = fetch_songtext._clean_query_title(title)
        artist_key = normalize_key(artist)
        titel_key = normalize_key(clean_title)
        migrated.append(
            (
                artist_key,
                titel_key,
                modell,
                transkript,
                no_speech_prob,
                avg_logprob,
                datum,
            )
        )

    conn.execute("ALTER TABLE transkripte RENAME TO transkripte_alt_v1")
    conn.executescript(_TRANSKRIPTE_SCHEMA)

    for (
        artist_key,
        titel_key,
        modell,
        transkript,
        no_speech_prob,
        avg_logprob,
        datum,
    ) in migrated:
        song_id = _get_or_create_song(conn, artist_key, titel_key)
        conn.execute(
            "INSERT INTO transkripte "
            "(song_id, transkript, no_speech_prob, avg_logprob, modell, datum) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(song_id) DO UPDATE SET "
            "transkript=excluded.transkript, no_speech_prob=excluded.no_speech_prob, "
            "avg_logprob=excluded.avg_logprob, modell=excluded.modell, datum=excluded.datum",
            (song_id, transkript, no_speech_prob, avg_logprob, modell, datum),
        )
    conn.commit()

    print(
        f"Migration transkripte v1->v2: {len(migrated)}/{len(by_path)} Pfade migriert "
        f"({len(old_rows)} alte Zeilen gelesen) — transkripte_alt_v1 bleibt als Backup."
    )
    if failed:
        print(f"  {len(failed)} nicht migrierbar:")
        for path_str, reason in failed:
            print(f"    - {reason}: {path_str}")


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
    conn: sqlite3.Connection, artist_key: str, titel_key: str
) -> dict | None:
    """Liefert das gecachte Whisper-Transkript für (artist_key, titel_key) oder None.

    Legt bei Fehlen KEINEN Song an (reiner Lookup) — existiert der Song nicht
    oder hat er noch kein Transkript, wird None geliefert.
    """
    row = conn.execute(
        "SELECT t.transkript, t.no_speech_prob, t.avg_logprob "
        "FROM transkripte t JOIN songs s ON s.id = t.song_id "
        "WHERE s.artist_key=? AND s.titel_key=?",
        (artist_key, titel_key),
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
    artist_key: str,
    titel_key: str,
    transcript: str,
    no_speech_prob: float,
    avg_logprob: float,
    modell: str | None = None,
    genre: str | None = None,
) -> None:
    """Speichert (Upsert) das EINE Whisper-Transkript für diesen Song.

    `modell` ist reine Info-Spalte, nicht Teil des Schlüssels — pro Song wird
    genau ein Transkript vorgehalten, unabhängig von Modell/Fenster-Parametern
    künftiger Aufrufe. Legt den Song bei Bedarf an (siehe _get_or_create_song).
    """
    song_id = _get_or_create_song(conn, artist_key, titel_key, genre)
    conn.execute(
        "INSERT INTO transkripte "
        "(song_id, transkript, no_speech_prob, avg_logprob, modell, datum) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(song_id) DO UPDATE SET "
        "transkript=excluded.transkript, no_speech_prob=excluded.no_speech_prob, "
        "avg_logprob=excluded.avg_logprob, modell=excluded.modell, datum=excluded.datum",
        (song_id, transcript, no_speech_prob, avg_logprob, modell, _now_iso()),
    )
    conn.commit()
