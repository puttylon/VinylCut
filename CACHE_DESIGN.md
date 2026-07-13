# Cache-Modul — Design

> **Status:** geplant, noch nicht implementiert. Dieses Dokument ist der abgestimmte Entwurf.

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

## Aufbau: drei Tabellen

### 1. `texte` — jeder Liedtext genau EINMAL
Jeder Text wird unter dem **Fingerabdruck** (Prüfsumme, z. B. SHA-256 des normalisierten Inhalts) gespeichert.
→ Liefert ein Anbieter denselben Text wie die lokale Datei, haben beide **denselben Fingerabdruck** → der Text liegt **nur einmal** da (De-Duplizierung). Man sieht sogar, *dass* Quellen identisch sind.

| Spalte | Bedeutung |
|---|---|
| `fingerabdruck` | Primärschlüssel (Hash des Inhalts) |
| `inhalt` | der LRC-Text |

### 2. `quelle` — wer hatte was (Anbieter UND lokal)
Eine Zeile pro Abfrage; zeigt nur auf den Fingerabdruck, nicht auf den ganzen Text.

| Spalte | Bedeutung |
|---|---|
| `quelle` | `lrclib` / `musixmatch` / `netease` / `genius` / **`lokal`** |
| `künstler_key`, `titel_key` | normalisiert (siehe *Normalisierung*) |
| `status` | `treffer` oder `nichts` |
| `fingerabdruck` | → Tabelle `texte` (leer bei `nichts`) |
| `datum` | Zeitpunkt des Holens |

Primärschlüssel: (`quelle`, `künstler_key`, `titel_key`).
**„Lokal" ist einfach eine weitere Quelle.** Vorhandene `.lrc` werden als Quelle `lokal` eingelesen; der Inhalt landet über den Fingerabdruck automatisch mit den Anbietern zusammen.

### 3. `gehört` — Whisper-Transkript MIT Parametern
| Spalte | Bedeutung |
|---|---|
| `datei_kennung` | Pfad + Größe + Änderungsdatum (ändert sich die Datei → neu anhören) |
| `modell` | z. B. `small` |
| `parameter_key` | kanonischer Schlüssel aller ergebnisrelevanten Einstellungen (Fenster-Start, Fenster-Länge, Sprache, `beam_size`, `condition_on_previous_text`, VAD an/aus, …) |
| `transkript`, `no_speech_prob`, `avg_logprob` | das Gehörte + die Kennzahlen |
| `datum` | Zeitpunkt |

Primärschlüssel: (`datei_kennung`, `modell`, `parameter_key`).
**Wiederverwendung nur, wenn Datei UND Modell UND Parameter passen.** Jede Einstellungs- oder Datei-Änderung macht den Eintrag automatisch ungültig → es wird neu angehört. Damit sind Ergebnisse sauber vergleichbar (auch A/B-Tests: gleiche Datei, andere Parameter = zwei getrennte Einträge).

## Die drei Ausgänge einer Anbieter-Abfrage

| Ausgang | Wird gecacht? |
|---|---|
| **Treffer** (Text bekommen) | ja, mit Datum |
| **Wirklich nichts** (Anbieter antwortet, hat den Song nicht) | ja, als `nichts`, mit Datum |
| **Timeout / Rate-Limit / Captcha / Netzfehler** | **NEIN** — keine Antwort, nächstes Mal neu fragen |

Kritisch: Ein transienter Fehlschlag darf **nicht** als „nichts" gespeichert werden — sonst würden während eines großen (gedrosselten) Laufs tausende Songs 30 Tage lang fälschlich als „hat keinen Text" abgestempelt. Die Drosselungs-/Captcha-Signale erkennt das Programm bereits (seit v1.7.3), die Unterscheidung ist machbar.

## Auffrischung (TTL)

- Cache-Eintrag **jünger als 30 Tage** → wird genutzt.
- **Älter** → gilt als nicht vorhanden → live neu holen (= automatischer „Verbesser-Rhythmus").
- Schalter: `--refresh-cache` (frisch erzwingen), `--cache-ttl <tage>` (Wert einstellbar), `--no-cache` (Cache komplett ignorieren — belegt zugleich das Grundprinzip).

## Lokale LRCs einlesen

Ein einmaliger Befehl liest **alle vorhandenen `.lrc`** der Bibliothek als Quelle `lokal` in den Cache ein (Inhalt via Fingerabdruck dedupliziert), damit schon der erste Neuaufbau profitiert.

## Normalisierung (Fallstrick vermeiden)

`künstler_key`/`titel_key` werden **exakt so** gebildet wie bei der echten Live-Abfrage: `unicodedata.normalize("NFC", …)`, klein geschrieben, gleiche Titel-Bereinigung (`_clean_query_title`). Sonst findet der Cache Treffer nicht wieder (vgl. früherer NFC/NFD-Bug).

## Grenzen (ehrlich)

- **Der allererste Durchlauf** wird nicht schneller — der Cache ist noch leer.
- Erst **Neuaufbauten** werden schnell — dann aber richtig (Anbieter *und* Whisper gespart).
- Gesamtgröße: deutlich **unter 1 GB**.
- **Empty-DB-Prinzip:** siehe ganz oben — der Cache ist immer optional und transparent.
