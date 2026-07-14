# Cache-Modul — Design

> **Status:** implementiert (v1.9.0, Schema normalisiert seit v1.9.2, `transkripte` auf Song-Identität umgestellt seit v1.9.3, `"lokal"` als fünfter Kandidat in `fetch_lrc()` seit v1.9.6, Löschung invalidiert `"lokal"` seit v1.9.7, Genre-Skip-Löschung invalidiert `"lokal"` ebenfalls seit v1.9.8, `cache_seed.py`-Qualitätsfilter auf pro-Track-Prüfung verschärft seit v1.9.9). Dieses Dokument beschreibt den aktuellen Stand.

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
- Schalter: `--refresh-cache` UND `--force` erzwingen beide eine frische Live-Abfrage (umgehen den Provider-Cache vollständig — `--force` bedeutet „wirklich alles neu", nicht nur den alten Track-Speicher). `--cache-ttl <tage>` stellt die Gültigkeitsdauer ein. `--no-cache` ignoriert den Cache komplett (belegt zugleich das Grundprinzip).

## Lokale LRCs einlesen

Ein einmaliger Befehl liest **alle vorhandenen `.lrc`** der Bibliothek als Quelle `lokal` in den Cache ein (Inhalt via Fingerabdruck dedupliziert), damit schon der erste Neuaufbau profitiert.

**Qualitätsfilter (pro Track, nicht pro Ordner — seit v1.9.9):** Eine `.lrc` wird nur eingelesen, wenn erstens neben ihr eine gleichnamige Audiodatei liegt (sonst fehlt die verlässliche Track-Identität) und zweitens GENAU DIESER Track (Schlüssel = Audiodateiname, NFC-normalisiert) in der `.fetch_songtext.json` desselben Ordners verzeichnet ist UND dort `"r": "ok"` trägt (Text gefunden und akzeptiert). Das bloße Vorhandensein irgendeiner `.fetch_songtext.json` im Ordner reicht nicht mehr — verzeichnet die Datei den Track gar nicht, oder mit `"r": "nf"`/`"r": "skip"`, wird übersprungen (mitgezählt, nicht verworfen). Nur Tracks, die nachweislich durch `fetch_songtext.py` liefen und dort als verifiziert gelten, sollen als vertrauenswürdige `"lokal"`-Quelle in den Cache. Das Laden der `.fetch_songtext.json` (inkl. NFC-Normalisierung/Kollisions-Handling) übernimmt die aus `fetch_songtext.py` importierte `_load_cache()` — keine eigene Implementierung.

## "lokal" als fünfter Kandidat in `fetch_lrc()` (seit v1.9.6)

Die Quelle `"lokal"` (per `cache_seed.py` eingelesen oder automatisch gepflegt, siehe unten) ist ein vollwertiger fünfter Kandidat in `fetch_lrc()` — aber **kein unabhängiger Provider**:

- Wird wie die 4 echten Provider auf inhaltliche Duplikate geprüft (`_dedupe_by_content`), aber mit **niedrigster Priorität**: wird `"lokal"` hinter die 4 echten Provider gehängt, gewinnt bei identischem Inhalt immer der echte Provider — `"lokal"` wird dann als Duplikat verworfen, nicht doppelt gezählt.
- Zählt **nicht** zum 3-von-4-Konsens (`_provider_consensus`) — nur die 4 echten Provider zählen dafür. `"lokal"` ist nur eine Erinnerung an einen früher akzeptierten Text, keine unabhängige Bestätigung.
- Überlebt `"lokal"` den Dedup (kein echter Provider hatte identischen Inhalt), landet es trotzdem zusätzlich in der Whisper-Vergleichsliste (`all_candidates`) — genau wie die vorhandene `.lrc` auf der Platte (`existing_lrc`).
- **Kein Freifahrtschein:** Liefert NUR `"lokal"` etwas (kein Provider antwortet), läuft der Song trotzdem ganz normal durch Whisper — `"lokal"` ist dann einziger Kandidat und kann bei Nichtübereinstimmung ganz normal verworfen werden.

**Automatische Rückkopplung:** Jedes Mal, wenn ein Song erfolgreich geschrieben/akzeptiert wird (Konsens oder Whisper-Treffer), schreibt `main()` den akzeptierten Inhalt zusätzlich als `"lokal"`-Treffer zurück in den Cache (`put_provider(..., "lokal", ...)`). So bleibt `"lokal"` dauerhaft aktuell, statt nur ein einmaliger `cache_seed.py`-Snapshot zu sein — nach Song-Identität (Künstler+Titel), bleibt also über Umbenennungen/Neuaufbauten hinweg gültig, analog zum Whisper-Transkript-Cache.

**Löschung invalidiert den Cache-Eintrag (seit v1.9.7):** Wird eine vorher vorhandene `.lrc` von `main()` gelöscht, weil der Song jetzt als "nicht gefunden" gilt, setzt `main()` den `"lokal"`-Eintrag zusätzlich auf `status="nichts"` (`put_provider(..., "lokal", ..., "nichts", None)`). Sonst würde ein soeben widerlegter Text über den `"lokal"`-Kandidaten in einem künftigen Lauf wieder auftauchen.

**Auch der Genre-Skip-Löschfall invalidiert (seit v1.9.8):** Wird eine vorher vorhandene `.lrc` gelöscht, weil das Genre keinen Songtext erwarten lässt (`_is_skip_genre`), gilt dieselbe Logik — `"lokal"` wird ebenfalls auf `status="nichts"` gesetzt. Ausgenommen bleibt bewusst der `no_tags`-Fall (fehlende Artist/Title-Tags): dort gibt es keine verlässliche Song-Identität für einen Cache-Schlüssel, daher kein Cache-Aufruf.

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
