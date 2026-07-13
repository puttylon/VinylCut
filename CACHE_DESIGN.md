# Cache-Modul — Design

> **Status:** implementiert (v1.9.0, Schema normalisiert seit v1.9.2). Dieses Dokument beschreibt den aktuellen Stand.

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
Pro Song bis zu **vier** Zeilen (`lrclib`/`musixmatch`/`netease`/`genius`), plus ggf. `lokal`. Jeder Versuch — auch ein Fehlschlag — hinterlässt eine Zeile; nichts bleibt spurlos.

| Spalte | Bedeutung |
|---|---|
| `song_id` | → `songs.id` |
| `quelle` | `lrclib` / `musixmatch` / `netease` / `genius` / **`lokal`** |
| `status` | `treffer` / `nichts` / **`fehlschlag`** |
| `fehlergrund` | bei `fehlschlag`: `"rate_limit"`, `"captcha"`, `"timeout"` |
| `fingerabdruck` | → Tabelle `texte` (nur bei `treffer`) |
| `datum` | Zeitpunkt des letzten Versuchs |

`UNIQUE(song_id, quelle)` — ein neuer Versuch überschreibt (Upsert) den alten Stand für diesen Provider; die Datenbank zeigt immer den **letzten** Versuch, nicht die volle Historie.

### 3. `texte` — jeder Liedtext genau EINMAL
Jeder Text wird unter dem **Fingerabdruck** (SHA-256 des Inhalts) gespeichert. Liefert ein Provider denselben Text wie die lokale Datei, haben beide **denselben Fingerabdruck** → der Text liegt nur einmal da (De-Duplizierung), verlinkt von beliebig vielen `ergebnisse`-Zeilen.

| Spalte | Bedeutung |
|---|---|
| `fingerabdruck` | Primärschlüssel |
| `inhalt` | der LRC-Text |

### 4. `transkripte` — Whisper-Ergebnis MIT Parametern
| Spalte | Bedeutung |
|---|---|
| `datei_kennung` | Pfad + Größe + Änderungsdatum (ändert sich die Datei → neu anhören) |
| `modell` | z. B. `small` |
| `parameter_key` | kanonischer Schlüssel aller ergebnisrelevanten Einstellungen (Fenster-Start, -Länge, Sprache, `beam_size`, `condition_on_previous_text`, …) |
| `transkript`, `no_speech_prob`, `avg_logprob` | das Gehörte + Kennzahlen |
| `datum` | Zeitpunkt |

Primärschlüssel: (`datei_kennung`, `modell`, `parameter_key`). **Wiederverwendung nur, wenn Datei UND Modell UND Parameter passen** — jede Änderung macht den Eintrag automatisch ungültig.

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
- Schalter: `--refresh-cache` UND `--force` erzwingen beide eine frische Live-Abfrage (umgehen den Provider-Cache vollständig — `--force` bedeutet „wirklich alles neu", nicht nur den alten Track-Speicher). `--cache-ttl <tage>` stellt die Gültigkeitsdauer ein. `--no-cache` ignoriert den Cache komplett (belegt zugleich das Grundprinzip).

## Lokale LRCs einlesen

Ein einmaliger Befehl liest **alle vorhandenen `.lrc`** der Bibliothek als Quelle `lokal` in den Cache ein (Inhalt via Fingerabdruck dedupliziert), damit schon der erste Neuaufbau profitiert.

## Normalisierung (Fallstrick vermeiden)

`künstler_key`/`titel_key` werden **exakt so** gebildet wie bei der echten Live-Abfrage: `unicodedata.normalize("NFC", …)`, klein geschrieben, gleiche Titel-Bereinigung (`_clean_query_title`). Sonst findet der Cache Treffer nicht wieder (vgl. früherer NFC/NFD-Bug).

## Mögliche Folge-Erweiterung: IDF-Tabelle als Abfallprodukt

Die IDF-Tabelle (`fetch_songtext_idf.json`) ist eine Wort-Häufigkeit über alle akzeptierten LRCs der Bibliothek. Sind die lokalen LRCs erst im Cache (Quelle `lokal`), lässt sich die IDF **direkt daraus** neu bauen — statt die `.lrc`-Dateien separat zu scannen. `--rebuild-idf` würde dann aus dem Cache lesen (eine Quelle der Wahrheit für Liedtext).

**Wichtige Feinheit:** Die Dokument-Häufigkeit muss **pro Song** gezählt werden — also über die `lokal`-Zeilen in `quelle` (eine je Song), NICHT über die deduplizierten `texte`-Blobs (das würde Doubletten unterzählen). Korpus = `lokal`-Einträge, damit die validierte 0,065-Schwelle erhalten bleibt (nicht plötzlich alle Provider-Texte mit reinnehmen). Reine Konsolidierung/DRY, kein Funktionsgewinn — daher **eigener Folge-Schritt nach dem Cache**, nicht Teil des ersten Baus.

## Grenzen (ehrlich)

- **Der allererste Durchlauf** wird nicht schneller — der Cache ist noch leer.
- Erst **Neuaufbauten** werden schnell — dann aber richtig (Anbieter *und* Whisper gespart).
- Gesamtgröße: deutlich **unter 1 GB**.
- **Empty-DB-Prinzip:** siehe ganz oben — der Cache ist immer optional und transparent.
