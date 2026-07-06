# VinylCut

Pipeline zum Digitalisieren von Vinyl-Alben: Roh-Aufnahme vorbereiten, Metadaten holen, Album interaktiv in Tracks schneiden, Songtexte laden.

## Ablauf

```
Roh-FLAC (alle Seiten) → assemble.py → Artist - Album.flac
                                               ↓
                               cut.py → einzelne getaggte FLACs + LRC-Dateien
```

### Schritt 1 — Aufnahme vorbereiten

```bash
python3 assemble.py "Artist - Album-raw.flac"
```

### Schritt 2 — Tracks schneiden

```bash
python3 cut.py "Artist - Album.flac"
```

`cut.py` ruft `fetch_metadata.py` und `fetch_songtext.py` automatisch auf.

---

## Skripte

### `assemble.py`
Bereitet eine Roh-FLAC (alle Vinyl-Seiten in einer Datei) non-destruktiv für den Schnitt vor. Erkennt Seitengrenzen automatisch via Stille-Erkennung, lässt Schnitt- und Trim-Punkte interaktiv setzen, zeigt Crossfade-Übergänge zur Feinkorrektur, fügt Segmente zusammen und normalisiert.

Ausgabe: `Artist - Album.flac` (bereinigt, normalisiert). Original bleibt unverändert.

```bash
python3 assemble.py "Artist - Album-raw.flac"
python3 assemble.py "Artist - Album-raw.flac" --preview 12
```

**Optionen:**

| Flag | Bedeutung |
|------|-----------|
| `--preview <Sek>` | Crossfade-Vorschau-Länge in Sekunden (Standard: 8) |
| `-h`, `--help` | Hilfe anzeigen |
| `-V`, `--version` | Versionsnummer ausgeben |

**Phasen:**

| Phase | Was passiert |
|-------|-------------|
| 1 | Seitenerkennung, Anzahl Seiten bestätigen, Trim- und Grenzpunkte interaktiv setzen |
| 2 | Crossfade-Vorschau je Seitengrenze, A/B feinjustieren |
| 3 | Segmente schneiden und mit Crossfade zusammenfügen → `_prepared.flac` |
| 4 | DC-Offset entfernen, optionaler Kanalausgleich, Peak-Normalisierung auf -0,1 dBFS → Ausgabedatei |

**Steuerung Phase 1:**

| Eingabe | Aktion |
|---------|--------|
| `p` | Snippet abspielen |
| `+` / `-` | Punkt ±0,5 s verschieben |
| `++` / `--` | Punkt ±2,0 s verschieben |
| `ok` | Punkt bestätigen, weiter |
| `u` | Letzten Schritt rückgängig |
| `n` | Normton (220 Hz, 0,25 s) aus-/einschalten (Standard: EIN) |
| Zahl oder `±m:ss` | Offset eingeben |

**Steuerung Phase 2 (Crossfade):**

| Eingabe | Aktion |
|---------|--------|
| `p` | Crossfade nochmal abspielen |
| `a` / `b` | Fokus auf Punkt A (Ende Musik) / B (Anfang Musik) |
| `+` / `-` | Aktiven Punkt ±0,5 s verschieben |
| `++` / `--` | Aktiven Punkt ±2,0 s verschieben |
| `ok` | Grenze bestätigen, weiter |
| `u` | Vorherige Grenze nochmal |
| `n` | Normton aus-/einschalten (Standard: EIN) |
| Zahl oder `±m:ss` | Aktiven Punkt um Offset verschieben |

Fortschritt wird nach jeder Bestätigung in `<Stem>/assemble.json` gespeichert und beim nächsten Start zum Fortsetzen angeboten.

---

### `fetch_metadata.py`
Sucht das Album auf Discogs, wählt die beste Pressung per Score (Vinyl bevorzugt, Gesamtdauer, fehlende Längen), zeigt die Trackliste interaktiv an und ermöglicht einen manuellen Discogs-ID-Override. Lädt das Cover vom popularsten Vinyl-Release (nach `community.have`).

Ausgabe in `<Album>/`:
- `release.json` — Artist, Album, Tracks mit Längen
- `cover.jpg` — Albumcover

```bash
python3 fetch_metadata.py "Artist - Album.flac"
```

Benötigt: `DISCOGS_TOKEN` als Umgebungsvariable.

---

### `cut.py`
Liest `release.json`, lässt dich für jeden Track den Startpunkt per Tastatur feinjustieren (Playback via ffplay), schneidet sample-genau mit SoX und taggt jede FLAC mit metaflac. Speichert Fortschritt nach jedem bestätigten Track. Ruft danach automatisch `fetch_songtext.py` auf.

```bash
python3 cut.py "Artist - Album.flac"
python3 cut.py "Artist - Album.flac" --out "/Ziel/Verzeichnis"
python3 cut.py "Artist - Album.flac" --no-songtext
python3 cut.py "Artist - Album.flac" --preview 5
```

**Optionen:**

| Flag | Bedeutung |
|------|-----------|
| `--out <Verzeichnis>` | Ausgabeverzeichnis für geschnittene Tracks |
| `--no-songtext` | Songtext-Suche am Ende überspringen (z.B. bei Instrumentalalben) |
| `--preview <Sek>` | Snippet-Länge in Sekunden (Standard: 3) |
| `-h`, `--help` | Hilfe anzeigen |
| `-V`, `--version` | Versionsnummer ausgeben |

**Steuerung:**

| Eingabe | Aktion |
|---------|--------|
| `p` | Snippet nochmal abspielen |
| `+` / `-` | ±0,5 Sekunden |
| `++` / `--` | ±2 Sekunden |
| `ok` | Startpunkt bestätigen, nächster Track |
| `u` | Letztes `ok` rückgängig machen |
| `n` | Normton (220 Hz, 0,25 s) vor Snippet aus-/einschalten (Standard: EIN) |
| Zahl oder `±m:ss` | Startpunkt um Offset verschieben (z.B. `+2:34` oder `-30`) |

Bei Abbruch wird der Fortschritt in `<Album>/progress.json` gespeichert und beim nächsten Start zum Fortsetzen angeboten.

Jede geschnittene FLAC erhält einen `COMMENT`-Tag mit Programmname und Version.

---

### `fetch_songtext.py`
Sucht für jede FLAC im Zielordner synchronisierte Songtexte via `syncedlyrics`, verifiziert das Ergebnis mit Whisper und speichert es als `.lrc`-Datei. Wird von `cut.py` automatisch aufgerufen; kann auch manuell verwendet werden.

```bash
python3 fetch_songtext.py "Artist - Album/"           # einzelnes Album
python3 fetch_songtext.py --recursive "/Musik/"       # alle Unterordner neu laden
```

**Suchverfahren:**

Alle vier Provider werden gleichzeitig befragt: `lrclib`, `musixmatch`, `netease`, `genius`. Danach entscheidet Whisper welcher Kandidat zum Audio passt.

1. **Whisper-Verifikation** (wenn `faster-whisper` installiert und FLAC verfügbar):
   - Whisper transkribiert 60 Sekunden ab dem ersten Lyrics-Timestamp (`_WHISPER_PRE_ROLL = 0 s`, direkt beim ersten `[mm:ss.xx]`)
   - Wort-Overlap (Jaccard) zwischen Transkription und LRC-Anfang bestimmt den Gewinner
   - Liegt der beste Overlap unter `_WHISPER_MIN_OVERLAP` (6 %) → **keine LRC gespeichert** (falscher Song)
   - Modell (`_WHISPER_MODEL = "base"`) wird beim ersten Aufruf geladen und für alle Tracks wiederverwendet
   - Liefert Whisper keine Transkription: Fallback-Prüfung — ≥ 2 Provider **und** ≥ 10 Lyrics-Zeilen → LRC trotzdem gespeichert (Vokalsong, den Whisper nicht erkannt hat, z. B. ungewöhnlicher Vokalstil)
   - Andernfalls (echtes Instrumental oder zu wenig Provider-Übereinstimmung) → **keine LRC gespeichert**
   - Ausgabe zeigt immer: Provider-Anzahl, Whisper-Wörterzahl und Overlap

2. **Fallback ohne Whisper** (wenn `faster-whisper` nicht installiert):
   - Scoring nach `(valid, synced, lines)` — lexikographisch, höher = besser
   - `valid = 0` wenn letzter Timestamp die Trackdauer um mehr als die Toleranzwerte über- oder unterschreitet:

| Richtung | Konstante | Wert | Begründung |
|----------|-----------|------|------------|
| LRC endet zu spät | `_LRC_TOO_LONG_TOLERANCE` | 10 % | Falscher (längerer) Song |
| LRC endet zu früh | `_LRC_TOO_SHORT_TOLERANCE` | 40 % | Legitim: Instrumental-Outro ohne Text |

**`--recursive` Modus** (ersetzt `refetch_lyrics.py`): Durchsucht alle Unterordner, lädt LRCs neu. Pro Track:
- `✓ gespeichert` — neues, verifiziertes Ergebnis
- `= unverändert` — identischer Inhalt wie vorher
- `✗ Kein Treffer` — kein Provider hat etwas gefunden, Whisper-Overlap zu niedrig (falscher Song), oder Whisper erkannte keine Sprache ohne ausreichende Provider-Bestätigung

**Genius-Token:** Datei `genius_token` im Skript-Verzeichnis ablegen oder `GENIUS_ACCESS_TOKEN` als Umgebungsvariable setzen.

---

## Abhängigkeiten

**Python-Pakete:**
```bash
pip install -r requirements.txt
pip install syncedlyrics
pip install faster-whisper   # optional — verbessert LRC-Treffsicherheit via Whisper
```

`requirements.txt` enthält: `pytest`, `rich`

**Systemprogramme:**
- `ffprobe` / `ffplay` / `ffmpeg` — Analyse, Playback, Crossfade, Normton
- `sox` — Schneiden, Normalisierung, DC-Offset, Kanalausgleich
- `metaflac` — FLAC-Tagging und Cover-Einbettung

**Tokens:**
- `DISCOGS_TOKEN` — Umgebungsvariable (erforderlich für metadata_fetcher.py)
- `genius_token` — Datei im Repo-Verzeichnis (optional, für Songtexte)

## Entwicklung

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
```
