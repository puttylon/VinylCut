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
| `p<Sek>` | Snippet-Länge für den Rest des Laufs ändern (z.B. `p18` → 18s). Nur 2–30s gültig, außerhalb wird die Eingabe ignoriert |
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
python3 fetch_songtext.py --fast --recursive "/Musik/"          # Phase 1: schneller Lauf, Whisper-Fälle aufgeschoben
python3 fetch_songtext.py --cache-only --recursive "/Musik/"   # Phase 2 ohne jede Live-Provider-Abfrage
python3 fetch_songtext.py --recursive "/Musik/"                 # Phase 2: normaler Lauf, füllt die Lücken
```

**Optionen:**

| Flag | Bedeutung |
|------|-----------|
| `--recursive`, `-r` | Alle Unterordner rekursiv durchsuchen und LRCs erneuern |
| `--force`, `-f` | Cache ignorieren, alle Tracks neu prüfen |
| `--no-whisper` | Whisper-Verifikation überspringen (Konsens/Dauer-Heuristik statt Content-Check). Cache-Einträge mit `reason=kein-vokal`/`unter-schwelle` werden automatisch neu geprüft, auch ohne `--force`. |
| `--fast` | Zwei-Phasen-Workflow, Phase 1 (siehe unten): Konsens und „kein Provider" werden erledigt und gecacht, alles was Whisper bräuchte wird **aufgeschoben** — kein Cache-Eintrag, vorhandene `.lrc` bleibt unangetastet. |
| `--no-cache` | Provider-/Whisper-Cache (`fetch_songtext_cache.db`, siehe `CACHE_DESIGN.md`) komplett ignorieren — Verhalten wie ohne Cache-Modul. Die Whisper-Verifikation braucht die Cache-DB immer als Hintergrund-Pool für die kontrastive Marge (siehe unten) — `--no-cache` ist deshalb nur zusammen mit `--no-whisper` oder `--fast` erlaubt. |
| `--refresh-cache` | Cache-Treffer überspringen (frisch von Anbieter holen / neu anhören), Ergebnis wird trotzdem neu in den Cache geschrieben. |
| `--cache-ttl TAGE` | Cache-Gültigkeit für Provider-Treffer in Tagen (Default 30). |
| `--cache-only` | Keine Live-Provider-Abfragen — auch gecachte Fehlschläge werden nicht erneut live geprüft. Betrifft NUR Provider-Abfragen, nicht Whisper (seit v1.10.1): ohne gecachtes Transkript wird trotzdem live transkribiert. Schließt sich mit `--force`/`--refresh-cache`/`--no-cache` aus. |
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

Die vier Anbieter `lrclib`, `musixmatch`, `netease` und `genius` werden gleichzeitig befragt (je max. 20 s Timeout). Artist und Titel kommen aus den Audio-Tags. Klammer-Zusätze im Titel (`(Live In Osaka Japan 16th August 1972)`, `[Deluxe Edition 2014 Remix]` u.ä.) werden für den Suchbegriff entfernt — Lyrics-Provider kennen meist nur den Kern-Titel, der Songtext ist ohnehin identisch zur Studio-Version. Title-Tag und Dateiname bleiben davon unberührt. Identische LRCs von verschiedenen Anbietern (gespiegelte Datenbanken) werden per Inhalt-Hash dedupliziert.

**Schritt 3 — Provider-Konsens (Schnellweg)**

Wenn mindestens 3 Anbieter eine LRC geliefert haben und deren Texte untereinander zu mindestens 40 % übereinstimmen (Jaccard-Ähnlichkeit), gilt das als Konsens. Der repräsentativste Kandidat — also der mit der höchsten Durchschnitts-Ähnlichkeit zu allen anderen — wird ohne Whisper-Prüfung gespeichert. Ausreißer-Anbieter (falscher Song, andere Sprache) werden dadurch automatisch übergangen.

→ Ergebnis: LRC gespeichert, kein Whisper nötig.

**Schritt 4 — Sprache erkennen**

Aus dem Text der Provider-LRCs wird die Sprache erkannt (z. B. `de`, `en`, `fr`) und als Hinweis an Whisper übergeben. Das verhindert, dass Whisper deutsche oder fremdsprachige Tracks auf Englisch transkribiert und dadurch kein Wort mit der LRC übereinstimmt.

**Schritt 5 — Whisper-Verifikation (small)**

Whisper transkribiert den gesamten Track (maximal 8 Minuten) mit dem `small`-Modell. Der Text wird mit jeder Provider-LRC verglichen. Das Ähnlichkeitsmaß ist **IDF-gewichtetes Jaccard** (seit v1.7.7, ersetzt die frühere Containment-Metrik): `Σ idf(w) für w in (Whisper ∩ LRC) ÷ Σ idf(w) für w in (Whisper ∪ LRC)`. Seltene, inhaltstragende Wörter zählen dabei stark, häufige Stopwords kaum — das verhindert Fehlmatches, bei denen zufällig ein paar generische Wörter übereinstimmen, obwohl der Song nicht passt. Die IDF-Werte (Dokumentfrequenz je Wort) stammen seit v1.10.0 aus einer **globalen, aus der Cache-DB gebauten Tabelle** (`fetch_songtext_cache.db`, siehe unten) — keine separate Datei, keine Sprach-Teiltabellen mehr.

Vor dem Vergleich: Wiederholungsschleifen (Whisper-Halluzinationen wie „lets go lets go lets go") werden erkannt und verworfen — die Einzigartigkeit der Wörter muss hoch genug sein *und* kein einzelnes Wort darf dominieren.

**Akzeptanz-Entscheidung — kontrastive Marge (seit v1.10.0 Standardverfahren):** Statt einer festen absoluten Schwelle wird gefragt „hebt sich der beste Kandidat deutlich vom Zufall ab?". Dazu wird der beste Score mit dem besten Score von 20 zufällig gezogenen ANDEREN Songs gleicher Sprache aus dem Cache verglichen (Hintergrund-Pool): `Marge = best_score − max(Hintergrund)`. Akzeptiert wird per Hybrid-Regel: `best_score ≥ 0,3` (absoluter Boden, unabhängig vom Hintergrund) **ODER** `Marge ≥ 0,0115`. Der absolute Boden fängt Fälle ab, in denen ein einzelner fehlerhafter Kandidat im Hintergrund-Pool die Marge eines eigentlich korrekten Songtexts unter die Schwelle drückt (siehe `ROADMAP.md`). Ist der gleichsprachige Hintergrund-Pool zu klein (< 5 andere Songs derselben Sprache im Cache), fällt die Entscheidung auf die alte, sprachspezifische absolute Schwelle zurück (aktuell nur Deutsch mit 0,043 eigens kalibriert, alle anderen Sprachen nutzen 0,065). Kein zweiter Pass, kein Vorab-Check mehr (`base` und die VAD-Probe wurden in v1.7.0 entfernt).

Die kontrastive Marge braucht dafür immer eine offene Cache-DB als Hintergrund-Pool — Whisper-Verifikation ohne Cache-DB ist daher nicht mehr möglich (siehe `--no-cache` oben).

`has_vocals` (steuert den „kein Vokal erkannt"-Zweig unten) kommt direkt aus dem `small`-Durchlauf (`no_speech_prob` und Wortanzahl) — ohne separate Probe.

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

**Mit `--fast` (Zwei-Phasen-Workflow):** Anders als `--no-whisper` wird hier
nicht geraten. Konsens (Schritt 3) und „kein Provider" laufen unverändert und
werden ganz normal gecacht — dort wird ohnehin nie Whisper gebraucht. Für
jeden Track, der im Normalmodus jetzt Whisper bräuchte (Konsens verfehlt,
Audiodatei vorhanden), wird **nichts** gemacht: kein Whisper, keine
Dauer-Heuristik-Vermutung, **kein Cache-Eintrag**, eine vorhandene `.lrc`
bleibt unangetastet. Das Skript lädt in diesem Modus auch das Whisper-Modell
und die IDF-Tabelle gar nicht erst (spart die Ladezeit).

Weil aufgeschobene Tracks keinen Cache-Eintrag bekommen, verarbeitet sie ein
späterer **normaler Lauf** (Phase 2, ohne `--fast`/`--no-whisper`) automatisch
als „ungesehen" — mit voller Whisper-Verifikation. Gedacht für: schnell die
einfachen Fälle (Konsens) erledigen, dann in Ruhe (oder über Nacht) die
Whisper-pflichtigen Lücken in einem zweiten Lauf schließen.

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
09:28:20  Artist/Album/01 Song.flac  2/4: lrclib, genius │ [small] de Whisper 265W idf-jacc=0.312  ✓
09:28:20  Artist/Album/02 Song.flac  3/4: lrclib, netease, genius │ Konsens 92%  ✓
09:28:20  Artist/Album/03 Song.flac  2/4: netease, genius │ Konsens 87% (kein Vokal)  ✓
09:28:20  Artist/Album/04 Song.flac  2/4: lrclib, genius │ [small] de Whisper 48W unter Schwelle idf-jacc=0.041  =
09:28:20  Artist/Album/05 Song.flac  0/4: — │ kein Provider  =
09:28:20  Artist/Album/06 Song.flac  2/4: netease, genius │ [small] de Whisper 0W kein Vokal  =
09:28:20  Artist/Album/07 Song.flac  2/4: lrclib, genius │ [small] de Whisper 12W unter Schwelle idf-jacc=0.023  –
09:28:20  Artist/Album/08 Song.flac  0/0: │ Genre=Instrumental  –
09:28:20  Artist/Album/09 Song.flac  0/0: │ Genre=Instrumental  =
```

Mit `--no-whisper`:
```
09:28:20  Artist/Album/01 Song.flac  2/4: lrclib, genius │ Konsens 62% (2P)  ✓
09:28:20  Artist/Album/02 Song.flac  2/4: netease, genius │ Heuristik  ✓
09:28:20  Artist/Album/03 Song.flac  2/4: lrclib, genius │ Heuristik Dauer-Abweichung  =
```

Mit `--fast`:
```
09:28:20  Artist/Album/01 Song.flac  3/4: lrclib, netease, genius │ Konsens 92%  ✓
09:28:20  Artist/Album/02 Song.flac  0/4: — │ kein Provider  =
09:28:20  Artist/Album/03 Song.flac  2/4: lrclib, genius │ aufgeschoben (Whisper)  =
```
Das Datei-Symbol bei „aufgeschoben" ist immer `=` (nichts angefasst) — die
Info steckt im Methoden-Teil nach `│`.

- **Modell**: `[small]` — einziges Whisper-Modell (seit v1.7.0, `base` entfernt)
- **Sprache**: z.B. `de`, `en` — von `langdetect` erkannt, als Hint an Whisper übergeben
- **Wörter**: von Whisper transkribierte Wörter (Qualitätsindikator: 5W idf-jacc=0.31 ist unsicherer als 280W idf-jacc=0.31)
- **idf-jacc**: IDF-gewichteter Jaccard-Score (0,0–1,0, meist deutlich unter 0,5 — kleine Zahlen sind normal, siehe Schritt 5). Die Akzeptanz-Entscheidung selbst läuft über die kontrastive Marge (Score allein ist nicht direkt mit einer festen Schwelle vergleichbar, siehe Schritt 5).
- **Konsens**: kein Whisper nötig, Provider einig — bei `(kein Vokal)` hat `has_vocals` (aus dem Whisper-Pass) ausgelöst, bei `(2P)` lief mit `--no-whisper` der abgesenkte 2-Provider-Konsens

---

#### Cache und Hilfs-Skripte

**Cache** (`.fetch_songtext.json` pro Ordner): Ein Eintrag pro Track — beim nächsten Lauf wird der Track übersprungen. `--force` ignoriert den Cache komplett.

**Parallele Instanzen:** `fetch_songtext.py -r` kann bewusst mehrfach gleichzeitig über dieselbe Bibliothek gestartet werden. Jede Instanz sperrt sich beim Betreten eines Ordners exklusiv über `.fetch_songtext.lock` (pro Ordner) — hält eine andere Instanz den Ordner bereits, wird er komplett übersprungen statt doppelt bearbeitet zu werden.

**Globale IDF-Tabelle für die kontrastive Marge** (seit v1.10.0): Keine separate Datei mehr — die Dokumentfrequenz je Wort wird bei jedem Lauf aus `texte.inhalt` der Cache-DB (`fetch_songtext_cache.db`) gebaut (`_global_cache_idf`), zusammen mit einem Hintergrund-Pool je Sprache (ein Provider-Treffer-Text pro Cache-Song, Sprache über denselben `langdetect`-Mechanismus wie Schritt 4 erkannt). Das ersetzt die frühere Datei-basierte, sprachspezifische Tabelle (`fetch_songtext_idf.json`, `--rebuild-idf`) vollständig — beides ist mit v1.10.0 entfernt. **Konsequenz: die Whisper-Verifikation braucht jetzt immer eine offene Cache-DB** (siehe `--no-cache` oben) — ohne Cache-DB gibt es keinen Hintergrund-Pool und keine globale IDF mehr.

**Cache-Modul** (`fetch_songtext_cache.db`, neben dem Skript; Design siehe `CACHE_DESIGN.md`): normalisiertes Schema — `songs` (ein Künstler/Titel = eine Zeile) verknüpft mit `ergebnisse` (ein Versuch je Provider, bis zu vier pro Song), `texte` (jeder Liedtext-Inhalt einmal, per Fingerabdruck dedupliziert) und `transkripte` (ein Whisper-Transkript je Song, gleiche Künstler+Titel-Identität wie `songs` — nicht an Datei/Modell/Parameter gebunden). Speichert erfolgreiche Provider-Antworten und Whisper-Transkripte, damit ein Neuaufbau der Bibliothek nach Code-Änderungen ohne erneute Provider-Abfragen/Whisper-Läufe möglich ist. Für die Provider-Abfragen gilt weiterhin: das Skript läuft **auch mit leerer oder fehlender Datenbank** exakt wie ohne Cache — der Cache ist dort nur ein Beschleuniger. Für die Whisper-Verifikation ist die Datenbank seit v1.10.0 dagegen **Voraussetzung** (kontrastive Marge, siehe oben) — ohne Cache-DB nur mit `--no-whisper` oder `--fast` nutzbar. Jeder Provider-Versuch wird festgehalten — auch ein Fehlschlag (`status="fehlschlag"` mit `fehlergrund`: `rate_limit`/`captcha`/`timeout`/`gesperrt`), aber ein Fehlschlag zählt nie als gültiger Cache-Treffer (immer erneuter Live-Versuch beim nächsten Lauf). Eskaliert der Rate-Limit-Backoff eines Providers auf 5 Treffer in Folge (z.B. dauerhaft blockiertes Musixmatch-Captcha), wird er für 15 Minuten komplett übersprungen statt bei jedem Song erneut zu warten — der Fehlschlag wird dabei sofort (ohne Live-Versuch) mit `fehlergrund="gesperrt"` festgehalten. Provider-Treffer verfallen nach 30 Tagen (`--cache-ttl`). Mit `--no-cache` komplett deaktivieren (nur mit `--no-whisper`/`--fast` kombinierbar); `--refresh-cache` **und** `--force` erzwingen beide eine frische Live-Abfrage (umgehen den Provider-Cache vollständig). `--cache-only` macht das Gegenteil: verbietet jede Live-Provider-Abfrage — auch für Provider mit gecachtem Fehlschlag. Whisper ist davon NICHT betroffen (seit v1.10.1): ohne gecachtes Transkript wird trotzdem live transkribiert (siehe Schritt 5).

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
| `score` | `0.0`–`1.0` / `null` | Whisper-IDF-Jaccard (seit v1.7.7, davor Containment) oder Jaccard-Konsens |
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

**`inspect_song.py`** — Diagnose für einen einzelnen Song: schreibt Provider-Texte (Genius, Netease, Lrclib, Musixmatch) und das Whisper-Transkript aus der Cache-Datenbank nebeneinander in eine TXT-Datei:

```bash
python3 inspect_song.py --artist "Nina Hagen" --title "Naturträne"
python3 inspect_song.py --artist "Nina Hagen" --title "Naturträne" --output custom_name.txt
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
