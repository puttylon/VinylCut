#!/usr/bin/env python3
"""Steuer-Skript für die Songtexte-Pipeline.

Orchestriert die 5 Schritte aus dem Architektur-Dokument "workflow für
songexte.txt" (Abschnitt "ZIELARCHITEKTUR"): scannen, Anbieter abfragen,
Anbieter nachholen, bewerten, .lrc schreiben. Jeder Schritt hat sein eigenes
Flag -- KEIN Sammel-Flag mehr (Nutzer-Feedback: "kein Mensch braucht im Flag
den Begriff 'phase'"). Frühere Versionen kannten `--phase LISTE`; das ist
mit diesem Umbau ersatzlos entfallen (siehe ROADMAP.md).

Verwendung:
    python3 songtext_pipeline.py PFAD [--recursive]
        Kein Schritt-Flag angegeben -> Normal-Durchlauf: scan, abfragen,
        bewerten, schreiben (in dieser Reihenfolge). OHNE nachholen -- das
        läuft nur, wenn ausdrücklich angegeben (siehe unten).
    python3 songtext_pipeline.py PFAD --abfragen --bewerten --schreiben
        Nur die angegebenen Schritte.
    python3 songtext_pipeline.py --nachholen
        Nachhol-Modus über die GANZE Bibliothek (kein PFAD nötig) --
        impliziert automatisch --bewerten + --schreiben mit, sonst würde
        ein frisch gefundener Provider-Treffer nirgendwo ankommen.
    python3 songtext_pipeline.py PFAD --nachholen
        Nachhol-Modus NUR für die Songs unter PFAD (seit diesem Umbau
        möglich -- vorher wurde --nachholen bei gesetztem PFAD komplett
        übersprungen, siehe ROADMAP.md) -- impliziert ebenfalls --bewerten
        + --schreiben, ebenfalls auf PFAD eingegrenzt.

Jeder Schritt ist einzeln UND in beliebiger Kombination aufrufbar, jeweils
auf PFAD eingegrenzt, wenn PFAD gesetzt ist (sonst: ganze Bibliothek).
--scan/--schreiben brauchen zwingend eine echte Audiodatei, also PFAD.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import cache_store
import evaluate_lyrics
import fetch_providers
import lyrics_core
import scan_songs
import write_lrc


def build_file_song_map(
    root: Path,
    recursive: bool,
    conn: sqlite3.Connection,
    files: list[tuple[Path, str, str, str]] | None = None,
) -> list[tuple[Path, str, str]]:
    """Ordnet Audiodateien unter root ihren "songs"-Einträgen in der Cache-DB zu.

    Liest Künstler/Titel-Tags je Datei (lyrics_core._read_audio_tags) und
    sucht per cache_store.normalize_key den passenden (artist_key, titel_key)
    in der Tabelle "songs" -- Titel dabei über _clean_query_title bereinigt,
    genau wie beim Anlegen der songs-Zeile (siehe CACHE_DESIGN.md,
    "Normalisierung"). Dateien ohne lesbare Tags oder ohne passenden
    DB-Eintrag tauchen einfach nicht in der Rückgabe auf -- kein Fehler
    (siehe Design-Dokument, Abschnitt 3, Randfall b). Es gibt bewusst KEINE
    dauerhafte Pfad-Speicherung in der DB -- diese Zuordnung wird bei jedem
    Lauf frisch berechnet.

    files: optional vorab per scan_songs._read_tagged_files() eingelesene
    Liste -- erspart einen erneuten Verzeichnis-Walk + Tag-Read (siehe
    dortiger Docstring, "bis zu sechs volle Durchläufe desselben Baums").
    """
    mapping: list[tuple[Path, str, str]] = []
    entries = (
        files if files is not None else scan_songs._read_tagged_files(root, recursive)
    )
    for audio_path, artist, title, _genre in entries:
        if not artist and not title:
            continue
        artist_key, titel_key = lyrics_core._song_keys(artist, title)
        row = conn.execute(
            "SELECT 1 FROM songs WHERE artist_key=? AND titel_key=?",
            (artist_key, titel_key),
        ).fetchone()
        if row is None:
            continue
        mapping.append((audio_path, artist_key, titel_key))
    return mapping


def _scope_from_root(
    root: Path | None,
    recursive: bool,
    conn: sqlite3.Connection,
    files: list[tuple[Path, str, str, str]] | None = None,
) -> set[tuple[str, str]] | None:
    """Berechnet den Scope (Menge von (artist_key, titel_key)) für root, oder
    None ohne PFAD (= keine Eingrenzung, ganze Cache-DB -- bewusste "alles
    nachziehen"-Absicht, siehe fetch_providers.fetch_all-Docstring).

    Wird an mehreren Stellen in main() jeweils FRISCH aufgerufen (der reine
    DB-Abgleich ist billig) -- NIE aber der teure Verzeichnis-Walk selbst,
    siehe `files`-Parameter: läuft --scan im selben Aufruf VOR einem anderen
    Schritt (Standardfall), stehen frisch gescannte Songs erst danach in der
    "songs"-Tabelle -- eine vorher berechnete Zuordnung sähe sie noch nicht
    (siehe ROADMAP.md, realer Bug: Datei-Zuordnung vor dem Scan zu klein).
    Die Audiodateien SELBST und ihre Tags ändern sich dagegen innerhalb
    eines Laufs nicht -- `files` wird deshalb einmal in main() eingelesen
    und hier nur noch gegen die (ggf. frisch aktualisierte) DB abgeglichen.
    """
    if root is None:
        return None
    mapping = build_file_song_map(root, recursive, conn, files=files)
    return {(artist_key, titel_key) for _, artist_key, titel_key in mapping}


def fetch_providers_normal(
    conn: sqlite3.Connection,
    scope: set[tuple[str, str]] | None = None,
    file_order: list[tuple[Path, str, str]] | None = None,
    quiet: bool = False,
) -> None:
    """--abfragen: Normal-Modus von fetch_providers -- fragt Songs in "songs"
    bei allen 4 Anbietern ab (siehe fetch_providers.fetch_all).

    scope wird unverändert durchgereicht: ist er gesetzt (PFAD-Lauf, siehe
    main()), werden NUR die Songs des aktuellen Umfangs abgefragt, nicht die
    komplette, historisch gewachsene Cache-DB (siehe fetch_all-Docstring,
    "Behebt einen echten Bug"). file_order (dieselbe Liste wie für scope,
    siehe main()) bestimmt zusätzlich die Reihenfolge + zeigt den Dateinamen
    in der Konsolenausgabe (Nutzer-Feedback: Dateireihenfolge statt
    alphabetisch nach Künstler/Titel).

    Songs mit Skip-Genre (Hörbuch/Hörspiel/... ) werden dabei übersprungen --
    die Anzahl wird separat sichtbar gemacht, nicht nur stillschweigend
    gezählt. Ebenso Songs, für die kein einziger Anbieter mehr wirklich
    angefragt werden muss (jeder Anbieter hat schon einen gültigen
    Treffer/Nichts-Eintrag oder einen -- von --nachholen zu behandelnden --
    Fehlschlag, siehe fetch_all-Docstring).

    quiet=True unterdrückt Kopf-/Zusammenfassungszeilen UND fetch_all()s
    eigene Treffer-Zeile (siehe dortiger Docstring) -- gesetzt von
    main()/_run_selected_steps(), wenn im selben Durchlauf gleich danach
    --bewerten + --schreiben für denselben Song laufen und dessen EINE
    Abschlusszeile die ausführliche Zwischenausgabe hier überflüssig macht."""
    queried, skipped_genre, skipped_up_to_date = fetch_providers.fetch_all(
        conn, scope=scope, file_order=file_order, quiet=quiet
    )
    if quiet:
        return
    print(f"abfragen: {queried} Song(s) abgefragt.")
    if skipped_genre:
        print(
            f"  {skipped_genre} Song(s) wegen Genre übersprungen "
            "(Hörbuch/Hörspiel/Instrumental/...)."
        )
    if skipped_up_to_date:
        print(f"  {skipped_up_to_date} Song(s) bereits aktuell, nichts abzufragen.")


def fetch_providers_nachhol(
    conn: sqlite3.Connection, scope: set[tuple[str, str]] | None = None
) -> None:
    """--nachholen: Nachhol-Modus von fetch_providers -- fragt gezielt nur
    (Song, Provider)-Kombinationen mit status 'nichts'/'fehlschlag' erneut ab
    (siehe fetch_providers.retry_missing).

    scope wie bei --abfragen: None (kein PFAD) = ganze Cache-DB, sonst nur
    die Songs des aktuellen Laufs. Seit diesem Umbau möglich -- vorher wurde
    --nachholen bei gesetztem PFAD komplett übersprungen, weil retry_missing
    keinen Scope kannte (siehe ROADMAP.md)."""
    print("nachholen:")
    fetch_providers.retry_missing(conn, scope=scope)


def evaluate_lyrics_normal(
    conn: sqlite3.Connection,
    scope: set[tuple[str, str]] | None = None,
    file_song_map: dict[tuple[str, str], Path] | None = None,
    quiet: bool = False,
) -> None:
    """--bewerten: bewertet Songs (Konsens/Whisper), siehe evaluate_lyrics.evaluate_all.

    scope wie bei --abfragen (None ohne PFAD = ganze DB, sonst nur die Songs
    des aktuellen Laufs). file_song_map erlaubt Whisper bei Cache-Miss live
    zu transkribieren -- ohne Eintrag fällt der Song auf Konsens/Dauer-
    Heuristik zurück.

    quiet=True unterdrückt Kopf-/Ergebnis-/Zusammenfassungszeilen (siehe
    evaluate_all-Docstring) -- gesetzt, wenn im selben Durchlauf gleich
    danach --schreiben für denselben Song dessen EINE Abschlusszeile
    zeigt."""
    counts = evaluate_lyrics.evaluate_all(
        conn, scope=scope, file_song_map=file_song_map, quiet=quiet
    )
    if not counts or quiet:
        return
    print(
        f"bewerten: {counts['konsens']} Konsens, "
        f"{counts['whisper-akzeptiert']} Whisper akzeptiert, "
        f"{counts['abgelehnt']} abgelehnt, {counts['kein-provider']} ohne Provider, "
        f"{counts['uebersprungen']} übersprungen (unverändert)."
    )
    stats = lyrics_core._early_stop_stats
    if stats["versuche"]:
        print(
            f"  Whisper-Early-Stop: {stats['frueh_gestoppt']}/{stats['versuche']} "
            f"Läufe früh gestoppt, ~{stats['audio_sek_gespart']:.0f}s Audio gespart."
        )


def write_lrc_normal(
    conn: sqlite3.Connection,
    file_song_map: list[tuple[Path, str, str]],
    quiet: bool = False,
    folder_lock: object | None = None,
) -> None:
    """--schreiben: schreibt/löscht .lrc-Dateien je nach --bewerten-Entscheidung
    (wird intern erneut berechnet, siehe write_lrc.write_all -- kein
    Ablageort in der DB nötig).

    quiet=True unterdrückt nur die Kopf-/Zusammenfassungszeile hier UND in
    write_all() (siehe dortiger Docstring) -- die eine Ergebniszeile pro
    Song bleibt davon unberührt.

    folder_lock: bereits von main()s Datei-Schleife für den aktuellen Ordner
    gehaltene Sperre (siehe dortigen Kommentar, ROADMAP.md Punkt 4) -- wird
    unverändert an write_lrc.write_all() durchgereicht, das dann selbst
    NICHT mehr versucht, dieselbe Sperre ein zweites Mal zu beanspruchen."""
    counts = write_lrc.write_all(
        conn, file_song_map, quiet=quiet, external_lock=folder_lock
    )
    if quiet:
        return
    print(
        f"schreiben: {counts['updated']} geschrieben, "
        f"{counts['skipped']} übersprungen, {counts['not_found']} nicht gefunden."
    )


def _default_db_path() -> Path:
    return cache_store.default_cache_path()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        metavar="PFAD",
        help=(
            "Audiodatei oder Ordner (mit --recursive für Unterordner). "
            "Weggelassen = ganze Bibliothek (nur sinnvoll zusammen mit "
            "--abfragen/--nachholen/--bewerten, die keine echte Datei "
            "brauchen)."
        ),
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"songtext_pipeline.py {lyrics_core.__version__}",
    )
    parser.add_argument(
        "--recursive",
        "-r",
        action="store_true",
        help="Unterordner von PFAD mit einbeziehen",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Tags lesen, Song in der Datenbank anlegen. Braucht PFAD.",
    )
    parser.add_argument(
        "--abfragen",
        action="store_true",
        help=(
            "Anbieter (lrclib, musixmatch, netease, genius) live abfragen. "
            "Mit PFAD: nur Songs aus PFAD. Ohne PFAD: die ganze Bibliothek."
        ),
    )
    parser.add_argument(
        "--nachholen",
        action="store_true",
        help=(
            "Nur die Anbieter nochmal fragen, bei denen bisher nichts "
            "gefunden wurde oder die fehlgeschlagen sind. Mit PFAD: nur "
            "Songs aus PFAD. Ohne PFAD: die ganze Bibliothek. Läuft NIE von "
            "allein mit (auch nicht ohne jedes Flag) -- impliziert dann "
            "--bewerten + --schreiben, damit ein neuer Treffer auch "
            "wirklich geschrieben wird."
        ),
    )
    parser.add_argument(
        "--bewerten",
        action="store_true",
        help=(
            "Entscheiden: Konsens der Anbieter, sonst Whisper-Check, sonst "
            "Dauer-Heuristik. Mit PFAD: nur Songs aus PFAD. Ohne PFAD: die "
            "ganze Bibliothek."
        ),
    )
    parser.add_argument(
        "--schreiben",
        action="store_true",
        help=(
            ".lrc-Datei schreiben oder löschen, je nach Entscheidung aus "
            "--bewerten. Braucht PFAD."
        ),
    )
    args = parser.parse_args()

    # Kein einziges Schritt-Flag gesetzt -> kompletter Normal-Durchlauf: scan,
    # abfragen, bewerten, schreiben -- OHNE nachholen (Nutzer-Feedback: ein
    # normaler Wiederholungslauf soll nicht bei jedem Mal erneut alle
    # historisch offenen "nichts"/"fehlschlag"-Kombis live nachfragen; das
    # ist ein bewusster, expliziter Schritt). --nachholen läuft deshalb NUR,
    # wenn es ausdrücklich angegeben wird -- und impliziert dann --bewerten
    # + --schreiben mit (ohne die beiden würde ein frisch gefundener
    # Provider-Treffer nirgendwo ankommen, siehe ROADMAP.md).
    any_step_selected = any(
        [args.scan, args.abfragen, args.nachholen, args.bewerten, args.schreiben]
    )
    run_scan = args.scan or not any_step_selected
    run_abfragen = args.abfragen or not any_step_selected
    run_nachholen = args.nachholen
    run_bewerten = args.bewerten or args.nachholen or not any_step_selected
    run_schreiben = args.schreiben or args.nachholen or not any_step_selected

    # Die Cache-Connection wird von jedem Schritt gebraucht (alle lesen/
    # schreiben in der Cache-DB) -- deshalb immer geöffnet, unabhängig von
    # PFAD. --scan/--schreiben brauchen zusätzlich eine echte Audiodatei;
    # fehlt PFAD, ist das kein Fehler -- der jeweilige Schritt meldet das nur
    # und tut nichts (siehe Design-Dokument, Abschnitt 3, Randfall b).
    conn = cache_store.open_cache(_default_db_path())
    root: Path | None = Path(args.path).resolve() if args.path else None

    def _run_selected_steps(
        step_root: Path | None,
        step_files: list[tuple[Path, str, str, str]] | None,
        folder_lock: object | None = None,
    ) -> None:
        """Führt die gewählten Schritte einmal aus -- entweder global (siehe
        main() ohne PFAD, step_root=None) oder für GENAU EINE Datei (siehe
        Datei-für-Datei-Schleife weiter unten, step_files hat dann genau ein
        Element). step_files: bereits eingelesene (Pfad, Artist, Titel,
        Genre)-Tupel für step_root (siehe scan_songs._read_tagged_files) --
        erspart jedem Schritt den erneuten Verzeichnis-Walk.

        folder_lock: von der Datei-Schleife für den aktuellen Ordner bereits
        gehaltene Sperre (siehe dort, ROADMAP.md Punkt 4) -- wird nur an
        write_lrc_normal durchgereicht, das damit write_lrc.write_all()s
        eigenen (redundanten) Sperrversuch für denselben Ordner überflüssig
        macht.

        quiet: True, wenn --schreiben in diesem Aufruf mitläuft UND ein PFAD
        gesetzt ist -- dann liefert --schreiben gleich die EINE gewollte
        Ergebniszeile pro Song (siehe write_lrc.write_all-Docstring), und
        scan/abfragen/bewerten unterdrücken ihre eigenen, sonst fast
        identischen Kopf-/Zwischen-/Zusammenfassungszeilen (Nutzer-Feedback:
        "zeig auf trackebene [...] pro track eine zeile [...] schau dir das
        bei dem alten programm ab", siehe ROADMAP.md -- das alte
        fetch_songtext.py verarbeitete jeden Track ohnehin in einem
        Rutsch und druckte genau eine Zeile). Ohne --schreiben (z.B.
        `--abfragen` allein) bleibt die ausführliche Ausgabe die einzige
        Rückmeldung -- dort NICHT unterdrückt."""
        quiet = run_schreiben and step_root is not None

        if run_scan:
            if step_root is None:
                print("scan: kein PFAD angegeben, nichts zu scannen.")
            else:
                count = scan_songs.scan(step_root, False, conn, files=step_files)
                if not quiet:
                    print(f"scan: {count} Song(s) gescannt/aktualisiert.")

        if run_abfragen:
            order: list[tuple[Path, str, str]] | None = None
            if step_root is not None:
                order = build_file_song_map(step_root, False, conn, files=step_files)
            scope = {(a, t) for _, a, t in order} if order is not None else None
            fetch_providers_normal(conn, scope=scope, file_order=order, quiet=quiet)

        if run_nachholen:
            scope = _scope_from_root(step_root, False, conn, files=step_files)
            fetch_providers_nachhol(conn, scope=scope)

        if run_bewerten:
            scope = _scope_from_root(step_root, False, conn, files=step_files)
            file_map: dict[tuple[str, str], Path] = {}
            if step_root is not None:
                mapping = build_file_song_map(step_root, False, conn, files=step_files)
                file_map = {(a, t): p for p, a, t in mapping}
            evaluate_lyrics_normal(
                conn, scope=scope, file_song_map=file_map, quiet=quiet
            )

        if run_schreiben:
            if step_root is None:
                print("schreiben: kein PFAD angegeben, nichts zu schreiben.")
            else:
                mapping = build_file_song_map(step_root, False, conn, files=step_files)
                write_lrc_normal(conn, mapping, quiet=quiet, folder_lock=folder_lock)

    current_folder_lock: object | None = None
    try:
        if root is None:
            # Kein PFAD -> keine Ordner zum Durchlaufen, bewusst weiterhin
            # global über die ganze Bibliothek (--nachholen/--abfragen/
            # --bewerten ohne PFAD, siehe Verwendung oben).
            _run_selected_steps(None, None)
        else:
            # Verzeichnis-Walk + Tag-Read laufen LAZY, Datei für Datei (siehe
            # scan_songs._read_tagged_files-Docstring) -- jede Datei wird
            # verarbeitet, SOBALD sie gefunden ist, statt erst den ganzen
            # Baum fertig einzulesen. Ein vorheriger Versuch sammelte den
            # kompletten Baum vorab in einer Liste (ein Walk statt bis zu
            # sechs) -- bei einer großen, netzwerk-gemounteten Bibliothek
            # wirkte das wie ein Hänger, bevor der erste Track überhaupt
            # verarbeitet wurde (Nutzer-Feedback: "Programm startet trotzdem
            # mit einem großen Scan über alle Verzeichnisse. Muss das
            # sein?", siehe ROADMAP.md). Bewusster Trade-off: jede Datei wird
            # weiterhin nur EINMAL getaggt (kein Rückfall auf die alten bis
            # zu sechs Durchläufe), aber die Gesamtzahl ist vor Laufende
            # nicht mehr bekannt -- deshalb KEINE "N Datei(en) gefunden."-
            # Zeile mehr vorab. Jede Datei durchläuft alle gewählten
            # Schritte (inkl. --bewerten mit ggf. mehrminütiger Live-
            # Whisper-Transkription), BEVOR die nächste --abfragen
            # überhaupt startet -- das schiebt von selbst genug Abstand
            # zwischen zwei Anbieter-Abfragen, ganz ohne eigene Sleep-/
            # Throttling-Logik (Nutzer-Feedback: "ich will, dass die phasen
            # für jeden einzelne datei laufen [...] dadurch haben die
            # provider auch wieder länger leerlauf"). Reihenfolge weiterhin
            # Datei-/Verzeichnisreihenfolge (_iter_audio_dfs: pro Ebene
            # alphabetisch).
            current_folder: Path | None = None
            skip_current_folder = False
            any_file = False
            for entry in scan_songs._read_tagged_files(root, args.recursive):
                any_file = True
                audio_path = entry[0]
                if audio_path.parent != current_folder:
                    # Ordnerwechsel: vorherige Sperre freigeben, BEVOR der
                    # neue Ordner beansprucht wird -- siehe ROADMAP.md
                    # Punkt 4. Die Sperre umfasst jetzt ALLE gewählten
                    # Schritte (scan/abfragen/bewerten/schreiben) für JEDE
                    # Datei dieses Ordners, nicht mehr nur noch das
                    # Schreiben (write_lrc.py bekommt sie unten als
                    # folder_lock durchgereicht und beansprucht sie deshalb
                    # nicht ein zweites Mal). Zweck: zwei bewusst parallel
                    # laufende songtext_pipeline.py-Instanzen, die
                    # überlappende Verzeichnisbäume abarbeiten, sollen sich
                    # NICHT gegenseitig denselben Ordner doppelt bei den
                    # Anbietern abfragen und doppelt per Whisper prüfen.
                    lyrics_core._release_folder(current_folder_lock)
                    current_folder = audio_path.parent
                    current_folder_lock = lyrics_core._try_claim_folder(current_folder)
                    skip_current_folder = (
                        current_folder_lock is lyrics_core._FOLDER_BUSY
                    )
                    try:
                        rel = current_folder.relative_to(root)
                        label = str(rel) if str(rel) != "." else current_folder.name
                    except ValueError:
                        label = str(current_folder)
                    if skip_current_folder:
                        lyrics_core._tprint(
                            f"{lyrics_core._ts()}  ── {label}  "
                            "(andere Instanz aktiv, übersprungen)"
                        )
                        continue
                    # Ordner-Kopfzeile im Stil des frueheren
                    # fetch_songtext.py (siehe Git-Historie, main(): dort
                    # `print(f"{_ts()}  ── {rel_dir}")`) -- EIN Marker pro
                    # Ordnerwechsel, kein "i/N"-Zaehler: darunter steht
                    # direkt die eine Ergebniszeile pro Track (siehe
                    # write_lrc.write_all), keine weitere Zwischenzeile
                    # noetig (Nutzer-Feedback: "zeig auf trackebene [...]
                    # pro track eine zeile [...] schau dir das bei dem alten
                    # programm ab"). _tprint() statt print(): loescht zuerst
                    # eine noch stehende transiente "Scanne: ..."-Statuszeile
                    # (siehe ROADMAP.md, sonst "beisst" sich die Ausgabe auf
                    # derselben Terminalzeile).
                    lyrics_core._tprint(f"{lyrics_core._ts()}  ── {label}")
                elif skip_current_folder:
                    continue
                _run_selected_steps(
                    audio_path.parent, [entry], folder_lock=current_folder_lock
                )
            if not any_file:
                # Keine Audiodatei unter PFAD gefunden -- trotzdem EINMAL
                # mit leerer Datei-Liste ausführen, damit z.B. --nachholen/
                # --abfragen ihre gewohnte "nichts gefunden/nichts zu
                # tun"-Rückmeldung geben, statt komplett stillzubleiben.
                _run_selected_steps(root, [])
    except FileNotFoundError:
        # syncedlyrics-Binary fehlt (z.B. falsches venv aktiv, siehe
        # ROADMAP.md) -- fetch_providers.fetch_all() bricht dafuer bewusst
        # mit dieser Exception ab, statt es pro Anbieter zu verschlucken:
        # ein fehlendes Binary betrifft JEDEN weiteren Song gleichermassen.
        # Sauberer Abbruch mit klarer Meldung statt rohem Traceback aus
        # einem Worker-Thread (Stil wie im frueheren fetch_songtext.py,
        # siehe Git-Historie).
        lyrics_core._tprint(
            f"{lyrics_core._ts()}  syncedlyrics nicht gefunden — Abbruch."
        )
    finally:
        lyrics_core._release_folder(current_folder_lock)
        conn.close()


if __name__ == "__main__":
    main()
