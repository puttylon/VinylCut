# VinylCut Roadmap

## ✓ v1.2.0 — Fortschritt speichern / fortsetzen
Nach jedem `ok` die bestätigten Startpunkte in `progress.json` schreiben. Bei Absturz oder versehentlichem Beenden kann die Session fortgesetzt werden.

## ✓ v1.3.0 — Undo
`[u]` macht das letzte bestätigte `ok` rückgängig. Track wird erneut bearbeitbar.

## ✓ v1.4.0 — Ausgabeverzeichnis (`--out`)
Ausgabeverzeichnis für geschnittene Tracks frei wählbar statt fest neben der Quell-FLAC.

## ✓ v1.5.0 — Normton (experimentell)
`[n]` schaltet einen 1000 Hz Normton (0,25 s) vor dem Schneidpunkt ein/aus. Nahtloser Übergang via ffmpeg concat-Filter.

## ✓ v1.6.0 — Preview-Dauer konfigurierbar (`--preview`)
Snippet-Länge frei wählbar statt fix 3 Sekunden (z.B. `--preview 5`).

## ✓ v1.7.0–v1.8.x — Rich Vollbild-UI
Metadatensuche, Schneiden, Export und Songtext-Suche laufen vollständig
im Rich Live-Screen. Eingabe zeichenweise mit tty.setcbreak.

---

# Refactoring-Roadmap (Architektur-Umbau)

Grundlage: ARCHITECTURE.md. Ziel: stabiler, testbarer, wartbarer Code.

## ✓ Schritt 1 — cut_ui.py anlegen
Alle Rich/tty-Abhängigkeiten aus interactive_cutter.py extrahiert:
- build_cutting_panel() — umbenannt von build_panel(), est als Parameter
- build_metadata_panel() — unverändert
- live_input() — umbenannt von _live_ask()
- fmt_dur() — Display-Hilfsfunktion
Ergebnis: eine Datei für alles Rich-spezifische (Schicht 2).

## ✓ Schritt 2 — Umbenennen der Skripte
- interactive_cutter.py → cut.py (v1.9.0)
- preparer.py → assemble.py
- metadata_fetcher.py → fetch_metadata.py
- songtext.py → fetch_songtext.py
- Alle Testdateien und README-Verweise angepasst.

## ✓ Schritt 3 — cut.py aufräumen
Importiert jetzt aus cut_ui. Doppelter Code entfernt.
Enthält: main(), run_metadata_search(), Logik-Funktionen, cut_and_tag(), play_snippet*()
Ergebnis: 280 Zeilen statt 671.

## ✓ Schritt 4 — test_cut_ui.py schreiben
21 Tests mit Console(force_terminal=False) + capture().
Läuft vollautomatisch mit pytest, kein Terminal nötig.

## ✓ Schritt 5 — test_smoke.py schreiben
7 Smoke-Tests: --version, --help, no-args für cut.py, assemble.py, fetch_songtext.py.
pexpect: noch nicht recherchiert — steht als offener Punkt in ARCHITECTURE.md.

---

## Zurückgestellt
- **Android/Termux-Port** — auf unbestimmte Zeit verschoben.

---

## Ideen (nicht geplant)

### Whisper-Verifikation
Die ersten ~30 Sekunden eines Tracks via `faster-whisper` transkribieren und
das Ergebnis mit dem Anfang der LRC-Kandidaten abgleichen. Bester Wort-Overlap
gewinnt. Kein Match → keine LRC (keine eigene Transkription erstellen).
Würde Fehlgriffe eliminieren die durch Dauer-Heuristik nicht erkannt werden.
Abhängigkeit: `faster-whisper`, Modell ~500 MB (base) bis 1,5 GB (medium).

### Unified Toolchain (`vinylcut`)
Einziger Einstiegspunkt für die gesamte Pipeline. Fragt beim Start (oder per Flag `--from 1/2/3`), an welchem Schritt begonnen werden soll:

1. **Record** — Aufnahme direkt aus der Befehlszeile via ffmpeg (Audiointerface → FLAC), mit Pegelanzeige und Start/Stop per Taste. Würde Audacity ersetzen.
2. **Assemble** — wie heute `assemble.py`
3. **Cut** — wie heute `cut.py`

Checkpoint-Logik: Das Tool erkennt anhand vorhandener Dateien, welcher Schritt als nächstes sinnvoll ist, und schlägt ihn vor.

---

# assemble.py — Roadmap

Werkzeug zur non-destruktiven Vorbereitung einer Roh-FLAC (alle Seiten in einer Datei) vor dem Schneiden mit `cut.py`. Die Original-FLAC wird nie verändert. Alle Schnittdaten landen in `assemble.json`, Zwischenergebnisse in neuen Dateien.

## ✓ v0.1 — Stille-Erkennung
Nimmt eine FLAC, erkennt lange Stillepausen via `ffmpeg silencedetect`, gibt vorgeschlagene A/B-Punkte für jede Nahtstelle im Terminal aus. Noch keine Interaktion — nur prüfen ob die Erkennung brauchbare Ergebnisse liefert.

## ✓ v0.2 — Interaktives Grob-Beschneiden
Für jede Nahtstelle: Punkt A (Ende Musik Seite N) und Punkt B (Anfang Musik Seite N+1) per Playback interaktiv setzen und in `assemble.json` speichern. Fortschritt wird nach jeder Bestätigung gespeichert, Session kann fortgesetzt werden.

## ✓ v0.3 — Crossfade-Vorschau + Feinschneiden
Jeden Übergang abhören: temporärer Crossfade (8 s Fenster, 0,5 s Blende) wird on-the-fly generiert und abgespielt. A/B per Fokus-Modell ([a]/[b] + [+]/[-]) verschieben. Nutzer gibt Anzahl Seiten an — beste Kandidaten nach Stillelänge gewählt.

## ✓ v0.4 — Schneiden + Zusammenfügen
Ausgabe: `<Name>_prepared.flac` mit Crossfades an allen Nahtstellen. Original-FLAC bleibt unangetastet.

## ✓ v0.5 — Normalisierung + DC-Offset
DC-Offset (highpass 5 Hz) + Peak-Normalisierung auf -0,1 dBFS via sox. Optionaler Kanalausgleich nach Pegelmessung. Ergebnis in `<Name>_final.flac`.

## ✓ v0.6 — Polish
Tests für `get_segments`, automatische Umbenennung der Ausgabedatei, ROADMAP aktualisiert.

## ✓ v1.0 — Stabile Version
README vollständig nachgezogen, Gesamtworkflow dokumentiert.

## ✓ v1.1.2 — Normalisierung auf ffmpeg loudnorm (echtes dBTP)
`normalize()` verwendet jetzt ffmpeg loudnorm im 2-Pass-Verfahren statt sox `norm`.
Pass 1 misst Pegel, Pass 2 wendet linearen Gain mit TP=-1.0 dBTP an.
Kanalausgleich über ffmpeg `pan`-Filter statt sox `remix`.

## ✓ v1.1.1 — Normalisierungsziel auf -1.0 dBTP
Zielwert von -0.1 auf -1.0 geändert (Vorstufe zum loudnorm-Umbau).

## ✓ v1.1 — Rich Vollbild-UI
assemble_ui.py (Schicht 2) mit 5 Panel-Buildern für alle Phasen. assemble.py
nutzt jetzt Rich Live(screen=True) + live_input() für alle interaktiven Schritte.
25 Tests in test_assemble_ui.py, laufen ohne Terminal.

---

# fetch_songtext.py / refetch_lyrics.py — Roadmap

## ✓ v1.0 — Grundfunktion
Songtext-Suche via syncedlyrics, LRC-Dateien neben FLAC speichern.

## ✓ v1.1 — Alle Provider, bestes Ergebnis
Alle vier Provider (lrclib, musixmatch, netease, genius) gleichzeitig befragen.
Scoring: (valid, synced, lines) — höher ist besser. megalobiz entfernt (lieferte
konsequent falsche Songs). Asymmetrische Dauer-Validierung gegen release.json:
LRC darf bis zu 40 % kürzer enden (Instrumental-Outro), höchstens 10 % länger.

## ✓ v1.0 refetch_lyrics.py — Rekursives Neu-Laden
Durchsucht alle Unterordner nach FLACs, lädt Songtexte neu. Zeigt Vorschau
nur wenn sich der Inhalt ändert, speichert still wenn kein Unterschied.

## ✓ v1.4.6 — mlx-whisper rückgebaut, faster_whisper wiederhergestellt
mlx-whisper (Apple Silicon GPU) erwiesen als unzuverlässig: Spracherkennung
scheitert bei instrumental-lastigen Passagen ("Shona" statt "English"), Jaccard
fällt auf 0% → base-Score liegt unter RETRY_MIN → small wird nie aufgerufen →
korrekte LRCs werden fälschlicherweise abgelehnt. Geschwindigkeitsgewinn (1.6–2.4×)
rechtfertigt die Instabilität nicht. Rückbau auf faster_whisper (ctranslate2, CPU).

## ✓ v1.4.5 — mlx-whisper Backend (rückgebaut in v1.4.6)
Apple Silicon GPU/Neural Engine via mlx-whisper. Im Benchmark 1.5–2.3× schneller
als faster_whisper, aber Qualität instabil — Sprachdetektionsfehler auf schwierigen
Tracks. Siehe v1.4.6.

## ✓ v1.4.4 — Konsens-Jaccard-Schwelle auf 40% gesenkt
Evidenzbasiert nach Analyse von Manu Chao und Marvin Gaye: Ausreißer-Provider
(z.B. Netease mit anderssprachiger Version) zieht den Paardurchschnitt unter 50%,
obwohl die drei anderen Provider stark übereinstimmen. Genius liefert mitunter
Metadaten-Rauschen (Contributors-Texte) das die Ähnlichkeit ebenfalls drückt.
40% ist das empirisch fundierte Minimum für konsistente Akzeptanz.

## ✓ v1.4.3 — Provider-Konsens überstimmt Whisper-Threshold
Wenn ≥3 Provider einen Treffer liefern UND deren LRC-Inhalt sich untereinander
≥40% (Jaccard) ähnelt UND Whisper Vokale hört (score ≥ 20%), wird die LRC
akzeptiert — auch wenn der Whisper-Score unter 40% bleibt.
Trifft auf Artists mit unkonventionellem Gesangsstil zu (z.B. Meat Puppets).
Gewinner = repräsentativster Kandidat (höchste Durchschnitts-Ähnlichkeit zu allen
anderen) — Ausreißer werden so automatisch übergangen.
Cache-Eintrag enthält `"consensus": true`. Ausgabe zeigt ", Konsens" statt "!".
Neues Tool: `lrc_recheck.py` — findet bereits gecachte "nf"-Tracks die vom
Konsens-Check profitieren würden, löscht ihre Cache-Einträge gezielt (--apply).

## ✓ v1.4.2 — Tracks ohne Artist- und Title-Tags überspringen
Dateien ohne beide Tags werden nicht mehr gegen Provider gesucht — LRC wird
gelöscht falls vorhanden. Kein Cache-Eintrag (wie Genre-Skip). Zähler "X ohne Tags"
in der Zusammenfassung.

## ✓ v1.4.1 — Timeout für Provider-Abfragen (20 s)
Hängende Provider blockieren ihren Thread nicht mehr unbegrenzt.
`_CACHE_MIN_VERSION` bleibt 1.4.0 — kein Neulauf der Bibliothek.

## ✓ v1.4.0 — Zweistufige Whisper-Verifikation (base → small im Grenzbereich)
Erster Pass immer mit `base` (schnell). Liegt der Score im Grenzbereich [25 %, 40 %),
folgt ein zweiter Pass mit `small` (genauer). Darunter oder darüber: kein zweiter Pass.
Cache speichert zusätzlich `model` (welches Modell die finale Entscheidung traf).
Ausgabe zeigt `+` wenn small den Ausschlag gab (z.B. `~238W, 64%+`).
Versionsprung auf 1.4 — das System ist jetzt evidenzbasiert kalibriert.

## ✓ v1.3.11 — Whisper-Modell: base → small
Evidenzbasierter Wechsel: `base` erzielte 37 % für korrekte italienische Lyrics (Mario Biondi)
und scheiterte knapp am 40 %-Threshold. `small` (~480 MB, ~3× langsamer) transkribiert
nicht-englische Inhalte deutlich zuverlässiger.

## ✓ v1.3.10 — Erweiterte Metadaten im Cache (score, providers, words, ts)
Cache-Einträge enthalten jetzt: Whisper-Overlap (`score`), Provider-Treffer (`providers`),
transkribierte Wörter (`words`), Zeitstempel (`ts`), bei Fallback auch `fallback: true`.
Ermöglicht nachträgliche Auswertung warum Tracks angenommen oder abgelehnt wurden.

## ✓ v1.3.9 — Vollständigen LRC-Text für Whisper-Vergleich nutzen
`_extract_lrc_words` verarbeitete nur die ersten 15 Zeilen (~120 Wörter). Mit adaptiver
Transkriptionsdauer (volle Song-Länge) führte das zu künstlich niedrigen Jaccard-Werten,
da die zweite Hälfte der Lyrics im Whisper-Output vorkommt, aber nicht im LRC-Vergleich.
Jetzt alle Zeilen verwendet.

## ✓ v1.3.8 — LRC auch bei Genre-Skip löschen
Genre-gefilterte Tracks (Instrumental, Hörbuch etc.) löschen jetzt eine ggf. vorhandene
LRC-Datei, statt sie zu behalten.

## ✓ v1.3.7 — Bestehende LRCs löschen wenn kein Treffer
Wenn kein Provider eine LRC findet (oder Whisper alle verwirft), wird eine ggf. vorhandene
alte LRC-Datei jetzt gelöscht statt behalten. Verhindert, dass falsche LRCs dauerhaft bestehen.

## ✓ v1.3.6 — Whisper-Qualitätsschwelle auf 40 % angehoben, adaptive Transkriptionsdauer
Threshold von 6 % auf 40 % erhöht. Transkriptionsdauer jetzt adaptiv: ≤ 3 min → volle Länge,
≤ 6 min → 75 %, > 6 min → 50 % (max 5 min). `_CACHE_MIN_VERSION` auf 1.3.6 → alle bisherigen
Einträge werden neu verarbeitet.

## ✓ v1.3.5 — Zeitstempel in Ausgabe
Alle Track-Zeilen beginnen mit `HH:MM:SS` (Systemzeit). `_ts()` Hilfsfunktion.

## ✓ v1.3.4 — Genre-Filter: Hörbuch, Hörspiel, Instrumental etc. überspringen
`_SKIP_GENRE_KEYWORDS` (Substring-Matching): hörbuch, hörspiel, audiobook, audio play,
radio play/drama, instrumental, podcast, speech, spoken word, lesung, vortrag,
sfx, noise, field recording u. a. Genre-übersprungene Tracks werden gezählt
(„X Genre übersprungen") aber nicht gecacht — damit Korrekturen am Genre-Tag
beim nächsten Lauf automatisch greifen.

## ✓ v1.3.3 — Per-Track-Cache statt Ordner-Marker
Ordner-Marker-System entfernt. Stattdessen `.fetch_songtext.json` pro Albumordner,
geschrieben nach jedem einzelnen Track. Unterbrechungen mitten im Ordner verlieren
keinen Fortschritt mehr. `r: "ok"` = LRC gefunden/bestätigt, `r: "nf"` = nicht gefunden
(Instrumental etc.) — beide werden beim nächsten Lauf übersprungen.
`--force` ignoriert den Cache komplett.

## ✓ v1.3.2 — Marker sofort pro Verzeichnis, Schreibfehler abgefangen
Marker wird jetzt direkt geschrieben wenn ein Verzeichnis verlassen wird (statt erst am Ende).
Bricht der Lauf mittendrin ab (Volume unmounted, Ctrl+C), haben bereits abgeschlossene
Ordner ihren Marker. `OSError` beim LRC-Schreiben (z. B. Volume nicht mehr gemounted)
wird sauber abgefangen statt als Crash zu enden.

## ✓ v1.3.0 — Unterstützung weiterer Audioformate (MP3, Opus, OGG, M4A …)
`metaflac` ersetzt durch `mutagen` (easy=True) für formatunabhängiges Tag-Lesen.
Dateisuche findet jetzt: `.flac`, `.mp3`, `.ogg`, `.opus`, `.m4a`, `.aac`, `.wav`.
Abhängigkeit: `mutagen` (in requirements.txt ergänzt).

## ✓ v1.2.11 — Marker-Logik korrigiert: vorhandene LRCs werden ohne Marker immer geprüft
Ohne Marker werden alle Tracks verarbeitet — auch solche mit bestehender LRC (Whisper-Verifikation).
Der Marker ist der einzige Skip-Mechanismus. Die frühere Sonderbehandlung
„im Normalmode vorhandene LRCs nicht anfassen" entfällt.

## ✓ v1.2.10 — Verarbeitungsmarker (Skip bereits geprüfter Ordner)
Nach der Verarbeitung eines Ordners wird `.fetch_songtext_v<version>` angelegt.
Folgeläufe überspringen Ordner mit kompatiblem Marker automatisch.
Kompatibel ab `_MARKER_MIN_VERSION` (aktuell 1.2.0) — kein Massenneulauf bei Bugfix-Versionen.
Neues Flag `--force` / `-f` ignoriert alle Marker und verarbeitet alles neu.

## ✓ v1.2.9 — Provider-Abfragen parallelisiert
Alle vier Provider werden jetzt gleichzeitig via ThreadPoolExecutor befragt statt
nacheinander. Reihenfolge der Ergebnisse bleibt deterministisch.

## ✓ v1.2.8 — Robustere Whisper-Verifikation
Diagnostische Ausgabe: Provider-Anzahl, Whisper-Wörterzahl und Overlap pro Track sichtbar.
Overlap-Schwellwert von 12 % auf 6 % gesenkt (deckt Grenzfälle wie gemischtsprachige Songs ab).
Neuer Fallback: Whisper erkennt keine Sprache, aber ≥ 2 Provider und ≥ 10 Lyrics-Zeilen → LRC
trotzdem gespeichert (Vokalsong mit ungewöhnlichem Vokalstil, z. B. Falco "Vienna Calling").
Artist/Titel-Abfrage nutzt FLAC-Metadaten (seit v1.2.7) statt Dateinamen.
