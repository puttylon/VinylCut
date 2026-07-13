# VinylCut Roadmap

## ✓ fetch_songtext.py v1.8.0 — `--fast`: Zwei-Phasen-Workflow

Neues Flag `--fast` für einen schnellen ersten Lauf (Phase 1), der nur die
Fälle erledigt, die kein Whisper brauchen, und alles Whisper-Bedürftige offen
lässt, damit ein späterer normaler Lauf (Phase 2) genau diese Lücken füllt.

Pro Track: 3+ Provider-Konsens und „kein Provider" laufen unverändert (dort
wird ohnehin nie Whisper gebraucht). Der Fall, in dem im Normalmodus jetzt
Whisper anliefe (Konsens verfehlt, Audiodatei vorhanden), wird stattdessen
**aufgeschoben** — kein Whisper, keine Dauer-Heuristik-Vermutung, **kein
Cache-Eintrag**, vorhandene `.lrc` bleibt unangetastet. Weil aufgeschobene
Tracks keinen Cache-Eintrag bekommen, verarbeitet sie ein normaler Lauf ohne
`--fast` automatisch als „ungesehen".

Anders als `--no-whisper`: dort würde im Whisper-Fall geraten (2-Provider-
Konsens/Dauer-Heuristik) und das Ergebnis als erledigt gecacht — `--fast`
vermeidet genau das bewusst, um die Lücke für Phase 2 offen zu lassen.

Umsetzung: `fetch_lrc()` bekommt Parameter `fast: bool`. Direkt an der
Stelle, wo im Normalpfad Whisper anliefe (`elif flac_path and
flac_path.exists():`), wird bei `fast=True` vorher abgezweigt und ein
Sentinel-Ergebnis (`found=False`, `extras["deferred"] = True`, `reason:
"deferred-whisper"`) zurückgegeben — ohne `_whisper_best`/`_transcribe`
aufzurufen. `main()` erkennt `extras["deferred"]`, schreibt keinen
Cache-Eintrag, fasst die vorhandene `.lrc` nicht an und zählt den Track in
einer eigenen Statistik (`deferred`), die auch die Zusammenfassung am
Laufende ausweist (`"N aufgeschoben für Whisper"`). Datei-Symbol dabei strikt
`=` (nichts angefasst) — die „aufgeschoben"-Info steht im Methoden-Teil nach
`│`, nicht im Symbol.

Whisper-Modell und IDF-Tabelle werden mit `--fast` gar nicht erst geladen
(spart die Ladezeit) — sie werden in diesem Modus nie gebraucht.

## ✓ fetch_songtext.py v1.7.9 — IDF-Tabelle sichtbar benannt

`.fetch_songtext_idf.json` (versteckt) → `fetch_songtext_idf.json` (sichtbar,
führender Punkt entfernt). Grund: Nutzer will die 3,5-MB-Tabelle im
Projektordner sehen können, ohne versteckte Dateien einzublenden.
`_IDF_CACHE_PATH` sowie alle Erwähnungen in `.gitignore`, README.md und
ROADMAP.md entsprechend angepasst.

## ✓ fetch_songtext.py v1.7.7 — Whisper-Matching: Containment durch IDF-Jaccard ersetzt (Bug 2)

Bug 2 (Provider-Fehlmatch): Die Whisper-Verifikation akzeptierte gelegentlich
den falschen Songtext, weil das Ähnlichkeitsmaß **Containment**
(`|Whisper ∩ LRC| ÷ |Whisper|`, Schwelle 40 %) jedes Wort gleich gewichtete —
ein paar zufällig übereinstimmende Stopwords (the/a/and/…) reichten, um über
die Schwelle zu kommen, auch wenn der eigentliche Songtext nicht passte.

Fix: Umstellung auf **IDF-gewichtetes Jaccard** (`_idf_jaccard`). Seltene,
inhaltstragende Wörter zählen stark, häufige Wörter kaum — Fehlmatches durch
zufällige Stopword-Überschneidungen werden dadurch verhindert. Die IDF-Werte
(Dokumentfrequenz je Wort über die ganze Bibliothek) liegen in
`.fetch_songtext_idf.json` (seit v1.7.9 sichtbar: `fetch_songtext_idf.json`)
neben dem Skript (nicht im Bibliotheks-Wurzelordner, damit auch lokale Läufe
ohne Netzwerk-Mount eine Tabelle haben) und werden per neuem Flag
`--rebuild-idf <bibliothekspfad>` aufgebaut.

Die neue Schwelle **0,065** wurde an 20 gelabelten Songs über 5 Sprachen
validiert (11 aus einer ersten, 9 aus einer zweiten Stichprobe): niedrigster
korrekter IDF-Jaccard-Wert 0,089, höchster falscher Wert 0,053 — Reserve nach
beiden Seiten. Die Produktionsfunktion `_idf_jaccard` trennt damit alle 20
Fälle korrekt (20/20).

`_containment` wurde entfernt (nur an dieser einen Stelle verwendet). Die
Log-Ausgabe zeigt den Score jetzt als `idf-jacc=0.XXX` (3 Nachkommastellen)
statt als Prozentwert — bei einer Schwelle von 0,065 wäre `0%`/`1%` kaum
unterscheidbar gewesen. Provider-Konsens (`_word_overlap`, unverändert)
bleibt weiterhin als Prozentwert dargestellt.

## ✓ fetch_songtext.py v1.7.6 — Klammer-Zusätze aus Suchtitel entfernt

Live beobachtet (Deep Purple – "Made In Japan [Deluxe Edition 2014 Remix]"):
Live-/Deluxe-Alben liefern reihenweise `0/4: — │ kein Provider`, obwohl der
Songtext (identisch zur Studio-Version) längst existiert. Ursache: Der
Such-Query wurde 1:1 aus dem Title-Tag gebaut, inkl. Zusätzen wie
`"Highway Star (Live In Osaka Japan 16th August 1972) (2014 Remix)"` — die
Lyrics-Provider indizieren aber nur den Kern-Titel `"Highway Star"` und liefern
auf den langen String keinen Treffer.

Fix: `_clean_query_title()` entfernt alle `(...)`/`[...]`-Zusätze pauschal aus
dem Titel, bevor der Such-Query gebaut wird (`query = f"{artist} {_clean_query_title(title)}"`).
Title-Tag, Dateiname und die gespeicherte `.lrc` bleiben unverändert — nur der
Suchbegriff wird bereinigt. Fällt bei einem rein aus Klammern bestehenden
Titel auf den Original-Titel zurück (leerer Query wäre sinnlos).

Bewusst pauschal statt Schlüsselwort-Liste (Live/Remix/Remaster/…): eine Liste
müsste bei jedem neuen Zusatz in der Bibliothek nachgepflegt werden, das
pauschale Entfernen deckt auch unbekannte künftige Schreibweisen ab. Risiko
(Titel, bei denen die Klammer Teil des eigentlichen Songnamens ist, z.B.
"I Want You (She's So Heavy)") wird als vernachlässigbar eingestuft, da die
Lyrics ohnehin identisch zur ungeklammerten Kurzform sind.

Bereits im Cache stehende `kein-provider`-Treffer werden NICHT automatisch neu
geprüft — stattdessen wurden die betroffenen Cache-Einträge einmalig aus den
`.fetch_songtext.json`-Dateien in der Bibliothek gelöscht, sodass nur die
tatsächlich betroffenen Tracks beim nächsten Lauf neu verarbeitet werden.

## ✓ fetch_songtext.py v1.7.5 — Ordner-Claim für bewusst parallele Instanzen

Live beobachtet: Zwei bewusst parallel gestartete `fetch_songtext.py -r`-Instanzen
über dieselbe Bibliothek standen beide zur selben Sekunde im selben Album
(`C/CocoRosie/Tales of a GrassWidow`) — die DFS-Traversal ist deterministisch
sortiert, ohne Koordination laufen beide Instanzen praktisch im Gleichschritt
und machen jeden Track doppelt (Provider-Abfragen + Whisper-Transkription).

Der `_save_cache`-Lock aus v1.7.3 schützt nur den finalen JSON-Schreibvorgang
vor Lost-Updates — verhindert aber nicht, dass beide Instanzen denselben Track
überhaupt erst bearbeiten. Der Rate-Limit-Backoff aus v1.7.3 ist zudem reiner
In-Memory-Zustand pro Prozess und koordiniert nichts zwischen zwei OS-Prozessen.

Fix: Beim Betreten eines neuen Ordners versucht die Instanz, `.fetch_songtext.lock`
exklusiv und non-blocking zu sperren (`_try_claim_folder`). Gelingt das nicht
(andere Instanz hält die Sperre bereits), wird der komplette Ordner ohne jede
Track-Bearbeitung übersprungen. Die Sperre wird für die gesamte Bearbeitungszeit
des Ordners gehalten und erst beim Wechsel zum nächsten Ordner freigegeben.

Fail-open statt fail-closed: Ein `OSError` beim Locken wird nur dann als "andere
Instanz hält den Ordner" gewertet, wenn `errno` tatsächlich `EAGAIN`/`EWOULDBLOCK`
ist. Jeder andere Fehler (z.B. `ENOTSUP` auf Netzwerk-Mounts ohne flock-Support)
führt stattdessen zu unkoordiniertem Weiterarbeiten — sonst würden beide
Instanzen bei jedem Locking-Aussetzer denselben Ordner überspringen und im
Extremfall die ganze Bibliothek still auslassen.

Mit Opus review-gegengeprüft und auf dem echten SMB-Mount (`/Volumes/music`,
Synology via smbfs) empirisch verifiziert: `flock` zwischen zwei echten
Prozessen funktioniert dort korrekt (Prozess B wird sauber blockiert, solange
A die Sperre hält). Kuriosität am Rande, ohne Auswirkung auf diesen Code: zwei
Filehandles *im selben Prozess* auf dieselbe Datei blockieren sich auf diesem
SMB-Mount nicht gegenseitig (lokal auf `/tmp` schon) — betrifft uns nicht, da
nie zwei Handles gleichzeitig im selben Prozess offen sind.

## ✓ fetch_songtext.py v1.7.4 — Whisper-Hänger durch condition_on_previous_text=False behoben

Live beobachtet: Ein Track (Yazoo – "I Before E Except After C") hing bei der
Whisper-Verifikation ~21 Minuten statt normal ~40-100s.

Drei Varianten durchprobiert, mit echten Messungen statt Vermutung:

1. **`temperature=0.0`** (kein Fallback-Loop) — behob den Hänger, führte aber
   live zu einer echten Fehlklassifikation: ein Track mit klaren Vocals
   (Jimmy Somerville, "I Feel Love-Johnny Remember Me") geriet in eine
   Wiederholungsschleife ("love to love you baby" × 50+) und wurde fälschlich
   als "kein Vokal" verworfen — die alte Fallback-Liste hätte diese Schleife
   nachweislich nach 1-2 Versuchen verlassen. Verworfen.
2. **`temperature=[0.0, 0.4]`** (Kompromiss) — instabil: zwei Läufe mit
   identischen Parametern auf demselben Track lieferten unterschiedliche
   Ergebnisse (einmal bestanden, einmal durchgefallen), da Sampling bei
   Temperatur > 0 echten Zufall in die Dekodierung bringt. Verworfen.
3. **`condition_on_previous_text=False`, temperature unverändert Standard**
   — isoliert getestet gegen beide Problem-Tracks: der Yazoo-Hänger lief in
   160s durch (statt >21 Min.), der Jimmy-Somerville-Track bestand über zwei
   Läufe hinweg stabil (kein Flip-Flop mehr). **Umgesetzt.**

Vermutlicher Mechanismus: nicht die Temperatur-Fallback-Liste selbst war das
Problem, sondern dass sich ein einzelnes schlechtes Segment über
`condition_on_previous_text=True` auf alle folgenden Segmente fortpflanzt
und so den Hänger über den gesamten Kontext hinweg verstärkt.

Nebenbefund beim Testen (separates, unabhängiges Problem — als Bug 2 in
v1.7.7 oben behoben): Bei besagtem Jimmy-Somerville-Track fand Musixmatch den
falschen Song ("You Make Me Feel Mighty Real" von Sylvester statt der echten
Medley-Lyrics) — der Containment-Score lag trotzdem knapp über der Akzeptanz-
schwelle (41,7%), nur durch geteiltes generisches Pop-Vokabular ("love",
"you", "feel"). Whisper selbst transkribierte den echten Song-Inhalt
("Johnny remember me" etc.) korrekt — das Whisper-Ergebnis war nicht die
Ursache. Bisher durch den jetzt behobenen "kein Vokal"-Bug verdeckt.

## ✓ fetch_songtext.py v1.7.3 — Rate-Limit-Backoff pro Provider

Frage: Bekommt man Probleme mit den Lyrics-Providern bei mehreren parallelen
Instanzen? Im syncedlyrics-Quellcode direkt nachgesehen (nicht geraten),
mit Opus die Design-Optionen abgewogen. Ergebnis: **stark asymmetrisch**.

- **Musixmatch**: meldet Rate-Limits über einen im JSON eingebetteten
  `status_code` — geloggt als `"Got status code N for ..."` auf stderr.
  402 = Kontingent/Rate-Limit, 401 = Captcha/Anti-Bot-Reaktion.
- **NetEase**: kein explizites Signal, aber eine generische Exception bei
  unerwarteter (Block-)Antwort, geloggt über `logger.error(...)`.
- **Genius und lrclib**: geben laut Quellcode bei JEDEM HTTP-Fehler
  (inkl. 429) **kein Signal** — `if not r.ok: return None`, still, nicht
  von "nicht gefunden" unterscheidbar. Ein Rate-Limit sieht dort identisch
  aus wie ein echtes Fehlen der Lyrics — stille False Negatives.

Fix: `_query_provider()` wartet jetzt vor jeder Anfrage auf eine ggf.
bestehende Sperre (`_rate_limit_wait`) und wertet stderr danach aus
(`_rate_limit_report`), pro Provider in einem Lock-geschützten
In-Memory-State (`next_allowed`, `consecutive_hits`, `time.monotonic()`).

- 402/generischer Fehler → Basis-Backoff 10s (verankert an syncedlyrics'
  eigenem `time.sleep(10)` beim Musixmatch-Token-Refresh nach 401),
  eskalierend bei Wiederholung, Obergrenze 60s.
- 401/Captcha → 30s Basis (kurzer Retry hilft bei Anti-Bot nicht).
- Genius/lrclib (kein Signal möglich) → proaktiver Mindestabstand von 1,5s
  zwischen Anfragen greift immer, auch bei sauberem Erfolg.
- Sleep passiert im Worker-Thread selbst (nicht vor `submit()`), damit die
  anderen 3 Provider nicht mitgebremst werden. `returncode` ist als Signal
  unbrauchbar (0 sowohl bei "nicht gefunden" als auch bei Rate-Limit) —
  ausschließlich stderr-Parsing zählt.

`result.stdout`/`result.stderr` wurden bisher komplett verworfen — werden
jetzt für die Auswertung gebraucht und mitgelesen.

## ✓ fetch_songtext.py v1.7.2 — Unicode-Normalisierung der Cache-Schlüssel (NFC/NFD)

Bug gemeldet: Ein Track, der bereits mit v1.7.1 verarbeitet worden war,
wurde bei einem erneuten `--recursive`-Lauf trotzdem nochmal geprüft — und
danach stand der Eintrag doppelt in der `.fetch_songtext.json`. Kein
Versions-Mismatch (alle Einträge waren bereits `v=1.7.1`), sondern Unicode:
Dateien wurden zuerst lokal geschrieben, dann auf die SMB-NAS gespielt.
macOS/SMB liefert Dateinamen mit ä/ö/ü dabei nicht garantiert in derselben
Normalisierungsform zurück — NFC (ü als ein Zeichen) vs. NFD (u + separater
Kombinationsakzent). Zwei Strings, die identisch aussehen, aber
byte-verschieden sind — für `dict`/JSON-Keys zwei komplett getrennte
Einträge. Der Cache-Lookup fand den alten Eintrag nicht, verarbeitete den
Track neu und schrieb einen zweiten Eintrag daneben.

Fix: `_load_cache()` normalisiert alle geladenen Schlüssel auf NFC; bei
einer Kollision (gleicher Name, unterschiedliche Normalisierung) gewinnt der
Eintrag mit dem neueren `"ts"`. Der Cache-Schlüssel im Hauptlauf wird
ebenfalls vorab auf NFC normalisiert. Von Opus im Review zusätzlich
gefunden: derselbe Bug lauerte in `_load_release()` beim Abgleich von
Tracktiteln aus `release.json` gegen den Dateinamen-Stem — ebenfalls
gefixt.

Da alte Cache-Einträge dadurch nicht ungültig werden (nur die Schlüssel
werden vereinheitlicht), bleibt `_CACHE_MIN_VERSION` unverändert — kein
erzwungenes Neu-Prüfen der ganzen Bibliothek nötig.

Zusätzlich: neues Skript `normalize_cache.py` bereinigt bereits bestehende
`.fetch_songtext.json`-Dateien proaktiv (Vorschau standardmäßig, `--apply`
zum Schreiben) statt nur lazy beim nächsten Zugriff auf den jeweiligen
Ordner. Einmal über die komplette Bibliothek (`/Volumes/music/musik`)
gelaufen: 3 Duplikate in 2 Ordnern gefunden und bereinigt (beide Betterov-
Alben), sonst überall sauber.

## ✓ cut.py v1.9.17 — Minimum für `p<Sek>` auf 2s gesenkt

`_MIN_PREVIEW_SEC` von 3s auf 2s. Grenzen jetzt 2–30s statt 3–30s.

## ✓ cut.py v1.9.16 — Cover-Download läuft im Hintergrund

Unerklärlicher „Hänger" direkt nach dem Akzeptieren der Metadaten
(`[Enter] Akzeptieren...`) gemeldet und aufgeklärt: Nach der Annahme lud
`run_metadata_search()` synchron das Cover — dabei wurden **alle** gefundenen
Kandidaten (bis zu 20) neu nach Vinyl/Popularität sortiert und der Reihe
nach probiert, bis einer klappt, mit 20s Timeout **pro Versuch** und ohne
jede Bildschirm-Rückmeldung während der Schleife. Ein einzelner langsamer/
kaputter Discogs-Bild-Link (mit echten Daten verifiziert: ein einzelner
Cover-Download brauchte teils 3s, teils 12s) reichte für einen spürbaren,
unerklärten Stillstand.

Fix: `_download_cover()` läuft jetzt in einem Hintergrund-Thread
(`threading.Thread(daemon=True)`), gestartet direkt nach der Metadaten-
Annahme — der interaktive Ablauf blockiert nicht mehr. Kurz vor dem Export
wird der Thread mit `join(timeout=20)` eingeholt, damit das Cover beim
FLAC-Tagging sicher bereit ist (im Normalfall längst der Fall, da das
Schneiden aller Tracks meist mehrere Minuten dauert). `run_metadata_search()`
gibt jetzt `(data, cover_thread)` zurück statt nur `data`.

Mit echtem Download-Thread verifiziert: Thread startet in 0.000s, Programm
blockiert nicht — der eigentliche Download lief im Hintergrund weiter
(einmal sogar 12,3s), unbemerkt statt als Hänger.

## ✓ cut.py v1.9.15 — Fehlende Tracklängen einzeln über MB-Recordings auffüllen

Vinyl-Releases bei Discogs/MB haben oft gar keine Tracklängen katalogisiert.
Bisher: `?:??` in der Anzeige, keine Schätzungshilfe beim Schneiden. Die
echte Länge existiert aber fast immer anderswo (CD/Digital-Ausgabe,
MusicBrainz-Recording).

Neue Funktion `fill_missing_durations()` in `fetch_metadata.py`: sucht pro
fehlendem Track einzeln in MusicBrainz' Recording-Datenbank (die Aufnahme
selbst, releaseübergreifend — anders als der bestehende releaseweite
MB-Fallback). Funktioniert dadurch auch bei Compilations/Samplern, wo
einzelne Tracks von unterschiedlichen Original-Aufnahmen stammen. Titel/
Reihenfolge der Tracklist bleiben unverändert, nur `dur_s` wird bei
zuverlässigem Titel-Match ergänzt.

Zwei Design-Entscheidungen kamen aus Opus-Konsultationen zustande:
1. **Quellwahl** (vor der Implementierung): MB-Recording-Suche statt
   externer Zusatz-API (z.B. iTunes) — nutzt bestehende Infrastruktur,
   bessere Abdeckung bei nicht-mainstream/internationalem Material.
2. **Median statt erster Treffer** (nach Code-Review, mit echtem Bug
   gefunden: „Bohemian Rhapsody" landete beim ersten Treffer bei 157s statt
   ~355s — MusicBrainz' „score"-Feld unterscheidet nicht zwischen Edit/Live/
   Studio-Varianten, alle Top-Treffer hatten Score 100). Fix: `limit=25`
   statt 5, Median über alle titel-passenden Ergebnisse statt des ersten —
   robust gegen einzelne kurze/lange Ausreißer.

Zusätzlich: Anführungszeichen im Titel (z.B. `She Said "Yes"`) werden jetzt
in der Lucene-Query escaped, hätten sonst die Suche unbemerkt kaputt gemacht.

12 Tests in `test_fetch_metadata.py` (davon 6 neu für `fill_missing_durations`,
inkl. Regressionstest für den Median-Bug). In beide Aufrufer verdrahtet
(`fetch_metadata.py main()` und `cut.py run_metadata_search()`), läuft
unconditional nach der bestehenden Release-Suche, unabhängig vom Pfad
(Discogs-Treffer oder direkter MB-Fallback).

## ✓ cut.py v1.9.14 — Absturz bei MusicBrainz-Tracks ohne Länge behoben

`KeyError: 'dur_s'` in `fetch_metadata.score_release()`, ausgelöst über den
MusicBrainz-Fallback in `run_metadata_search()` (greift wenn Discogs keine
Tracklängen liefert). Ursache: `fetch_musicbrainz_by_id()` setzt den Key
`dur_s` nur, wenn MB tatsächlich eine `length` liefert — fehlt sie, fehlt der
Key komplett (nicht `None`). Discogs-Tracks haben den Key dagegen immer
(`fetch_discogs_by_id()` setzt ihn unconditional). `score_release()` griff
mit `t["dur_s"]` direkt zu statt mit `.get()` — Fix ist eine Zeile.

Neue Testdatei `test_fetch_metadata.py` (bisher keine vorhanden) mit 4 Tests
für `score_release()`, inkl. Regressionstest für den fehlenden Key.

## ✓ cut.py v1.9.13 — „Länge"-Spalte bei zweistelligen Minuten nicht mehr abgeschnitten

`width=7` reichte für „M:SS.SS" (7 Zeichen, z.B. `7:40.00`), aber ab 10
Minuten wird's „MM:SS.SS" (8 Zeichen, z.B. `11:10.00`) — wurde mit „…"
abgeschnitten und war unlesbar (Jean-Michel Jarre „Oxygène (Part V)",
11:10). Betraf beide Track-Tabellen (Metadaten-Vorschau und Haupt-Panel).
Spaltenbreite auf 8 erhöht, 2 neue Tests.

## ✓ cut.py v1.9.12 — Standard-Antwort bei j/n-Fragen erkennbar, besserer Kontrast

Zwei UI-Verbesserungen (Screenshot-Feedback: helles Terminal-Theme, grauer
Text auf Weiß kaum lesbar):

- Alle vier `[j/n]`-Abfragen (Metadaten übernehmen, Fortsetzen, Songtexte
  suchen) zeigen jetzt `[j/N]` — großes N markiert die Standard-Antwort bei
  bloßem Enter (leere Eingabe zählt überall als „nein", das war schon vorher
  so, nur nicht sichtbar).
- `style="dim"` (Rich-ANSI-„faint"-Attribut, Helligkeit hängt vom Terminal
  ab) in `cut_ui.py` durchgängig durch `style="grey35"` ersetzt — eine feste,
  terminal-unabhängige Farbe mit deutlich besserem Kontrast auf hellem
  Hintergrund. `"dim yellow"` (Pending-Symbol ○) bewusst unverändert
  gelassen, das ist eine andere Farbsemantik.

## ✓ cut.py v1.9.11 — Absturz bei inkonsistentem progress.json/release.json behoben

Realer Absturz beim Fortsetzen einer Session: `IndexError: list index out of
range` in `estimate_start()`, weil `progress.json` mehr bestätigte Tracks
(15) enthielt als `release.json` aktuell Tracks hat (11) — release.json und
progress.json werden unabhängig geladen und können auseinanderlaufen (z.B.
wenn zwischen zwei Läufen ein anderer Metadaten-Kandidat gewählt wurde).

Zwei Fixes, root cause + Absicherung:

1. **Root cause behoben:** `run_metadata_search()` löscht `progress.json`
   automatisch, sobald eine frische Metadaten-Suche stattfindet (keine
   release.json vorhanden, oder „Gespeicherte Metadaten verwenden?" mit „n"
   beantwortet). Danach gibt es keine alten JSON-Daten mehr, die zu den neuen
   Metadaten passen müssten — einfach die neuen Suchergebnisse nutzen.
2. **Absicherung:** Der `i`-Index (Anzahl bestätigter Tracks) wird beim
   Aufbau des Fortsetzen-Vorschau-Panels konsequent auf `min(i, n)` bzw.
   `min(i, n-1)` geklammert, statt roh in `estimate_start()`/
   `build_cutting_panel()` zu laufen. Ist `i > n` trotzdem noch der Fall
   (z.B. bei manuell bearbeiteten Dateien), wird `progress.json` beim
   Fortsetzen-Prompt unconditional verworfen (`ans != "j" or i > n"`), auch
   bei versehentlichem „j".

Von einer Opus-Instanz gegengeprüft: Absicherung vollständig (kein
Absturzpfad übersehen), keine Regression in den vorherigen Fixes (v1.9.7–
v1.9.10). Ein Restfall bleibt bewusst unbehandelt: `i < n` (weniger
bestätigte Tracks als aktuelle Metadaten) wird mangels Erkennbarkeit
weiterhin stillschweigend fortgesetzt — durch Fix 1 (progress.json wird bei
jeder frischen Suche gelöscht) tritt dieser Fall in der Praxis aber nicht
mehr auf.

## ✓ cut.py v1.9.10 — Länge des letzten Tracks zur Absicherung anzeigen

Wenn ein Album keine Discogs/MB-Tracklängen liefert, wird die „Länge"-Spalte
für alle Tracks außer dem letzten aus den bestätigten Schnittpunkten
abgeleitet (`display_starts[i+1] - display_starts[i]`) — nur der letzte Track
hat keinen "nächsten" Punkt und zeigte bisher `?:??`, ebenso die
Gesamtdauer im Footer.

Fix: tatsächliche Dateidauer (`mf.get_flac_duration()`) wird einmal in
`main()` ermittelt und als `total_flac_dur` an `build_cutting_panel()`
durchgereicht. Dort als virtueller Endpunkt an `display_starts` angehängt —
derselbe Formel berechnet jetzt auch die Länge des letzten Tracks korrekt
(zur Absicherung, kein Datenverlust). Footer-Gesamtdauer nutzt denselben
Wert als Fallback wenn die Summe der `dur_s`-Felder 0 ergibt. Redundanter
zweiter `ffprobe`-Aufruf im Export-Loop entfernt (nutzt jetzt denselben Wert).

4 neue Tests in test_cut_ui.py. `cut_ui.py`-Logik automatisiert getestet
(Console-Capture); die `cut.py`-Verdrahtung (echter ffprobe-Aufruf) braucht
noch eine manuelle Bestätigung im laufenden Programm.

## ✓ cut.py v1.9.9 — `p<Sek>` spielt nicht mehr automatisch ab

Nachbesserung zu v1.9.8: `p<Sek>` änderte die Preview-Dauer, spielte dabei
aber sofort das Snippet in der neuen Länge ab (Loop-Kopf spielt unconditional
bei jedem `continue`). Jetzt überspringt ein `skip_play`-Flag den Auto-Play
für genau eine Runde — `p<Sek>` ändert nur den Wert, `p` allein spielt weiter
wie gewohnt ab. Manuell im laufenden Programm bestätigt (Terminalverhalten,
pytest kann das nicht prüfen).

## ✓ cut.py v1.9.8 — Preview-Länge live per `p<Sek>` änderbar

Bisher war die Snippet-Länge nur beim Start via `--preview` fix setzbar.
Neuer Befehl `p<Sek>` (z.B. `p18`) ändert sie während des Laufs, für alle
folgenden Previews. Feste Grenzen 3–30s (`_MIN_PREVIEW_SEC`/`_MAX_PREVIEW_SEC`)
als Bedienfehler-Schutz — außerhalb wird die Eingabe komplett ignoriert statt
geklemmt. `p` ohne Zahl spielt weiterhin unverändert ab.

Neue Funktion `parse_preview_duration()` (testbar, analog `compute_last_gap`).
`build_cutting_panel()` bekommt `preview_duration` als neuen Keyword-Parameter
(Default 3.0, bestehende Aufrufe unbetroffen) und zeigt sie in der Steuerzeile
an: `[p] 18s abspielen` statt statisch `[p] abspielen`.

## ✓ cut.py v1.9.7 — last_gap verwirft unplausibel große Abweichungen

Bug gefunden beim manuellen Schneiden: Discogs listete für einen Track eine
falsche Länge (Differenz zur echten Länge: 71s). Die Korrektur des
Startpunkts wurde von `last_gap` als „gelernte Pause" interpretiert und in
der Vorschau auf alle folgenden (noch unbestätigten) Tracks weitergereicht —
eine einmalige Metadaten-Korrektur wurde so fälschlich zu einer dauerhaften
Verschiebung in der Schätzung.

Neue Funktion `compute_last_gap()`: Abweichungen ≥ `_MAX_PLAUSIBLE_GAP` (10s)
gelten als falsche Metadaten-Länge, nicht als echte Inter-Track-Pause, und
werden verworfen (`last_gap` bleibt 0.0) statt übernommen zu werden. Echte
Pausen (typisch < 10s bei Vinyl-Mastering) werden weiterhin gelernt und
fortgeschrieben.

## ✓ v1.7.1 — Abbruch bei fehlendem Whisper-Modell

Manueller Testlauf in einer Shell ohne aktivierte `.venv` (`which python3` zeigte
auf `/opt/homebrew/bin/python3` statt `.venv/bin/python3`): `faster-whisper` war
nicht importierbar, `_get_whisper_model()` gab still `None` zurück — jeder
Track ohne Provider-Konsens landete fälschlich bei „kein Vokal", ganz ohne
Fehlermeldung (z.B. Rheingold „Dreiklangsdimensionen" erneut falsch verworfen,
obwohl `small` das eigentlich löst).

Neuer Hard-Check direkt nach dem Modell-Preload in `main()`: Ist Whisper aktiv
(kein `--no-whisper`) und `_get_whisper_model()` liefert `None`, bricht das
Skript sofort mit klarer Fehlermeldung und Exit-Code 1 ab, statt stillschweigend
falsche Ergebnisse zu produzieren. `_CACHE_MIN_VERSION` auf `1.7.1` angehoben —
der fehlerhafte Lauf könnte falsche Cache-Einträge geschrieben haben.

## ✓ v1.7.0 — `base` und VAD-Probe entfernt, nur noch `small`

Untersuchung (Stichprobe via `whisper_sample.py`/`whisper_model_test.py`, siehe
„Ideen" unten) ergab: `base` scheitert bei nicht-englischen Songs mit schlechter
Provider-Abdeckung (bestätigter Fall: Rheingold „Dreiklangsdimensionen" —
`base` 19%, `small` 53%, `medium` 61%). Bei bekannten/gut produzierten Songs
(Piaf, Zaz, Bocelli, Dalida) scheiterte `base` dagegen nicht — die Evidenz ist
dünn (1 von 9 getesteten Tracks), aber der Fehlerfall ist teuer genug
(fälschlich verworfene LRCs) und billig genug zu beheben (mehr Rechenzeit im
Hintergrund), dass sich der Wechsel lohnt.

- `_WHISPER_MODEL_FAST`/`_WHISPER_MODEL_FULL`-Unterscheidung entfernt — nur
  noch ein Modell (`_WHISPER_MODEL = "small"`), überall wo Whisper läuft.
- Zweistufige Verifikation (`base` immer, `small` nur im Grenzbereich 20–40%)
  entfällt — ein einziger Pass, ein einziges Modell.
- VAD-Probe (`_vad_peak_start`, 15s-Kurzcheck vor dem Vollpass) komplett
  entfernt. Sie gatete ausschließlich den jetzt ebenfalls entfernten zweiten
  Pass — `has_vocals` kam schon vorher aus dem vollen Durchlauf, nicht aus der
  Probe (v1.5.2 „V3"-Fix stellte sicher, dass der Vollpass immer lief). Kein
  Funktionsverlust, nur Zeitersparnis.
- Performance ist kein Gegenargument: `small` ist laut ROADMAP v1.3.11 ~3×
  langsamer als `base`, das wird bewusst in Kauf genommen.
- `_CACHE_MIN_VERSION` auf `1.7.0` (= `__version__`) angehoben → alle
  bestehenden Cache-Einträge ungültig, komplette Bibliothek wird neu geprüft.

## ✓ v1.6.0 — `--no-whisper` Flag

Whisper (`base`) transkribiert nicht-englische Songs unzuverlässig — viele
Tracks landen fälschlich bei „0W kein Vokal", obwohl Gesang vorhanden ist
(siehe z.B. Dalida "Forever", französisch). Neues Flag `--no-whisper`
überspringt die Whisper-Verifikation komplett:

- Strikter 3-Provider-Konsens (≥40% Jaccard) bleibt unverändert der Schnellweg.
- Ohne diesen Konsens wird jetzt immer ein 2-Provider-Konsens versucht
  (`Konsens NN% (2P)`) — vorher nur erreichbar wenn Whisper „kein Vokal" meldete.
- Schlägt auch das fehl: Dauer-Heuristik (`_heuristic_best`) mit Reject-Schwelle
  — Kandidaten deren Dauer-Toleranz überschritten ist werden abgelehnt
  (`reason: "dauer-abweichung"`) statt blind geschrieben. Vorher schrieb die
  Legacy-Heuristik immer den besten der schlechten Kandidaten.
- Cache-Einträge mit `reason=kein-vokal`/`unter-schwelle` werden bei
  `--no-whisper` automatisch neu geprüft, auch ohne `--force` — spart den
  vollen Neulauf um gezielt frühere Whisper-Ablehnungen nachzuholen.
- Modell-Preload beim Start wird übersprungen.

Dient als Zwischenschritt bis eine bessere Whisper-Modellwahl (Schritt 2:
Stichprobe der 0W-Fehlschläge, siehe „Ideen" unten) gefunden ist.

## ✓ v1.5.3 — BFS-Traversal für --recursive

Bei 20000+ Dateien in hunderten Ordnern wartete das Skript auf den vollständigen
`rglob`-Scan bevor die erste Datei verarbeitet wurde. Neuer `_iter_audio_bfs`-Generator:
Breadth-first mit `iterdir()`, innerhalb jeder Ebene alphabetisch sortiert. Startet
sofort mit Level 0 statt alles vorab zu sammeln. Im Header wird die Gesamtzahl bei
`--recursive` weggelassen (unbekannt bis zum Ende).

## ✓ v1.5.2 — Robustere Konsens- und VAD-Logik

**C1** `_extract_lrc_words` entfernt jetzt alle `[...]`-Tokens ohne Doppelpunkt (Sektion-Labels
wie `[Chorus]`, `[Verse 1]`, `[Guitar Solo]` von Genius). Verhindert strukturelle Ausreißer
durch annotierte LRCs.

**C3** `_provider_consensus` wirft bei initialem Scheitern den stärksten Ausreißer heraus
(niedrigste Durchschnitts-Ähnlichkeit zu anderen) und wiederholt den Check auf den
verbleibenden Kandidaten. Für n=3 äquivalent zu Best-Pair, für n=4 strenger.

**V1** VAD-Gate konsistent mit `has_vocals`: kein early-skip wenn die Probe bereits ≥5 Wörter
liefert, auch wenn `no_speech_prob` hoch ist. Schließt die Lücke zwischen Gate-Bedingung
und der Logik die das Gate kurzschließt.

**V2** VAD-Probe übergibt jetzt `language=lrc_lang` an Whisper (war vorher vergessen).
Reduziert falsch-hohe `no_speech_prob` durch Sprach-Fehldetection auf kurzem Fenster.

**V3** Kein early return nach VAD-Probe — Base-Pass läuft immer. VAD-Ergebnis (`likely_no_vocals`)
gatet nur noch den teuren Small-Pass. Echte Instrumentals zahlen einen Base-Pass extra;
fälschlich abgewürgte Vokalsongs (wie "Fortune Faded") werden gerettet.

## ✓ v1.5.1 — Genre-Skip: Terminal-Ausgabe, Cache-Eintrag, lrc_analyse

Genre-übersprungene Tracks erscheinen jetzt im Terminal (0/0: │ Genre=…  –/=),
werden gecacht (r=skip, reason=genre) und in lrc_analyse.py als eigene Zeile
gezählt. JSON-Cache-Struktur im README dokumentiert.

## ✓ v1.5.0 — Neues Terminal-Format, vollständiger JSON-Cache

Terminale Ausgabe neu strukturiert: Datei-Ergebnis (✓ = –) strikt getrennt von
Methoden-Info. Info-Spalte zeigt Modell, Sprache, Methode, Wörter, Ergebnis.
JSON-Cache erweitert: method, no_vocal, outcome, provider_names, language,
reason — ersetzt verstreute consensus/fallback/model-Felder. _CACHE_MIN_VERSION
auf 1.5.0 erhöht → alle alten Einträge werden neu verarbeitet.

## ✓ v1.4.22 — Provider-Übernahme durch Jaccard-Konsens ersetzt

Wenn die VAD-Probe keinen Gesang erkennt, wurde bisher bei ≥2 Providern + ≥10
Zeilen blind gespeichert. Jetzt wird stattdessen derselbe Jaccard-Check wie beim
normalen Konsens durchgeführt (min_providers=2 statt 3). Nur wenn die Provider
inhaltlich übereinstimmen (≥40% Jaccard), wird gespeichert — als "Konsens (kein
Vokal) XX%". lrc_analyse.py zeigt diese Tracks als eigene Methode.

## ✓ v1.4.21 — VAD-Probe: Fallback-Positionen bei 30% und 50%

Energie-Peak ≠ Vokal-Peak: bei leisen Songs (R.E.M. "Drive") oder Songs mit
instrumentalem Outro als lautester Stelle ("Everybody Hurts" — Peak bei 4:48,
no_speech=1.00!) feuerte die erste Probe fälschlich. Fix: wenn erste Probe
anschlägt, werden 30% und 50% der Trackdauer als Fallback getestet. Erster
Treffer (no_speech ≤ 0.65) beendet die Suche. Nur wenn alle drei Positionen
> 0.65 → echt instrumental.

## ✓ v1.4.20 — Halluzinationsfilter: doppelte Bedingung

_is_hallucination() feuerte fälschlich auf repetitiven Popsongs (z.B. Wolfgang
Petry "Du bist ein Wunder": unique-Ratio 24.4% < 25% → als Halluzination
klassifiziert obwohl Whisper korrekt transkribierte). Fix: zusätzlich muss ein
einzelnes Wort ≥ 25% aller Wörter ausmachen. Repetitive Songs: "ein" = 9% → kein
Alarm. Echte Halluzination "lets go" × 20: "lets" = 50% → Alarm. Alle 118 Tests
bestehen weiterhin.

## ✓ v1.4.19 — Einzeldatei-Unterstützung

fetch_songtext.py akzeptiert jetzt auch einzelne Audiodateien als Argument
(nicht nur Verzeichnisse). Modus-Anzeige: "Datei" statt "Album".

## ✓ v1.4.18 — Spracherkennung aus LRC, language-Hint an Whisper

Whisper transkribiert bei nicht-englischen Songs fälschlich auf Englisch (Grund:
base-Modell bevorzugt Englisch). Fix: Sprache des LRC-Texts per langdetect erkannt
(55 Sprachen, Konfidenz ≥ 80 %), als language-Parameter an model.transcribe()
übergeben. Behebt 0W-Problem für deutsche und andere nicht-englische Tracks
(getestet: Morgenrot "Strom" 95W→199W de, "Frank liegt krank" 189W Nonsense→248W de).
Neues Paket: langdetect (requirements.txt).

## ✓ v1.4.17 — VAD-Peak als Probe-Start (unabhängig von LRC-Timestamps)

VAD-Probe startet jetzt an der lautesten Stelle des Tracks statt am ersten
LRC-Timestamp. ffmpeg volumedetect scannt 5 Positionen (10–90 % der Dauer,
je 10s) und wählt die lauteste. Verhindert dass die Probe im instrumentalen
Intro landet und Vocals verpasst ("Jajaja"-Problem).

## ✓ v1.4.16 — logprob-Filter entfernt, Label "kein Vokal erkannt"

avg_logprob-Schwelle entfernt: der Filter war sprachbiased (Deutsch/nicht-Englisch
bekam niedrigere Konfidenzwerte → Wörter fälschlich verworfen → 0W trotz vorhandener
Lyrics). _is_hallucination() reicht als Schutz gegen Wiederholungsschleifen.
Label "instrumental" → "kein Vokal erkannt": kein Urteil über den Track, nur
Beschreibung was Whisper gehört (oder nicht gehört) hat.

## ✓ v1.4.15 — Containment + vollständiges Transkriptionsfenster

Zwei Änderungen gemeinsam:
1. Whisper transkribiert jetzt immer den gesamten Track (max 8 Minuten statt
   adaptiv 50-100%). Verhindert dass charakteristische Textpassagen am Ende
   des Songs nie gehört werden.
2. Vergleichsmetrik von Jaccard auf Containment umgestellt:
   `|transcript ∩ LRC| / |transcript|` statt `|A∩B| / |A∪B|`.
   Jaccard-Problem: LRC enthält vollständigen Text, Transkript nur einen Teil
   → Nenner aufgebläht → Score systematisch gedrückt. Containment ist
   asymmetrisch und misst nur "wie viel des Gehörten passt zur LRC".
   Provider-Konsens-Check verwendet weiterhin Jaccard (dort symmetrisch korrekt).

## ✓ v1.4.13 — Akzeptanzlogik vereinfacht (zurück zu absolutem Threshold)
v1.4.13 führte relative Marge ein, die revertiert wurde: höchster Jaccard-Score
gewinnt, Akzeptanz bei ≥ 40%. Keine Marge nötig — wer am besten zu Whisper
passt, ist der richtige Kandidat.

## ✓ v1.4.12 — VAD-Kurzprobe (15s) vor vollständigem Whisper-Pass
15 Sekunden ab erstem LRC-Timestamp transkribieren und no_speech_prob prüfen.
Bei instrumentalen Tracks (no_speech_prob > 0.65) wird der vollständige Pass
übersprungen — statt mehrerer Minuten nur ~2 Sekunden. Probe nur wenn
vollständiger Kontext > 30s (2× Probe-Länge), sonst direkt voller Pass.

## ✓ v1.4.11 — faster_whisper Eigenmetriken nutzen
`no_speech_prob` pro Segment (prinzipientreuer Instrumental-Detektor) und
`avg_logprob` (prinzipientreuer Halluzinations-Indikator) aus faster_whisper
statt unserer 5-Wörter-/25%-Heuristiken. Sprachdetektions-Wahrscheinlichkeit
für forced-language in Pass 2 und sprachadaptive Schwellen nutzen.

## ✓ v1.4.10 — Konsens zuerst, Whisper zum Tie-Breaking
Aktuell läuft Whisper immer. Bei hohem inter-Provider-Konsens (≥ 40%) ist
Whisper überflüssig. Konsens-Check vor Whisper → spart base-Pass auf dem
Großteil der Bibliothek. Whisper nur wenn Konsens nicht eindeutig.

## ✓ v1.4.8 — LRC-Deduplizierung vor Konsens-Check
Identische LRCs von verschiedenen Providern (gespiegelte Datenbanken) per
Content-Hash deduplizieren. "3 Provider einig" darf nicht bedeuten "eine
Quelle dreifach gespiegelt".

## ✓ v1.2.0 — Fortschritt speichern / fortsetzen
Nach jedem `ok` die bestätigten Startpunkte in `progress.json` schreiben. Bei Absturz oder versehentlichem Beenden kann die Session fortgesetzt werden.

## ✓ v1.3.0 — Undo
`[u]` macht das letzte bestätigte `ok` rückgängig. Track wird erneut bearbeitbar.

## ✓ v1.4.0 — Ausgabeverzeichnis (`--out`)
Ausgabeverzeichnis für geschnittene Tracks frei wählbar statt fest neben der Quell-FLAC.

## ✓ v1.5.0 — Normton (experimentell)
`[n]` schaltet einen 1000 Hz Normton (0,25 s) vor dem Schneidpunkt ein/aus. Nahtloser Übergang via ffmpeg concat-Filter.

## ✓ v1.6.0 — Preview-Dauer konfigurierbar (`--preview`)
Snippet-Länge frei wählbar statt fix 3 Sekunden (z.B. `--preview 5`).

## ✓ v1.7.0–v1.8.x — Rich Vollbild-UI
Metadatensuche, Schneiden, Export und Songtext-Suche laufen vollständig
im Rich Live-Screen. Eingabe zeichenweise mit tty.setcbreak.

---

# Refactoring-Roadmap (Architektur-Umbau)

Grundlage: ARCHITECTURE.md. Ziel: stabiler, testbarer, wartbarer Code.

## ✓ Schritt 1 — cut_ui.py anlegen
Alle Rich/tty-Abhängigkeiten aus interactive_cutter.py extrahiert:
- build_cutting_panel() — umbenannt von build_panel(), est als Parameter
- build_metadata_panel() — unverändert
- live_input() — umbenannt von _live_ask()
- fmt_dur() — Display-Hilfsfunktion
Ergebnis: eine Datei für alles Rich-spezifische (Schicht 2).

## ✓ Schritt 2 — Umbenennen der Skripte
- interactive_cutter.py → cut.py (v1.9.0)
- preparer.py → assemble.py
- metadata_fetcher.py → fetch_metadata.py
- songtext.py → fetch_songtext.py
- Alle Testdateien und README-Verweise angepasst.

## ✓ Schritt 3 — cut.py aufräumen
Importiert jetzt aus cut_ui. Doppelter Code entfernt.
Enthält: main(), run_metadata_search(), Logik-Funktionen, cut_and_tag(), play_snippet*()
Ergebnis: 280 Zeilen statt 671.

## ✓ Schritt 4 — test_cut_ui.py schreiben
21 Tests mit Console(force_terminal=False) + capture().
Läuft vollautomatisch mit pytest, kein Terminal nötig.

## ✓ Schritt 5 — test_smoke.py schreiben
7 Smoke-Tests: --version, --help, no-args für cut.py, assemble.py, fetch_songtext.py.
pexpect: noch nicht recherchiert — steht als offener Punkt in ARCHITECTURE.md.

---

---

## Ideen (nicht geplant)

### Dauer-Vergleich small vs. medium (pro Schritt, nicht nur Modell-Laufzeit)
Für eine ausgewählte Trackliste die Dauer **jedes einzelnen Schritts** der
Whisper-Verifikation messen und small gegen medium vergleichen — nicht nur die
reine Transkriptionszeit des Modells, sondern die **Gesamtdauer der Bewertung**
pro Track (Provider-Abfragen, ffprobe/Dauer-Ermittlung, Transkription, Scoring
— jeder Schritt einzeln gestoppt). Kernfrage: Wie wirkt sich ein Modellwechsel
small→medium auf die Gesamtdauer eines kompletten Bibliotheks-Durchlaufs aus,
nicht nur auf die Modell-Laufzeit isoliert betrachtet?

### Whisper-Modell-Stichprobe — erledigt, siehe v1.7.0
Umgesetzt (temporär): `whisper_sample.py` (Cache nach `kein-vokal`-Ablehnungen
mit Provider-Konsens-Bestätigung durchsuchen) und `whisper_model_test.py`
(mehrere Modelle gegen eine Kandidatenliste testen, resumable). Ergebnis:
`base`→`small`-Wechsel in v1.7.0. Beide Einweg-Skripte nach Abschluss der
Untersuchung wieder entfernt — basierten auf dem alten (base-Ära) Cache-Stand,
der durch den v1.7.0-Neu-Scan ohnehin ersetzt wird. Bei Bedarf (z.B. späterer
Test von `medium`/`large-v3`) müssten sie neu gebaut werden.

### Whisper-Verifikation
Die ersten ~30 Sekunden eines Tracks via `faster-whisper` transkribieren und
das Ergebnis mit dem Anfang der LRC-Kandidaten abgleichen. Bester Wort-Overlap
gewinnt. Kein Match → keine LRC (keine eigene Transkription erstellen).
Würde Fehlgriffe eliminieren die durch Dauer-Heuristik nicht erkannt werden.
Abhängigkeit: `faster-whisper`, Modell ~500 MB (base) bis 1,5 GB (medium).

### Unified Toolchain (`vinylcut`)
Einziger Einstiegspunkt für die gesamte Pipeline. Fragt beim Start (oder per Flag `--from 1/2/3`), an welchem Schritt begonnen werden soll:

1. **Record** — Aufnahme direkt aus der Befehlszeile via ffmpeg (Audiointerface → FLAC), mit Pegelanzeige und Start/Stop per Taste. Würde Audacity ersetzen.
2. **Assemble** — wie heute `assemble.py`
3. **Cut** — wie heute `cut.py`

Checkpoint-Logik: Das Tool erkennt anhand vorhandener Dateien, welcher Schritt als nächstes sinnvoll ist, und schlägt ihn vor.

---

# assemble.py — Roadmap

Werkzeug zur non-destruktiven Vorbereitung einer Roh-FLAC (alle Seiten in einer Datei) vor dem Schneiden mit `cut.py`. Die Original-FLAC wird nie verändert. Alle Schnittdaten landen in `assemble.json`, Zwischenergebnisse in neuen Dateien.

## ✓ v0.1 — Stille-Erkennung
Nimmt eine FLAC, erkennt lange Stillepausen via `ffmpeg silencedetect`, gibt vorgeschlagene A/B-Punkte für jede Nahtstelle im Terminal aus. Noch keine Interaktion — nur prüfen ob die Erkennung brauchbare Ergebnisse liefert.

## ✓ v0.2 — Interaktives Grob-Beschneiden
Für jede Nahtstelle: Punkt A (Ende Musik Seite N) und Punkt B (Anfang Musik Seite N+1) per Playback interaktiv setzen und in `assemble.json` speichern. Fortschritt wird nach jeder Bestätigung gespeichert, Session kann fortgesetzt werden.

## ✓ v0.3 — Crossfade-Vorschau + Feinschneiden
Jeden Übergang abhören: temporärer Crossfade (8 s Fenster, 0,5 s Blende) wird on-the-fly generiert und abgespielt. A/B per Fokus-Modell ([a]/[b] + [+]/[-]) verschieben. Nutzer gibt Anzahl Seiten an — beste Kandidaten nach Stillelänge gewählt.

## ✓ v0.4 — Schneiden + Zusammenfügen
Ausgabe: `<Name>_prepared.flac` mit Crossfades an allen Nahtstellen. Original-FLAC bleibt unangetastet.

## ✓ v0.5 — Normalisierung + DC-Offset
DC-Offset (highpass 5 Hz) + Peak-Normalisierung auf -0,1 dBFS via sox. Optionaler Kanalausgleich nach Pegelmessung. Ergebnis in `<Name>_final.flac`.

## ✓ v0.6 — Polish
Tests für `get_segments`, automatische Umbenennung der Ausgabedatei, ROADMAP aktualisiert.

## ✓ v1.0 — Stabile Version
README vollständig nachgezogen, Gesamtworkflow dokumentiert.

## ✓ v1.1.2 — Normalisierung auf ffmpeg loudnorm (echtes dBTP)
`normalize()` verwendet jetzt ffmpeg loudnorm im 2-Pass-Verfahren statt sox `norm`.
Pass 1 misst Pegel, Pass 2 wendet linearen Gain mit TP=-1.0 dBTP an.
Kanalausgleich über ffmpeg `pan`-Filter statt sox `remix`.

## ✓ v1.1.1 — Normalisierungsziel auf -1.0 dBTP
Zielwert von -0.1 auf -1.0 geändert (Vorstufe zum loudnorm-Umbau).

## ✓ v1.1 — Rich Vollbild-UI
assemble_ui.py (Schicht 2) mit 5 Panel-Buildern für alle Phasen. assemble.py
nutzt jetzt Rich Live(screen=True) + live_input() für alle interaktiven Schritte.
25 Tests in test_assemble_ui.py, laufen ohne Terminal.

---

# fetch_songtext.py / refetch_lyrics.py — Roadmap

## ✓ v1.0 — Grundfunktion
Songtext-Suche via syncedlyrics, LRC-Dateien neben FLAC speichern.

## ✓ v1.1 — Alle Provider, bestes Ergebnis
Alle vier Provider (lrclib, musixmatch, netease, genius) gleichzeitig befragen.
Scoring: (valid, synced, lines) — höher ist besser. megalobiz entfernt (lieferte
konsequent falsche Songs). Asymmetrische Dauer-Validierung gegen release.json:
LRC darf bis zu 40 % kürzer enden (Instrumental-Outro), höchstens 10 % länger.

## ✓ v1.0 refetch_lyrics.py — Rekursives Neu-Laden
Durchsucht alle Unterordner nach FLACs, lädt Songtexte neu. Zeigt Vorschau
nur wenn sich der Inhalt ändert, speichert still wenn kein Unterschied.

## ✓ v1.4.7 — has_vocals robuster, Halluzinations-Erkennung
Zwei Schwachstellen im Whisper-Verifikationsschritt behoben:
1. `has_vocals` erfordert jetzt ≥ 5 Wörter (vorher: ≥ 1) — verhindert dass
   Sonder-Token wie "(upbeat music)" instrumental-lastige Tracks als vokal markieren.
2. Halluzinations-Erkennung: Transkriptionen mit ≥ 20 Wörtern aber < 25 %
   einzigartigen Wörtern werden als Schleife erkannt und verworfen (→ leere Liste),
   statt mit Jaccard 0 % in den Score einzugehen und den Konsens-Check zu blockieren.

## ✓ v1.4.6 — mlx-whisper rückgebaut, faster_whisper wiederhergestellt
mlx-whisper (Apple Silicon GPU) erwiesen als unzuverlässig: Spracherkennung
scheitert bei instrumental-lastigen Passagen ("Shona" statt "English"), Jaccard
fällt auf 0% → base-Score liegt unter RETRY_MIN → small wird nie aufgerufen →
korrekte LRCs werden fälschlicherweise abgelehnt. Geschwindigkeitsgewinn (1.6–2.4×)
rechtfertigt die Instabilität nicht. Rückbau auf faster_whisper (ctranslate2, CPU).

## ✓ v1.4.5 — mlx-whisper Backend (rückgebaut in v1.4.6)
Apple Silicon GPU/Neural Engine via mlx-whisper. Im Benchmark 1.5–2.3× schneller
als faster_whisper, aber Qualität instabil — Sprachdetektionsfehler auf schwierigen
Tracks. Siehe v1.4.6.

## ✓ v1.4.4 — Konsens-Jaccard-Schwelle auf 40% gesenkt
Evidenzbasiert nach Analyse von Manu Chao und Marvin Gaye: Ausreißer-Provider
(z.B. Netease mit anderssprachiger Version) zieht den Paardurchschnitt unter 50%,
obwohl die drei anderen Provider stark übereinstimmen. Genius liefert mitunter
Metadaten-Rauschen (Contributors-Texte) das die Ähnlichkeit ebenfalls drückt.
40% ist das empirisch fundierte Minimum für konsistente Akzeptanz.

## ✓ v1.4.3 — Provider-Konsens überstimmt Whisper-Threshold
Wenn ≥3 Provider einen Treffer liefern UND deren LRC-Inhalt sich untereinander
≥40% (Jaccard) ähnelt UND Whisper Vokale hört (score ≥ 20%), wird die LRC
akzeptiert — auch wenn der Whisper-Score unter 40% bleibt.
Trifft auf Artists mit unkonventionellem Gesangsstil zu (z.B. Meat Puppets).
Gewinner = repräsentativster Kandidat (höchste Durchschnitts-Ähnlichkeit zu allen
anderen) — Ausreißer werden so automatisch übergangen.
Cache-Eintrag enthält `"consensus": true`. Ausgabe zeigt ", Konsens" statt "!".
Neues Tool: `lrc_recheck.py` — findet bereits gecachte "nf"-Tracks die vom
Konsens-Check profitieren würden, löscht ihre Cache-Einträge gezielt (--apply).

## ✓ v1.4.2 — Tracks ohne Artist- und Title-Tags überspringen
Dateien ohne beide Tags werden nicht mehr gegen Provider gesucht — LRC wird
gelöscht falls vorhanden. Kein Cache-Eintrag (wie Genre-Skip). Zähler "X ohne Tags"
in der Zusammenfassung.

## ✓ v1.4.1 — Timeout für Provider-Abfragen (20 s)
Hängende Provider blockieren ihren Thread nicht mehr unbegrenzt.
`_CACHE_MIN_VERSION` bleibt 1.4.0 — kein Neulauf der Bibliothek.

## ✓ v1.4.0 — Zweistufige Whisper-Verifikation (base → small im Grenzbereich)
Erster Pass immer mit `base` (schnell). Liegt der Score im Grenzbereich [25 %, 40 %),
folgt ein zweiter Pass mit `small` (genauer). Darunter oder darüber: kein zweiter Pass.
Cache speichert zusätzlich `model` (welches Modell die finale Entscheidung traf).
Ausgabe zeigt `+` wenn small den Ausschlag gab (z.B. `~238W, 64%+`).
Versionsprung auf 1.4 — das System ist jetzt evidenzbasiert kalibriert.

## ✓ v1.3.11 — Whisper-Modell: base → small
Evidenzbasierter Wechsel: `base` erzielte 37 % für korrekte italienische Lyrics (Mario Biondi)
und scheiterte knapp am 40 %-Threshold. `small` (~480 MB, ~3× langsamer) transkribiert
nicht-englische Inhalte deutlich zuverlässiger.

## ✓ v1.3.10 — Erweiterte Metadaten im Cache (score, providers, words, ts)
Cache-Einträge enthalten jetzt: Whisper-Overlap (`score`), Provider-Treffer (`providers`),
transkribierte Wörter (`words`), Zeitstempel (`ts`), bei Fallback auch `fallback: true`.
Ermöglicht nachträgliche Auswertung warum Tracks angenommen oder abgelehnt wurden.

## ✓ v1.3.9 — Vollständigen LRC-Text für Whisper-Vergleich nutzen
`_extract_lrc_words` verarbeitete nur die ersten 15 Zeilen (~120 Wörter). Mit adaptiver
Transkriptionsdauer (volle Song-Länge) führte das zu künstlich niedrigen Jaccard-Werten,
da die zweite Hälfte der Lyrics im Whisper-Output vorkommt, aber nicht im LRC-Vergleich.
Jetzt alle Zeilen verwendet.

## ✓ v1.3.8 — LRC auch bei Genre-Skip löschen
Genre-gefilterte Tracks (Instrumental, Hörbuch etc.) löschen jetzt eine ggf. vorhandene
LRC-Datei, statt sie zu behalten.

## ✓ v1.3.7 — Bestehende LRCs löschen wenn kein Treffer
Wenn kein Provider eine LRC findet (oder Whisper alle verwirft), wird eine ggf. vorhandene
alte LRC-Datei jetzt gelöscht statt behalten. Verhindert, dass falsche LRCs dauerhaft bestehen.

## ✓ v1.3.6 — Whisper-Qualitätsschwelle auf 40 % angehoben, adaptive Transkriptionsdauer
Threshold von 6 % auf 40 % erhöht. Transkriptionsdauer jetzt adaptiv: ≤ 3 min → volle Länge,
≤ 6 min → 75 %, > 6 min → 50 % (max 5 min). `_CACHE_MIN_VERSION` auf 1.3.6 → alle bisherigen
Einträge werden neu verarbeitet.

## ✓ v1.3.5 — Zeitstempel in Ausgabe
Alle Track-Zeilen beginnen mit `HH:MM:SS` (Systemzeit). `_ts()` Hilfsfunktion.

## ✓ v1.3.4 — Genre-Filter: Hörbuch, Hörspiel, Instrumental etc. überspringen
`_SKIP_GENRE_KEYWORDS` (Substring-Matching): hörbuch, hörspiel, audiobook, audio play,
radio play/drama, instrumental, podcast, speech, spoken word, lesung, vortrag,
sfx, noise, field recording u. a. Genre-übersprungene Tracks werden gezählt
(„X Genre übersprungen") aber nicht gecacht — damit Korrekturen am Genre-Tag
beim nächsten Lauf automatisch greifen.

## ✓ v1.3.3 — Per-Track-Cache statt Ordner-Marker
Ordner-Marker-System entfernt. Stattdessen `.fetch_songtext.json` pro Albumordner,
geschrieben nach jedem einzelnen Track. Unterbrechungen mitten im Ordner verlieren
keinen Fortschritt mehr. `r: "ok"` = LRC gefunden/bestätigt, `r: "nf"` = nicht gefunden
(Instrumental etc.) — beide werden beim nächsten Lauf übersprungen.
`--force` ignoriert den Cache komplett.

## ✓ v1.3.2 — Marker sofort pro Verzeichnis, Schreibfehler abgefangen
Marker wird jetzt direkt geschrieben wenn ein Verzeichnis verlassen wird (statt erst am Ende).
Bricht der Lauf mittendrin ab (Volume unmounted, Ctrl+C), haben bereits abgeschlossene
Ordner ihren Marker. `OSError` beim LRC-Schreiben (z. B. Volume nicht mehr gemounted)
wird sauber abgefangen statt als Crash zu enden.

## ✓ v1.3.0 — Unterstützung weiterer Audioformate (MP3, Opus, OGG, M4A …)
`metaflac` ersetzt durch `mutagen` (easy=True) für formatunabhängiges Tag-Lesen.
Dateisuche findet jetzt: `.flac`, `.mp3`, `.ogg`, `.opus`, `.m4a`, `.aac`, `.wav`.
Abhängigkeit: `mutagen` (in requirements.txt ergänzt).

## ✓ v1.2.11 — Marker-Logik korrigiert: vorhandene LRCs werden ohne Marker immer geprüft
Ohne Marker werden alle Tracks verarbeitet — auch solche mit bestehender LRC (Whisper-Verifikation).
Der Marker ist der einzige Skip-Mechanismus. Die frühere Sonderbehandlung
„im Normalmode vorhandene LRCs nicht anfassen" entfällt.

## ✓ v1.2.10 — Verarbeitungsmarker (Skip bereits geprüfter Ordner)
Nach der Verarbeitung eines Ordners wird `.fetch_songtext_v<version>` angelegt.
Folgeläufe überspringen Ordner mit kompatiblem Marker automatisch.
Kompatibel ab `_MARKER_MIN_VERSION` (aktuell 1.2.0) — kein Massenneulauf bei Bugfix-Versionen.
Neues Flag `--force` / `-f` ignoriert alle Marker und verarbeitet alles neu.

## ✓ v1.2.9 — Provider-Abfragen parallelisiert
Alle vier Provider werden jetzt gleichzeitig via ThreadPoolExecutor befragt statt
nacheinander. Reihenfolge der Ergebnisse bleibt deterministisch.

## ✓ v1.2.8 — Robustere Whisper-Verifikation
Diagnostische Ausgabe: Provider-Anzahl, Whisper-Wörterzahl und Overlap pro Track sichtbar.
Overlap-Schwellwert von 12 % auf 6 % gesenkt (deckt Grenzfälle wie gemischtsprachige Songs ab).
Neuer Fallback: Whisper erkennt keine Sprache, aber ≥ 2 Provider und ≥ 10 Lyrics-Zeilen → LRC
trotzdem gespeichert (Vokalsong mit ungewöhnlichem Vokalstil, z. B. Falco "Vienna Calling").
Artist/Titel-Abfrage nutzt FLAC-Metadaten (seit v1.2.7) statt Dateinamen.

## v1.9.3: Transkript-Cache auf Song-Identität umgestellt (erledigt)

`transkripte` hing bisher an Datei-Pfad+Größe+Datum — invalidierte den Cache unnötig bei Umbenennungen/Verschiebungen. Umgestellt auf Künstler+Titel (analog `songs`, siehe `CACHE_DESIGN.md`): **ein Song = EIN Whisper-Transkript**, unabhängig von Modell/Fenster-Parametern. Nach Bereinigung der Klammer-Zusätze teilen sich mehrere Versionen/Mixe desselben Songs bewusst ein gemeinsames Transkript. `_whisper_best` prüft jetzt vor der Fenster-Schleife auf einen Song-Cache-Treffer (überspringt bei Treffer alle Whisper-Aufrufe komplett) und schreibt am Ende genau einmal das Transkript des gewählten (bzw. bestverfügbaren) Kandidaten zurück. Bestehende Zeilen im alten Format wurden automatisch migriert (Künstler/Titel aus den Audio-Tags gelesen, nicht neu transkribiert); die alte Tabelle bleibt als `transkripte_alt_v1`-Backup erhalten. `audio_key_for`/`params_key_for` entfernt (keine Aufrufer mehr).

## v1.9.2: Cache-Bugfixes + Schema-Normalisierung (erledigt)

Zwei reale Fehler behoben, die den Cache seit v1.9.0 unbrauchbar machten:
- **`check_same_thread=False`** (v1.9.1): Provider-Abfragen laufen in Worker-Threads, die Verbindung wird im Hauptthread geöffnet — jeder Cache-Zugriff warf bislang eine sqlite3-Exception, die von der bewusst großzügigen `except Exception`-Absicherung stillschweigend verschluckt wurde. Der Cache hat dadurch NIE etwas geschrieben, trotz laufender Scans.
- **Fehlschläge wurden gar nicht festgehalten** — nur stillschweigend übersprungen, dadurch fehlten bei gedrosselten Providern (v.a. Musixmatch) ganze Zeilen. Jetzt: `status="fehlschlag"` mit `fehlergrund` (`rate_limit`/`captcha`/`timeout`) wird IMMER gespeichert, zählt aber nie als gültiger Cache-Treffer.
- **`--force` umging den neuen Provider-Cache nicht** (nur den alten Track-Speicher) — jetzt erzwingen `--force` UND `--refresh-cache` beide eine frische Live-Abfrage.
- **Schema normalisiert**: zentrale `songs`-Tabelle (ein Künstler/Titel = eine Zeile) statt Künstler/Titel in jeder Provider-Zeile zu duplizieren; `ergebnisse` (vormals `quelle`) verweist per `song_id` darauf; `transkripte` (vormals `gehoert`) unverändert in der Funktion.

## v1.9.0: Cache-Modul (erledigt)

Siehe `CACHE_DESIGN.md` — intelligenter SQLite-Cache (Anbieter-Antworten + Whisper-Transkripte), damit Neuaufbauten nach Code-Änderungen ohne erneute Provider-Abfragen/Whisper laufen. Grundprinzip: läuft immer auch mit leerer/fehlender DB.

- `cache_store.py` — Speicherschicht (SQLite, WAL): `texte` (Liedtexte, per SHA-256-Fingerabdruck dedupliziert), `quelle` (Provider-/`"lokal"`-Treffer inkl. TTL), `gehoert` (Whisper-Transkripte, geschlüsselt über Datei+Modell+Parameter).
- `cache_seed.py <bibliothekspfad>` — liest alle vorhandenen `.lrc` als Quelle `"lokal"` in den Cache ein.
- `fetch_songtext.py`: neue Flags `--no-cache`, `--refresh-cache`, `--cache-ttl TAGE` (Default 30). Provider-Abfragen (`_query_provider`) und Whisper-Transkription (`_cached_transcribe`) cachen transparent — geschützter Import (`cache_store` fehlt → Verhalten exakt wie vorher). Drei Ausgänge sauber getrennt: Treffer und „wirklich nichts" werden gecacht, transiente Fehler (Timeout/Rate-Limit/Captcha) nie.
