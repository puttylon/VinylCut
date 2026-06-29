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
Liest `release.json`, lässt dich für jeden Track den Startpunkt per Tastatur feinjustieren (Playback via ffplay), schneidet sample-genau mit SoX und taggt jede FLAC mit metaflac. Ruft danach automatisch `songtext.py` auf.

```bash
python3 interactive_cutter.py "Artist - Album.flac"
```

Steuerung im interaktiven Modus:
| Eingabe | Aktion |
|---------|--------|
| Enter / `p` | Snippet nochmal abspielen |
| `+` / `-` | ±0,5 Sekunden |
| `++` / `--` | ±2 Sekunden |
| `ok` | Startpunkt bestätigen, nächster Track |
| Zahl | Startpunkt um N Sekunden verschieben |

### `songtext.py`
Sucht für jede FLAC im Zielordner synchronisierte Songtexte via `syncedlyrics` und speichert sie als `.lrc`-Datei.

```bash
python3 songtext.py "Artist - Album/"
```

Optionaler Genius-Token (bessere Trefferquote): Datei `genius_token` im Skript-Verzeichnis ablegen oder `GENIUS_ACCESS_TOKEN` als Umgebungsvariable setzen.

## Abhängigkeiten

**Python-Pakete:**
```bash
pip install syncedlyrics
```

**Systemprogramme:**
- `ffprobe` / `ffplay` — Dauer messen, Snippets abspielen (Teil von ffmpeg)
- `sox` — sample-genaues Schneiden
- `metaflac` — FLAC-Tagging und Cover-Einbettung

**Tokens:**
- `DISCOGS_TOKEN` — Umgebungsvariable (erforderlich)
- `genius_token` — Datei im Repo-Verzeichnis (optional, für Songtexte)
