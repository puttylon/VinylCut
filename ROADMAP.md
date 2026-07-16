# VinylCut Roadmap

## ✓ Songtexte-Pipeline-Umbau — Steuer-Skript + Phasen-Skripte (alle 5 Meilensteine erledigt)

**Auslöser:** Nach dem lrclib-Dump-Bugfix (v1.13.1) und dem Fund, dass der
Dump falsch verknüpfte Songtexte enthalten kann (Art Blakey „Blues March"
bekam über den Dump den Text von Elvis' „That's All Right" — siehe v1.13.0-
Eintrag unten), wollte der Nutzer den gesamten Songtexte-Prüf-Teil
durchleuchten. Die dabei gestellten Leitfragen (typische Abläufe, fehlende
Funktionen, Obsoletes) sind inzwischen in einer eigenen Design-Session
beantwortet worden — Ergebnis ist das Architektur-Dokument
**[`workflow für songexte.txt`](workflow%20f%C3%BCr%20songexte.txt)**
(Abschnitt „ZIELARCHITEKTUR"). Kernproblem, das den Umbau motiviert: Ein
einzelner Provider-Ausfall (Rate-Limit, Netzwerk) lässt den Konsens-
Mechanismus (braucht ≥3 Provider) fälschlich scheitern, obwohl ein späterer
Retry das vermieden hätte — Whisper springt dann unnötig und langsam an.
Die 5 Phasen aus den ursprünglichen Notizen (identifizieren → abfragen →
nachholen → bewerten → schreiben) werden dafür in eigene, einzeln
wiederholbare Programme zerlegt, orchestriert von einem Steuer-Skript.

**Weiterhin offen, NICHT Teil dieses Umbaus (separates Thema, zurückgestellt):**
Die ursprüngliche Bestandsaufnahme hatte drei mögliche Lücken benannt, die
mit dem reinen Pipeline-Umbau nichts zu tun haben und hier bewusst nicht
mitgelöst werden:
1. Keine Statistik-/Aggregat-Sicht für den SQLite-Cache — `lrc_analyse.py`
   deckt nur die JSON-Seite (`.fetch_songtext.json`) ab.
2. Kein Bindeglied zwischen JSON-Cache und SQLite-Cache: findet
   `--retry-missing`/der künftige Nachhol-Modus einen neuen Provider-Treffer,
   bleibt unklar, welche Datei/welches Album im JSON-Cache noch als `nf`
   markiert ist und einen normalen Neu-Lauf bräuchte, damit der Treffer
   tatsächlich als `.lrc` geschrieben wird.
3. Kein Werkzeug, um bereits AKZEPTIERTE (geschriebene) `.lrc`-Dateien
   nachträglich auf Plausibilität zu prüfen — relevant nach dem
   Art-Blakey/Elvis-Fund (v1.13.0/v1.13.1).

**Hinweis für eine spätere, hier noch nicht getroffene Entscheidung** (was
mit `fetch_songtext.py` selbst passiert — behalten/löschen/parallel betreiben):
Mehrere andere Skripte importieren `fetch_songtext` heute als Modul —
`compare_whisper_models.py` (Zeile 91), `test_compare_whisper_models.py`
(Zeile 8) und `test_fetch_songtext.py` (Zeile 16); `cache_store.py` importiert
es zusätzlich lazy für die `transkripte`-v1→v2-Migration (Zeile 120). Diese
Abhängigkeiten sind von einer Aufteilung betroffen und sollten bei der
späteren Entscheidung mitbedacht werden.

---

### Bau-Fahrplan (geplant, noch nicht begonnen)

Reihenfolge: zuerst das Steuer-Skript-Grundgerüst (Phasen zunächst Stubs),
danach phasenweise 1 → 2 (+Nachhol-Modus) → 4 → 5 — jede Phase wird gebaut
UND sofort ins Steuer-Skript eingehängt, bevor die nächste beginnt. Jeder
Meilenstein muss lauffähig und geprüft sein, bevor der nächste beginnt
(kein Big-Bang am Ende, siehe CLAUDE.md).

**✓ Meilenstein 0 — erledigt — Steuer-Skript-Grundgerüst (`songtext_pipeline.py`)**
- Baut: CLI mit PFAD-Argument (Datei oder Ordner, `--recursive` für
  Unterordner), `--phase`-Auswahl inkl. Mehrfachauswahl (`--phase 2,4,5`),
  Datei-zu-DB-Zuordnung über Künstler+Titel (siehe Dokument, Abschnitt 3
  „PFAD und Audiodateien"). Die 4 Phasen zunächst nur als Platzhalter
  (Log-Zeile „Phase X würde hier laufen").
- Übernehmen: Datei-Traversal `_iter_audio_dfs` (fetch_songtext.py Zeile
  1798) direkt wiederverwendbar; Argparse-Grundgerüst kann sich an `main()`
  (Zeile 2020 ff.) orientieren, wird aber neu geschrieben — die alten Flags
  (`--fast`, `--retry-missing` usw.) entfallen zugunsten von `--phase`.
- Neu: die komplette Phasen-Auswahl-Logik und die Live-Zuordnung
  Datei↔DB-Eintrag (gibt es heute so nicht, siehe Dokument).
- Prüfen: `songtext_pipeline.py <testordner> --phase 1,2,4,5` läuft ohne
  Fehler durch und druckt für jede gewählte Phase ihre Platzhalter-Zeile;
  `--phase 3` allein ignoriert PFAD wie im Dokument beschrieben. Neuer
  pytest für die Argument-Parsing-Logik (welche Phasen bei welchem Flag
  aktiv werden, Mehrfachauswahl-Parsing).
- Abhängigkeit: keine — braucht keine der Phasen und keine DB-Änderung.
- **Umgesetzt und verifiziert:** `songtext_pipeline.py` +
  `test_songtext_pipeline.py` (17 Tests, alle grün) neu angelegt. Enthält
  `_parse_phase_list`/`_phase_arg_type` (Mehrfachauswahl-Parsing,
  Validierung 1-5), `_iter_audio_files`/`build_file_song_map`
  (Datei-zu-DB-Zuordnung, wiederverwendet `fetch_songtext._iter_audio_dfs`/
  `_read_audio_tags`/`_clean_query_title`, `cache_store.normalize_key` —
  KEINE dauerhafte Pfad-Speicherung), die 4 Platzhalter-Funktionen
  (`scan_songs`, `fetch_providers(mode)`, `evaluate_lyrics`, `write_lrc`)
  mit `_PHASE_DISPATCH`-Mapping, `main()` mit Argparse. `ruff check` +
  `ruff format` sauber. Manueller Smoke-Test bestätigt `--phase 2,4,5`
  gegen leeren Ordner, `--phase 3` ganz ohne PFAD, `--phase 9` liefert
  sauberen Fehler (Exit 2). Die volle Suite zeigt 13 vorbestehende, von
  dieser Änderung unabhängige Fehlschläge in `test_fetch_songtext.py`
  (verursacht durch ein bereits vor dieser Session uncommittetes
  Debug-Flag `_LRCLIB_LIVE_FALLBACK = False`, per `git stash` bestätigt:
  mit der committeten Version 177/177 grün) — separates, offenes Thema,
  nicht Teil dieses Umbaus.

**✓ Meilenstein 1 — erledigt — Phase 1 (`scan_songs.py`)**
- Baut: aktiver Scan-Schritt, der Audiodateien im Umfang durchgeht, Tags
  (Künstler/Titel/Genre) liest und die Song-Identität in `songs` einträgt.
- Übernehmen: `_read_audio_tags` (Zeile 256), `_clean_query_title` (Zeile
  272), `cache_store._get_or_create_song` (bereits vorhanden, unverändert
  nutzbar).
- Dauer: KEINE Dauer-Speicherung — `_load_release`/`release.json` wird
  hierfür nicht genutzt. Die `songs`-Tabelle hat keine Dauer-Spalte; das
  wäre eine Schema-Änderung gewesen, die vorher nicht abgesegnet war. Der
  Nutzer wurde dazu gezielt gefragt und hat entschieden: „Songdauer
  benötigen wir erst einmal nicht." Damit erledigt, keine Schema-Änderung.
- Neu: der aktive Scan-Schritt selbst als eigener Durchlauf — heute wird die
  Song-Identität nur als Nebeneffekt von `put_provider` beim Abfragen
  angelegt, nicht vorab.
- Einhängen: `--phase 1` im Steuer-Skript ruft `scan_songs` jetzt echt auf
  statt der Platzhalter-Zeile.
- Prüfen: Lauf gegen einen Testordner, danach per `inspect_song.py` oder
  direkt per `sqlite3`-CLI verifizieren, dass für jede Datei eine
  `songs`-Zeile existiert. pytest für die wiederverwendeten Tag-Lese-
  Funktionen (bestehende Fälle in `test_fetch_songtext.py`, z.B.
  `TestLoadRelease`, als Vorlage/Wiederverwendung prüfen).
- Abhängigkeit: keine Vorbedingung, direkt gegen echte Testdateien
  entwickelbar.
- **Umgesetzt und verifiziert:** `scan_songs.py` (Funktion
  `scan(root, recursive, conn) -> int`, liest Tags über
  `fetch_songtext._read_audio_tags`, normalisiert wie
  `songtext_pipeline.build_file_song_map`, trägt Song via
  `cache_store._get_or_create_song` ein, Dateien ohne Tags werden
  übersprungen) + `test_scan_songs.py` (8 Tests) neu angelegt.
  `_iter_audio_files` ist von `songtext_pipeline.py` nach `scan_songs.py`
  gewandert (vermeidet Zirkelimport). `songtext_pipeline.py`: `--phase 1`
  ruft jetzt echt `scan_songs.scan()` auf, DB-Connection bleibt über die
  ganze Phasen-Schleife offen statt zweimal geöffnet. `pytest
  test_scan_songs.py test_songtext_pipeline.py` 27/27 grün, volle Suite 394
  grün + dieselben 13 vorbestehenden, unabhängigen Fehlschläge wie bei
  Meilenstein 0 (keine Verschiebung). `ruff check`/`ruff format` sauber.
  Code-Diff gegengelesen.

**✓ Meilenstein 2 — erledigt — Phase 2 + Nachhol-Modus (`fetch_providers.py`)**
- Baut: Normal-Modus (alle Anbieter für jeden Song aus `songs` abfragen)
  und Nachhol-Modus (nur `status IN (nichts, fehlschlag)` erneut abfragen)
  in einem Skript, per Flag umschaltbar.
- Übernehmen: `_query_provider` (Zeile 443) komplett inkl. Rate-Limit-
  Handling und lrclib-Dump-Lookup, der `ThreadPoolExecutor`-Block aus
  `fetch_lrc` (Zeile 1354-1375), `_retry_missing` (Zeile 1825) fast
  unverändert als zweiter Modus, `_open_lrclib_dump_conn` (Zeile 1986).
- Neu: der CLI-Eintrittspunkt, der zwischen beiden Modi umschaltet, und die
  Verdrahtung mit dem Scan-Ergebnis aus Phase 1 (`songs`-Tabelle) statt
  Live-Tag-Lesen.
- Einhängen: `--phase 2` (Normal) und `--phase 3` (Nachhol) im Steuer-Skript
  rufen jetzt echt auf.
- Prüfen: Lauf gegen Testordner mit bekannten Konsens- und Fehlschlag-
  Fällen; bestehende Tests als Vorlage/Wiederverwendung migrieren
  (`test_fetch_songtext.py::TestProviderCache`, `TestRateLimit`,
  `TestRetryMissing`, `TestRetryMissingCli`, `TestLrclibDumpLookup`).
  Danach gezielt den Nachhol-Modus gegen künstlich erzeugte
  `fehlschlag`-Einträge testen.
- Abhängigkeit: braucht befüllte `songs`-Tabelle (Meilenstein 1) als Input
  für einen vollständigen Pipeline-Lauf — kann aber auch gegen die
  bestehende, bereits befüllte Produktions-Cache-DB entwickelt werden, da
  dort aus früheren `fetch_songtext.py`-Läufen schon Songs stehen.
- **Umgesetzt und verifiziert:** `fetch_providers.py` mit `fetch_all(conn)
  -> (queried, skipped)` (Phase 2: fragt jeden Song aus `songs` bei allen 4
  Anbietern ab, per `ThreadPoolExecutor`, wiederverwendet
  `fetch_songtext._query_provider` unverändert statt zu duplizieren) und
  `retry_missing(conn, providers=None)` (Phase 3: dünner Wrapper um das
  bereits fertige `fetch_songtext._retry_missing`).
  `_prepare_fetch_songtext_globals()` repliziert das
  Cache-Connection/TTL/lrclib-Dump-Setup aus `fetch_songtext.main()`.
  `songtext_pipeline.py`: `--phase 2`/`--phase 3` rufen jetzt echt auf; die
  Cache-Connection wird jetzt IMMER geöffnet (nicht mehr nur bei PFAD+
  Datei-Phase), da alle 5 Phasen die DB brauchen — nur Phase 1/4 brauchen
  zusätzlich PFAD.
  Im Bau entdeckte Lücke, direkt behoben (kein bereits bekannter Punkt aus
  dem „Weiterhin offen"-Absatz oben, sondern neu beim Umsetzen gefunden):
  `fetch_all()` fragte anfangs ALLE Songs ab, auch Hörbücher/Hörspiele/
  Instrumentals — im alten `fetch_songtext.py` verhindert `_is_skip_genre()`
  das VOR der Anbieter-Abfrage (spart ratenlimitierte Anfragen für Songs
  ohne zu erwartenden Songtext). Nachgezogen: `fetch_all()` liest `genre`
  mit, überspringt Skip-Genre-Songs komplett (mit `None`-Guard, da `genre`
  in der DB NULL sein kann), gibt `(queried, skipped)` zurück,
  `songtext_pipeline.py` zeigt die übersprungene Zahl in einer eigenen
  Log-Zeile.
  `pytest test_fetch_providers.py test_songtext_pipeline.py` 30/30 grün.
  Volle Suite: 405 grün + dieselben 13 vorbestehenden, unabhängigen
  Fehlschläge (Debug-Hack in `fetch_songtext.py`) — keine neuen.
  `ruff check`/`ruff format` sauber. Code-Diff gegengelesen. Auf einen
  Live-Smoke-Test von `--phase 2`/`--phase 3` wurde bewusst verzichtet
  (würde echte Netzwerk-Abfragen gegen die Produktions-Cache-DB auslösen) —
  die gemockten Tests decken das Verhalten ab.
- **Nachtrag — kritischer Scope-Bug, erst beim echten Lauf gefunden (nicht
  im Test):** Der Nutzer startete `songtext_pipeline.py
  ".../Saturday Night Fever" --recursive` gegen die echte Musikbibliothek
  und musste abbrechen (Strg+C) — `fetch_all()` fragte nicht nur die Songs
  des Albums ab, sondern JEDEN Song, der jemals in der Cache-DB gelandet
  war (Jahre an Historie), weil die Funktion die komplette `songs`-Tabelle
  unconditional abfragte. Hätte tausende echte, ratenlimitierte
  Live-Anfragen ausgelöst. Ein zweiter, subtilerer Teilbug kam dazu: der
  Scope wurde ursprünglich VOR Phase 1 in der Phasen-Schleife berechnet und
  sah deshalb neu gescannte Songs desselben Laufs noch gar nicht (im Log:
  „Datei-Zuordnung: 2 Datei(en)" statt der tatsächlichen 17).
  Fix: `fetch_all(conn, scope=None)` — neuer optionaler Parameter
  `scope: set[tuple[str,str]] | None`. Ohne PFAD bleibt `scope=None`
  (bewusste „ganze Bibliothek"-Absicht). Mit PFAD wird `scope` auf die
  Songs des aktuellen Albums/Laufs eingegrenzt — UND erst an der Stelle in
  der Phasen-Schleife berechnet, an der Phase 2 tatsächlich dran ist, damit
  er sieht, was ein vorheriges Phase-1 im selben Lauf gerade neu
  eingetragen hat. Phase 3 (`retry_missing`) bewusst NICHT verändert —
  bleibt PFAD-unabhängig, deckt absichtlich die ganze DB ab (bereits vorher
  vom Nutzer bestätigte Design-Entscheidung).
  Neue Tests bilden den echten Bug nach:
  `test_main_phase_1_2_fragt_nur_pfad_songs_ab_nicht_die_ganze_db` (ein
  „fremder" Song aus einem simulierten früheren Lauf wird bei
  `--phase 1,2` mit PFAD NIE angefragt, nur die 2 Album-Songs = 8 Aufrufe)
  und die Gegenprobe
  `test_main_phase_2_ohne_pfad_fragt_weiterhin_die_ganze_db_ab`.
  `pytest test_fetch_providers.py test_songtext_pipeline.py`: 35/35 grün.
  Volle Suite: 410 grün + dieselben 13 bekannten, unabhängigen
  Fehlschläge. `ruff check`/`ruff format` sauber.
  Lehre: Der Bug betraf Skalierung/Scope (kleine Test-DBs mit wenigen
  Songs verhalten sich dabei unauffällig) — reines Unit-Testen gegen kleine
  synthetische DBs hat ihn nicht aufgedeckt, erst der echte Lauf gegen die
  gewachsene Produktionsbibliothek.
- **Nachtrag — fehlende Fortschrittsanzeige, zweiter Fund aus demselben
  echten Lauf:** Nachdem der Scope-Bug behoben war, lief Phase 2 korrekt,
  aber sichtbar stumm — `fetch_all()` gab während des Laufs KEINE
  Fortschrittsanzeige aus; bei 17 Songs × 4 Providern mit teils langen
  Live-Timeouts wirkte das wie ein Hänger. Fix: `fetch_all()` bekam eine
  Fortschrittsanzeige nach dem bereits etablierten Muster aus
  `fetch_songtext._retry_missing`/`fetch_lrc` (`_print_status` für eine
  überschreibbare Statuszeile pro Song vor der Abfrage, `_tprint` für eine
  persistente Ergebniszeile danach mit Treffer-Zusammenfassung wie
  `"artist / title  2/4: lrclib, genius"`), plus eine Kopfzeile mit der
  Gesamtzahl der abzufragenden Songs. Reines Logging, keine
  Verhaltensänderung an der Abfrage-/Cache-Logik.
  `pytest test_fetch_providers.py test_songtext_pipeline.py`: 38/38 grün.
  Volle Suite: 413 grün + dieselben 13 bekannten Fehlschläge. `ruff check`/
  `ruff format` sauber.

**✓ Meilenstein 3 — erledigt — Phase 4 (`evaluate_lyrics.py`)**
- Baut: Konsens-Prüfung + Whisper-Entscheidung, wie im Dokument unter
  „Wie ruft das Steuer-Programm die Phasen auf?" beschrieben (ein Prozess,
  direkter Funktionsaufruf, Whisper-Modell + IDF-Hintergrund einmal pro
  Lauf laden, IDF alle N Songs auffrischen).
- Übernommen: `_provider_consensus`, `_whisper_best`, `_whisper_accept`,
  `_heuristic_best`, `_dedupe_by_content`, `_build_contrastive_context`,
  `_detect_lrc_language` — unverändert importiert, der Entscheidungsbaum aus
  `fetch_lrc` (Konsens → Whisper → Dauer-Heuristik) wurde als eigene
  Funktion `evaluate_song()` nachgebaut, weil `fetch_lrc()` selbst live
  abfragt UND schreibt (beides in der neuen Pipeline unerwünscht) — die
  einzelnen Algorithmen bleiben dabei 1:1 dieselben Funktionsaufrufe.
- **Modellwahl nach Sprache umgesetzt** (siehe unten, "Nachtrag: `large-v3`
  ergänzt" — dort war die Umsetzung als "noch offen" markiert): Englische
  Songs nutzen `medium`, nicht-englische (Sprach-Hint != "en") `large-v3`.
  `fetch_songtext._whisper_best()` kennt kein Modell-Argument (liest
  `_WHISPER_MODEL` immer als Modul-Global) — `_select_whisper_model()`
  ruft `fetch_songtext._detect_lrc_language()` (dieselbe Funktion, die
  `_whisper_best` intern sowieso nochmal aufruft) VOR dem `_whisper_best`-
  Aufruf auf und setzt `fetch_songtext._WHISPER_MODEL` kurzzeitig um
  (try/finally, immer zurückgesetzt) -- `fetch_songtext.py` selbst bleibt
  unangetastet. Beide Modelle werden lazy von `_get_whisper_model()`
  geladen und gecacht (höchstens je einmal pro Lauf, nicht pro Song).
- Die frühere offene Frage aus Abschnitt 2 des Dokuments — wohin Phase 4
  ihre Entscheidung schreibt, falls `--phase 5` separat aufgerufen wird —
  ist wie geplant gelöst: KEIN neuer Ablageort in der DB. `write_lrc.py`
  ruft `evaluate_lyrics.evaluate_song()` direkt erneut auf.
- Einhängen: `--phase 4`, Scope-Prinzip identisch zu Phase 2 (ohne PFAD die
  ganze DB, mit PFAD nur die Songs des aktuellen Laufs, frisch berechnet an
  der Stelle in der Phasen-Schleife, an der Phase 4 dran ist).
- **Umgesetzt und verifiziert:** `evaluate_lyrics.py` neu (`evaluate_song()`
  für einen Song, `evaluate_all()` für den Lauf über mehrere Songs inkl.
  IDF-Refresh alle 50 Songs) + `test_evaluate_lyrics.py` (20 Tests). Kandidaten
  kommen aus `ergebnisse`/`texte` (Phase 2/3 haben schon geschrieben), keine
  eigene Live-Abfrage, kein Datei-Schreibvorgang. Dabei einen echten Bug
  gefunden und behoben: `evaluate_song()` nutzte anfangs
  `fetch_songtext._lookup_cache_song_id()` für die song_id-Suche, die intern
  am Modul-Global `_cache_conn` hängt statt am übergebenen `conn`-Parameter
  — bei nicht vorbereiteten Globals (z.B. isolierter Unit-Test oder ein
  eigenständiger `--phase 5`-Lauf) lieferte das immer `None`. Jetzt fragt
  `evaluate_song()` die `songs`-Tabelle direkt über den übergebenen `conn`
  ab. `pytest test_evaluate_lyrics.py` 20/20 grün.

**✓ Meilenstein 4 — erledigt — Phase 5 (`write_lrc.py`)**
- Baut: `.lrc` schreiben/unverändert lassen/löschen je nach Phase-4-
  Entscheidung, JSON-Ordner-Cache und Ordner-Lock pflegen.
- Übernommen: der Schreib-/Vergleichsblock aus `main()`, `_load_cache`/
  `_save_cache`, `_try_claim_folder`/`_release_folder`, `_cache_entry_valid`
  — unverändert importiert und wiederverwendet, gleiche Cache-Eintrag-
  Struktur (v/r/outcome/providers/...) wie im alten `fetch_songtext.py`,
  damit `lrc_analyse.py`/`lrc_recheck.py` den Cache weiter lesen können.
- Nichts Neues in der Datenbank: `write_lrc.write_all()` ruft
  `evaluate_lyrics.evaluate_song()` direkt für jeden Song erneut auf (kein
  DB-Umweg) — dafür bereitet es dieselben `fetch_songtext`-Globals vor wie
  Phase 4 (`fetch_providers._prepare_fetch_songtext_globals`), damit ein
  eigenständiger `--phase 5`-Lauf den Whisper-Transkript-Cache findet statt
  unnötig neu zu transkribieren.
- Bekannter, akzeptierter Unterschied zum alten `fetch_songtext.py`: der
  explizite Genre-Skip mit `"reason": "genre"` passiert jetzt schon in
  Phase 2 — ein Skip-Genre-Song hat hier deshalb einfach keine Provider-
  Kandidaten und landet als `"kein-provider"`. Funktional gleichwertig
  (kein falscher Songtext, vorhandene `.lrc` wird trotzdem gelöscht), nur
  die berichtete Ursache im Cache-Eintrag unterscheidet sich.
- Einhängen: `--phase 5`, braucht PFAD zwingend (schreibt echte Dateien) —
  ohne PFAD kein Fehler, nur eine Meldung, dass nichts zu schreiben ist.
  `_PHASES_NEEDING_FILE` um `5` ergänzt.
- **Umgesetzt und verifiziert:** `write_lrc.py` neu (`write_all()`) +
  `test_write_lrc.py` (6 Tests: schreiben, löschen bei Nichtfund,
  unveränderter Inhalt wird nicht neu geschrieben, JSON-Cache-Skip beim
  zweiten Lauf, `force=True` umgeht den Skip, leere `file_song_map`).
  Zusätzlich zwei End-zu-Ende-Tests in `test_songtext_pipeline.py`, die
  einen VOLLEN Pipeline-Lauf (alle 5 Phasen, gemockte Provider-Antworten im
  3-Provider-Konsens-Fall, kein Whisper nötig) gegen einen Testordner
  fahren und die tatsächlich geschriebene `.lrc`-Datei prüfen — inklusive
  Wiederholbarkeits-Test (zweiter identischer Lauf fasst die Datei dank
  JSON-Cache-Skip nicht erneut an, `mtime` unverändert). `pytest
  test_evaluate_lyrics.py test_write_lrc.py test_songtext_pipeline.py`
  46/46 grün. Volle Suite: 458 grün + dieselben 13 vorbestehenden,
  unabhängigen Fehlschläge (Debug-Hack in `fetch_songtext.py`) — keine
  neuen. `ruff check`/`ruff format` sauber auf allen neuen/geänderten
  Dateien. Auf einen Live-Smoke-Test gegen die echte Produktionsbibliothek
  wurde für diesen Meilenstein bewusst verzichtet (die End-zu-Ende-Tests
  decken die reale Verdrahtung zwischen allen 5 Phasen bereits ab) — der
  Nutzer testet manuell selbst im Anschluss.

Damit sind alle 5 Phasen der Pipeline real implementiert. Was noch offen
bleibt: eine Entscheidung, was mit `fetch_songtext.py` selbst passiert
(behalten/löschen/parallel betreiben, siehe Hinweis oben) — bewusst nicht
Teil dieses Umbaus.

## ✓ fetch_songtext.py v1.13.0 — lokaler LRCLib-Datenbank-Abzug vor der Live-Abfrage

**Auslöser:** Neben der eigenen Cache-DB gibt es jetzt einen lokalen Abzug der
kompletten LRCLib-Datenbank unter `/Volumes/music/db.sqlite3` (SMB-Netzlaufwerk,
ca. 112 GB, Original-LRCLib-Schema mit Tabellen `tracks`/`lyrics`, per litestream
repliziert, aktuell nicht mehr aktiv befüllt). Bevor die lrclib-Quelle LIVE
gefragt wird, lohnt es sich, zuerst diesen Abzug zu durchsuchen — spart eine
echte Netzabfrage, wenn der Song dort bereits steht.

**Lösung:** Neue Funktion `cache_store.lookup_lrclib_dump(conn, artist_key,
title_key)` (liegt im Cache-Modul, kennt aber weiterhin keine Provider-/
Whisper-Logik — reiner zusätzlicher Lookup gegen eine zweite, extern verwaltete
SQLite-Datei). Exakter Abgleich auf `tracks.artist_name_lower`/`tracks.name_lower`
— bewusst KEINE Songdauer, KEINE Fuzzy-Ähnlichkeit: eine Recherche im echten
`syncedlyrics`-Quellcode (`syncedlyrics/providers/lrclib.py`) zeigte, dass die
echte Live-Suche selbst auch nur Text vergleicht ("Künstler - Titel"), nicht
Dauer — ein exakter Abgleich auf die bereits normalisierten Schlüssel
(`cache_store.normalize_key`, wie beim eigenen Cache) ist hier einfacher als
Fuzzy-Scoring nachzubauen. Mehrfachtreffer (mehrere Alben/Versionen desselben
Songs, z.B. "queen"/"bohemian rhapsody" mit 4 exakten Treffern) werden
pragmatisch aufgelöst: zuerst ein Track mit `synced_lyrics`, sonst mit
`plain_lyrics`, sonst gilt "kein Songtext" — bei mehreren gleichwertigen
Kandidaten gewinnt deterministisch die kleinste `tracks.id` (keine Dauer-Angabe
zum Abgleichen verfügbar, Fuzzy-Matching bewusst abgelehnt).

Eingehängt in `fetch_songtext._query_provider`, genau zwischen dem eigenen
Cache-Lookup und dem `--cache-only`-Guard, nur für `provider == "lrclib"` und
nur wenn `not _cache_refresh` (`--refresh-cache`/`--force` erzwingen weiterhin
eine wirklich frische Live-Abfrage und umgehen daher auch den Dump, genau wie
den eigenen Cache). Ablauf: 0 Treffer im Dump → weiter wie bisher (Schritt
2/3, kein Cache-Schreibvorgang — kein echter Versuch, analog zum
`--cache-only`-Guard). Treffer mit Songtext → genau wie ein Live-Treffer über
`cache_store.put_provider(..., "treffer", content)` im eigenen Cache
abgelegt und sofort zurückgegeben, kein `subprocess.run` mehr nötig. Treffer
ohne Songtext (Instrumental, oder kein `lyrics`-Eintrag verknüpft) → als
`"nichts"` gewertet und ebenso gecacht, ohne Live-Nachfrage. `--cache-only`
ist hier irrelevant (der Dump ist keine Live-Abfrage) — ein Dump-Treffer wird
also auch unter `--cache-only` verwendet.

**Verbindungsmanagement:** `_lrclib_dump_conn` wird EINMAL pro Lauf in
`main()` geöffnet (`sqlite3.connect(f"file:{path}?mode=ro&immutable=1",
uri=True, check_same_thread=False)`) und über den bestehenden `_cache_lock`
serialisiert — kein eigenes Lock-Objekt, die Lookups sind kurz. Scheitert das
Öffnen (Mount fehlt, Datei fehlt, sonstiger Fehler), bleibt `_lrclib_dump_conn`
`None` — still degradieren, kein Absturz, keine störende Meldung (Cache ist
nur Beschleuniger, kein Fundament, siehe `CACHE_DESIGN.md`). Genauso bei
jedem Fehler während eines einzelnen Lookups.

**SMB-Falle (verifiziert):** `sqlite3.connect("file:...?mode=ro", uri=True)`
scheitert auf dem SMB-Mount mit "unable to open database file" — SMB
unterstützt die von SQLite fürs Locking benötigten Dateisperren nicht. Erst
`immutable=1` (überspringt jegliches Locking, setzt voraus, dass sich die
Datei während des Zugriffs nicht ändert — hier unproblematisch, der Abzug
wird aktuell nicht mehr aktiv befüllt) behebt das.

`--retry-missing` (siehe oben) profitiert automatisch mit, ohne Zusatzarbeit:
es setzt `_cache_refresh` für die Dauer des Laufs auf `True`, und der
Dump-Lookup ist wie der eigene Cache-Lookup an `not _cache_refresh` gebunden
— ein `--retry-missing`-Lauf fragt also, wie beabsichtigt, immer wirklich
live nach, nie aus dem Dump.

Kein neues CLI-Flag (YAGNI).

Version: `1.13.0` (Minor — neue Funktionalität, kein reiner Bugfix).

Tests: `test_cache_store.py::TestLookupLrclibDump` (synthetische Mini-SQLite-DB
mit `tracks`/`lyrics`-Tabellen als Fixture, NICHT die echte 112GB-Datei — auf
CI/anderen Rechnern nicht erreichbar): Treffer mit `synced_lyrics`, Treffer nur
mit `plain_lyrics`, Mehrfachtreffer (synced schlägt plain, gleichwertige
Kandidaten → kleinste `tracks.id`), Treffer ganz ohne Songtext (auch bei
Mehrfachtreffer), Track ohne verknüpfte `lyrics`-Zeile, kein Treffer
überhaupt, sowie dass die Funktion selbst nicht normalisiert (Aufrufer muss
bereits normalisierte Schlüssel übergeben). `test_fetch_songtext.py::
TestLrclibDumpLookup` (Integration in `_query_provider`, In-Memory-SQLite
statt echter Datei): Dump-Treffer mit Songtext wird zurückgegeben und in den
eigenen Cache geschrieben, Dump-Treffer ohne Songtext wird als "nichts"
gecacht, 0 Treffer im Dump fällt auf die Live-Abfrage zurück, fehlende
Dump-Verbindung (`None`) fällt auf Live zurück, Nicht-lrclib-Provider
ignorieren den Dump komplett, `--refresh-cache`/`--force` umgehen auch den
Dump, `--cache-only` + Dump-Treffer wird trotzdem verwendet, `--cache-only` +
Dump-Miss liefert weiterhin `None` ohne Live-Versuch, ein Fehler bei der
Dump-Abfrage (z.B. geschlossene Verbindung) stört den Lauf nicht und fällt
auf Live zurück.

## ✓ fetch_songtext.py v1.12.0 — `--retry-missing NAME|all`: gezielte Cache-Neuabfrage

**Auslöser:** In einer Session wurde entdeckt, dass lrclib stundenlang
fälschlich in der langen "gesperrt"-Ruhephase steckte (`_rate_limit_state`/
`_RATE_LIMIT_LONG_PAUSE_SEC`, siehe `_rate_limit_wait`/`_rate_limit_report`),
obwohl ein direkter Live-Test zeigte, dass der Provider einwandfrei
funktionierte. Dadurch stehen in der Cache-DB vermutlich etliche
(Song, Provider)-Kombinationen mit `status='nichts'` oder `status='fehlschlag'`,
bei denen ein erneuter Versuch heute erfolgreich wäre — ein kompletter
`--force`-Lauf über die ganze Bibliothek wäre dafür unnötig teuer (jeder
Provider für jeden Song erneut, nicht nur die betroffenen Lücken).

**Lösung:** Neues Flag `--retry-missing lrclib|musixmatch|netease|genius|all`
(ungültiger Wert -> `parser.error()` via argparse `choices`). Reine
Cache-DB-Operation: kein Whisper, keine `.lrc`-Datei wird gelesen oder
geschrieben, keine Änderung an der Musikbibliothek. Der positionale `path`-
Parameter ist dafür nicht mehr zwingend (`nargs="?"`, aber weiterhin
Pflicht für den normalen Betrieb — geprüft per `parser.error()`, falls
weder `path` noch `--retry-missing` angegeben ist).

Neue Funktion `_retry_missing()`: sucht in `ergebnisse` alle Zeilen mit
`status IN ('nichts', 'fehlschlag')` für die betroffenen Provider (optional
eingeschränkt via `--artist`/`--title`: beides zusammen -> genau ein Song
(Lookup wie in `inspect_song.py` — unbekannter Song -> Fehlermeldung,
Exit-Code 1), `--artist` allein -> alle Songs dieses Künstlers in der
Cache-DB (unbekannter Künstler -> ebenfalls Fehlermeldung, Exit-Code 1),
keins von beiden -> keine Eingrenzung, ganze Cache-DB), baut je Zeile die
Suchanfrage aus den in der Cache-DB gespeicherten `artist_key`/`titel_key`
und ruft `_query_provider()` unverändert erneut auf (inkl. Rate-Limit-
Handling und Cache-Schreiblogik über `cache_store.put_provider`, die den
alten Eintrag automatisch überschreibt). Die Zeilen werden dabei immer
sortiert nach `artist_key`, `titel_key` (unabhängig von der Eingrenzung)
abgearbeitet — vorher war die Reihenfolge zufällig (SQLite-Rowscan-
Reihenfolge ohne `ORDER BY`), was bei einer Künstler-weiten Neuabfrage
unnötig unübersichtlich in der Konsolenausgabe war. Dafür wird
`_cache_refresh` für die Dauer des Laufs auf `True` gesetzt und danach
zurückgesetzt — ohne das würde `_query_provider` ein gecachtes `"nichts"`
als gültigen, nicht abgelaufenen Cache-Treffer werten und nie live
nachfragen (nur `status='fehlschlag'` erzwingt dort von sich aus einen
Live-Versuch). Schließt sich mit `--no-cache` (braucht die Cache-DB) und
`--cache-only` (verbietet jede Live-Abfrage) aus.

**Bekannte Einschränkung:** Die Cache-DB speichert nur normalisierte
Schlüssel (NFC, gestrippt, kleingeschrieben — siehe `cache_store.
normalize_key`), nicht die Original-Groß-/Kleinschreibung von Künstler/Titel.
Die erneute Suchanfrage nutzt daher zwangsläufig die kleingeschriebene Form,
was die Trefferquote gegenüber der ursprünglichen Live-Abfrage in seltenen
Fällen leicht verschlechtern könnte.

**Rate-Limit-Ruhephase:** `_rate_limit_state` lebt rein im Prozessspeicher,
nicht in der Cache-DB — ein separat gestarteter `--retry-missing`-Lauf
beginnt automatisch mit leerem Zustand, unabhängig davon, ob ein früherer
(anderer) Lauf gerade "gesperrt" war. Der zugrundeliegende Stuck-Bug selbst
(fälschliche lange Ruhephase trotz funktionierendem Provider) wird hier
NICHT behoben — nur festgestellt, dass er `--retry-missing` in der Praxis
nicht blockiert.

**Bugfix — "weiterhin kein Treffer" verwechselte echtes Fehlen mit erneutem
transienten Fehler:** Beim ersten echten Testlauf (`--retry-missing lrclib
--artist "Simon & Garfunkel" --title "El Condor Pasa"`) meldete das Skript
"weiterhin kein Treffer", obwohl der Song bei lrclib nachweislich existiert
(manuell per direktem `syncedlyrics`-Aufruf verifiziert — sofortiger
Treffer). Ursache: die Cache-DB zeigte für diese Zeile hinterher
`status='fehlschlag'`/`fehlergrund='rate_limit'`, nicht `status='nichts'` —
`_query_provider()` gibt bei EINEM erneuten transienten Fehler (Timeout/
Rate-Limit/Captcha, hier ausgelöst durch `syncedlyrics`' generische
"An error occurred while searching for an LRC on …"-Meldung, die entgegen
dem bisherigen Docstring-Kommentar NICHT nur bei NetEase auftritt, sondern
bei jedem Provider, dessen `get_lrc()` eine Exception wirft — siehe
`syncedlyrics/__init__.py`) exakt denselben Rückgabewert `(provider, None)`
zurück wie bei einem echten, bestätigten "nichts gefunden". `_retry_missing()`
konnte daraus bisher nicht unterscheiden. Fix: nach jedem `path is None`-Fall
wird die soeben von `_query_provider()` geschriebene Zeile in `ergebnisse`
nachgeschlagen (`status`/`fehlergrund`) — bei `status='fehlschlag'` heißt es
jetzt „weiterhin Fehler (‹grund›) — später erneut versuchen" statt „weiterhin
kein Treffer", und die Abschlusszeile zählt beide Fälle getrennt
(`N weiterhin ohne Treffer, M weiterhin mit Fehler`). Am realen Fall
bestätigt: ein erneuter Lauf direkt danach fand den Song sofort.

Version: `1.12.0` (Minor — neues Flag mit eigenem Verhalten, kein reiner Bugfix).

```bash
python3 fetch_songtext.py --retry-missing lrclib
python3 fetch_songtext.py --retry-missing all --artist "Nina Hagen" --title "Naturträne"
python3 fetch_songtext.py --retry-missing lrclib --artist "Nina Hagen"
```

Tests: neue Klassen `TestRetryMissing` (Cache mit gemischten Status,
`_retry_missing()` direkt aufgerufen — nur passende (Song, Provider)-Zeilen
werden angefragt, unberührte Einträge bleiben unverändert, `--artist`+
`--title` beschränkt auf einen Song, `--artist` allein beschränkt auf alle
Songs dieses Künstlers (ein anderer Künstler in der Cache-DB bleibt
unberührt), unbekannter Song bzw. unbekannter Künstler bricht mit
Exit-Code 1 ab, `all` fragt alle vier Provider ab, leere Treffermenge nur
ein Hinweis, Ergebnisse laufen sortiert nach Artist/Titel unabhängig von
der Einfügereihenfolge, ein simulierter transienter Fehlschlag wird als
„weiterhin Fehler (‹grund›)" gemeldet und getrennt gezählt, ein echtes
„nichts" weiterhin als „weiterhin kein Treffer") und `TestRetryMissingCli`
(nur Argparse-Fehlerfälle vor jedem Cache-DB-Zugriff: ungültiger
Providername, fehlender `path`, `--no-cache`/`--cache-only`-Ausschluss,
`--title` ohne `--artist`, `--artist` ohne `--retry-missing`). Bewusst KEIN
Subprozess-Test für den Erfolgsfall: `main()` öffnet die Cache-DB immer
relativ zu `__file__`, ein `--retry-missing`-Lauf über
`subprocess.run(["python3", "fetch_songtext.py", ...])` würde also die ECHTE
Produktions-`fetch_songtext_cache.db` öffnen und live abfragen (gleiches
Problem wie bei `--fast`, siehe `TestFastFlagMain`).

## ✓ compare_whisper_models.py — Modellqualitätsvergleich small/medium/turbo

**Motivation:** v1.7.0 hatte bei einem einzelnen Testfall (Rheingold
„Dreiklangsdimensionen") einen deutlichen Qualitätsunterschied zwischen den
Whisper-Modellen gemessen (`base` 19 %, `small` 53 %, `medium` 61 %) — Anlass
für die Frage, ob `medium` oder ein Turbo-Modell (`large-v3-turbo`) das
produktiv genutzte `small` auf einer breiteren Stichprobe schlagen. Da es hier
rein um Transkriptionsqualität geht (Geschwindigkeit ist ausdrücklich
irrelevant), gibt es KEIN automatisches Scoring — nur nebeneinandergestellte
Transkripte zum manuellen Lesevergleich.

Neues eigenständiges Skript (nach dem Vorbild von `inspect_song.py`/
`lrc_recheck.py`): zieht `--n` (Standard 20) Songs aus der Cache-Datenbank,
die dort mindestens einen Provider-Treffer haben (`songs JOIN ergebnisse`,
`status='treffer'`), sprachlich stratifiziert ~80 % englisch / ~20 % deutsch
(`round(n * 0.8)` / Rest), und transkribiert jeden gefundenen Song FRISCH mit
allen drei Modellen (`small`, `medium`, `turbo`; Turbo-Modellname mit der
installierten `faster-whisper`-Version 1.2.1 live verifiziert — `"turbo"`
funktioniert direkt, kein `"large-v3-turbo"` nötig). Ob für einen Song
bereits ein Whisper-Transkript im Cache existiert, ist für die Auswahl
bewusst KEIN Kriterium (`select_all_candidate_pairs()`, ursprünglich
`songs JOIN transkripte`) — jeder gefundene Song wird ohnehin frisch
transkribiert, ein Provider-Treffer wird nur gebraucht, damit die Sprache
klassifizierbar ist.

**Sprachstratifizierung 80/20 (v2):** Ein erster echter Testlauf zog die
Stichprobe rein zufällig, ohne Sprachbezug — bei einer überwiegend
englischsprachigen Bibliothek verzerrt das den Modellvergleich unnötig. Vor
der finalen Auswahl ermittelt `select_language_pools()` für die (zufällig
gemischten) Kandidaten aus der Cache-DB die Sprache (`detect_language_hint()`,
Provider-Kandidatentexte + `fetch_songtext._detect_lrc_language`, wie im
Produktivbetrieb) und sortiert sie in einen "en"- und einen "de"-Pool ein —
Kandidaten mit anderer/nicht erkennbarer Sprache zählen zu keinem Pool und
werden übersprungen. Jeder Pool enthält neben der Zielquote (`round(n*0.8)`
bzw. Rest) einen Puffer (Standard das 3-fache) als Ersatzkandidaten für den
Fall, dass ein Primärkandidat später keinen Bibliothekstreffer hat — die
Klassifizierung bricht ab, sobald beide Puffer voll sind oder die
Kandidatenliste erschöpft ist (nicht die komplette Cache-DB muss klassifiziert
werden). Die dabei bereits ermittelte Sprache wird direkt als
Whisper-Sprach-Hint weiterverwendet — ein zweiter `detect_language_hint`-
Aufruf nach der Bibliotheksauflösung entfällt für diese Songs (nur
Pflicht-Songs, die nicht stratifiziert werden, brauchen ihn weiterhin
separat nach der Auflösung).

**Gezielte Ein-Durchlauf-Suche mit Früh-Abbruch statt Voll-Index (v2):**
Vorher baute das Skript IMMER zuerst einen kompletten Index über die gesamte
Bibliothek auf (`build_library_index()`, jede der ~19.000 Dateien einzeln mit
`mutagen` gelesen), bevor es die paar gesuchten Songs nachschlug (`resolve_
songs()`) — unnötige Arbeit, denn die gesuchten `(artist_key, titel_key)`-
Paare stehen bereits vorher aus der Cache-DB-Auswahl fest. Zusätzlich lief ein
echter Testlauf gegen die auf SMB liegende Bibliothek in einen Hänger — je
mehr Dateien angefasst werden, desto größer das Risiko. `build_library_
index()`/`resolve_songs()`/`resolve_forced_songs()` wurden deshalb zu einer
Funktion `resolve_all_songs()` verschmolzen, die Pflicht-Songs UND die
sprachlich stratifizierten Zufallskandidaten (inkl. Ersatzkandidaten aus
demselben Sprachpool) in EINEM Bibliotheksdurchlauf sucht und sofort
abbricht, sobald alle gesuchten Songs gefunden wurden. Songs ohne
Bibliothekstreffer werden übersprungen; reicht ein Sprachpool trotz Puffer
nicht aus, läuft das Skript mit weniger als `--n` Songs weiter (kein
Abbruch).

```bash
python3 compare_whisper_models.py
python3 compare_whisper_models.py --n 10 --seed 42
python3 compare_whisper_models.py --library /Volumes/music/musik --output-dir whisper_modellvergleich
python3 compare_whisper_models.py --include "Kraftwerk:Autobahn" --include "Nina Hagen:Naturträne"
```

Pro Song eine TXT-Datei (`<Artist>_<Titel>_modellvergleich.txt`) mit
`Artist:`/`Titel:`/`Sprache (Hint):`-Kopf und drei Abschnitten (`=== small
===`, `=== medium ===`, `=== turbo ===`) — die Transkripte kommen aus
`fetch_songtext._transcribe()` und sind daher tokenisiert (klein geschrieben,
ohne Satzzeichen), nicht Fließtext mit Original-Zeichensetzung. Zusätzlich
eine `modellvergleich_index.txt` mit der Liste aller bearbeiteten und
übersprungenen Songs. Reiner Lesezugriff auf Cache-DB und Bibliothek, keine
Schreibzugriffe außer den neuen Ausgabedateien im `--output-dir`.

**Speicherschonung (Modell-für-Modell statt Song-für-Song):** Ein erster
Testlauf auf einer 8-GB-Maschine zeigte, dass alle drei Modelle gleichzeitig
im Speicher (small+medium+turbo ≈ 3,6 GB) zu Swapping führen konnten — ein
Song brauchte über 20 Minuten statt der erwarteten Sekunden bis wenigen
Minuten. Deshalb läuft die äußere Schleife jetzt über die drei Modelle statt
über die Songs: jedes Modell wird EINMAL geladen, transkribiert ALLE
gefundenen Songs, wird danach wieder aus dem Speicher entfernt (`del` aus
`fetch_songtext._whisper_models` + `gc.collect()`), bevor das nächste Modell
geladen wird. Nie mehr als ein Modell gleichzeitig resident.

**Ausgabe so schnell wie möglich, nicht erst am Ende (v2):** Vorher wurden
alle Transkripte aller drei Modelle im Speicher gesammelt und die
Pro-Song-TXT-Dateien erst geschrieben, nachdem `turbo` als letztes Modell
komplett durchgelaufen war — bei einer größeren Stichprobe blieb die
Ausgabe damit unnötig lange komplett unsichtbar, und ein Abbruch mitten in
`medium`/`turbo` hätte auch die längst fertigen `small`-Ergebnisse verloren.
Jetzt legt `write_song_header()` jede Song-Datei SOFORT mit Kopf an (noch
vor dem ersten Modell-Durchlauf), und `append_model_transcript()` hängt
direkt nach jeder einzelnen Transkription den jeweiligen Modell-Abschnitt an
die Datei an — nichts wird mehr im Speicher zwischengehalten. Nach dem
`small`-Durchlauf sind damit bereits alle Song-Dateien mit diesem Abschnitt
vollständig auf der Platte, unabhängig davon, ob `medium`/`turbo` noch
laufen. Format der fertigen Datei unverändert. `write_song_report()`
(schrieb alle drei Abschnitte auf einmal) ist damit entfallen.

**Sprach-Hint für einen fairen Vergleich:** Im Produktivbetrieb
(`fetch_songtext.py`) bekommt Whisper immer einen Sprach-Hint aus den
Provider-Kandidatentexten (`_detect_lrc_language`) — ohne Hint wäre der
Modellvergleich nicht repräsentativ für die echte Nutzung, und größere
Modelle sind bei der Selbst-Erkennung der Sprache erfahrungsgemäß robuster
als `small`, was `small` unfair benachteiligen könnte. Deshalb ermittelt das
Skript vor der Transkription pro Song EINMAL einen Sprach-Hint aus den
gecachten Provider-Kandidatentexten (`ergebnisse` JOIN `texte`,
`status='treffer'`) und übergibt ihn identisch an alle drei Modelle. Ohne
Provider-Treffer im Cache bleibt der Hint `None` (Produktiv-Fallback). Die
erkannte Sprache erscheint in der TXT-Datei als `Sprache (Hint): de` bzw.
`Sprache (Hint): nicht erkannt`.

**Pflicht-Songs (`--include`):** "Nina Hagen Band" / "Rangehn" ist fest
verdrahtet immer zusätzlich zur Zufallsauswahl in der Stichprobe (unabhängig
von `--n`/`--seed`) — war der Song ohnehin unter den zufällig gezogenen, wird
er dedupliziert statt doppelt verarbeitet. Mit `--include ARTIST:TITEL`
(wiederholbar) lassen sich weitere Pflicht-Songs ergänzen; sie brauchen dafür
keinen Cache-Eintrag, nur einen Treffer im Bibliotheks-Scan. Fehlt die
Audiodatei in der Bibliothek, erscheint das als klarer Hinweis in der
Konsolenausgabe und als eigener Abschnitt in `modellvergleich_index.txt`.

## ✓ inspect_song.py — Diagnose-Werkzeug für einzelne Songs

Neues eigenständiges Skript (nach dem Vorbild von `lrc_recheck.py`): fragt die
Cache-Datenbank (`fetch_songtext_cache.db`) gezielt für einen Künstler/Titel
ab und schreibt Provider-Texte (Genius, Netease, Lrclib, Musixmatch) sowie
das Whisper-Transkript nebeneinander in eine lesbare TXT-Datei — genau das,
was zuvor manuell per `sqlite3`-Kommandozeile gemacht wurde, jetzt als
wiederverwendbares Werkzeug. Reiner Lesezugriff, ändert nichts am Cache.

```bash
python3 inspect_song.py --artist "Nina Hagen" --title "Naturträne"
python3 inspect_song.py --artist "Nina Hagen" --title "Naturträne" --output custom_name.txt
```

Pro Provider (feste Reihenfolge genius/netease/lrclib/musixmatch) ein
Abschnitt: Text bei `status="treffer"`, `(kein Treffer)` bei `"nichts"`,
`(Fehlschlag: <grund>)` bei `"fehlschlag"`, `(nie abgefragt)` ohne
`ergebnisse`-Zeile. Whisper-Abschnitt analog mit `(kein Transkript
vorhanden)`. Song nicht in der DB gefunden -> Fehlermeldung auf stderr,
Exit-Code 1, keine Datei wird geschrieben.

## ✓ fetch_songtext.py v1.11.0 — --wer-experiment entfernt, Kontrastive-Marge-Rückgabewert-Refactor

**Problem:** `--wer-experiment` (Provider-Konsens und Whisper-Verifikation
probeweise über Word Error Rate statt Jaccard/IDF-Jaccard) war ein
experimentelles Flag, das inhaltlich bereits verworfen wurde: längenempfindlich,
26 von 38 echten Treffern wurden fälschlich abgelehnt (siehe
`wer_whisper_uneinigkeit.md`). Trotzdem lag der komplette Code — Flag,
Schwellen, WER-Berechnung, Vergleichspfade in `_provider_consensus()` und
`_whisper_best()`, Sicherheitsnetz-Sentinel, CSV-Logging — weiter tot im
Skript.

**Lösung:** `--wer-experiment` vollständig entfernt: CLI-Flag, `_wer_experiment`,
`_score_against_wer()`, `_wer()`, `_edit_distance()`, `_wer_symmetric()`
(beide nur noch für die WER-Berechnung gebraucht, sonst keine Aufrufer mehr),
`_log_wer_experiment()`, `_WER_CONSENSUS_MAX_THRESHOLD`,
`_WER_WHISPER_MAX_THRESHOLD`, `_WER_EXPERIMENT_LOG_PATH`,
`_WER_SKIP_NO_TRANSCRIPT` (inkl. des zugehörigen Sicherheitsnetz-Zweigs in
`_whisper_best()` und des `elif`-Zweigs in `fetch_lrc()`), sowie der komplette
WER-Vergleichspfad in `_provider_consensus()` (die Funktion prüft Konsens
jetzt wieder ausschließlich über Jaccard, wie vor der Einführung des
Experiments). Zusätzlich das reine Logging von `contrastive_experiment_log.csv`
entfernt (`_log_contrastive_experiment()` + `_CONTRASTIVE_EXPERIMENT_LOG_PATH`)
— sein Zweck (Validierung der kontrastiven Marge gegen die alte absolute
Schwelle) ist erledigt. Die kontrastive Marge selbst
(`_contrastive_margin_and_decision`, `_contrastive_result_for`,
`_CONTRASTIVE_MARGIN`, `_CONTRASTIVE_ABSOLUTE_FLOOR` usw.) ist davon nicht
betroffen — bleibt vollständig erhalten.

**Architektur-Cleanup (keine Verhaltensänderung):** `_whisper_best()` schrieb
die kontrastive Marge bisher in ein optionales `debug_scores`-Dict, das
`fetch_lrc()` für die echte Akzeptanz-Entscheidung auswertete — obwohl der
Parameter wie reines Debug-Logging aussah, war er die einzige Quelle für die
Marge. Jetzt gibt `_whisper_best()` die Marge direkt als zusätzlichen
(siebten) Rückgabewert zurück (`(best_path, score, has_vocals, words,
model_used, language, contrastive_margin)`), `fetch_lrc()` liest sie direkt
aus dem Rückgabewert. Der `debug_scores`-Parameter entfällt komplett — er
wurde nur noch von den beiden jetzt entfernten Loggern gebraucht. Die Marge
wird jetzt unbedingt berechnet (vorher nur wenn `debug_scores is not None`
übergeben wurde), was `fetch_lrc()` ohnehin immer tat — funktional identisch,
nur nicht mehr über ein Seiteneffekt-Dict versteckt.

Version: `1.11.0` (Minor — ein CLI-Flag verschwindet komplett, Rückgabewert-
Signatur von `_whisper_best()` ändert sich, kein reiner Patch).

Tests: alle `--wer-experiment`-bezogenen Testklassen entfernt
(`TestWerExperimentWhisperSafetyNet`, `TestFetchLrcWerSkip`,
`TestLogWerExperiment`, WER-Teile aus `TestWerCalculation`/
`TestProviderConsensusWerExperiment`/`TestWhisperAccept`), `TestLogContrastive
Experiment` entfernt. `TestWhisperBestContrastiveExperiment` umgeschrieben:
prüft jetzt direkt den zurückgegebenen `margin`-Wert (statt eines
`debug_scores`-Dicts) inkl. Gegenprobe, dass `_whisper_accept()` mit einer
tatsächlich berechneten (nicht `None`) Marge entscheidet.

## ✓ fetch_songtext.py v1.10.1 — Bugfix: --cache-only blockierte Live-Whisper + ein Whisper-Lauf statt bis zu vier

**Bugfix — `--cache-only` blockierte fälschlich Live-Whisper:** `--cache-only`
war laut ursprünglichem Design (siehe Docstring bei `_cache_only`) immer nur
für Live-PROVIDER-Abfragen zuständig, nie für Whisper. Der v1.10.0-Umbau
(siehe Eintrag darunter, Abschnitt „Judgment Call") hatte das versehentlich
gekoppelt: `_whisper_best()` übersprang die Whisper-Transkription bei
fehlendem Transkript-Cache auch unter `--cache-only` (`model_used =
_CONTRASTIVE_SKIP_NO_TRANSCRIPT`). Das bricht neue Songs komplett — kein Song
hätte je zum ersten Mal verifiziert werden können, solange `--cache-only`
aktiv ist. Jetzt korrigiert: Ein Cache-Miss transkribiert immer live,
unabhängig von `--cache-only`. Der `if _cache_only: return (...)`-Sicherheits-
netz-Block in `_whisper_best()` ist ersatzlos entfernt.

**Effizienz — ein Whisper-Lauf statt bis zu vier pro Track:**
`_whisper_best()` berechnete bisher pro Kandidaten-LRC (lrclib, genius,
musixmatch, netease, „lokal" — bis zu 4-5) einen eigenen Start-Zeitpunkt aus
dem jeweils ersten Zeitstempel und transkribierte für JEDEN unterschiedlichen
(auf 3 Sekunden gerundeten) Start separat mit Whisper. Das war unnötig: alle
Kandidaten beschreiben dieselbe Audiodatei, die Vergleichslogik ist ohnehin
ein reiner Wort-/Score-Vergleich ohne Zeit-Ausrichtung. Jetzt: EIN
Start-Zeitpunkt (`min()` über alle Kandidaten-Start-Hinweise, damit keine
echten frühen Vokale verpasst werden), EIN `_transcribe()`-Aufruf, das eine
Transkript wird gegen alle Kandidaten gescort — spart bis zu 75 % der
Whisper-Laufzeit pro Track ohne Genauigkeitsverlust. Entfernt wurden dabei
`candidate_starts` (Liste von (Pfad, Start)-Paaren pro Kandidat), das
`cache`/`raw_cache`-Dict (keyed by gerundetem Start), die
`chosen_key`/`chosen_words`-Logik sowie die `distinct`/`done`-
Fortschrittszählung („Whisper transkribiert (2/3)…"); an ihrer Stelle gibt es
nur noch eine einzelne `start`-Variable, `words`/`raw_words`/`no_speech`/
`logprob` als Einzelwerte (kein Dict mehr) und eine Kandidaten-Scoring-
Schleife über `for p in candidates:`.

Version: `1.10.1` (Patch — Bugfix + Effizienzverbesserung, kein
Verhaltenswechsel im Ergebnis der Verifikation).

Tests: `TestContrastiveExperimentWhisperSafetyNet::
test_cache_only_kein_cache_treffer_kein_live_transcribe` erwartete noch das
alte (falsche) Verhalten und wurde umgeschrieben zu
`test_cache_only_transkribiert_trotzdem_live_bei_cache_miss` (erwartet jetzt
live-`_transcribe`-Aufruf statt `_CONTRASTIVE_SKIP_NO_TRANSCRIPT`). Neuer Test
`TestTranscriptCache::
test_mehrere_kandidaten_unterschiedlicher_start_nur_ein_transcribe_aufruf`
verifiziert explizit: zwei Kandidaten mit unterschiedlichen ersten
Zeitstempeln (`00:40.00` / `00:05.00`) führen zu genau einem
`_transcribe`-Aufruf mit dem früheren Start. 168 Tests grün.

**Bekannt, noch offen (nicht Teil dieses Fixes):**
`_CONTRASTIVE_SKIP_NO_TRANSCRIPT` wird von `_whisper_best()` nirgends mehr
zurückgegeben (totes Konstrukt, nur noch der `elif`-Zweig in `fetch_lrc()`
und ein Test, der `_whisper_best` direkt mockt, referenzieren die Konstante
noch) — Cleanup für ein andermal.

## ✓ fetch_songtext.py v1.10.0 — Kontrastive Marge + Hybrid-Boden als Standardverhalten, alte IDF-Datei-Infrastruktur entfernt

Produktiv-Umbau nach der ausgiebigen Testphase (siehe „✓ v1.9.14 — Hybrid-Boden"
und „✗ Bigramm-Jaccard … — getestet, verworfen" oben): Die kontrastive Marge
(inkl. Hybrid-Boden) ist keine Experimentier-Option mehr, sondern der EINZIGE,
immer aktive Verifikationsweg für Whisper. Das alte, dateibasierte IDF-Verfahren
(feste sprachspezifische Schwelle gegen `fetch_songtext_idf.json`) ist komplett
entfernt.

**Entfernt:**
- `--contrastive-experiment`-CLI-Flag und die globale Variable
  `_contrastive_experiment` — der bisherige `if`-Zweig (kontrastive Marge) ist
  jetzt der einzige, unbedingt ausgeführte Code; der bisherige `else`-Zweig
  (alte absolute IDF-Jaccard-Schwelle als primäre Entscheidung) ist gelöscht.
  `_whisper_accept()` akzeptiert jetzt unbedingt per Hybrid-Regel, sobald eine
  Marge berechnet wurde (`margin is not None`).
- `--rebuild-idf`-CLI-Flag, `_build_idf`, `_load_idf`, `_idf_table_for`,
  `fetch_songtext_idf.json`, `_IDF_CACHE_PATH`, `_idf_cache`,
  `_IDF_MIN_LANG_DOCS`, `_WHISPER_THRESHOLD_WARN_GROWTH` — die gesamte
  Datei-basierte, sprachbezogene IDF-Tabelle samt Bau-Logik. Ersetzt durch die
  bereits vorhandene globale, aus der Cache-DB gebaute `_contrastive_idf`
  (`_global_cache_idf`, `_build_contrastive_context`).
- Der `idf_data`-Parameter, der bisher durch die `_whisper_best`-Aufrufkette
  gereicht wurde, ist entfallen (ungenutzt, da nur noch `_contrastive_idf`
  gebraucht wird).

**Bleibt unverändert (nicht Teil dieses Umbaus):** `--wer-experiment` und alles
was daran hängt (separate, weiterhin experimentelle Option für den
Provider-Konsens- und Whisper-Vergleichspfad). `_whisper_threshold_for()`,
`_WHISPER_MIN_OVERLAP`, `_WHISPER_MIN_OVERLAP_BY_LANG` bleiben ebenfalls
erhalten — sie greifen weiterhin als Fallback, wenn der gleichsprachige
Hintergrund-Pool zu klein ist (`margin is None`, siehe
`_CONTRASTIVE_MIN_BACKGROUND`).

**Neue Einschränkung — Whisper-Verifikation braucht jetzt immer eine
Cache-DB:** `_build_contrastive_context()` (baut die globale IDF + die
Sprach-Hintergrund-Pools) läuft jetzt vor JEDEM Lauf mit aktiver
Whisper-Verifikation, nicht mehr nur optional hinter dem Flag. Ohne offene
`fetch_songtext_cache.db` gibt es keinen Hintergrund-Pool — das Skript bricht
mit einer klaren Fehlermeldung ab. `--no-cache` ist deshalb jetzt nur noch in
Kombination mit `--no-whisper` oder `--fast` erlaubt (beide überspringen die
Whisper-Verifikation vollständig); `main()` prüft das per `parser.error()`
VOR dem Lauf, nicht erst beim Abbruch mitten im Verzeichnisbaum. Vorher lief
`--contrastive-experiment` + `--no-cache` ebenfalls schon nicht (identischer
Fehlerfall war bereits ausgeschlossen) — neu ist nur, dass es jetzt IMMER
gilt, weil es keinen dateibasierten Fallback mehr gibt, der ohne Cache-DB
auskäme.

**Judgment Call — `--cache-only` statt eines pauschalen Sicherheitsnetzes:**
Der bisherige `--contrastive-experiment`-Sicherheitsnetz-Pfad in
`_whisper_best()` (kein gecachtes Transkript → sofort `model_used =
_CONTRASTIVE_SKIP_NO_TRANSCRIPT`, kein Live-Whisper-Lauf) wäre bei wörtlicher
Umsetzung ("Flag-Abfrage entfernen, if-Zweig wird unbedingt") permanent
unbedingt geworden — das hätte die bestehende, vom Flag unabhängige
Fenster-Transkriptions-Schleife (inkl. `cache_store.put_transcript`, siehe
`TestTranscriptCache`) dauerhaft unerreichbar gemacht: KEIN neuer Song hätte
je wieder eine erste Whisper-Transkription bekommen können. Das wurde beim
Testlauf konkret sichtbar (`TestTranscriptCache::test_miss_transcribes_and_
writes_cache` schlug fehl). Da die real validierte Nutzung dieses
Sicherheitsnetzes immer mit `--cache-only` kombiniert war (siehe
`CHECKPOINT_kontrastiv.md`: „echter Bibliothekslauf --cache-only
--contrastive-experiment"), ist das Sicherheitsnetz jetzt an `_cache_only`
gekoppelt statt an die entfernte Flag-Variable: `--cache-only` bedeutet jetzt
konsequent „nur Cache-Treffer, keine Live-Arbeit" — auch für Whisper, nicht
nur für Provider-Abfragen. Außerhalb von `--cache-only` transkribiert ein
Cache-Miss weiterhin live wie vor dieser Umstellung.

**Ebenfalls entfernt (Kollateral der Flag-Entfernung):** Der erzwungene Rerun
JEDES bereits Whisper-verarbeiteten Songs in `_whisper_rerun_needed()` (an
`--contrastive-experiment` gekoppelt) war eine einmalige Migrationsmaßnahme
für die Umstellungsphase (damit der Ordner-Cache-Skip die Neubewertung unter
der neuen Logik nicht verhindert). Dauerhaft unbedingt gemacht, hätte das
JEDEN Whisper-Song bei JEDEM künftigen Lauf neu verarbeiten lassen (Provider
erneut abgefragt, Ordner-Cache-Skip nie mehr wirksam) — das ist mit dem Flag
entfallen. Bereits unter der alten absoluten Schwelle gecachte Einträge lassen
sich bei Bedarf einmalig per `--force` auffrischen.

Version: `1.10.0` (Minor-Bump, analog v1.4.0 „Zweistufige Whisper-
Verifikation" — Normalverhalten ändert sich grundlegend, ein CLI-Flag
verschwindet komplett, kein reiner Bugfix).

Tests: alte Datei-IDF-Tests entfernt (`TestLoadIdf`, `TestBuildIdf`,
`TestIdfTableFor`, `TestWhisperRerunNeeded`-Fälle für den entfernten
Contrastive-Parameter, `--contrastive-experiment`/`--rebuild-idf`-CLI-Tests),
neue Tests für die `--no-cache`-Guard-Kombinationen und die
`--cache-only`-Sicherheitsnetz-Kopplung (inkl. Gegenprobe: Cache-Miss ohne
`--cache-only` transkribiert weiterhin live). 321 Tests grün (vorher 335 —
Differenz durch mehr entfernte als neu hinzugekommene Tests, keine
unerklärten Ausfälle).

## ✗ Bigramm-Jaccard statt Bag-of-Words (kontrastive Marge) — getestet, verworfen

**Status (2026-07-15): Getestet und VERWORFEN.** Bigramm-Jaccard (2-Wort-Tupel,
ungewichtet, keine IDF) sollte `_idf_jaccard` in der kontrastiven Marge
(`_CONTRASTIVE_MARGIN` / `_contrastive_margin_and_decision` in
`fetch_songtext.py`) ersetzen. Auslöser: Garth Brooks „White Christmas" —
`best_score=0,890`, trotzdem abgelehnt (`marge=-0,0162`), siehe
`contrastive_run_vergleich.md` Fall #0.

**Validierungstest** (rein lesend gegen `fetch_songtext_cache.db`, Details in
`bigram_jaccard_test_ergebnis.md`; die rohe Score-Verteilung aus
`bigram_jaccard_log.csv` war nur Zwischenstand für diese Auswertung, danach
aufgeräumt) — **Ergebnis:**
- Auf dem harten 33-Fälle-Testset (`contrastive_run_vergleich.md`) schneidet
  Bigramm-Jaccard **schlechter** ab als das bestehende Verfahren: **27/33
  (82 %)** richtig gegenüber bisher **31/33 (94 %)**. 3 neue Fehl-Akzeptanzen
  (Hannes Wittmer, JETZT! „Du Bist Nicht Allein", JETZT! „Was man Heimat
  nennt" — Margen kippen knapp über 0, weil bei kurzen Kandidatentexten sehr
  wenige Bigramme extrem rauschanfällig sind) und 1 neue Fehl-Ablehnung
  (Heino — Whisper transkribierte fälschlich auf Englisch, dadurch 0
  gemeinsame Bigramme trotz inhaltlich korrektem Kandidat).
- Auf den 86 unstrittigen Kandidaten-Reselektionen (nie das eigentliche
  Problem) funktioniert es dagegen nahezu perfekt (84/84) — bestätigt nur,
  dass Bigramme bei EINDEUTIGEN Treffern gut trennen, hilft aber nicht bei
  den harten Grenzfällen.
- Kein Schwellwert (`margin_bigram > 0` oder sonst ein Cutoff) trennt die 4
  bekannten „sollte akzeptiert werden"-Fälle sauber von den 29 bekannten
  „sollte abgelehnt werden"-Fällen — die Werte liegen ineinander verschachtelt.

**Wichtigste Erkenntnis — der Garth-Brooks-Fall hat eine ANDERE Ursache als
angenommen, auch mit Bigrammen weiterhin abgelehnt
(`best_score_bigram=0,762`, `margin_bigram=-0,0381`):** Kein zufälliger
generischer Vokabular-Zufall zwischen zwei verschiedenen Weihnachtsliedern,
sondern **Datenkontamination im Hintergrund-Pool selbst**. Der
Hintergrund-Song „Michael Bublé – Christmas" (`song_id=15349`) hat vier
gecachte Provider-Kandidatentexte in `ergebnisse`/`texte` — Genius und
Netease korrekt („Christmas (Baby Please Come Home)"), aber **Musixmatch
liefert wortwörtlich den „White Christmas"-Text** und lrclib einen dritten,
ebenfalls falschen Song („Holly Jolly Christmas") — beides Provider-
Fehltreffer bei einem fremden Song, unabhängig verifiziert (2026-07-15, per
direkter DB-Abfrage). Die kontrastive Marge nimmt für jeden Hintergrund-Song
den MAX über all seine Kandidatentexte (`_song_candidate_words`/
`_contrastive_margin_and_decision`) — trifft also zwangsläufig den
kontaminierten Musixmatch-Text. **Das ist ein Problem der
Hintergrund-Pool-Zusammensetzung, keine Schwäche der Ähnlichkeitsmetrik** —
weder Bigramme noch eine andere Metrik können das umgehen, solange
Hintergrund-Kandidaten ungefiltert aus `ergebnisse` gezogen werden.

**Entscheidung:** Bigramm-Ansatz nicht weiterverfolgen. Stattdessen bleibt
der bereits im Checkpoint geplante **Hybrid-Boden**
(`best_score ≥ 0,3 ODER Marge ≥ Schwelle`) die richtige nächste Maßnahme —
er hängt NICHT von der Hintergrund-Pool-Qualität ab: `best_score=0,89` liegt
so klar über 0,3, dass Garth Brooks damit unabhängig von jeder Kontamination
korrekt akzeptiert würde. Die Hintergrund-Pool-Kontamination selbst ist ein
eigenständiges, potenziell wiederkehrendes Problem (jeder Song mit einem
Provider-Fehltreffer kann fälschlich als Hintergrund-Störsignal wirken) —
mögliches eigenes Vorhaben für später (z. B. Hintergrund-Kandidaten per
Provider-Konsens vorfiltern), aber keine Voraussetzung für den Hybrid-Boden.

**Erkenntnisse aus der Untersuchung, die für eine ECHTE Sequenz-bewusste
Implementierung (falls später doch angegangen) weiter gelten:**
1. `_extract_lrc_words` sortiert nicht nach Zeitstempel, verkettet nur in
   Datei-Reihenfolge — bei Bag-of-Words irrelevant, bei jeder
   ordnungssensitiven Metrik (N-Gramme, WER, Alignment) ein echter
   Korrektheitsfehler. LRC-Zeilen können mehrere führende Zeitstempel haben
   (derselbe Text an mehreren Song-Zeitpunkten, z. B. wiederkehrender
   Refrain), real gefunden in
   `lrc_backup/U/U2/Achtung Baby/01 The Fly.lrc`:
   `[03:24.71][03:06.98]Love...we shine like a`. Betrifft 36 von 16.183
   Backup-Dateien (≈0,22 %). Bei künftigen ordnungssensitiven Ansätzen: pro
   Zeile alle Zeitstempel einzeln auswerten, Text entsprechend vervielfachen,
   nach Zeit sortieren, erst danach Wortfolge bilden.
2. `_extract_lrc_words` filtert nur eckige Klammern (Zeitstempel,
   `[Verse]`/`[Chorus]`) heraus, aber keine Genius-Klartext-Kopfzeilen wie
   „6 Contributors" / „<Titel> Lyrics" — deren Wörter landen mit im
   Wortschatz. Betrifft alte wie neue Metrik gleichermaßen, vermutlich
   vernachlässigbar bei vollständigen Songtexten, aber separates
   Aufräumthema.

## ✓ fetch_songtext.py v1.9.14 — Hybrid-Boden für kontrastive Marge

Problem: Der `--contrastive-experiment`-Zweig von `_whisper_accept()` akzeptierte
bislang ausschließlich über `margin >= _CONTRASTIVE_MARGIN` — der absolute Score
spielte keine Rolle mehr. Per Audio-Abhören und Datenbank-Analyse bestätigter
Fehlerfall: Garth Brooks „White Christmas" hat `best_score = 0,890` (Transkript und
Genius/Netease/Backup-Text nahezu wortidentisch, siehe `contrastive_run_vergleich.md`
Fall #0), wurde aber mit `margin = −0,0162` fälschlich abgelehnt — der K=20-
Hintergrund-Pool enthielt zufällig einen anderen Song mit noch höherem Score
(`max_hintergrund = 0,906`), ein Artefakt sehr kurzer, hochrepetitiver,
alltagssprachlicher Songtexte (Weihnachtslied-Standardvokabular). Ein Wechsel der
Metrik (Bigramm-Jaccard) wurde bereits geprüft und verworfen (siehe „✗ Bigramm-
Jaccard"-Eintrag oben) — das Problem liegt nicht an der Metrik, sondern daran, dass
ein einzelner kontaminierter Hintergrund-Kandidat die Marge eines eigentlich
korrekten Songtexts unter die Schwelle drücken kann.

Lösung: Hybrid-Akzeptanzregel in `_whisper_accept()` — akzeptiert wenn
`score >= _CONTRASTIVE_ABSOLUTE_FLOOR` (neue Konstante, `0.3`) ODER
`margin >= _CONTRASTIVE_MARGIN` (unverändert `0,0115`). Der absolute Boden greift
unabhängig vom Hintergrund-Vergleich und fängt genau die Fälle ab, in denen der
Hintergrund-Pool kontaminiert ist. `margin=None` (kein/zu kleiner gleichsprachiger
Hintergrund-Pool, `_CONTRASTIVE_MIN_BACKGROUND`) bleibt unverändert der Fallback
auf die alte absolute Schwelle `_whisper_threshold_for(lang)`.

Verifikation gegen die bereits vorhandenen 784 Zeilen aus dem
`--cache-only --contrastive-experiment`-Testlauf-Log (kein neuer
Bibliothekslauf; Rohdaten danach aufgeräumt): Die neue Hybrid-
Entscheidung wurde für jede Zeile neu berechnet und mit der alten
Entscheidung verglichen. Ergebnis: genau **1 Zeile kippt** von
Ablehnung zu Akzeptanz — Garth Brooks „White Christmas" (jetzt korrekt
akzeptiert). **Keine** Zeile kippt in die falsche Richtung (Akzeptanz →
Ablehnung). Die 29 bereits bekannten RICHTIG-ablehnung-Fälle aus
`contrastive_run_vergleich.md` haben durchweg `best_score <= 0,109` — weit unter
dem neuen Boden 0,3, keiner davon kippt fälschlich. Die 84 HARMLOS- und 2
VERBESSERUNG-Fälle aus `contrastive_reselection_check.md` betreffen ausschließlich
Kandidaten-Neuauswahl bei bereits übereinstimmender Akzeptanz/Ablehnung, sind von
der Akzeptanzregel-Änderung nicht berührt.

Tests: 3 neue Unit-Tests in `TestWhisperAcceptContrastive` (hoher Score + negative
Marge → akzeptiert; niedriger Score + negative Marge → weiterhin abgelehnt;
niedriger Score + positive Marge → weiterhin akzeptiert), bestehender Test
`test_marge_unter_schwelle_...` an die neue Regel angepasst (Score jetzt bewusst
unter dem Boden gewählt, sonst hätte der Hybrid-Boden ihn akzeptiert). 335 Tests
grün (vorher 332).

## ✓ fetch_songtext.py v1.9.13 — sprachspezifische Whisper-Schwelle + Rekalibrierung

Problem: v1.9.12 machte die IDF-Tabelle sprachbezogen (eigene, kleinere
Teiltabelle für Deutsch statt der alten globalen Tabelle), ließ die
Akzeptanz-Schwelle `_WHISPER_MIN_OVERLAP = 0.065` aber bewusst unangetastet.
Das hatte einen nicht offensichtlichen Nebeneffekt: JEDER Score (nicht nur
Fehltreffer, auch echte Treffer) fällt mit einer kleineren Tabelle
systematisch niedriger aus — reiner Skaleneffekt der Formel
`idf(w) = ln((n_docs+1)/(df(w)+1))`, kein inhaltlicher Unterschied. Mit der
unveränderten Schwelle 0,065 hätte das echte deutsche Treffer wie „Warum"
(Score fiel von 0,082 auf 0,049) fälschlich abgelehnt — eine Regression.

Kalibrierung: An 8 Testfällen (4 sollten akzeptiert werden: Ich Frag Mich,
Afrika, Warum, Liebeslied — alle Edo Zanki/Jetzt!; 4 sollten abgelehnt
werden: Die Zeit/lrclib, Die Zeit/genius, Wie es war, Du Bist Nicht Allein —
alle Jetzt!, absichtlich falsch zugeordnete Provider-Texte) wurde mit
echten Whisper-Transkriptionen gegen die neue deutsche IDF-Tabelle (2.212
Dokumente) gemessen: höchster Fehltreffer-Score 0,0373, niedrigster
korrekter Score 0,0490. Neue Schwelle für Deutsch: **0,043** (mittig
zwischen beiden, gleiches Prinzip wie die ursprüngliche 0,065-Eichung).

Ein zusätzliches Subsampling-Experiment (dieselben 8 Testfälle gegen
künstlich verkleinerte deutsche Teilkorpora bei 500/1.000/1.500/2.212
Dokumenten) zeigte: die Mitte zwischen Fehltreffer-Max und Treffer-Min
konvergiert schon ab ~1.000 Dokumenten auf ~0,043 (Werte: 0,0461 / 0,0431 /
0,0439 / 0,0431 — kein sauberer fortlaufender Trend, nur eine anfängliche
Stabilisierung). Für Englisch (13.074 Dokumente, kaum kleiner als die alte
globale Tabelle) bestätigte eine Stichprobe (gecachtes Transkript, Frank
Sinatra „Ol' Man River"): Scores ändern sich kaum (0,577→0,566 korrekt,
0,0262→0,0236 falsch) — die alte Schwelle 0,065 bleibt für Englisch und alle
auf `global` zurückfallenden Sprachen weiterhin gültig, unverändert.

Lösung: `_WHISPER_MIN_OVERLAP_BY_LANG` als sprachspezifische Override-Tabelle
(`{"de": (0.043, 2212)}`), ausgewertet über die neue Funktion
`_whisper_threshold_for(lang)`. `fetch_lrc()` nutzt sie statt der festen
`_WHISPER_MIN_OVERLAP`-Konstante.

Entscheidung (mit Nutzer abgestimmt): Kein automatisches Neukalibrieren,
sondern eine **Warnung** beim nächsten `--rebuild-idf`, falls sich die
Dokumentenzahl einer kalibrierten Sprache stark ändert (Faktor 1,5 —
`_WHISPER_THRESHOLD_WARN_GROWTH`), damit die Schwelle bei Bedarf manuell
nachjustiert werden kann. Grund: das Subsampling-Experiment zeigt Konvergenz
statt fortlaufendem Trend — eine Formel wäre nicht belastbar, aber ein
Sicherheitsnetz für starkes Wachstum ist sinnvoll. Zusätzlich weist
`--rebuild-idf` darauf hin, wenn eine bisher unkalibrierte Sprache erstmals
eine eigene IDF-Teiltabelle bekommt, aber noch mit der Default-Schwelle
0,065 läuft.

## ✓ fetch_songtext.py v1.9.12 — sprachbezogene IDF-Tabelle

Problem: Die IDF-Tabelle (`fetch_songtext_idf.json`, Basis für die Whisper-
Matching-Metrik `_idf_jaccard`) maß die Wortseltenheit bisher über die GESAMTE
Bibliothek hinweg, unabhängig von der Sprache. Die Bibliothek ist aber ~81 %
Englisch, ~13 % Deutsch, Rest je ~1 % — dadurch wirkten generische deutsche
Wörter (z. B. „verstehen", „zeit", „leben") künstlich „selten" (hohe IDF),
obwohl sie innerhalb deutscher Songtexte ganz gewöhnlich sind. Das führte zu
einem Beinahe-Fehlmatch: eine falsch zugeordnete LRC erreichte einen Score von
0,063, knapp unter der Schwelle von 0,065 — einzig weil zufällig geteiltes,
generisches deutsches Vokabular den Zähler aufblähte, ohne dass wirklich
Satzinhalte übereinstimmten.

Lösung: Die IDF-Tabelle führt jetzt zusätzlich zur globalen Tabelle
Teiltabellen je Sprache, mit einer Mindestgröße `_IDF_MIN_LANG_DOCS = 1000`
Dokumenten — darunter fällt eine Sprache auf die bisherige globale,
bibliotheksweite Tabelle zurück (unverändertes Verhalten als Fallback). Die
Sprache wird dabei NICHT aus den JSON-Provider-Caches übernommen (dort nur
~22 % Abdeckung), sondern direkt aus dem `.lrc`-Inhalt erkannt (über die
bereits vorhandene `_detect_lrc_language`, wie in Schritt 4 der
Whisper-Verifikation).

Wichtig: Die Schwelle `_WHISPER_MIN_OVERLAP = 0.065` wurde in diesem Schritt
NOCH NICHT neu kalibriert — das folgt in einem separaten Schritt, der NICHT
Teil dieser Änderung ist.

## ✓ fetch_songtext.py v1.9.11 — `--cache-only` Flag

Neues Flag `--cache-only`: garantiert, dass ein Lauf **keine einzige** Live-Provider-Abfrage
auslöst — nur was bereits im Provider-Cache (`fetch_songtext_cache.db`) steht, wird verwendet.
Der eigentliche Kern des Features: das gilt auch für Provider, deren letzter Versuch als
`status="fehlschlag"` (Timeout/Rate-Limit/Captcha/„gesperrt") im Cache steht — normalerweise
zählt ein Fehlschlag bewusst **nie** als Cache-Treffer (`get_provider` gibt dafür `None`
zurück, siehe `CACHE_DESIGN.md`) und `_query_provider` fragt danach live nach. Mit
`--cache-only` wird dieser automatische Live-Nachschlag unterdrückt: ein neuer Guard direkt
nach dem Cache-Lookup liefert `(provider, None)` zurück, ohne `subprocess.run`, ohne
`_rate_limit_wait` und ohne neuen Cache-Eintrag (kein echter Versuch fand statt). Motivation:
Zwei-Phasen-Workflow — Phase 1 `--fast` (Whisper-Fälle aufgeschoben, ungecacht), Phase 2 soll
nur noch diese Whisper-Lücken füllen, ohne dass Provider, die in Phase 1 z. B. an einem
Rate-Limit hingen, jetzt nochmal live angefragt werden. `--cache-only` schließt sich mit
`--force`/`--refresh-cache` (erzwingen frische Live-Abfragen) und `--no-cache` (kein Cache
vorhanden) aus — argparse bricht dann mit Exit-Code 2 ab.

## ✓ fetch_songtext.py v1.9.10 — lokal-Cache-Feature zurückgebaut

Die "lokal"-Erweiterung (fünfter Kandidat in `fetch_lrc()`, automatische
Rückkopplung/Invalidierung, `cache_seed.py`) wurde komplett entfernt — Cache
ist wieder ein reiner Provider-Cache (nur `lrclib`/`musixmatch`/`netease`/
`genius`). Begründung: In den meisten Fällen ohnehin redundant zu bereits
gecachten Provider-Treffern oder zum immer schon vorhandenen
`existing_lrc`-Mechanismus (aktuelle Datei wird sowieso live verglichen); der
schmale Zusatznutzen (Text nach Provider-TTL-Ablauf retten) rechtfertigte die
Komplexität/Fehleranfälligkeit nicht — zwei echte Konsistenz-Bugs wurden
bereits gefunden und geflickt, bevor diese Entscheidung fiel. In der echten
Bibliothek standen zum Rückbau-Zeitpunkt 0 `"lokal"`-Einträge (nie gegen die
echte Bibliothek befüllt) — nichts zu migrieren.

## ✓ cache_seed.py v1.9.9 — Qualitätsfilter pro Track statt pro Ordner

Bisher prüfte `cache_seed.py` nur, ob IRGENDEINE `.fetch_songtext.json` im
selben Ordner wie die `.lrc` lag — zu grob, das sagt nichts über den
konkreten Track aus. Jetzt wird pro Track geprüft: `_load_cache()` (aus
`fetch_songtext.py` importiert statt neu gebaut) lädt die `.fetch_songtext.json`
des Ordners, und eine `.lrc` wird nur eingelesen, wenn der Schlüssel
`unicodedata.normalize("NFC", audio.name)` darin existiert UND der Eintrag
`"r": "ok"` trägt. Ohne gleichnamige Audiodatei fehlt die verlässliche
Track-Identität — dann wird übersprungen. Die bisherige eigene
`_APP_CACHE_FILENAME`-Konstante entfällt (überflüssig, `_load_cache()` kennt
den Dateinamen selbst und liefert `{}` bei Fehlen).

## ✓ fetch_songtext.py v1.9.8 — Genre-Skip-Löschung invalidiert "lokal"-Cache

Letzte verbliebene Lücke der v1.9.7-Invalidierung geschlossen: Löscht `main()`
eine vorher vorhandene `.lrc`, weil das Genre keinen Songtext erwarten lässt
(`_is_skip_genre`, z. B. Instrumental/Hörbuch), wurde `"lokal"` bisher NICHT
auf `status="nichts"` gesetzt — reiner Reihenfolge-Bug: `query_artist`/
`clean_title` wurden erst NACH dem Genre-Check berechnet. Fix: Berechnung vor
den Genre-Check verschoben (steht jetzt in beiden Zweigen zur Verfügung, im
Normalfall keine Neuberechnung mehr nötig — `query`-String unverändert), im
Genre-Skip-Zweig bei `had_lrc=True` zusätzlich `_invalidate_lokal_cache`
aufgerufen. Der `no_tags`-Fall bleibt bewusst unangetastet — keine verlässliche
Song-Identität, daher kein Cache-Schlüssel.

## ✓ fetch_songtext.py v1.9.7 — Löschung invalidiert "lokal"-Cache

Zwei Lücken in der v1.9.6-Rückkopplung geschlossen (`main()`, `use_compare`-Zweig):
Wird eine vorher vorhandene `.lrc` jetzt als "nicht gefunden" gelöscht
(`had_lrc=True`), setzt `main()` den `"lokal"`-Cache-Eintrag zusätzlich auf
`status="nichts"` (`_invalidate_lokal_cache`) — sonst würde ein künftiger Lauf
den soeben widerlegten Text über den `"lokal"`-Kandidaten in `fetch_lrc()`
wieder ins Spiel bringen. Der "unverändert"-Fall (gefundener Text ist
byte-identisch zur bestehenden `.lrc`) rief `_feedback_lokal_cache` bereits
vorher korrekt mit auf (steht im Code nach dem `if/else`, gilt für beide
Zweige) — keine Änderung nötig, nur als Regressionstest abgesichert.

## ✓ fetch_songtext.py v1.9.6 — "lokal" als fünfter Kandidat in `fetch_lrc()`

Die Cache-Quelle `"lokal"` (per `cache_seed.py` eingelesen oder automatisch
gepflegt) ist jetzt ein vollwertiger fünfter Kandidat in `fetch_lrc()`, statt
nur beim Provider-Cache mitzulaufen. Wird wie die 4 echten Provider auf
inhaltliche Duplikate geprüft (`_dedupe_by_content`), aber mit niedrigster
Priorität — hat ein echter Provider identischen Inhalt, gewinnt dieser und
`"lokal"` wird verworfen, nicht doppelt gezählt. Zählt NICHT zum
3-von-4-Konsens (`_provider_consensus`) — nur die 4 echten Provider zählen
dafür, `"lokal"` ist nur eine Erinnerung an einen früher akzeptierten Text,
keine unabhängige Bestätigung. Überlebt `"lokal"` den Dedup, landet es
zusätzlich in der Whisper-Vergleichsliste (`all_candidates`), genau wie die
vorhandene `.lrc` auf der Platte. Kein Freifahrtschein: Liefert nur `"lokal"`
etwas (kein Provider antwortet), läuft der Song trotzdem ganz normal durch
Whisper und kann bei Nichtübereinstimmung verworfen werden.

Zusätzlich: jeder akzeptierte Song (Konsens oder Whisper-Treffer) wird in
`main()` automatisch als neuer `"lokal"`-Stand zurückgeschrieben
(`put_provider(..., "lokal", ...)`) — hält die Quelle dauerhaft aktuell statt
nur ein einmaliger `cache_seed.py`-Snapshot zu bleiben, analog zum
Whisper-Transkript-Cache (Song-Identität statt Datei-Pfad).

## ✓ fetch_songtext.py v1.9.4 — Dauerhaft blockierten Provider überspringen statt jedes Mal neu zu warten

Beleg aus der Cache-Datenbank: Musixmatch schlug bei praktisch jedem Song mit
`fehlergrund="captcha"` fehl, dutzende Male hintereinander. Die bestehende
Eskalation (`_rate_limit_report`) ist bei `_RATE_LIMIT_MAX_SEC` (60s) gedeckelt
— bei einem dauerhaft blockierten Provider verlor dadurch trotzdem **jeder**
folgende Song erneut ~60s, ohne je zum Ziel zu kommen.

Ein einfaches "warte eben länger" ist keine Option: `fetch_lrc()` wartet über
`ThreadPoolExecutor`+`as_completed` synchron auf alle 4 Provider-Threads, bevor
es zum nächsten Track übergeht — ein echter `time.sleep()` über z.B. 15 Minuten
würde den gesamten Lauf einfrieren.

Lösung: Nach `_RATE_LIMIT_STUCK_THRESHOLD` (5) Treffern IN FOLGE wechselt
`_rate_limit_report` in eine lange Ruhephase (`_RATE_LIMIT_LONG_PAUSE_SEC`,
15 Minuten) statt weiter zu eskalieren. `_rate_limit_wait` gibt in dieser Phase
`True` zurück, OHNE zu schlafen — `_query_provider` überspringt den Live-
Versuch dann komplett (kein `subprocess.run`, kein sleep) und meldet sofort
`(provider, None)`. Mit aktivem Cache wird der übersprungene Fall trotzdem als
Fehlschlag festgehalten (`fehlergrund="gesperrt"` — eigener Wert, der anzeigt
"wegen aktiver Ruhephase übersprungen", nicht der ursprüngliche Grund wie
"captcha"), OHNE `consecutive_hits`/`next_allowed` zu verändern — kein neuer
Versuch fand statt, also kein neues Signal. Läuft die Ruhephase ab, ist wieder
ein einzelner echter Versuch fällig: gelingt er, geht der Provider zurück in
den Normalzustand (`consecutive_hits` auf 0 zurückgesetzt durch sauberen
Erfolg); scheitert er erneut, geht es direkt zurück in die lange Ruhephase.

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

### Sequenz-bewusster Vergleich statt/zusätzlich zu Bag-of-Words (IDF-Jaccard)

**Hinweis:** Wird inzwischen konkret umgesetzt/getestet — siehe
„Geplant: Bigramm-Jaccard statt Bag-of-Words" ganz oben in diesem Dokument,
nicht mehr nur Idee. Der Rest dieses Eintrags ist die ursprüngliche
Problem-/Rechercheanalyse (2026-07-14), die zu diesem Vorhaben geführt hat.

**Problem (2026-07-14 erkannt, im Rahmen der sprachbezogenen IDF-Analyse):**
`_idf_jaccard` (und davor `_word_overlap`/Containment) sind alle drei
**Bag-of-Words** — sie zählen nur, welche Wörter gemeinsam vorkommen, nie in
welcher Reihenfolge oder als welche Phrase. Das betrifft nicht nur Deutsch
(siehe v1.9.12): Englische Popsongs sind voll von thematischem
"Allerweltsvokabular" ("heart", "night", "baby", "fire", "forever" — nicht
super häufig, aber auch nicht selten), das zwei völlig unabhängige Songs
zufällig teilen können, ohne dass ein einziger Satz wirklich übereinstimmt
(siehe "Die Zeit"-Fall in v1.9.12, dasselbe Muster droht auch auf Englisch,
~81 % der Bibliothek — dort bislang nicht konkret nachgewiesen, aber
strukturell genauso möglich).

**Wichtige Erkenntnis (Nutzer, 2026-07-14):** Im Code gibt es ZWEI
verschiedene Vergleiche, die nicht zwangsläufig denselben Algorithmus
brauchen:
1. Provider-Texte untereinander (`_provider_consensus`/`_word_overlap`,
   Konsens-Pfad in `fetch_lrc`) — reines Jaccard, kein IDF.
2. Provider-Text gegen Whisper-Transkript (`_whisper_best`/`_idf_jaccard`).

Eine sequenz-bewusste Verbesserung müsste nicht zwingend beide Vergleiche
gleich behandeln — z. B. könnte Vergleich 2 (der Whisper-Fall, wo es um echte
Verifikation gegen eine Rauschquelle geht) von mehr Robustheit profitieren,
während Vergleich 1 (zwei bereits sauberen Texten) evtl. mit einfachem
Jaccard weiterhin gut genug bedient ist.

**Recherche (2026-07-14) — es gibt etablierte Ansätze für genau dieses
Problem, nicht neu erfinden:**
- **WER (Word Error Rate) / Levenshtein-Editierdistanz** ist der
  Standard-Vergleich in der ASR-Welt für Hypothese-vs-Referenz-Text — von
  Natur aus sequenzbewusst (Alignment über Editieroperationen: Ersetzen/
  Einfügen/Löschen). Wird in **SongPrep** (arXiv:2509.17404, "WER-FIX"-Schritt)
  bereits konkret für Lyrics-Verifikation genutzt: Kandidat wird nur behalten
  wenn WER < 0,7 gegen das ASR-Transkript, danach Verfeinerung per
  WER-Scoring. Direktes Vorbild für unseren Use-Case.
- **Forced-Alignment-Tools** (Gentle, Montreal Forced Aligner — beide
  Open-Source, Kaldi-basiert) verfolgen einen anderen Architektur-Ansatz:
  statt zwei Texte zu vergleichen, wird der KANDIDATEN-Text direkt an die
  Audiospur ausgerichtet; schlägt die Ausrichtung fehl bzw. ist die
  Konfidenz niedrig, ist das selbst ein Signal für falschen Text. MFA
  unterstützt mehrere Sprachen (eigene Akustikmodelle nötig), Gentle ist auf
  Englisch fokussiert.
- **WEALY** (arXiv:2510.08176, "Leveraging Whisper Embeddings for
  Audio-based Lyrics Matching", 2025/2026): nutzt Whisper-Decoder-Embeddings
  + Transformer für semantisches Lyrics-Matching. Deutlich schwergewichtiger
  (eigenes Trainings-/Embedding-Setup) — vermutlich Overkill für dieses
  Solo-Projekt, aber Referenz für den aktuellen Stand der Forschung.

**Trade-off, den jede sequenzbewusste Lösung berücksichtigen muss:** Whisper
verhört sich auf Wortebene ständig (belegt in dieser Session: "verbrannt"→
"verbracht", "Land"→"laus", "Tarzan"→"tatsam"). Bag-of-Words verliert bei
so einem Fehler nur das eine Wort; ein N-Gramm/Phrasen-Vergleich verliert bei
einem falsch erkannten Wort die GANZE Phrase — mehr Trennschärfe gegen
Zufallstreffer, aber auch mehr Fragilität gegen echte Whisper-Fehler bei
korrekten Treffern (Risiko: mehr Fehlablehnungen statt weniger). Muss
genauso empirisch geprüft werden wie die v1.9.12-Kalibrierung, kein
Selbstläufer.

**Nächster Schritt, falls angegangen:** Eigene Analyse/Kalibrierung wie in
v1.9.12 (Testfälle mit bekannt richtig/falsch, Score-Verteilung vor/nach
Umstellung vergleichen) — eigenständiges Vorhaben, nicht nebenbei.

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
- v1.9.5: `cache_seed.py` liest eine `.lrc` nur ein, wenn im selben Ordner auch eine `.fetch_songtext.json` liegt — sonst übersprungen (mitgezählt). Qualitätsfilter: nur Tracks, die nachweislich durch `fetch_songtext.py` liefen, gelten als vertrauenswürdige `"lokal"`-Quelle.
- v1.9.9: Qualitätsfilter verschärft — pro Track statt pro Ordner geprüft. Eine `.lrc` wird nur eingelesen, wenn der Track (Schlüssel = Audiodateiname) in der `.fetch_songtext.json` des Ordners mit `"r": "ok"` verzeichnet ist (`_load_cache()` aus `fetch_songtext.py` wiederverwendet).
- `fetch_songtext.py`: neue Flags `--no-cache`, `--refresh-cache`, `--cache-ttl TAGE` (Default 30). Provider-Abfragen (`_query_provider`) und Whisper-Transkription (`_cached_transcribe`) cachen transparent — geschützter Import (`cache_store` fehlt → Verhalten exakt wie vorher). Drei Ausgänge sauber getrennt: Treffer und „wirklich nichts" werden gecacht, transiente Fehler (Timeout/Rate-Limit/Captcha) nie.
