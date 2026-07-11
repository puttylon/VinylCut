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
| `p<Sek>` | Snippet-Länge für den Rest des Laufs ändern (z.B. `p18` → 18s). Nur 3–30s gültig, außerhalb wird die Eingabe ignoriert |
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
Sucht für jede Audiodatei synchronisierte Songtexte (`.lrc`) bei vier Anbietern, prüft das Ergebnis mit Whisper und speichert die beste passende Datei. Wird von `cut.py` automatisch aufgerufen; kann auch manuell verwendet werden.

```bash
python3 fetch_songtext.py "Artist - Album/"                    # einzelnes Album
python3 fetch_songtext.py "/Pfad/zur/Datei.flac"              # einzelne Datei
python3 fetch_songtext.py --recursive "/Musik/"                # alle Unterordner
python3 fetch_songtext.py --force "Artist - Album/"            # Cache ignorieren
python3 fetch_songtext.py --no-whisper --recursive "/Musik/"   # ohne Whisper-Verifikation
```

**Optionen:**

| Flag | Bedeutung |
|------|-----------|
| `--recursive`, `-r` | Alle Unterordner rekursiv durchsuchen und LRCs erneuern |
| `--force`, `-f` | Cache ignorieren, alle Tracks neu prüfen |
| `--no-whisper` | Whisper-Verifikation überspringen (Konsens/Dauer-Heuristik statt Content-Check). Cache-Einträge mit `reason=kein-vokal`/`unter-schwelle` werden automatisch neu geprüft, auch ohne `--force`. |
| `-h`, `--help` | Hilfe anzeigen |
| `-V`, `--version` | Versionsnummer ausgeben |

**Unterstützte Formate:** FLAC, MP3, OGG, Opus, M4A, AAC, WAV

---

#### Wie der Algorithmus funktioniert

Für jeden Track läuft folgendes Verfahren — in dieser Reihenfolge:

**Schritt 1 — Vorab-Filter**

- Tracks ohne Artist- *und* Title-Tag werden übersprungen (kein sinnvoller Suchbegriff).
- Tracks mit Genre-Tags wie `Instrumental`, `Hörbuch`, `Podcast` o.ä. werden übersprungen.
- Bereits im Cache gespeicherte Ergebnisse werden nicht erneut verarbeitet (außer mit `--force`).

**Schritt 2 — Provider-Abfragen**

Die vier Anbieter `lrclib`, `musixmatch`, `netease` und `genius` werden gleichzeitig befragt (je max. 20 s Timeout). Artist und Titel kommen aus den Audio-Tags. Identische LRCs von verschiedenen Anbietern (gespiegelte Datenbanken) werden per Inhalt-Hash dedupliziert.

**Schritt 3 — Provider-Konsens (Schnellweg)**

Wenn mindestens 3 Anbieter eine LRC geliefert haben und deren Texte untereinander zu mindestens 40 % übereinstimmen (Jaccard-Ähnlichkeit), gilt das als Konsens. Der repräsentativste Kandidat — also der mit der höchsten Durchschnitts-Ähnlichkeit zu allen anderen — wird ohne Whisper-Prüfung gespeichert. Ausreißer-Anbieter (falscher Song, andere Sprache) werden dadurch automatisch übergangen.

→ Ergebnis: LRC gespeichert, kein Whisper nötig.

**Schritt 4 — Sprache erkennen**

Aus dem Text der Provider-LRCs wird die Sprache erkannt (z. B. `de`, `en`, `fr`) und als Hinweis an Whisper übergeben. Das verhindert, dass Whisper deutsche oder fremdsprachige Tracks auf Englisch transkribiert und dadurch kein Wort mit der LRC übereinstimmt.

**Schritt 5 — Whisper-Verifikation (small)**

Whisper transkribiert den gesamten Track (maximal 8 Minuten) mit dem `small`-Modell. Der Text wird mit jeder Provider-LRC verglichen. Das Ähnlichkeitsmaß ist **Containment**: Anteil der Whisper-Wörter, die in der LRC vorkommen (`|Whisper ∩ LRC| ÷ |Whisper|`). Diese Metrik ist asymmetrisch — sie bestraft nicht, wenn die LRC mehr Text enthält als Whisper gehört hat (z. B. Verse, die außerhalb des Fensters liegen).

Vor dem Vergleich: Wiederholungsschleifen (Whisper-Halluzinationen wie „lets go lets go lets go") werden erkannt und verworfen — die Einzigartigkeit der Wörter muss hoch genug sein *und* kein einzelnes Wort darf dominieren.

Score ≥ 40 % → LRC gespeichert. Darunter → kein Treffer. Kein zweiter Pass, kein Vorab-Check mehr (`base` und die VAD-Probe wurden in v1.7.0 entfernt — `base` transkribierte nicht-englische Songs unzuverlässig und lieferte falsch-negative „kein Vokal"-Ergebnisse; die VAD-Probe diente nur dazu, einen inzwischen ebenfalls entfernten zweiten Pass zu gaten).

`has_vocals` (steuert den „kein Vokal erkannt"-Zweig unten) kommt jetzt direkt aus diesem einen `small`-Durchlauf (`no_speech_prob` und Wortanzahl) — ohne separate Probe.

→ Bei „kein Vokal erkannt" werden die Provider-LRCs untereinander verglichen (Jaccard). Stimmen mindestens 2 Provider zu ≥ 40 % überein, wird die repräsentativste LRC gespeichert — als „Konsens (kein Vokal)". Sind die Provider sich uneinig, wird nichts gespeichert.

**Mit `--no-whisper`:** Schritte 4–5 entfallen komplett. Statt Whisper wird
immer ein 2-Provider-Konsens versucht (gleicher Jaccard-Schwellwert wie
Schritt 3, aber schon ab 2 statt 3 übereinstimmenden Anbietern). Schlägt auch
das fehl, entscheidet eine reine Dauer-Heuristik: der Kandidat mit dem besten
`_score_lrc`-Wert wird genommen — außer seine Dauer weicht zu stark vom Track
ab (siehe Toleranzen oben), dann wird nichts gespeichert
(`reason: "dauer-abweichung"`). Nützlich um Whisper ganz zu überspringen
(z. B. für einen schnellen Durchlauf ohne Modell-Ladezeit) — kostet die
inhaltliche Verifikation gegen falsch zugeordnete Songtexte.

---

#### Ausgabe-Zeichen

Jede Zeile endet mit einem Datei-Ergebnis-Symbol (was mit der `.lrc`-Datei passiert ist):

| Symbol | Bedeutung |
|--------|-----------|
| `✓` | LRC geschrieben (neu oder ersetzt) |
| `=` | Nichts geschrieben — war bereits identisch oder kein Treffer |
| `–` | Vorhandene LRC gelöscht — neuer Lauf fand kein brauchbares Ergebnis |

Davor steht die Methoden-Info mit sechs Teilen:

```
[Zeit]  [Pfad]  [Anzahl/Total: Provider] │ [Modell] [Sprache] [Methode] [Wörter] [Ergebnis]
```

Beispiele:
```
09:28:20  Artist/Album/01 Song.flac  2/4: lrclib, genius │ [small] de Whisper 265W 62%  ✓
09:28:20  Artist/Album/02 Song.flac  3/4: lrclib, netease, genius │ Konsens 92%  ✓
09:28:20  Artist/Album/03 Song.flac  2/4: netease, genius │ Konsens 87% (kein Vokal)  ✓
09:28:20  Artist/Album/04 Song.flac  2/4: lrclib, genius │ [small] de Whisper 48W unter Schwelle 12%  =
09:28:20  Artist/Album/05 Song.flac  0/4: — │ kein Provider  =
09:28:20  Artist/Album/06 Song.flac  2/4: netease, genius │ [small] de Whisper 0W kein Vokal  =
09:28:20  Artist/Album/07 Song.flac  2/4: lrclib, genius │ [small] de Whisper 12W unter Schwelle 8%  –
09:28:20  Artist/Album/08 Song.flac  0/0: │ Genre=Instrumental  –
09:28:20  Artist/Album/09 Song.flac  0/0: │ Genre=Instrumental  =
```

Mit `--no-whisper`:
```
09:28:20  Artist/Album/01 Song.flac  2/4: lrclib, genius │ Konsens 62% (2P)  ✓
09:28:20  Artist/Album/02 Song.flac  2/4: netease, genius │ Heuristik  ✓
09:28:20  Artist/Album/03 Song.flac  2/4: lrclib, genius │ Heuristik Dauer-Abweichung  =
```

- **Modell**: `[small]` — einziges Whisper-Modell (seit v1.7.0, `base` entfernt)
- **Sprache**: z.B. `de`, `en` — von `langdetect` erkannt, als Hint an Whisper übergeben
- **Wörter**: von Whisper transkribierte Wörter (Qualitätsindikator: 5W 62% ist unsicherer als 280W 62%)
- **Konsens**: kein Whisper nötig, Provider einig — bei `(kein Vokal)` hat `has_vocals` (aus dem Whisper-Pass) ausgelöst, bei `(2P)` lief mit `--no-whisper` der abgesenkte 2-Provider-Konsens

---

#### Cache und Hilfs-Skripte

**Cache** (`.fetch_songtext.json` pro Ordner): Ein Eintrag pro Track — beim nächsten Lauf wird der Track übersprungen. `--force` ignoriert den Cache komplett.

**Felder je Eintrag:**

| Feld | Werte | Bedeutung |
|------|-------|-----------|
| `v` | `"1.7.0"` | Version des schreibenden Skripts |
| `r` | `"ok"` / `"nf"` / `"skip"` | Ergebnis: LRC vorhanden / nicht gefunden / übersprungen |
| `outcome` | `"write"` / `"none"` / `"delete"` | Datei-Aktion: geschrieben / nichts / gelöscht |
| `providers` | `0`–`4` | Anzahl Provider mit Treffer |
| `provider_names` | `["lrclib", "genius"]` | Namen der liefernden Provider |
| `method` | `"whisper-small"` / `"konsens"` / `"heuristik"` / `null` | Entscheidungsweg (`"whisper-base"` gab es bis v1.6.x, seit v1.7.0 nur noch `"whisper-small"`) |
| `no_vocal` | `true` / `false` | Whisper-Pass hat keinen Gesang erkannt (bei `method=konsens`: Konsens trotzdem möglich) |
| `score` | `0.0`–`1.0` / `null` | Whisper-Containment oder Jaccard-Konsens |
| `reason` | `"kein-provider"` / `"kein-vokal"` / `"unter-schwelle"` / `"dauer-abweichung"` / `"genre"` | Grund bei `r=nf` oder `r=skip` (`dauer-abweichung` nur bei `--no-whisper`) |
| `words` | `0`–`n` / `null` | Von Whisper transkribierte Wörter |
| `language` | `"de"` / `"en"` / … / `null` | Erkannte Sprache (Hint an Whisper) |
| `ts` | ISO-8601 | Zeitstempel des Laufs |

Beispiel-Einträge:

```json
"01 Song.flac": {
  "v": "1.7.0", "r": "ok", "outcome": "write",
  "providers": 2, "provider_names": ["lrclib", "genius"],
  "method": "whisper-small", "no_vocal": false,
  "score": 0.62, "words": 265, "language": "de", "ts": "2026-07-09T09:28:20"
},
"02 Instrumental.flac": {
  "v": "1.7.0", "r": "skip", "outcome": "delete",
  "providers": 0, "provider_names": [],
  "method": null, "no_vocal": false,
  "score": null, "reason": "genre", "words": null, "language": null, "ts": "2026-07-09T09:28:25"
}
```

**`lrc_recheck.py`** — sucht gecachte „nicht gefunden"-Einträge und löscht sie gezielt, damit sie beim nächsten Lauf neu geprüft werden:

```bash
python3 lrc_recheck.py /Musik/                            # Vorschau (≥ 3 Provider)
python3 lrc_recheck.py /Musik/ --apply                    # Cache-Einträge löschen
python3 lrc_recheck.py /Musik/ --min-providers 1 --min-score 0.0 --apply   # alle neu prüfen
```

**`lrc_analyse.py`** — zeigt Statistiken über eine gesamte Musikbibliothek:

```bash
python3 lrc_analyse.py /Musik/
```

Ausgabe: Trefferquote, verwendete Methoden, Ablehnungsgründe, Score-Verteilung, Risiko-Tracks (niedriger Score oder nur ein Anbieter) und Tracks, die ohne Whisper-Verifikation gespeichert wurden.

**`whisper_analyse.py`** — zeigt speziell, ob und warum Whisper pro Track gelaufen ist (unabhängig von der Skriptversion des Cache-Eintrags):

```bash
python3 whisper_analyse.py /Musik/
```

**Genius-Token:** Datei `genius_token` im Skript-Verzeichnis ablegen oder `GENIUS_ACCESS_TOKEN` als Umgebungsvariable setzen.

---

## Abhängigkeiten

**Python-Pakete:**
```bash
pip install -r requirements.txt
pip install syncedlyrics
pip install faster-whisper   # optional — Whisper-Verifikation
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
