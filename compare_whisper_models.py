#!/usr/bin/env python3
"""Manueller Qualitätsvergleich der Whisper-Modelle small/medium/turbo.

Wählt --n Songs aus der Cache-Datenbank (cache.db), die dort
mindestens einen Provider-Treffer haben (songs JOIN ergebnisse, status=
'treffer') — ob für den Song bereits einmal ein Whisper-Transkript existiert,
ist für die Auswahl UNERHEBLICH, da ohnehin jeder gefundene Song frisch
transkribiert wird (s.u.). Ein Provider-Treffer wird nur gebraucht, damit die
Sprache klassifizierbar ist (detect_language_hint) — ohne jeden
Kandidatentext bliebe der Hint immer None und der Song würde in
select_language_pools ohnehin übersprungen. Die Auswahl ist sprachlich
stratifiziert: ca. 80 % englisch- / 20 % deutschsprachig (bei --n 20 also
16/4, bei --n 10 8/2 usw. -- gerundet über round(n * 0.8)). Songs ohne
eindeutig erkennbare Sprache zählen nicht zur Quote und werden übersprungen.
Für jeden gefundenen Song läuft EINE FRISCHE Transkription mit JEDEM der drei
Modelle (small, medium, turbo — Turbo-Modellname mit der installierten
faster-whisper-Version live verifiziert, siehe ROADMAP.md), damit alle drei
unter identischen Bedingungen verglichen werden (nicht das gecachte
small-Transkript wiederverwendet). Pro Song entsteht eine TXT-Datei mit allen
drei Transkripten nebeneinander — für den rein manuellen Lesevergleich. Es
gibt KEIN automatisches Scoring.

Ausgabe so schnell wie möglich: Jede Song-Datei wird SOFORT (mit Kopf, aber
noch ohne Transkript) angelegt, bevor überhaupt ein Modell läuft. Danach wird
nach jedem transkribierten Song direkt der jeweilige Modell-Abschnitt an
seine Datei angehängt (write_song_header + append_model_transcript) — es
wird NICHTS im Speicher zwischengehalten und erst am Ende geschrieben. Damit
sind alle Song-Dateien nach dem `small`-Durchlauf schon vollständig mit
diesem Abschnitt auf der Platte, auch wenn `medium`/`turbo` noch laufen oder
der Lauf dazwischen abbricht.

Modell-für-Modell statt Song-für-Song: die drei Modelle werden NACHEINANDER
geladen (nicht alle drei gleichzeitig im Speicher gehalten) — small+medium+
turbo zusammen brauchen ca. 3,6 GB, was auf einer 8-GB-Maschine zu Swapping
und damit zu drastisch verlangsamten Transkriptionen führen kann. Für jedes
Modell wird EINMAL geladen, dann laufen ALLE gefundenen Songs durch dieses
eine Modell (Ergebnis wird direkt je Song angehängt, s.o.), danach wird das
Modell wieder aus dem Speicher entfernt (`del` + `gc.collect()`), bevor das
nächste Modell geladen wird — so ist nie mehr als ein Modell gleichzeitig
resident.

Die Cache-DB speichert nur normalisierte artist_key/titel_key, keine
Dateipfade. Die gesuchten (artist_key, titel_key)-Paare stehen aber bereits
VOR jedem Bibliothekszugriff fest (aus der Cache-DB-Auswahl) -- deshalb
durchsucht das Skript die Bibliothek (--library) EINMALIG und GEZIELT nach
genau diesen Paaren (Tags lesen via mutagen + normalisieren, Schlüssel gegen
die gesuchte Menge prüfen) und bricht den Durchlauf sofort ab, sobald alle
gesuchten Songs (Pflicht-Songs + stratifizierte Zufallsauswahl je Sprachpool)
gefunden wurden -- es gibt keinen separaten "kompletter Index zuerst"-Schritt
mehr. Das minimiert unnötige Dateizugriffe, was besonders bei einer
Bibliothek auf einer Netzwerkfreigabe (SMB) wichtig ist: je mehr Dateien
angefasst werden, desto größer das Risiko eines Hängers. Ein
Fortschritts-Hinweis erscheint alle 1000 gescannte Dateien.

Songs ohne Treffer in der Bibliothek werden übersprungen. Um trotzdem auf
--n echte Treffer zu kommen, enthält jeder Sprachpool von vornherein
Ersatzkandidaten (Puffer, Standard das 3-fache der Zielquote) -- fehlt einem
Primärkandidaten der Bibliothekstreffer, zählt stattdessen automatisch ein
Ersatzkandidat AUS DEMSELBEN Sprachpool zur Quote (die 80/20-Verteilung
bleibt dadurch auch im Endergebnis erhalten, nicht nur in der
Anfangsauswahl). Reicht ein Sprachpool trotz Puffer nicht aus, macht das
Skript mit weniger als --n Songs weiter (kein Abbruch).

Pflicht-Songs: "Nina Hagen Band" / "Rangehn" ist immer zusätzlich zur
Zufallsauswahl in der Stichprobe (unabhängig von --n und --seed) — war sie
ohnehin unter den zufällig gezogenen Songs, wird sie nicht doppelt
verarbeitet. Mit --include ARTIST:TITEL (wiederholbar) lassen sich weitere
Pflicht-Songs ergänzen. Wird die zugehörige Audiodatei nicht in der
Bibliothek gefunden, erscheint das klar vermerkt in der Konsolenausgabe und
in modellvergleich_index.txt. Pflicht-Songs laufen im selben einmaligen
Bibliotheksdurchlauf mit wie die stratifizierte Zufallsauswahl (kein
zweiter Durchlauf).

Verwendung:
    python3 compare_whisper_models.py
    python3 compare_whisper_models.py --n 10 --seed 42
    python3 compare_whisper_models.py --library /Volumes/music/musik --output-dir whisper_modellvergleich
    python3 compare_whisper_models.py --include "Kraftwerk:Autobahn" --include "Nina Hagen:Naturträne"
"""

from __future__ import annotations

import argparse
import gc
import random
import sys
import tempfile
from pathlib import Path

import cache_store
import lyrics_core
from inspect_song import sanitize_filename

DEFAULT_MODELS = ("small", "medium", "turbo")

# Wird IMMER zusätzlich zur Zufallsauswahl in die Stichprobe aufgenommen,
# unabhängig von --include/--n/--seed (auf Nutzerwunsch fest garantiert).
_ALWAYS_INCLUDE: tuple[tuple[str, str], ...] = (("Nina Hagen Band", "Rangehn"),)

# Sprachverteilung der stratifizierten Zufallsauswahl: ~80 % en, ~20 % de.
_EN_SHARE = 0.8
# Puffer je Sprachpool (Vielfaches der Zielquote), damit Ersatzkandidaten
# bereitstehen, falls ein Primärkandidat keinen Bibliothekstreffer hat.
_POOL_BUFFER_FACTOR = 3


def _default_db_path() -> Path:
    return cache_store.default_cache_path()


def _parse_include(value: str) -> tuple[str, str]:
    """Parst einen --include-Wert im Format "Artist:Titel"."""
    if ":" not in value:
        raise argparse.ArgumentTypeError(
            f"--include erwartet das Format ARTIST:TITEL, bekommen: {value!r}"
        )
    artist, _, title = value.partition(":")
    artist, title = artist.strip(), title.strip()
    if not artist or not title:
        raise argparse.ArgumentTypeError(
            f"--include: Artist und Titel dürfen nicht leer sein, bekommen: {value!r}"
        )
    return artist, title


def dedupe_forced_songs(forced: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Entfernt Duplikate aus einer Liste von (Artist, Titel)-Pflicht-Songs.

    Vergleich über normalisierte Schlüssel (cache_store.normalize_key), erste
    Nennung gewinnt.
    """
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for artist, title in forced:
        key = (cache_store.normalize_key(artist), cache_store.normalize_key(title))
        if key in seen:
            continue
        seen.add(key)
        unique.append((artist, title))
    return unique


def select_all_candidate_pairs(conn) -> list[tuple[str, str]]:
    """Alle (artist_key, titel_key)-Paare mit mindestens einem Provider-Treffer.

    Ob bereits ein Whisper-Transkript existiert, ist hier bewusst KEIN
    Kriterium (siehe Moduldocstring) -- jeder gefundene Song wird ohnehin
    frisch transkribiert. Ein Provider-Treffer wird nur gebraucht, damit die
    Sprache klassifizierbar ist (detect_language_hint); ohne jeden
    Kandidatentext bliebe der Hint immer None und der Song würde in
    select_language_pools ohnehin übersprungen -- ihn hier schon auszulassen
    spart unnötige detect_language_hint-Aufrufe.
    """
    rows = conn.execute(
        "SELECT DISTINCT s.artist_key, s.titel_key FROM songs s "
        "JOIN ergebnisse e ON e.song_id = s.id "
        "WHERE e.status = 'treffer'"
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def _read_duration_sec(path: Path) -> float:
    """Liest die Tracklänge in Sekunden via mutagen. 0.0 bei Fehler (-> 8-Min-Fallback)."""
    try:
        from mutagen import File as MutagenFile

        tags = MutagenFile(path, easy=True)
        if tags is None or tags.info is None:
            return 0.0
        return float(getattr(tags.info, "length", 0.0) or 0.0)
    except Exception:
        return 0.0


def get_candidate_texts(conn, artist_key: str, titel_key: str) -> list[str]:
    """Alle Provider-Kandidatentexte (status='treffer') für einen Song.

    Gleiches JOIN-Muster wie cache_store.get_provider() (ergebnisse JOIN songs
    JOIN texte), nur über alle Provider gleichzeitig statt für einen
    bestimmten -- Grundlage für den Sprach-Hint vor der Transkription.
    """
    rows = conn.execute(
        "SELECT t.inhalt FROM ergebnisse e "
        "JOIN songs s ON s.id = e.song_id "
        "JOIN texte t ON t.fingerabdruck = e.fingerabdruck "
        "WHERE e.status='treffer' AND s.artist_key=? AND s.titel_key=?",
        (artist_key, titel_key),
    ).fetchall()
    return [row[0] for row in rows if row[0]]


def detect_language_hint(conn, artist_key: str, titel_key: str) -> str | None:
    """Ermittelt den Sprach-Hint für einen Song aus seinen Provider-Kandidatentexten.

    Genau wie im Produktivbetrieb (siehe evaluate_lyrics.py) wird EIN Sprach-
    Hint aus allen gecachten Kandidatentexten ermittelt (lyrics_core.
    _detect_lrc_language erwartet eine Liste von .lrc-Dateipfaden, deshalb
    schreiben wir jeden Kandidatentext kurz in eine Temp-Datei). Ohne
    Kandidatentexte (kein Provider-Treffer im Cache) bleibt der Hint None --
    entspricht dem Produktiv-Fallback in diesem Fall.
    """
    texts = get_candidate_texts(conn, artist_key, titel_key)
    if not texts:
        return None

    tmp_paths: list[Path] = []
    try:
        for content in texts:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".lrc", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(content)
                tmp_paths.append(Path(tmp.name))
        return lyrics_core._detect_lrc_language(tmp_paths)
    finally:
        for p in tmp_paths:
            p.unlink(missing_ok=True)


def select_language_pools(
    conn,
    pairs: list[tuple[str, str]],
    n: int,
    seed: int | None,
    exclude_artists: set[str] | None = None,
) -> dict[str, tuple[list[tuple[str, str]], int]]:
    """Mischt `pairs` und klassifiziert sie in einen "en"- und "de"-Pool.

    Für jeden Kandidaten (in zufälliger Reihenfolge) wird per
    detect_language_hint die Sprache ermittelt. Kandidaten mit anderer/nicht
    erkennbarer Sprache zählen nicht zur Stratifizierung und werden
    übersprungen (weder Pool). Die Klassifizierung bricht ab, sobald BEIDE
    Pools ihren Puffer (Zielquote * _POOL_BUFFER_FACTOR) erreicht haben, oder
    sobald `pairs` erschöpft ist -- damit muss nicht für die komplette
    Cache-DB die Sprache ermittelt werden.

    Jeder Künstler (artist_key) taucht höchstens EINMAL in der gesamten
    Auswahl auf, über beide Pools hinweg -- ohne diese Sperre kann ein im
    Cache stark vertretener Künstler rein zufällig mehrfach gezogen werden
    (real beobachtet: einzelne Künstler mit hunderten gecachten Songs bei nur
    wenigen hundert Künstlern insgesamt), was die Stichprobe unnötig auf
    dessen Vokabular/Stimme verengt statt eine breite Auswahl zu liefern.
    `exclude_artists` nimmt zusätzlich bereits anderweitig vergebene
    Künstler entgegen (z.B. Pflicht-Songs aus main()), damit auch die nicht
    nochmal zufällig gezogen werden.

    Gibt {"en": (pool, zielquote), "de": (pool, zielquote)} zurück. Die
    ersten `zielquote` Einträge jedes Pools sind die Primärauswahl, der Rest
    dient als Ersatzkandidaten für die Bibliothekssuche (siehe
    resolve_all_songs).
    """
    en_target = round(n * _EN_SHARE)
    de_target = n - en_target
    en_buffer = en_target * _POOL_BUFFER_FACTOR
    de_buffer = de_target * _POOL_BUFFER_FACTOR

    shuffled = list(pairs)
    random.Random(seed).shuffle(shuffled)

    en_pool: list[tuple[str, str]] = []
    de_pool: list[tuple[str, str]] = []
    used_artists: set[str] = set(exclude_artists or ())
    for artist_key, titel_key in shuffled:
        if len(en_pool) >= en_buffer and len(de_pool) >= de_buffer:
            break
        if artist_key in used_artists:
            continue
        language = detect_language_hint(conn, artist_key, titel_key)
        if language == "en" and len(en_pool) < en_buffer:
            en_pool.append((artist_key, titel_key))
            used_artists.add(artist_key)
        elif language == "de" and len(de_pool) < de_buffer:
            de_pool.append((artist_key, titel_key))
            used_artists.add(artist_key)
        # Alles andere (andere Sprache, None): ignoriert, keine Quote.

    return {"en": (en_pool, en_target), "de": (de_pool, de_target)}


def resolve_all_songs(
    library_root: Path,
    forced_songs: list[tuple[str, str]],
    language_pools: dict[str, tuple[list[tuple[str, str]], int]],
) -> tuple[list[dict], list[tuple[str, str]], list[dict], list[tuple[str, str]]]:
    """Durchsucht library_root EINMALIG gezielt nach den gesuchten Songs.

    `forced_songs` sind Pflicht-Songs (Artist, Titel), `language_pools`
    stratifizierte Zufallskandidaten je Sprache (siehe select_language_pools:
    {"en": (pool, zielquote), "de": (pool, zielquote)} -- ein Pool enthält
    Primär- UND Ersatzkandidaten in derselben Liste, es zählt nur, dass am
    Ende `zielquote` Treffer aus dem jeweiligen Pool gefunden werden, egal ob
    von einem Primär- oder Ersatzkandidaten).

    Bricht den Durchlauf sofort ab, sobald alle Pflicht-Songs gefunden sind
    UND jeder Sprachpool seine Zielquote erreicht hat. Wird die komplette
    Bibliothek durchsucht, ohne dass alle Ziele erreicht wurden, gelten die
    verbleibenden Pool-Kandidaten als tatsächlich fehlend (-> skipped); bei
    frühem Abbruch bleiben sie unbestimmt und werden NICHT als fehlend
    gemeldet (sie wurden schlicht nicht mehr gebraucht).

    Gibt (gefundene Pflicht-Songs, fehlende Pflicht-Songs, gefundene
    Zufalls-Songs, übersprungene Zufalls-Schlüssel) zurück. Gefundene
    Zufalls-Songs tragen ein "language"-Feld ("en"/"de"), Pflicht-Songs
    "forced": True.
    """
    forced_wanted: dict[tuple[str, str], tuple[str, str]] = {
        (cache_store.normalize_key(artist), cache_store.normalize_key(title)): (
            artist,
            title,
        )
        for artist, title in forced_songs
    }
    forced_found: dict[tuple[str, str], dict] = {}

    pool_state = {
        lang: {"wanted": set(keys), "target": target, "found": []}
        for lang, (keys, target) in language_pools.items()
    }

    scanned = 0
    exhausted = True
    for path in library_root.rglob("*"):
        if (
            not path.is_file()
            or path.suffix.lower() not in lyrics_core._AUDIO_EXTENSIONS
        ):
            continue
        scanned += 1
        if scanned % 1000 == 0:
            print(f"   ... {scanned} Dateien gescannt")

        artist, title, _genre = lyrics_core._read_audio_tags(path)
        if not artist and not title:
            continue
        artist_key = cache_store.normalize_key(artist)
        titel_key = cache_store.normalize_key(title)
        key = (artist_key, titel_key)

        if key in forced_wanted and key not in forced_found:
            forced_found[key] = {
                "artist_key": artist_key,
                "titel_key": titel_key,
                "path": path,
                "artist": artist,
                "title": title,
                "duration": _read_duration_sec(path),
                "forced": True,
            }
        else:
            for state in pool_state.values():
                if key in state["wanted"] and len(state["found"]) < state["target"]:
                    state["found"].append(
                        {
                            "artist_key": artist_key,
                            "titel_key": titel_key,
                            "path": path,
                            "artist": artist,
                            "title": title,
                            "duration": _read_duration_sec(path),
                            "forced": False,
                        }
                    )
                    state["wanted"].discard(key)
                    break

        forced_remaining = len(forced_found) < len(forced_wanted)
        pools_remaining = any(
            len(state["found"]) < state["target"] for state in pool_state.values()
        )
        if not forced_remaining and not pools_remaining:
            exhausted = False
            break
    else:
        exhausted = True

    print(f"Bibliotheks-Suche abgeschlossen: {scanned} Dateien angesehen.")

    found_forced = list(forced_found.values())
    forced_missing = [
        artist_title
        for key, artist_title in forced_wanted.items()
        if key not in forced_found
    ]

    found_random: list[dict] = []
    skipped_random: list[tuple[str, str]] = []
    for lang, state in pool_state.items():
        for entry in state["found"]:
            entry["language"] = lang
            found_random.append(entry)
        if exhausted:
            skipped_random.extend(state["wanted"])

    return found_forced, forced_missing, found_random, skipped_random


def transcribe_song_with_model(
    entry: dict, model_name: str, language: str | None
) -> str:
    """Transkribiert entry['path'] frisch mit EINEM Modell.

    _transcribe() gibt eine tokenisierte Wortliste zurück (klein geschrieben,
    ohne Satzzeichen/Zahlen) — für den Qualitätsvergleich per Leseabgleich
    ausreichend, aber kein Fließtext mit Original-Zeichensetzung.
    """
    context_sec = lyrics_core._whisper_context_sec(entry["duration"])
    words, _no_speech_prob, _avg_logprob = lyrics_core._transcribe(
        entry["path"],
        start=0.0,
        context_sec=context_sec,
        model_name=model_name,
        language=language,
    )
    return " ".join(words) if words else "(kein Text erkannt)"


def _unique_output_path(output_dir: Path, artist: str, title: str) -> Path:
    """Dateiname <Artist>_<Titel>_modellvergleich.txt; hängt bei Kollision _2, _3, ... an."""
    base_name = (
        f"{sanitize_filename(artist)}_{sanitize_filename(title)}_modellvergleich"
    )
    path = output_dir / f"{base_name}.txt"
    counter = 2
    while path.exists():
        path = output_dir / f"{base_name}_{counter}.txt"
        counter += 1
    return path


def _existing_output_path(output_dir: Path, artist: str, title: str) -> Path | None:
    """Findet die Song-Datei eines früheren Laufs (ohne Kollisions-Suffix), falls vorhanden.

    Für Ergänzungsläufe mit --models: ein zusätzliches Modell soll seinen
    Abschnitt an die bestehende Datei eines Songs anhängen statt eine neue
    Datei mit _2-Suffix anzulegen (siehe _unique_output_path).
    """
    base_name = (
        f"{sanitize_filename(artist)}_{sanitize_filename(title)}_modellvergleich"
    )
    path = output_dir / f"{base_name}.txt"
    return path if path.exists() else None


def write_song_header(
    entry: dict,
    output_dir: Path,
    language_hint: str | None = None,
) -> Path:
    """Legt die Song-Datei SOFORT an (Kopf, noch ohne Transkript-Abschnitte).

    Läuft VOR jedem Modell-Durchlauf -- die Datei existiert damit schon,
    bevor überhaupt transkribiert wurde (siehe append_model_transcript,
    Moduldocstring: Ausgabe so schnell wie möglich).
    """
    hint_display = language_hint if language_hint else "nicht erkannt"
    header = (
        f"Artist: {entry['artist']}\n"
        f"Titel: {entry['title']}\n"
        f"Sprache (Hint): {hint_display}\n"
        "\n"
    )
    output_path = _unique_output_path(output_dir, entry["artist"], entry["title"])
    output_path.write_text(header, encoding="utf-8")
    return output_path


def append_model_transcript(
    output_path: Path, model_name: str, transcript: str
) -> None:
    """Hängt den Abschnitt EINES Modells sofort an eine bestehende Song-Datei an.

    Wird direkt nach jeder einzelnen Transkription aufgerufen (nicht erst
    gesammelt und am Ende geschrieben) -- die Datei ist damit nach jedem
    Modell-Durchlauf bereits vollständig auf der Platte.
    """
    with output_path.open("a", encoding="utf-8") as f:
        f.write(f"=== {model_name} ===\n{transcript}\n\n")


def write_index_file(
    processed: list[tuple[dict, Path]],
    skipped: list[tuple[str, str]],
    output_dir: Path,
    forced_missing: list[tuple[str, str]] | None = None,
) -> Path:
    forced_missing = forced_missing or []
    lines = [
        f"Whisper-Modellvergleich — {len(processed)} Songs bearbeitet, "
        f"{len(skipped)} übersprungen (nicht in Bibliothek gefunden)",
        "",
        "Bearbeitet:",
    ]
    for entry, out_path in processed:
        marker = " [Pflicht-Song]" if entry.get("forced") else ""
        language_hint = entry.get("language_hint")
        lang_marker = f" [{language_hint}]" if language_hint else ""
        lines.append(
            f"  - {entry['artist']} - {entry['title']} -> {out_path.name}"
            f"{marker}{lang_marker}"
        )

    lines.append("")
    lines.append("Übersprungen (nicht in Bibliothek gefunden):")
    if skipped:
        for artist_key, titel_key in skipped:
            lines.append(f"  - artist_key={artist_key!r}, titel_key={titel_key!r}")
    else:
        lines.append("  (keine)")

    lines.append("")
    lines.append(
        "Pflicht-Songs NICHT in Bibliothek gefunden (--include / immer dabei):"
    )
    if forced_missing:
        for artist, title in forced_missing:
            lines.append(f"  - {artist} - {title}")
    else:
        lines.append("  (keine)")

    index_path = output_dir / "modellvergleich_index.txt"
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return index_path


def _unload_model(model_name: str) -> None:
    """Entfernt ein geladenes Whisper-Modell wieder aus dem globalen Cache.

    lyrics_core._get_whisper_model() hält geladene Modelle in
    lyrics_core._whisper_models (Prozess-globaler Cache, für den
    Produktivbetrieb gedacht, wo immer nur EIN Modell läuft). Für den
    Modellvergleich muss dieser Cache-Eintrag nach jedem Modell-Durchlauf
    wieder entfernt werden, sonst blieben am Ende alle drei Modelle
    gleichzeitig resident (siehe Docstring oben: Speicherschonung auf
    8-GB-Maschinen).
    """
    lyrics_core._whisper_models.pop(model_name, None)
    gc.collect()


def run_model_over_songs(
    model_name: str,
    found: list[dict],
    language_hints: list[str | None],
    output_paths: list[Path],
) -> bool:
    """Lädt model_name EINMAL, transkribiert ALLE Songs, entlädt danach wieder.

    Jedes Transkript wird SOFORT über append_model_transcript an die
    zugehörige (bereits von write_song_header angelegte) Song-Datei
    angehängt -- nichts wird im Speicher gesammelt und erst am Ende
    geschrieben (siehe Moduldocstring: Ausgabe so schnell wie möglich).

    Gibt False zurück, wenn das Modell nicht geladen werden konnte (Aufrufer
    muss das als fatalen Fehler behandeln), sonst True.
    """
    if lyrics_core._get_whisper_model(model_name) is None:
        print(
            f"Fehler: Whisper-Modell {model_name!r} konnte nicht geladen werden "
            "(faster-whisper installiert?).",
            file=sys.stderr,
        )
        return False

    for i, entry in enumerate(found, start=1):
        print(
            f"  [{model_name}] [{i}/{len(found)}] {entry['artist']} - {entry['title']} ...",
            flush=True,
        )
        transcript = transcribe_song_with_model(
            entry, model_name, language_hints[i - 1]
        )
        append_model_transcript(output_paths[i - 1], model_name, transcript)

    _unload_model(model_name)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--n",
        type=int,
        default=20,
        metavar="N",
        help="Anzahl zu vergleichender Songs (Standard: 20), stratifiziert ~80%% "
        "englisch / ~20%% deutsch. Reicht ein Sprachpool nicht aus, macht das "
        "Skript mit weniger als N Songs weiter (kein Abbruch).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        metavar="SEED",
        help="Seed für die Zufallsauswahl (Standard: kein fester Seed, jeder Lauf unterschiedlich)",
    )
    parser.add_argument(
        "--library",
        default="/Volumes/music/musik",
        metavar="PFAD",
        help="Wurzelverzeichnis der Musikbibliothek (Standard: /Volumes/music/musik)",
    )
    parser.add_argument(
        "--output-dir",
        default="whisper_modellvergleich",
        metavar="PFAD",
        help="Zielverzeichnis für die Ausgabedateien (Standard: whisper_modellvergleich/ im aktuellen Verzeichnis)",
    )
    parser.add_argument(
        "--include",
        action="append",
        type=_parse_include,
        metavar="ARTIST:TITEL",
        default=[],
        help="Zusätzlicher Pflicht-Song, wiederholbar (Format 'Artist:Titel', z.B. "
        "'Nina Hagen Band:Rangehn'). Wird garantiert in die Stichprobe "
        "aufgenommen -- zusätzlich zu --n Zufalls-Songs, oder als einer davon "
        "falls er ohnehin gezogen würde (Dedupe, keine Doppelverarbeitung). "
        "'Nina Hagen Band:Rangehn' ist unabhängig von diesem Flag immer dabei.",
    )
    parser.add_argument(
        "--models",
        type=lambda s: tuple(m.strip() for m in s.split(",") if m.strip()),
        default=DEFAULT_MODELS,
        metavar="MODELL1,MODELL2,...",
        help="Kommagetrennte Liste zu vergleichender Whisper-Modelle (Standard: "
        f"{','.join(DEFAULT_MODELS)}). Existiert für einen Song bereits eine "
        "Datei aus einem früheren Lauf im selben --output-dir, wird der neue "
        "Modell-Abschnitt an diese Datei angehängt statt eine neue Datei "
        "anzulegen -- so lässt sich ein einzelnes zusätzliches Modell (z.B. "
        "--models large-v3) nachträglich derselben Stichprobe hinzufügen.",
    )
    args = parser.parse_args()

    forced_requested = list(_ALWAYS_INCLUDE) + list(args.include)
    forced_songs = dedupe_forced_songs(forced_requested)

    conn = cache_store.open_cache(_default_db_path())
    pairs = select_all_candidate_pairs(conn)
    if not pairs:
        # Kein Abbruch: Pflicht-Songs (--include / _ALWAYS_INCLUDE) brauchen
        # keinen Cache-Eintrag, nur einen Bibliothekstreffer -- die laufen
        # unten trotzdem weiter. Der eigentliche "nichts gefunden"-Fall wird
        # später über die leere `found`-Liste abgefangen.
        print(
            "Hinweis: keine Songs mit gecachtem Whisper-Transkript in der "
            "Cache-Datenbank gefunden -- nur Pflicht-Songs werden versucht."
        )

    forced_keys = {
        (cache_store.normalize_key(artist), cache_store.normalize_key(title))
        for artist, title in forced_songs
    }
    forced_artist_keys = {
        cache_store.normalize_key(artist) for artist, _ in forced_songs
    }
    pairs = [p for p in pairs if p not in forced_keys]

    print("Ermittle Sprachverteilung der Kandidaten (80% en / 20% de) ...")
    language_pools = select_language_pools(
        conn, pairs, args.n, args.seed, exclude_artists=forced_artist_keys
    )
    en_pool, en_target = language_pools["en"]
    de_pool, de_target = language_pools["de"]
    print(
        f"Sprachpools: {len(en_pool)} en-Kandidaten (Ziel {en_target}), "
        f"{len(de_pool)} de-Kandidaten (Ziel {de_target})."
    )

    library_root = Path(args.library)
    print(f"Durchsuche Bibliothek EINMALIG und gezielt: {library_root} ...")
    forced_found, forced_missing, random_found, skipped = resolve_all_songs(
        library_root, forced_songs, language_pools
    )
    for artist, title in forced_missing:
        print(f"Hinweis: Pflicht-Song nicht in Bibliothek gefunden: {artist} - {title}")

    found = forced_found + random_found
    if not found:
        print(
            "Weder Pflicht-Songs noch zufällig gezogene Songs wurden in der "
            "Bibliothek gefunden."
        )
        sys.exit(1)
    target_total = en_target + de_target
    if len(random_found) < target_total:
        print(
            f"Hinweis: nur {len(random_found)}/{target_total} zufällige Songs in "
            f"der Bibliothek gefunden ({len(skipped)} übersprungen)."
        )

    print("Ermittle Sprach-Hint für Pflicht-Songs aus Provider-Kandidatentexten ...")
    language_hints: list[str | None] = []
    for entry in found:
        if entry.get("forced"):
            language_hints.append(
                detect_language_hint(conn, entry["artist_key"], entry["titel_key"])
            )
        else:
            language_hints.append(entry["language"])
        entry["language_hint"] = language_hints[-1]
    conn.close()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Jede Song-Datei wird SOFORT angelegt (Kopf, noch ohne Transkript) --
    # Ausgabe so schnell wie möglich, nichts wartet auf das Ende aller drei
    # Modell-Durchläufe (siehe Moduldocstring). Existiert die Datei schon aus
    # einem früheren Lauf (--models-Ergänzungslauf auf derselben Stichprobe),
    # wird sie wiederverwendet statt eine neue mit _2-Suffix anzulegen.
    output_paths = [
        _existing_output_path(output_dir, entry["artist"], entry["title"])
        or write_song_header(entry, output_dir, language_hints[i])
        for i, entry in enumerate(found)
    ]

    # Modell-für-Modell statt Song-für-Song: jedes Modell wird einmal
    # geladen, läuft durch ALLE Songs (Ergebnis wird direkt je Song an die
    # Datei angehängt), wird danach wieder entladen -- nie mehr als ein
    # Modell gleichzeitig im Speicher (siehe Modul-Docstring).
    for model_name in args.models:
        ok = run_model_over_songs(model_name, found, language_hints, output_paths)
        if not ok:
            sys.exit(1)

    processed = list(zip(found, output_paths))
    index_path = write_index_file(processed, skipped, output_dir, forced_missing)
    print(f"\nFertig: {len(processed)} Songs verglichen, Index: {index_path}")


if __name__ == "__main__":
    main()
