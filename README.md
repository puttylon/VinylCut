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

`cut.py` ruft `fetch_metadata.py` automatisch auf und holt Songtexte direkt über die `lyrics_core`-Pipeline mit (siehe `songtext_pipeline.py` weiter unten für den eigenständigen Aufruf über eine ganze Bibliothek).

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
| 4 | DC-Offset entfernen, optionaler Kanalausgleich, Peak-Normalisierung auf -1 dBFS → Ausgabedatei |

**Steuerung Phase 1:**

| Eingabe | Aktion |
|---------|--------|
| `p` | Snippet abspielen |
| `p<Sek>` | Snippet-Länge für den Rest des Laufs ändern (z.B. `p18` → 18s). Nur 2–30s gültig, außerhalb wird die Eingabe ignoriert |
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
| `p<Sek>` | Crossfade-Vorschaulänge für den Rest des Laufs ändern (z.B. `p12` → 12s). Nur 2–30s gültig, außerhalb wird die Eingabe ignoriert |
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
Liest `release.json`, lässt dich für jeden Track den Startpunkt per Tastatur feinjustieren (Playback via ffplay), schneidet sample-genau mit SoX und taggt jede FLAC mit metaflac. Speichert Fortschritt nach jedem bestätigten Track. Ruft danach automatisch die Songtext-Suche auf (`lyrics_core`/`evaluate_lyrics`, siehe `songtext_pipeline.py` unten für den Algorithmus).

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

### `songtext_pipeline.py`
Sucht für die Audiodateien im gewählten Umfang synchronisierte Songtexte (`.lrc`) bei vier Anbietern, prüft das Ergebnis mit Whisper und speichert die beste passende Datei. Wird von `cut.py` automatisch pro Track aufgerufen; für eine ganze Bibliothek (Batch-Lauf, Nachpflege, Neuaufbau nach Code-Änderungen) wird `songtext_pipeline.py` manuell verwendet.

Steuer-Skript für die Songtexte-Pipeline (Architektur siehe `workflow für songexte.txt`, Abschnitt „ZIELARCHITEKTUR"; Baufortschritt siehe `ROADMAP.md`, „Songtexte-Pipeline-Umbau"). Jeder Schritt hat sein eigenes Flag — kein Sammel-Flag `--phase` mehr (Nutzer-Feedback: „kein Mensch braucht im Flag den Begriff 'phase'"). Jeder Schritt liest/schreibt ausschließlich über die gemeinsame Cache-DB (`fetch_songtext_cache.db`, siehe unten) und ist einzeln wiederholbar:

| Flag | Was passiert | Eigenständiges Modul |
|---|---|---|
| `--scan` | Audio-Tags lesen, Song-Identität (Artist/Titel) in der Cache-DB anlegen. Braucht PFAD. | `scan_songs.py` |
| `--abfragen` | Alle vier Anbieter (`lrclib`, `musixmatch`, `netease`, `genius`) gleichzeitig abfragen, Ergebnis in die Cache-DB schreiben — überspringt dabei Songs mit Skip-Genre (Hörbuch/Hörspiel/Instrumental/…) sowie Songs, für die jeder Anbieter schon einen gültigen, nicht abgelaufenen Treffer/Nichts-Eintrag oder einen Fehlschlag (Sache von `--nachholen`) hat — nur pro (Song, Anbieter) noch offene Kombinationen werden wirklich angefragt | `fetch_providers.py` (`fetch_all`) |
| `--nachholen` | Nachhol-Modus: fragt gezielt nur (Song, Anbieter)-Kombinationen mit `status IN ('nichts', 'fehlschlag')` erneut ab — z. B. nachdem ein Anbieter fälschlich als gesperrt galt, obwohl er längst wieder funktioniert. Läuft NIE von allein mit (auch nicht im Normal-Durchlauf ohne jedes Flag) — impliziert dann `--bewerten` + `--schreiben` mit. | `fetch_providers.py` (`retry_missing`) |
| `--bewerten` | Entscheidet je Song: Provider-Konsens, sonst Whisper-Verifikation, sonst Dauer-Heuristik (siehe „Wie der Algorithmus funktioniert" unten). Mit PFAD wird ein Track übersprungen, wenn sein JSON-Ordner-Cache-Eintrag noch gültig ist UND die Cache-DB seitdem nichts Neueres hat (derselbe Vergleich wie bei `--schreiben`) — ein Wiederholungslauf über einen unveränderten Ordner bewertet dann nichts neu. | `evaluate_lyrics.py` |
| `--schreiben` | `.lrc` schreiben/löschen je nach Bewertung, JSON-Ordner-Cache pflegen. Braucht PFAD. | `write_lrc.py` |

Mit PFAD grenzt jedes Flag (außer `--scan`/`--schreiben`, die ohnehin PFAD brauchen) auf die Songs unter PFAD ein; ohne PFAD arbeitet es über die GANZE Bibliothek (explizite „alles nachziehen"-Absicht). Das gilt auch für `--nachholen` — gezielt nur die fehlenden Anbieter EINES Albums nachholen ist damit möglich, ohne die ganze Bibliothek anzufassen.

**Mit PFAD läuft die Pipeline Ordner für Ordner:** Tags werden einmal für den ganzen Baum gelesen, dann nach Ordner gruppiert — für jeden Ordner (Album) laufen alle gewählten Schritte komplett durch (`scan` → `abfragen`/`nachholen` → `bewerten` → `schreiben`), bevor der nächste Ordner beginnt. Das gibt sichtbaren Fortschritt Ordner für Ordner statt eines langen, stillen Laufs über die ganze Bibliothek, und bei einem Abbruch mitten im Lauf sind bereits fertige Ordner schon geschrieben. Innerhalb eines Ordners laufen `--abfragen`/`--bewerten` in Dateinamen-Reihenfolge (nicht alphabetisch nach Künstler/Titel) — die Konsolenausgabe zeigt dabei den Dateinamen statt Künstler/Titel, wenn eine Audiodatei bekannt ist, das deckt sich besser mit der Tracklist im Ordner. Ohne PFAD (z.B. `--nachholen` allein) läuft weiterhin alles global in einem Rutsch über die ganze Cache-DB, da es dort keine Ordner-Struktur gibt.

Für die lrclib-Quelle wird vor jeder echten Live-Abfrage zuerst ein lokaler LRCLib-Datenbank-Abzug durchsucht (`/Volumes/music/db.sqlite3`, falls erreichbar) — nur bei 0 Treffern dort wird wie bisher live gefragt. Kein eigenes Flag nötig; fehlt der Abzug (Mount nicht vorhanden), degradiert die Pipeline automatisch auf reines Live-Fragen (siehe `CACHE_DESIGN.md`).

```bash
python3 songtext_pipeline.py "Artist - Album/"                        # scan+abfragen+bewerten+schreiben, ein Album
python3 songtext_pipeline.py "/Pfad/zur/Datei.flac"                   # scan+abfragen+bewerten+schreiben, eine Datei
python3 songtext_pipeline.py --recursive "/Musik/"                    # scan+abfragen+bewerten+schreiben, ganze Bibliothek
python3 songtext_pipeline.py "/Musik/" --abfragen --bewerten --schreiben  # nur ausgewählte Schritte
python3 songtext_pipeline.py --nachholen                              # nachholen+bewerten+schreiben, ganze Bibliothek
python3 songtext_pipeline.py "Artist - Album/" --nachholen             # nachholen+bewerten+schreiben, nur dieses Album
```

**Optionen:**

| Flag | Bedeutung |
|------|-----------|
| `--recursive`, `-r` | Unterordner von PFAD mit einbeziehen |
| `--scan` | siehe Tabelle oben |
| `--abfragen` | siehe Tabelle oben |
| `--nachholen` | siehe Tabelle oben |
| `--bewerten` | siehe Tabelle oben |
| `--schreiben` | siehe Tabelle oben |
| `-h`, `--help` | Hilfe anzeigen |

Kein Schritt-Flag angegeben → Normal-Durchlauf: `--scan --abfragen --bewerten --schreiben`, in dieser Reihenfolge — **ohne** `--nachholen` (ein Wiederholungslauf soll nicht jedes Mal ungefragt alle historisch offenen „nichts"/„fehlschlag"-Kombis erneut live nachfragen). `--nachholen` läuft deshalb nur, wenn es ausdrücklich angegeben wird, und impliziert dann automatisch `--bewerten` + `--schreiben` mit (sonst käme ein frisch gefundener Provider-Treffer nirgendwo an). Mindestens ein anderes Schritt-Flag angegeben → nur die angegebenen Schritte laufen, weiterhin in derselben festen Reihenfolge. `--scan`/`--schreiben` brauchen für Datei-Zuordnung/Schreiben zwingend PFAD — ohne PFAD werden sie mit einem Hinweis übersprungen statt einen Fehler zu werfen.

`lyrics_core.py` bündelt die eigentliche Such-/Bewertungslogik (Provider-Abfragen, Whisper, Cache-Helfer) als reine, von allen Schritten geteilte Bibliothek — hat kein eigenes CLI und wird nicht direkt aufgerufen.

**Unterstützte Formate:** FLAC, MP3, OGG, Opus, M4A, AAC, WAV

---

#### Wie der Algorithmus funktioniert

Für jeden Track läuft folgendes Verfahren — in dieser Reihenfolge:

**Vorab-Filter**

- Tracks ohne Artist- *und* Title-Tag werden übersprungen (kein sinnvoller Suchbegriff).
- Tracks mit Genre-Tags wie `Instrumental`, `Hörbuch`, `Podcast` o.ä. werden übersprungen (Schritt `--abfragen`).
- Bereits im JSON-Ordner-Cache gespeicherte Ergebnisse werden nicht erneut geschrieben (Schritt `--schreiben`, siehe unten).

**Provider-Abfragen** (Schritt `--abfragen`/`--nachholen`)

Die vier Anbieter `lrclib`, `musixmatch`, `netease` und `genius` werden gleichzeitig befragt (je max. 20 s Timeout). Artist und Titel kommen aus den Audio-Tags. Klammer-Zusätze im Titel (`(Live In Osaka Japan 16th August 1972)`, `[Deluxe Edition 2014 Remix]` u.ä.) werden für den Suchbegriff entfernt — Lyrics-Provider kennen meist nur den Kern-Titel, der Songtext ist ohnehin identisch zur Studio-Version. Title-Tag und Dateiname bleiben davon unberührt. Identische LRCs von verschiedenen Anbietern (gespiegelte Datenbanken) werden per Inhalt-Hash dedupliziert.

**Provider-Konsens (Schnellweg)** (Schritt `--bewerten`)

Wenn mindestens 3 Anbieter eine LRC geliefert haben und deren Texte untereinander zu mindestens 40 % übereinstimmen (Jaccard-Ähnlichkeit), gilt das als Konsens. Der repräsentativste Kandidat — also der mit der höchsten Durchschnitts-Ähnlichkeit zu allen anderen — wird ohne Whisper-Prüfung gespeichert. Ausreißer-Anbieter (falscher Song, andere Sprache) werden dadurch automatisch übergangen.

→ Ergebnis: LRC gespeichert, kein Whisper nötig.

**Sprache erkennen**

Aus dem Text der Provider-LRCs wird die Sprache erkannt (z. B. `de`, `en`, `fr`) und als Hinweis an Whisper übergeben. Das verhindert, dass Whisper deutsche oder fremdsprachige Tracks auf Englisch transkribiert und dadurch kein Wort mit der LRC übereinstimmt.

**Whisper-Verifikation**

Whisper transkribiert den gesamten Track (maximal 8 Minuten). Das Modell richtet sich nach der erkannten Sprache: englischsprachige Songs nutzen `medium`, alle anderen (insbesondere Deutsch und gemischtsprachige Songs) `large-v3` — der Qualitätsgewinn von `large-v3` ist dort laut Testlauf real und deutlich, bei Englisch dagegen zu gering für dessen Mehrkosten (siehe `whisper_modellvergleich_ergebnis.md`, `ROADMAP.md`).

Der Text wird mit jeder Provider-LRC verglichen. Das Ähnlichkeitsmaß ist **IDF-gewichtetes Jaccard**: `Σ idf(w) für w in (Whisper ∩ LRC) ÷ Σ idf(w) für w in (Whisper ∪ LRC)`. Seltene, inhaltstragende Wörter zählen dabei stark, häufige Stopwords kaum — das verhindert Fehlmatches, bei denen zufällig ein paar generische Wörter übereinstimmen, obwohl der Song nicht passt. Die IDF-Werte (Dokumentfrequenz je Wort) stammen aus einer **globalen, aus der Cache-DB gebauten Tabelle** (`fetch_songtext_cache.db`, siehe unten) — keine separate Datei, keine Sprach-Teiltabellen.

Vor dem Vergleich: Wiederholungsschleifen (Whisper-Halluzinationen wie „lets go lets go lets go") werden erkannt und verworfen — die Einzigartigkeit der Wörter muss hoch genug sein *und* kein einzelnes Wort darf dominieren.

**Akzeptanz-Entscheidung — kontrastive Marge:** Statt einer festen absoluten Schwelle wird gefragt „hebt sich der beste Kandidat deutlich vom Zufall ab?". Dazu wird der beste Score mit dem besten Score von 20 zufällig gezogenen ANDEREN Songs gleicher Sprache aus dem Cache verglichen (Hintergrund-Pool): `Marge = best_score − max(Hintergrund)`. Akzeptiert wird per Hybrid-Regel: `best_score ≥ 0,3` (absoluter Boden, unabhängig vom Hintergrund) **ODER** `Marge ≥ 0,0115`. Der absolute Boden fängt Fälle ab, in denen ein einzelner fehlerhafter Kandidat im Hintergrund-Pool die Marge eines eigentlich korrekten Songtexts unter die Schwelle drückt (siehe `ROADMAP.md`). Ist der gleichsprachige Hintergrund-Pool zu klein (< 5 andere Songs derselben Sprache im Cache), fällt die Entscheidung auf die alte, sprachspezifische absolute Schwelle zurück (aktuell nur Deutsch mit 0,043 eigens kalibriert, alle anderen Sprachen nutzen 0,065).

Die kontrastive Marge braucht dafür immer eine offene Cache-DB als Hintergrund-Pool — Whisper- und IDF-Kontext werden dafür einmal pro Lauf aufgebaut, nicht pro Song.

`has_vocals` (steuert den „kein Vokal erkannt"-Zweig unten) kommt direkt aus dem Whisper-Durchlauf (`no_speech_prob` und Wortanzahl) — ohne separate Probe.

→ Bei „kein Vokal erkannt" werden die Provider-LRCs untereinander verglichen (Jaccard). Stimmen mindestens 2 Provider zu ≥ 40 % überein, wird die repräsentativste LRC gespeichert — als „Konsens (kein Vokal)". Sind die Provider sich uneinig, wird nichts gespeichert.

**Ohne Audiodatei** (Schritt `--bewerten` ohne zugeordnete Datei, z. B. bei einem eigenständigen Lauf ohne PFAD): Whisper entfällt, es entscheidet eine reine Dauer-Heuristik — der Kandidat mit dem besten `_score_lrc`-Wert wird genommen, außer seine Dauer weicht zu stark vom Track ab, dann wird nichts gespeichert (`reason: "dauer-abweichung"`).

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
09:28:20  Artist/Album/01 Song.flac  2/4: lrclib, genius │ [large-v3] de Whisper 265W idf-jacc=0.312  ✓
09:28:20  Artist/Album/02 Song.flac  3/4: lrclib, netease, genius │ Konsens 92%  ✓
09:28:20  Artist/Album/03 Song.flac  2/4: netease, genius │ Konsens 87% (kein Vokal)  ✓
09:28:20  Artist/Album/04 Song.flac  2/4: lrclib, genius │ [medium] en Whisper 48W unter Schwelle idf-jacc=0.041  =
09:28:20  Artist/Album/05 Song.flac  0/4: — │ kein Provider  =
09:28:20  Artist/Album/06 Song.flac  2/4: netease, genius │ [large-v3] de Whisper 0W kein Vokal  =
09:28:20  Artist/Album/07 Song.flac  2/4: lrclib, genius │ [medium] en Whisper 12W unter Schwelle idf-jacc=0.023  –
09:28:20  Artist/Album/08 Song.flac  0/4: — │ kein Provider  –
```
Ein Skip-Genre-Track (Hörbuch/Hörspiel/Instrumental/…) hat schon in Schritt `--abfragen` keine Provider-Kandidaten bekommen und zeigt sich hier deshalb nicht anders als „kein Provider" — die genauere Ursache steht nur noch in der Konsole von Schritt `--abfragen` selbst, nicht mehr im Datei-Ergebnis von Schritt `--schreiben`.

- **Modell**: `[medium]` (englische Songs) oder `[large-v3]` (alle anderen Sprachen), siehe „Whisper-Verifikation" oben
- **Sprache**: z.B. `de`, `en` — von `langdetect` erkannt, als Hint an Whisper übergeben
- **Wörter**: von Whisper transkribierte Wörter (Qualitätsindikator: 5W idf-jacc=0.31 ist unsicherer als 280W idf-jacc=0.31)
- **idf-jacc**: IDF-gewichteter Jaccard-Score (0,0–1,0, meist deutlich unter 0,5 — kleine Zahlen sind normal). Die Akzeptanz-Entscheidung selbst läuft über die kontrastive Marge (Score allein ist nicht direkt mit einer festen Schwelle vergleichbar, siehe oben).
- **Konsens**: kein Whisper nötig, Provider einig — bei `(kein Vokal)` hat `has_vocals` (aus dem Whisper-Pass) ausgelöst

---

#### Cache und Hilfs-Skripte

**Cache** (`.fetch_songtext.json` pro Ordner, Dateiname bewusst unverändert seit `fetch_songtext.py`): Ein Eintrag pro Track — Schritt `--schreiben` überspringt einen Track beim nächsten Lauf, wenn der Eintrag noch gültig ist.

**Parallele Instanzen:** Sowohl `songtext_pipeline.py --recursive` als auch mehrere gleichzeitig laufende `cut.py`-Sessions können bewusst gleichzeitig über dieselbe Bibliothek laufen. Jede Instanz sperrt sich beim Betreten eines Ordners (Schritt `--schreiben`) exklusiv über `.fetch_songtext.lock` (pro Ordner) — hält eine andere Instanz den Ordner bereits, wird er komplett übersprungen statt doppelt bearbeitet zu werden.

**Globale IDF-Tabelle für die kontrastive Marge:** Keine separate Datei — die Dokumentfrequenz je Wort wird bei jedem Lauf aus `texte.inhalt` der Cache-DB (`fetch_songtext_cache.db`) gebaut (`lyrics_core._global_cache_idf`), zusammen mit einem Hintergrund-Pool je Sprache (ein Provider-Treffer-Text pro Cache-Song, Sprache über denselben `langdetect`-Mechanismus erkannt). Schritt `--bewerten` baut Whisper-Modell(e) und diesen Kontext einmal pro Lauf auf (`lyrics_core._build_contrastive_context`), nicht pro Song. Whisper-Verifikation ohne offene Cache-DB ist nicht möglich.

**Cache-Modul** (`fetch_songtext_cache.db`, neben den Skripten; Design siehe `CACHE_DESIGN.md`): normalisiertes Schema — `songs` (ein Künstler/Titel = eine Zeile) verknüpft mit `ergebnisse` (ein Versuch je Provider, bis zu vier pro Song), `texte` (jeder Liedtext-Inhalt einmal, per Fingerabdruck dedupliziert) und `transkripte` (ein Whisper-Transkript je Song, gleiche Künstler+Titel-Identität wie `songs` — nicht an Datei/Modell/Parameter gebunden). Speichert erfolgreiche Provider-Antworten und Whisper-Transkripte, damit ein Neuaufbau der Bibliothek nach Code-Änderungen ohne erneute Provider-Abfragen/Whisper-Läufe möglich ist. Jeder Provider-Versuch wird festgehalten — auch ein Fehlschlag (`status="fehlschlag"` mit `fehlergrund`: `rate_limit`/`captcha`/`timeout`/`gesperrt`), aber ein Fehlschlag zählt nie als gültiger Cache-Treffer (immer erneuter Live-Versuch, außer über Schritt `--nachholen`, siehe oben). Eskaliert der Rate-Limit-Backoff eines Providers auf 5 Treffer in Folge (z.B. dauerhaft blockiertes Musixmatch-Captcha), wird er für 15 Minuten komplett übersprungen statt bei jedem Song erneut zu warten — der Fehlschlag wird dabei sofort (ohne Live-Versuch) mit `fehlergrund="gesperrt"` festgehalten. Provider-Treffer verfallen nach 30 Tagen.

**Felder je Eintrag** (JSON-Ordner-Cache, geschrieben von Schritt `--schreiben`):

| Feld | Werte | Bedeutung |
|------|-------|-----------|
| `v` | z.B. `"1.13.11"` | `lyrics_core.__version__` zum Zeitpunkt des Schreibens |
| `r` | `"ok"` / `"nf"` | Ergebnis: LRC vorhanden / nicht gefunden |
| `outcome` | `"write"` / `"none"` / `"delete"` | Datei-Aktion: geschrieben / nichts / gelöscht |
| `providers` | `0`–`4` | Anzahl Provider mit Treffer |
| `provider_names` | `["lrclib", "genius"]` | Namen der liefernden Provider |
| `method` | `"whisper-medium"` / `"whisper-large-v3"` / `"konsens"` / `"heuristik"` / `null` | Entscheidungsweg — Whisper-Modell je nach Sprache, siehe oben |
| `no_vocal` | `true` / `false` | Whisper-Pass hat keinen Gesang erkannt (bei `method=konsens`: Konsens trotzdem möglich) |
| `score` | `0.0`–`1.0` / `null` | Whisper-IDF-Jaccard oder Jaccard-Konsens |
| `reason` | `"kein-provider"` / `"kein-vokal"` / `"unter-schwelle"` / `"dauer-abweichung"` | Grund bei `r="nf"`. Skip-Genre-Tracks (Hörbuch/Hörspiel/Instrumental/…) landen hier ebenfalls als `"kein-provider"` — der Skip passiert schon in Schritt `--abfragen`, siehe „Ausgabe-Zeichen" oben. |
| `words` | `0`–`n` / `null` | Von Whisper transkribierte Wörter |
| `language` | `"de"` / `"en"` / … / `null` | Erkannte Sprache (Hint an Whisper) |
| `ts` | ISO-8601 | Zeitstempel des Laufs |

Beispiel-Einträge:

```json
"01 Song.flac": {
  "v": "1.13.11", "r": "ok", "outcome": "write",
  "providers": 2, "provider_names": ["lrclib", "genius"],
  "method": "whisper-large-v3", "no_vocal": false,
  "score": 0.62, "words": 265, "language": "de", "ts": "2026-07-09T09:28:20"
},
"02 Instrumental.flac": {
  "v": "1.13.11", "r": "nf", "outcome": "delete",
  "providers": 0, "provider_names": [],
  "method": null, "no_vocal": false,
  "score": null, "reason": "kein-provider", "words": null, "language": null, "ts": "2026-07-09T09:28:25"
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

**`db_analyse.py`** — Gegenstück zu `lrc_analyse.py`: wertet nicht den JSON-Ordner-Cache aus, sondern direkt die SQLite-Cache-DB (`fetch_songtext_cache.db`):

```bash
python3 db_analyse.py
python3 db_analyse.py --db /pfad/zu/anderer.db
```

Ausgabe: Treffer-/Nichts-/Fehlschlag-Quote je Anbieter inkl. Fehlschlag-Gründen, Songs ganz ohne Provider-Treffer, Songs mit allen 4 Providern fehlgeschlagen (Kandidaten für `--nachholen`), Whisper-Transkript-Abdeckung + Modell-Aufschlüsselung, Provider-Aktivität der letzten 24h/7 Tage.

**`whisper_analyse.py`** — zeigt speziell, ob und warum Whisper pro Track gelaufen ist (unabhängig von der Skriptversion des Cache-Eintrags):

```bash
python3 whisper_analyse.py /Musik/
```

**`inspect_song.py`** — Diagnose für einen einzelnen Song: schreibt Provider-Texte (Genius, Netease, Lrclib, Musixmatch) und das Whisper-Transkript aus der Cache-Datenbank nebeneinander in eine TXT-Datei:

```bash
python3 inspect_song.py --artist "Nina Hagen" --title "Naturträne"
python3 inspect_song.py --artist "Nina Hagen" --title "Naturträne" --output custom_name.txt
```

**`compare_whisper_models.py`** — rein manueller Qualitätsvergleich der Whisper-Modelle `small`/`medium`/`turbo` (kein automatisches Scoring): zieht Songs aus der Cache-Datenbank, die mindestens einen Provider-Treffer haben (ob bereits ein Whisper-Transkript existiert, ist für die Auswahl unerheblich — jeder Song wird ohnehin frisch transkribiert), sprachlich stratifiziert ~80 % englisch / ~20 % deutsch mit höchstens EINEM Song pro Künstler (verhindert, dass ein im Cache stark vertretener Künstler die Stichprobe dominiert), sucht die Audiodateien EINMALIG und gezielt in einem einzigen Bibliotheks-Durchlauf (bricht ab, sobald alle gesuchten Songs gefunden sind — kein Voll-Index der ganzen Bibliothek mehr) und transkribiert jeden gefundenen Song frisch mit allen drei Modellen:

```bash
python3 compare_whisper_models.py
python3 compare_whisper_models.py --n 10 --seed 42
python3 compare_whisper_models.py --library /Volumes/music/musik --output-dir whisper_modellvergleich
python3 compare_whisper_models.py --include "Kraftwerk:Autobahn" --include "Nina Hagen:Naturträne"
```

Die drei Modelle werden NACHEINANDER geladen (nicht gleichzeitig im Speicher — spart RAM, siehe ROADMAP.md): jedes Modell läuft einmal durch alle gefundenen Songs und wird danach wieder entladen, bevor das nächste Modell geladen wird. Die Sprache jedes stratifiziert ausgewählten Songs wird bereits bei der Auswahl aus den gecachten Provider-Kandidatentexten ermittelt (wie im Produktivbetrieb) und direkt als identischer Sprach-Hint an alle drei Modelle übergeben — für einen fairen Vergleich. Pflicht-Songs (siehe unten) bekommen ihren Sprach-Hint separat nach der Bibliotheksauflösung.

Pro Song eine TXT-Datei (`<Artist>_<Titel>_modellvergleich.txt`, Kopf mit `Sprache (Hint):`, Abschnitte `=== small ===`/`=== medium ===`/`=== turbo ===`) plus eine `modellvergleich_index.txt` mit bearbeiteten (inkl. Sprach-Kürzel je Song) und übersprungenen Songs. Songs ohne Bibliothekstreffer werden übersprungen; jeder Sprachpool enthält von vornherein Ersatzkandidaten, damit die 80/20-Quote auch dann noch stimmt, wenn ein Primärkandidat keinen Treffer hat (reicht ein Pool trotzdem nicht aus, läuft das Skript mit weniger als `--n` Songs weiter, kein Abbruch). "Nina Hagen Band"/"Rangehn" ist immer zusätzlich zur Auswahl dabei (Dedupe falls ohnehin gezogen); mit `--include ARTIST:TITEL` (wiederholbar) lassen sich weitere Pflicht-Songs ergänzen — fehlt deren Audiodatei in der Bibliothek, wird das klar in Konsole und Index-Datei vermerkt.

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
- `ffprobe` / `ffplay` / `ffmpeg` — Analyse, Playback, Crossfade, Normton, Normalisierung, DC-Offset, Kanalausgleich
- `sox` — Schneiden (`cut.py`), Pegelmessung für Kanalausgleich (`assemble.py`)
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
