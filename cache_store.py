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

lookup_lrclib_dump() ist ein Sonderfall: liest NICHT die eigene Cache-DB,
sondern einen externen, read-only geöffneten LRCLib-Datenbank-Abzug (eigenes
Schema, siehe Docstring dort) — als Beschleuniger VOR einer echten Live-
Abfrage bei lrclib (siehe fetch_songtext._query_provider).
"""

from __future__ import annotations

import hashlib
import re
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


_PUNCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_punctuation_for_lrclib_dump(text: str) -> str:
    """Entfernt Satzzeichen und kollabiert Mehrfach-Leerzeichen -- NUR für den
    Abgleich gegen den externen LRCLib-Datenbank-Abzug (siehe
    lookup_lrclib_dump), NICHT Teil von normalize_key() selbst.

    Root Cause (gegen den echten lokalen Dump verifiziert, nicht geraten):
    LRCLib speichert `name_lower`/`artist_name_lower` bereits ohne
    Satzzeichen, z.B. "Stayin' Alive" -> "stayin alive" (Apostroph weg),
    "Dusk Till Dawn (Radio Edit)" -> "dusk till dawn radio edit" (Klammern
    weg, Inhalt bleibt als Wort), "Arthas, My Son (Cinematic Intro)" ->
    "arthas my son cinematic intro" (Komma + Klammern weg). Unsere eigene
    normalize_key() (NFC + strip + lower) lässt Satzzeichen dagegen
    unangetastet -- ein Song mit Apostroph/Klammern/Komma/Bindestrich im
    Titel fand im Dump deshalb keinen Treffer, obwohl er dort vorhanden war.

    Bekannter, NICHT behobener Rest-Fall: mindestens ein Beleg zeigte auch
    eine Diakritika-Umschrift im Dump (z.B. "Eivør Pálsdóttir" ->
    "eivor palsdottir", ø->o, á->a, ö->o). Das wird hier bewusst NICHT
    nachgebildet -- ein einzelner Beleg reicht nicht, um den zugrunde
    liegenden Algorithmus (z.B. vollständige Transliteration vs. nur
    bestimmte Zeichen) sicher zu kennen. Songs mit akzentuierten Buchstaben
    können deshalb weiterhin am Dump vorbeigehen und fallen dann auf die
    normale Live-Abfrage zurück -- kein Datenverlust, nur ein verpasster
    Beschleuniger.

    Regel: alles außer Wortzeichen (\\w, Unicode-bewusst) und Leerraum
    entfernen, dann Mehrfach-Leerzeichen zu einem kollabieren und trimmen.
    """
    return _WHITESPACE_RE.sub(" ", _PUNCTUATION_RE.sub("", text)).strip()


def lookup_lrclib_dump(
    conn: sqlite3.Connection, artist_key: str, title_key: str
) -> dict | None:
    """Sucht (artist_key, title_key) im lokalen LRCLib-Datenbank-Abzug (Original-
    LRCLib-Schema, Tabellen `tracks`/`lyrics` — siehe fetch_songtext._query_provider).

    Exakter Abgleich auf `tracks.artist_name_lower`/`tracks.name_lower` — KEINE
    Dauer, KEINE Fuzzy-Ähnlichkeit: die echte lrclib-Live-Suche matcht laut
    ihrem eigenen Quellcode ebenfalls nur Text, nicht Dauer, und ein exakter
    Abgleich ist hier einfacher als Fuzzy-Scoring nachzubauen.

    artist_key/title_key kommen vom Aufrufer bereits normalize_key()-
    normalisiert (NFC + strip + lower), wie im Rest des Moduls üblich. Für den
    SQL-Vergleich wird intern zusätzlich _strip_punctuation_for_lrclib_dump
    angewendet (siehe dortiger Docstring) -- NUR hier, NICHT als Änderung an
    normalize_key() selbst (das würde jeden bestehenden songs/ergebnisse-
    Eintrag betreffen, viel zu großer Blast-Radius für einen reinen
    Dump-Abgleichs-Fix).

    `conn` muss der Aufrufer bereits offen übergeben — üblicherweise mit
    `sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)`: der Abzug
    liegt typischerweise auf einem SMB-Netzlaufwerk, das die von SQLite fürs
    Locking benötigten Dateisperren nicht unterstützt (`mode=ro` allein scheitert
    dort mit "unable to open database file"). `immutable=1` überspringt jegliches
    Locking — setzt voraus, dass sich die Datei während des Zugriffs nicht ändert.

    Gibt None zurück, wenn zu (artist_key, title_key) GAR KEIN Track existiert
    (0 Zeilen) — dann soll der Aufrufer wie bisher live nachfragen. Existiert
    mindestens ein Track, wird IMMER ein Dict geliefert (gleiche Form wie
    get_provider): {"status": "treffer", "content": str} wenn ein Songtext
    gefunden wurde, sonst {"status": "nichts", "content": None}.

    Mehrfachtreffer (z.B. mehrere Alben/Versionen desselben Songs) sind normal.
    Da keine Dauer-Angabe zum Abgleichen zur Verfügung steht und Fuzzy-Matching
    bewusst nicht gewünscht ist, wird pragmatisch ausgewählt: zuerst ein Track
    mit synced_lyrics, sonst einer mit plain_lyrics, sonst gilt "kein Songtext".
    Bei mehreren gleichwertigen Kandidaten gewinnt deterministisch die kleinste
    tracks.id.
    """
    artist_lookup = _strip_punctuation_for_lrclib_dump(artist_key)
    title_lookup = _strip_punctuation_for_lrclib_dump(title_key)
    rows = conn.execute(
        "SELECT t.id, l.has_synced_lyrics, l.has_plain_lyrics, "
        "l.synced_lyrics, l.plain_lyrics "
        "FROM tracks t LEFT JOIN lyrics l ON t.last_lyrics_id = l.id "
        "WHERE t.artist_name_lower = ? AND t.name_lower = ?",
        (artist_lookup, title_lookup),
    ).fetchall()
    if not rows:
        return None  # kein Track zu Künstler+Titel überhaupt — Aufrufer fragt live

    def _rank(row: tuple) -> tuple[int, int]:
        track_id, has_synced, has_plain, synced, plain = row
        if has_synced and synced:
            tier = 0
        elif has_plain and plain:
            tier = 1
        else:
            tier = 2
        return (tier, track_id)

    _track_id, has_synced, has_plain, synced, plain = min(rows, key=_rank)
    if has_synced and synced:
        return {"status": "treffer", "content": synced}
    if has_plain and plain:
        return {"status": "treffer", "content": plain}
    return {"status": "nichts", "content": None}


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
