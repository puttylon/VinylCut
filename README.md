# VinylCut

Pipeline zum Digitalisieren von Vinyl-Alben: Metadaten holen, Album interaktiv in Tracks schneiden, Songtexte laden.

## Ablauf

```
Vinyl-Rippung (eine FLAC) → metadata_fetcher.py → interactive_cutter.py → einzelne getaggte FLACs + LRC-Dateien
```

`interactive_cutter.py` ruft `metadata_fetcher.py` und `songtext.py` automatisch auf — für den normalen Einsatz reicht ein einziger Befehl:

```bash
python3 interactive_cutter.py "Artist - Album.flac"
```

## Skripte

### `metadata_fetcher.py`
Sucht das Album auf Discogs, wählt die beste Pressung per Score (Vinyl bevorzugt, Gesamtdauer, fehlende Längen), zeigt die Trackliste interaktiv an und ermöglicht einen manuellen Discogs-ID-Override. Lädt das Cover vom popularsten Vinyl-Release (nach `community.have`).

Ausgabe in `<Album>/`:
- `release.json` — Artist, Album, Tracks mit Längen
- `cover.jpg` — Albumcover

```bash
python3 metadata_fetcher.py "Artist - Album.flac"
```

Benötigt: `DISCOGS_TOKEN` als Umgebungsvariable.

### `interactive_cutter.py`
Liest `release.json`, lässt dich für jeden Track den Startpunkt per Tastatur feinjustieren (Playback via ffplay), schneidet sample-genau mit SoX und taggt jede FLAC mit metaflac. Speichert Fortschritt nach jedem bestätigten Track. Ruft danach automatisch `songtext.py` auf.

```bash
python3 interactive_cutter.py "Artist - Album.flac"
python3 interactive_cutter.py "Artist - Album.flac" --out "/Ziel/Verzeichnis"
python3 interactive_cutter.py "Artist - Album.flac" --no-songtext
```

**Optionen:**

| Flag | Bedeutung |
|------|-----------|
| `--out <Verzeichnis>` | Ausgabeverzeichnis für geschnittene Tracks |
| `--no-songtext` | Songtext-Suche am Ende überspringen (z.B. bei Instrumentalalben) |
| `--preview <Sek>` | Snippet-Länge in Sekunden (Standard: 3) |
| `-h`, `--help` | Hilfe anzeigen |
| `-V`, `--version` | Versionsnummer ausgeben |

**Steuerung im interaktiven Modus:**

| Eingabe | Aktion |
|---------|--------|
| `p` | Snippet nochmal abspielen |
| `+` / `-` | ±0,5 Sekunden |
| `++` / `--` | ±2 Sekunden |
| `ok` | Startpunkt bestätigen, nächster Track |
| `u` | Letztes `ok` rückgängig machen |
| `n` | Normton (1000 Hz, 0,25 s) vor Snippet ein-/ausschalten |
| Zahl oder `±m:ss` | Startpunkt um Offset verschieben (z.B. `+2:34` oder `-30`) |

Bei Abbruch wird der Fortschritt in `<Album>/progress.json` gespeichert und beim nächsten Start zum Fortsetzen angeboten.

Jede geschnittene FLAC erhält einen `COMMENT`-Tag mit Programmname und Version.

### `songtext.py`
Sucht für jede FLAC im Zielordner synchronisierte Songtexte via `syncedlyrics` und speichert sie als `.lrc`-Datei.

```bash
python3 songtext.py "Artist - Album/"
```

Optionaler Genius-Token (bessere Trefferquote): Datei `genius_token` im Skript-Verzeichnis ablegen oder `GENIUS_ACCESS_TOKEN` als Umgebungsvariable setzen.

## Abhängigkeiten

**Python-Pakete:**
```bash
pip install -r requirements.txt
pip install syncedlyrics
```

**Systemprogramme:**
- `ffprobe` / `ffplay` / `ffmpeg` — Dauer messen, Snippets abspielen, Normton-Funktion (Teil von ffmpeg)
- `sox` — sample-genaues Schneiden
- `metaflac` — FLAC-Tagging und Cover-Einbettung

**Tokens:**
- `DISCOGS_TOKEN` — Umgebungsvariable (erforderlich)
- `genius_token` — Datei im Repo-Verzeichnis (optional, für Songtexte)

## Entwicklung

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
```
