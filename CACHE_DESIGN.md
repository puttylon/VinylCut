# Cache-Modul — Design

> **Status:** implementiert (v1.9.0, Schema normalisiert seit v1.9.2, `transkripte` auf Song-Identität umgestellt seit v1.9.3). Reiner Provider-Cache für die 4 echten Anbieter (`lrclib`/`musixmatch`/`netease`/`genius`) — dieses Dokument beschreibt den aktuellen Stand.
>
> **Verworfene Erweiterung (v1.9.6–v1.9.9, zurückgebaut in v1.9.10):** `"lokal"` als fünfter Kandidat (Cache-Erinnerung an zuletzt akzeptierte Songtexte, samt `cache_seed.py` zum Einlesen vorhandener `.lrc` und automatischer Rückkopplung/Invalidierung bei jeder Datei-Operation) wurde entwickelt, getestet und wieder entfernt. Begründung: In den meisten Fällen redundant zu bereits gecachten Provider-Treffern oder zum ohnehin vorhandenen `existing_lrc`-Vergleich; der schmale Zusatznutzen (Text nach Provider-TTL-Ablauf retten) rechtfertigte die Komplexität nicht — zwei echte Konsistenz-Bugs wurden bereits gefunden und geflickt, bevor diese Entscheidung fiel. In der echten Datenbank standen zum Rückbau-Zeitpunkt 0 `"lokal"`-Einträge (nie gegen die echte Bibliothek befüllt).

## Grundprinzip (das Wichtigste zuerst)

**Der Cache ist nur ein intelligenter Beschleuniger — kein Fundament.**
Das Programm funktioniert **jederzeit vollständig auch mit leerer oder fehlender Datenbank.**

- **Keine Datenbank / leere Datenbank** → jede Abfrage läuft live, genau wie heute. Nichts bricht.
- **Cache-Treffer** → die langsame Arbeit (Anbieter-Abfrage bzw. Whisper) wird nur **übersprungen**, das Ergebnis ist dasselbe.
- Der Cache **entscheidet nie** über Richtig/Falsch — er liefert nur schneller, was ohnehin herausgekommen wäre.

Diese Eigenschaft muss an jeder Stelle gelten: Fällt der Cache weg, verhält sich VinylCut exakt wie ohne Cache-Modul.

## Ziel

Anbieter-Antworten **und** das von Whisper Gehörte werden gespeichert, damit die Bibliothek nach Code-Änderungen **mehrfach neu aufgebaut** werden kann, ohne jedes Mal die Anbieter zu befragen oder neu anzuhören. Die 30-Tage-Auffrischung dient zugleich als „Verbesser-Rhythmus" (neue Anbieter-Texte kommen automatisch nach).

## Speicherort

Eine einzige **SQLite-Datei** neben dem Code: `fetch_songtext_cache.db` (gitignored, wie die IDF-Tabelle).
SQLite statt JSON, weil die parallelen `--fast`-Läufe **gleichzeitig** schreiben — SQLite (WAL-Modus, `busy_timeout`) verträgt das sicher, JSON würde sich gegenseitig überschreiben. Nachschlagen bei 20.000 × 4 Anbietern ist so ebenfalls schnell.

## Aufbau: vier normalisierte Tabellen

### 1. `songs` — die zentrale Entität: ein Song = eine Zeile
Jeder Künstler/Titel bekommt **genau eine** Zeile, unabhängig davon, wie viele Provider ihn kennen.

| Spalte | Bedeutung |
|---|---|
| `id` | Primärschlüssel (Autoincrement) |
| `artist_key`, `titel_key` | normalisiert (siehe *Normalisierung*), `UNIQUE(artist_key, titel_key)` |
| `genre` | optional, wird beim ersten Bekanntwerden gesetzt |

### 2. `ergebnisse` — ein Versuch pro (Song, Provider), IMMER festgehalten
Pro Song bis zu **vier** Zeilen (`lrclib`/`musixmatch`/`netease`/`genius`). Jeder Versuch — auch ein Fehlschlag — hinterlässt eine Zeile; nichts bleibt spurlos.

| Spalte | Bedeutung |
|---|---|
| `song_id` | → `songs.id` |
| `quelle` | `lrclib` / `musixmatch` / `netease` / `genius` |
| `status` | `treffer` / `nichts` / **`fehlschlag`** |
| `fehlergrund` | bei `fehlschlag`: `"rate_limit"`, `"captcha"`, `"timeout"` |
| `fingerabdruck` | → Tabelle `texte` (nur bei `treffer`) |
| `datum` | Zeitpunkt des letzten Versuchs |

`UNIQUE(song_id, quelle)` — ein neuer Versuch überschreibt (Upsert) den alten Stand für diesen Provider; die Datenbank zeigt immer den **letzten** Versuch, nicht die volle Historie.

### 3. `texte` — jeder Liedtext genau EINMAL
Jeder Text wird unter dem **Fingerabdruck** (SHA-256 des Inhalts) gespeichert. Liefern zwei Provider denselben Text (gespiegelte Datenbanken), haben beide **denselben Fingerabdruck** → der Text liegt nur einmal da (De-Duplizierung), verlinkt von beliebig vielen `ergebnisse`-Zeilen.

| Spalte | Bedeutung |
|---|---|
| `fingerabdruck` | Primärschlüssel |
| `inhalt` | der LRC-Text |

### 4. `transkripte` — EIN Whisper-Transkript je Song
Seit v1.9.3 an derselben Künstler+Titel-Identität wie `songs` hängend — nicht mehr an Datei/Modell/Parameter gebunden. Begründung: Path-Bindung invalidierte den Cache unnötig bei Umbenennungen/Verschiebungen der Audiodatei; Künstler+Titel ist stabiler und passt zum bereits bestehenden Prinzip der `songs`-Tabelle.

| Spalte | Bedeutung |
|---|---|
| `song_id` | Primärschlüssel, → `songs.id` |
| `transkript`, `no_speech_prob`, `avg_logprob` | das Gehörte + Kennzahlen |
| `modell` | z. B. `small` — **reine Info-Spalte, nicht Teil des Schlüssels** |
| `datum` | Zeitpunkt |

**Ein Song = EIN Transkript**, unabhängig von Modell oder Fenster-Parametern (Start, Länge, Sprache, `beam_size`, …) künftiger Aufrufe — die werden bei jedem Bedarf einfach wiederverwendet statt neu zu transkribieren. Nach Bereinigung der Klammer-Zusätze (`_clean_query_title`) teilen sich mehrere Versionen/Mixe desselben Songs (z. B. Dance Mix, Radio Version, Extended Mix) bewusst EIN gemeinsames Transkript — kein Bug.

In `_whisper_best` wird VOR der Fenster-Schleife geprüft, ob für den Song bereits ein Transkript vorliegt: bei Treffer wird die komplette Whisper-Verarbeitung für den Lauf übersprungen (nur die Vergleichslogik gegen die LRC-Kandidaten läuft weiter); bei Fehltreffer läuft die bestehende Fenster-Schleife wie bisher, und am Ende wird genau einmal das zum gewählten (oder — falls keiner akzeptiert wurde — bestverfügbaren) Kandidaten gehörende Transkript persistent gespeichert. Die Halluzinations-Erkennung (`_is_hallucination`) wird dabei immer frisch auf das (gecachte oder frische) Rohtranskript angewendet, damit sich ihre Schwellwerte künftig ändern können, ohne den Cache zu invalidieren.

**Migration (v1.9.2 → v1.9.3):** Beim ersten `open_cache()` nach dem Schema-Wechsel werden bestehende Zeilen im alten Format (`datei_kennung`/`modell`/`parameter_key`) automatisch übernommen — Künstler/Titel werden aus den Audio-Tags der (noch existierenden) Datei gelesen, nicht neu transkribiert. Die alte Tabelle bleibt als `transkripte_alt_v1`-Backup erhalten. Nicht migrierbare Zeilen (Datei fehlt oder Tags nicht lesbar) werden mit sichtbarer Warnung übersprungen, nie stillschweigend verworfen.

## Die drei Ausgänge einer Anbieter-Abfrage

| Ausgang | Wird festgehalten? |
|---|---|
| **Treffer** (Text bekommen) | ja, `status="treffer"`, mit Datum |
| **Wirklich nichts** (Anbieter antwortet, hat den Song nicht) | ja, `status="nichts"`, mit Datum |
| **Timeout / Rate-Limit / Captcha / Netzfehler** | ja, `status="fehlschlag"` **mit Grund** — aber **nie als gültiger Cache-Treffer** |

**Kein Ausgang bleibt unsichtbar.** Ein transienter Fehlschlag wird **festgehalten** (Grund: `rate_limit`/`captcha`/`timeout`), zählt aber beim Nachschlagen (`get_provider`) **nie** als brauchbares Ergebnis — der Aufrufer fragt beim nächsten Lauf automatisch wieder live. Damit ist sowohl sichtbar, *dass* und *warum* ein Versuch gescheitert ist, als auch sichergestellt, dass ein „geht gerade nicht" nie 30 Tage lang als „hat keinen Text" verwechselt wird.

## Auffrischung (TTL)

- Cache-Eintrag **jünger als 30 Tage** UND `status` ≠ `fehlschlag` → wird genutzt.
- **Älter, oder Fehlschlag** → gilt als nicht vorhanden → live neu holen (= automatischer „Verbesser-Rhythmus").
- Schalter: `--refresh-cache` UND `--force` erzwingen beide eine frische Live-Abfrage (umgehen den Provider-Cache vollständig — `--force` bedeutet „wirklich alles neu", nicht nur den alten Track-Speicher). `--cache-ttl <tage>` stellt die Gültigkeitsdauer ein. `--no-cache` ignoriert den Cache komplett (belegt zugleich das Grundprinzip). `--cache-only` ist das Gegenstück zu `--refresh-cache`/`--force`: statt eine frische Live-Abfrage zu erzwingen, verhindert es JEDE Live-Abfrage — auch für Provider, deren letzter Versuch als `status="fehlschlag"` im Cache steht (die sonst, s.o., nie als Treffer zählen und deshalb normalerweise sofort einen erneuten Live-Versuch auslösen).

## Lokaler LRCLib-Datenbank-Abzug (Beschleuniger VOR der lrclib-Live-Abfrage)

Seit v1.13.0 gibt es zusätzlich zur eigenen Cache-DB einen **externen, read-only geöffneten** Abzug der kompletten LRCLib-Datenbank (`/Volumes/music/db.sqlite3`, SMB-Netzlaufwerk, ca. 112 GB, per litestream repliziert, aktuell nicht mehr aktiv befüllt). `cache_store.lookup_lrclib_dump(conn, artist_key, title_key)` durchsucht ihn — als reiner Beschleuniger VOR einer echten Live-Abfrage bei der `lrclib`-Quelle, in `lyrics_core._query_provider` zwischen dem eigenen Cache-Lookup und dem `--cache-only`-Guard eingehängt. Das Grundprinzip gilt auch hier: fehlt der Abzug (Mount nicht vorhanden, Datei weg, sonstiger Öffnungsfehler), degradiert das Programm still auf reines Live-Fragen — kein Absturz, keine störende Meldung.

**Fremdes Schema, nur gelesen:** Anders als die eigene, selbst verwaltete Cache-DB ist der Abzug das **Original-LRCLib-Schema** (Tabellen `tracks`/`lyrics`, u. a. `tracks.artist_name_lower`/`tracks.name_lower` sowie `lyrics.has_synced_lyrics`/`has_plain_lyrics`/`synced_lyrics`/`plain_lyrics`, verknüpft über `tracks.last_lyrics_id`) — `cache_store.py` legt dafür nichts an und schreibt nie hinein, nur Lesezugriff.

**Exakter Text-Match, keine Dauer, keine Fuzzy-Ähnlichkeit:** Eine Recherche im echten `syncedlyrics`-Quellcode (`syncedlyrics/providers/lrclib.py`) zeigte, dass die echte Live-Suche bei lrclib selbst **keine Songdauer** zum Abgleichen nutzt, sondern nur Text-Ähnlichkeit von „Künstler - Titel". Der Abzug bildet das nach — aber bewusst noch einfacher: exakter Abgleich auf `artist_name_lower = ?` und `name_lower = ?` mit denselben normalisierten Schlüsseln wie beim eigenen Cache (`cache_store.normalize_key`), statt Fuzzy-Scoring nachzubauen.

**Mehrfachtreffer:** Mehrere Tracks zu exakt demselben Künstler+Titel sind normal (unterschiedliche Alben/Versionen, z. B. „queen"/„bohemian rhapsody" mit 4 Treffern). Ohne Dauer-Angabe und ohne Fuzzy-Matching wird pragmatisch ausgewählt: zuerst ein Track mit `synced_lyrics`, sonst mit `plain_lyrics`, sonst gilt „kein Songtext". Bei mehreren gleichwertigen Kandidaten gewinnt deterministisch die kleinste `tracks.id`.

**SMB-Falle:** `sqlite3.connect("file:...?mode=ro", uri=True)` scheitert auf dem SMB-Mount mit „unable to open database file" — SMB unterstützt die von SQLite fürs Locking benötigten Dateisperren nicht. Nötig ist zusätzlich `immutable=1` (`sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)`), das jegliches Locking überspringt — setzt voraus, dass sich die Datei während des Zugriffs nicht ändert (hier unproblematisch, der Abzug wird aktuell nicht mehr aktiv befüllt).

**Zusammenspiel mit den Cache-Schaltern:** ein Treffer im Abzug (mit oder ohne Songtext) wird genau wie ein Live-Treffer über `put_provider` in der eigenen Cache-DB abgelegt.
- `--no-cache` schaltet den Abzug **ebenfalls** ab (nicht nur den eigenen Cache) — konsistent zum Grundprinzip weiter oben: `_lrclib_dump_conn` wird dann gar nicht erst geöffnet.
- `--refresh-cache`/`--force` umgehen den Abzug genauso wie den eigenen Cache-Lookup — beide erzwingen eine wirklich frische Live-Abfrage.
- `--cache-only` nutzt den Abzug weiterhin: ein Treffer dort ist keine Live-Abfrage, sondern ein Nachschlagen in einer bereits vorhandenen Datei — das verbietet `--cache-only` nicht, es verbietet nur echte Netzanfragen.

## Normalisierung (Fallstrick vermeiden)

`künstler_key`/`titel_key` werden **exakt so** gebildet wie bei der echten Live-Abfrage: `unicodedata.normalize("NFC", …)`, klein geschrieben, gleiche Titel-Bereinigung (`_clean_query_title`). Sonst findet der Cache Treffer nicht wieder (vgl. früherer NFC/NFD-Bug).

## Mögliche Folge-Erweiterung: IDF-Tabelle als Abfallprodukt (obsolet)

**Hinfällig seit dem Rückbau der `"lokal"`-Quelle (v1.9.10):** Diese Idee setzte voraus, dass alle Bibliotheks-LRCs als Quelle `lokal` im Cache liegen (per `cache_seed.py`) — diesen Mechanismus gibt es nicht mehr. Eine künftige IDF-Neuberechnung aus dem Cache müsste auf einer anderen Grundlage aufsetzen (z. B. direktes Scannen der `.lrc`-Dateien, wie `--rebuild-idf` es heute schon tut). Abschnitt nur noch als historische Notiz erhalten.

## Grenzen (ehrlich)

- **Der allererste Durchlauf** wird nicht schneller — der Cache ist noch leer.
- Erst **Neuaufbauten** werden schnell — dann aber richtig (Anbieter *und* Whisper gespart).
- Gesamtgröße: deutlich **unter 1 GB**.
- **Empty-DB-Prinzip:** siehe ganz oben — der Cache ist immer optional und transparent.
