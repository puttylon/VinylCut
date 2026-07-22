# VinylCut Roadmap

Aktueller Stand + geplante Arbeit. Die komplette bisherige Historie (Bug-
Ursachen, verworfene Ansätze, Kalibrierungsergebnisse) steht unverändert
weiter unten im Archiv — viele Code-Kommentare zitieren einzelne
Archiv-Abschnitte namentlich, deshalb bleibt deren Titel/Inhalt stabil.

## Feature-Status

**assemble.py** (`1.1.12`) — Roh-FLAC vorbereiten, zerstörungsfrei:
Stille-basierte Seitenerkennung, interaktives Setzen von Trim-/
Übergangspunkten, Crossfade-Vorschau mit Feinjustierung, Zusammenfügen,
DC-Offset-Entfernung, optionaler Kanalausgleich, Peak-Normalisierung auf
−1 dBFS. Rich-Vollbild-UI, Fortsetzen über `assemble.json`.

**fetch_metadata.py** — Discogs-Release-Auswahl per Score (Vinyl
bevorzugt), interaktive Tracklist, manuelle ID-Vorgabe, Cover-Download →
`release.json` + `cover.jpg`. Benötigt `DISCOGS_TOKEN`.

**cut.py** (`1.9.19`) — interaktives Setzen der Track-Startpunkte
(ffplay-Vorschau, optionaler Normton), sample-genauer Schnitt via SoX,
FLAC-Tagging inkl. Versions-Kommentar, Fortsetzen über `progress.json`,
Flags `--out`/`--no-songtext`/`--preview`. Holt Songtexte pro Track
automatisch nach.

**Songtexte-Pipeline** (`songtext_pipeline.py` als Orchestrator +
`scan_songs`/`fetch_providers`/`evaluate_lyrics`/`write_lrc`, Kernlogik in
`lyrics_core.py`, `2.0.3`):
- Einzelne Phasen-Flags `--scan`/`--abfragen`/`--nachholen`/`--bewerten`/
  `--schreiben`, jede unabhängig wiederholbar; Pfad-eingrenzbar,
  Datei-für-Datei, Ordner-Sperre (parallele Instanzen möglich).
- 4 Anbieter (lrclib inkl. lokalem DB-Abzug, musixmatch, netease, genius),
  Konsens-Schnellpfad, IDF-gewichtetes Jaccard.
- Whisper-Verifikation: Modellwahl nach Sprache (`medium` EN / `large-v3`
  sonst), Early-Stop bei sicherer Erkennung, kontrastive Marge gegen einen
  sprachgleichen Zufalls-Hintergrund.
- Cache `cache.db` (`songs`/`ergebnisse`/`texte`/`transkripte` +
  `early_stop_log`); 90 Tage Anbieter-TTL; JSON-Ordner-Cache für
  Schreib-Skip.

**Gemeinsame Bausteine:** `library.py` (UI-unabhängige Utilities),
`cache_store.py` (DB-Schicht), `cut_ui.py`/`assemble_ui.py` (zentralisierte
Styles).

**Diagnose-Werkzeuge:** `db_analyse.py` (DB-Statistik), `lrc_analyse.py`
(JSON-Cache-Statistik), `lrc_recheck.py` (fehlgeschlagene Einträge erneut
einreihen), `inspect_song.py` (Einzelsong-Dump), `compare_whisper_models.py`
(manueller Modellvergleich).

## Geplant / offen

1. **`--nachpruefen` (Plausibilitätsprüfung):** bereits akzeptierte
   `.lrc`-Dateien erneut bewerten, auch wenn die DB nichts Neues hat --
   z.B. weil inzwischen ein besseres Whisper-Modell verfügbar ist. Bisher
   nur diskutiert, bewusst zurückgestellt (siehe Archiv). Schließt den
   Fall mit ein, dass alte `small`-Modell-Transkripte nie automatisch mit
   dem heutigen sprachabhängigen Modell nachgeprüft werden.
2. **Sequenzbewusstes Text-Matching** (WER/Levenshtein statt reinem
   Bag-of-Words-Jaccard) als eigenständige Forschungsidee -- nicht zu
   verwechseln mit dem bereits getesteten und verworfenen Bigramm-Jaccard
   (siehe Archiv, "✗ Bigramm-Jaccard").
3. **Einheitliches `vinylcut`-Kommandozeilenwerkzeug** statt separater
   Skripte, inkl. eines möglichen Record-Schritts.

## Design-Dokumente

- `CACHE_DESIGN.md` -- Cache-Schema/-Verhalten
- `CLAUDE.md` -- Arbeitsregeln, Doku-Struktur, Testpflicht

---

# Änderungshistorie (Archiv — chronologisch, neueste zuerst)

## ✓ Bugfix: Whisper ohne Sprachvorgabe löschte übereinstimmende Provider-Texte fälschlich ("Ilumbarada"-Fall)

**Auslöser:** Live-Lauf des Nutzers zeigte für "27 Ilumbarada.flac"
(20 Minuten Whisper-Laufzeit, gleiche Musterklasse wie der vorige Eintrag)
`idf-jacc=0.000` und eine gelöschte `.lrc`-Datei -- obwohl zwei unabhängige
Provider (lrclib, netease) inhaltlich übereinstimmende Texte lieferten.

**Untersuchung:** `"language": null` im Cache-Eintrag -- `_resolve_lrc_
language()` konnte den Kandidatentexten keine Sprache zuordnen (der Song
enthält eine erfundene Kunstsprache, "Ilumbarada eja / Kuedere I / Ku
aramane..."). Ohne Sprachvorgabe rät Whisper, hat dabei bereits zweimal live
eine falsche Sprache erraten und halluziniert ("Dooh Dooh", "Dragostea Din
Tei"). Bei "Kumba Yo!" (ebenfalls `language: null`, ebenfalls 12 Minuten
Laufzeit, siehe voriger Eintrag) zeigte ein direkter Test beider
Kandidatentexte gegen `_detect_lrc_language()`: **kein Widerspruch zwischen
den Quellen** -- beide liefern schon einzeln `None`, obwohl der Text
eindeutig Englisch ist (Nutzer-Einwand: "Kumba ya my lord... hätte ich als
englisch eingestuft"). Vermutlich verwässert der dominante Wiederholungs-
Chant ("Kumba kumba a kumba ya") die statistische Spracherkennung. Der
fehlende Sprach-Hint ist also nicht immer eine echte Kunstsprache -- das
Risiko für Whisper (Rateerei, Halluzination) ist aber in beiden Fällen
identisch.

**Fix:** `evaluate_lyrics.evaluate_song()` versucht bei fehlender
Sprachvorgabe (`_resolve_lrc_language(all_candidates) is None`) zusätzlich
einen Konsens mit abgesenkter Mindestanzahl (`_provider_consensus(...,
min_providers=2)` statt der sonst nötigen 3, siehe `_CONSENSUS_MIN_
PROVIDERS`). Weicht der Wortlaut ab, bleibt es beim normalen Whisper-Pfad --
keine pauschale Aufweichung der Konsens-Schwelle.

**Zweiter Bug, live beim Wiederherstellen von "Ilumbarada" entdeckt:**
`min_providers=2` allein reichte nicht. `_group_candidates` fasst
inhaltlich (fast) identische Rohquellen bereits VOR `_provider_consensus`
zu einer einzigen Gruppe zusammen (>= 90% Wort-Jaccard) -- stimmen genau 2
unabhängige Quellen so stark überein (wie bei Ilumbarada: lrclib/netease
praktisch wortgleich), bleibt danach nur noch 1 Gruppe übrig, und
`_provider_consensus` kann strukturell keinen paarweisen Vergleich mehr
rechnen (braucht mindestens 2 Gruppen, siehe dortiger Docstring). Der Song
lief beim ersten Testlauf des Fixes prompt erneut komplett durch Whisper.
Fix: bei exakt 1 Gruppe aus >= 2 Rohquellen (stärkste Form von
Übereinstimmung, wortgleich statt nur ähnlich) wird direkt akzeptiert, ohne
`_provider_consensus` erneut zu bemühen.

Produktionsdaten repariert: die live gelöschte `.lrc` für "Ilumbarada"
über die (jetzt reparierte) echte Pipeline neu erzeugt -- 0,8s statt der
vorherigen 20 Minuten, "Konsens 100%", kein Whisper mehr nötig.

Kleine UX-Ergänzung (Nutzer-Feedback: "blöd, dass nicht der genaue
Zeitstempel vor 'Whisper transkribiert' steht"): die transiente Statuszeile
in `lyrics_core._whisper_best()` zeigt jetzt `_ts()` mit an.

3 neue Tests (`TestEvaluateSongKonsens`, davon einer als direkter
Regressionstest für den zweiten Bug). 584/584 Tests grün, `ruff` sauber.
`lyrics_core.__version__` auf `2.0.3` erhöht.

## ✓ Wall-Clock-Deckel gegen Whisper-Hänger ("Dooh Dooh"-Fall)

**Auslöser:** Live-Lauf des Nutzers blieb bei "17 Dooh Dooh.flac" 26+ Minuten
bei Whisper hängen ("das ist nun wieder so ein Fall wo Whisper viel länger
braucht als der Song lang ist"). `Ctrl+C` griff nicht (Hänger steckte in
nativem ctranslate2-Code, das Python-Signal kam erst nach dessen Rückkehr
an) -- Prozess musste per `kill -9` beendet werden.

**Untersuchung, live reproduziert (Wegwerf-Skript, siehe CLAUDE.md-Vorgabe):**
Der Song besteht fast nur aus einer extrem repetitiven, wortlosen Hookline
("dooh dooh dooh..."). Whisper erkennt die Sprache dadurch falsch (Russisch
statt Englisch/instrumental) und halluziniert dazu passenden kyrillischen
Text ("Невеста", "Музыка") -- mit bis zu 135s zwischen einzelnen Segmenten.
Ein erzwungener korrekter Sprach-Hint (`language="en"`) behob es NICHT
(lief weiterhin 20+ Minuten) -- die Wiederholung selbst löst offenbar eine
interne Fallback-/Wiederholungsschleife aus, unabhängig von der Sprache.

Ein zweiter realer Fall ("Dragostea Din Tei", ebenfalls eine kurze
wiederholte Hookline vor viel echtem Text) zeigte dasselbe Muster OHNE
Sprachvorgabe (fälschlich als Russisch erkannt, Halluzination). MIT der von
der Produktion tatsächlich genutzten korrekten Sprachvorgabe (`language=
"ro"`, aus den Kandidatentexten abgeleitet) transkribierte Whisper dagegen
korrekt -- zeigt, dass nicht jeder Song mit repetitiver Hookline gleich
betroffen ist.

**Fix:** `lyrics_core._transcribe_with_early_stop()` bekommt einen
absoluten Wall-Clock-Deckel (`_TRANSCRIBE_TIMEOUT_SEC`) für den GESAMTEN
Transkriptionsversuch (nicht pro Segment, kein Reset) -- geprüft zwischen
den ohnehin einzeln konsumierten Segmenten, kein Hard-Kill nötig, da
Segmente auch im pathologischen Fall weiter nachkommen, nur sehr langsam.
Bei Überschreitung: Abbruch, `early_stopped=True` (kein Cache des
unvollständigen Transkripts, wie beim regulären früher Stopp), eigene
Live-Statuszeile ("Whisper-Timeout nach ...s, breche ab...") und ein
eigener Zähler `_early_stop_stats["timeout"]`, der in der
Abschlusszeile des Laufs mit ausgewiesen wird.

Wichtige, per Test verifizierte Erkenntnis: die bis zum Abbruch gesammelten
Wörter werden GENAUSO gegen die Provider-Kandidatentexte gescort wie ein
vollständiges Transkript (kein Sonderfall nötig) -- bei "Dragostea Din Tei"
reichten die vor dem Timeout gesammelten 205 Wörter für eine korrekte
Annahme des echten Kandidatentexts (`score=0.391`, `accept=True`). Ein
Timeout bedeutet also nicht zwangsläufig ein falsches Verdikt, nur weniger
Beweismaterial.

**Kalibrierung des Deckels, mit echten Daten statt Schätzung:** Erster
Wert 180s wirkte im Praxistest zu knapp -- Verteilung über 4142 echte
historische Whisper-Versuche (Zeitabstand aufeinanderfolgender
`early_stop_log`-Einträge als Näherung) zeigte: 4-6% aller LEGITIMEN Läufe
brauchen länger als 180s (medium p99=289s, large-v3 p99=331s), aber nur
0,1-0,2% länger als 1200s (Extremwerte bis 47 Minuten -- klar abgesetzter
Ausreißer-Schwanz). Live an zwei weiteren, unauffälligen Songs bestätigt:
"Dragostea Din Tei" (235,9s, danach korrekt akzeptiert) und "Helden Und
Diebe" (bei 180s fälschlich abgeschnitten, hätte 186,2s gebraucht). Deckel
auf **300s** gesetzt -- lässt die legitimen langsamen Fälle durch, kappt
weiterhin die extremen Ausreißer.

**Bewusst NICHT umgesetzt:** eine gesonderte Behandlung "Timeout zählt
nicht als finales Whisper-Verdikt" (würde eine bestehende `.lrc` vor
Löschung schützen, selbst wenn Whisper bei einem echten Hänger wie Dooh
Dooh nichts Verwertbares liefert) -- nach Live-Test zurückgestellt, da der
befürchtete Fall (korrekter Text wird durch Timeout fälschlich gelöscht)
sich bei "Dragostea Din Tei" NICHT bestätigte (Timeout lieferte trotzdem
genug korrekte Wörter für eine richtige Annahme). Bleibt ein möglicher
Ansatzpunkt für den selteneren Fall "Whisper halluziniert auch mit
korrekter Sprachvorgabe weiter" (wie Dooh Dooh), falls das künftig
auffällt.

Neue Tests: `test_bricht_bei_ueberschrittenem_zeit_deckel_ab`
(`TestTranscribeWithEarlyStop`). 581/581 Tests grün, `ruff` sauber.
`lyrics_core.__version__` auf `2.0.2` erhöht.

## ✓ Sig-Backfill: reines Nachtragen der Signatur ohne Neubewertung für die Migrationswelle

**Auslöser:** Nach den beiden Fixes unten lief die "volle Selbstheilung"
tatsächlich korrekt -- aber der Nutzer bemerkte live im Log, dass dabei
Songs wie "01 Black Night"/"02 Love Machine" erneut per Konsens bewertet
wurden, obwohl exakt dasselbe Ergebnis wie vorher rauskam ("Irgendwie schade,
dass nur wegen der sig die Songs nochmal laufen müssen. Einen Mehrwert für
die lrc hat es ja nicht, oder?"). Zutreffend: diese Songs hatten schlicht nie
ein `sig`-Feld (sie stammen aus der Zeit VOR dem Signatur-Fix) -- am Genre
oder der eigentlichen Textentscheidung hatte sich nichts geändert, die
komplette Neubewertung (inkl. bei anderen Songs echtem Whisper-Aufwand) war
für die eigentliche `.lrc`-Datei reine Verschwendung.

**Fix:** Neue Funktion `lyrics_core._sig_backfill(entry, conn, artist_key,
titel_key)`: für Einträge, denen NUR die `sig` fehlt, wird geprüft, ob sich
der Genre-Skip-Status seit dem alten Eintrag nachweislich NICHT geändert hat
(`entry["reason"] == "kein-provider"` vs. aktuelles `is_skip` müssen
übereinstimmen) UND seit `entry["ts"]` keine neue DB-Aktivität dazukam
(`_db_newer_than_json_entry`, z.B. ein neuer Provider-Treffer) -- nur dann
wird die aktuelle `sig` gefahrlos in den bestehenden Eintrag nachgetragen,
OHNE `evaluate_song()` (und damit ohne Whisper/Provider-Abfrage) erneut
aufzurufen. Weicht der Genre-Status ab oder ist seit `ts` etwas Neues
dazugekommen, greift weiterhin die normale volle Neubewertung (der
eigentliche "Big City Beats"-Fall bleibt damit unverändert erkannt).

Eingebunden an beiden Stellen, die tatsächlich Whisper/Provider-Arbeit
auslösen könnten: `write_lrc.write_all()` (schreibt den nachgetragenen
Eintrag auch dauerhaft) und `evaluate_lyrics._skip_reevaluation()`
(--bewerten-Phase, rein lesend -- die eigentliche Persistierung übernimmt
im selben Durchlauf ohnehin write_lrc.py für denselben Song).

Bewusste Einschränkung: Artist/Titel werden beim Backfill NICHT geprüft --
alte Einträge ohne `sig` speichern sie gar nicht. Ein gleichzeitiges
Retagging von Artist/Titel (selten, siehe ROADMAP-Archiv "Umlaut-Tag-
Korrekturen") bliebe hier unentdeckt; der ganz überwiegende Teil der
fehlenden Signaturen ist reine Migration ohne echte Änderung.

10 neue Tests (`TestSigBackfill` in `test_lyrics_core.py`,
`TestWriteAllSigBackfill` in `test_write_lrc.py`, ein Test in
`TestEvaluateAllSkipUnveraendert`). 580/580 Tests grün, `ruff` sauber.
`lyrics_core.__version__` auf `2.0.1` erhöht.

## ✓ Bugfix: Selbstheilung landete nie auf der Platte -- Song wurde bei JEDEM Lauf neu gewhispert ("Helikopter-Fall")

**Auslöser:** Direkt nach Freigabe der Signatur-Snapshot-Selbstheilung (siehe
nächster Eintrag) meldete der Nutzer live während eines `--recursive`-Laufs:
"ich lasse den Aufruf zum zweiten Mal laufen und nun wird der Song wieder
gewhispert?" -- derselbe Track wurde nicht nur einmalig (erwarteter
Selbstheilungs-Preis), sondern bei JEDEM erneuten Lauf wieder komplett neu
transkribiert.

**Untersuchung, per DB-Abfrage verifiziert:** `early_stop_log` zeigte für
"04 Helikopter (Markus Becker Solo Mix)" Whisper-Läufe an zwei verschiedenen
Tagen (21.07. und 22.07.), aber der JSON-Ordner-Cache-Eintrag für diesen Song
blieb bei beiden Läufen unverändert auf uraltem Stand (`v=1.13.17`,
kein `sig`-Feld, `ts` vom 17.07.) stehen -- obwohl `_save_cache()` nachweislich
in der Zwischenzeit erfolgreich für ANDERE Songs im selben Ordner schrieb.

**Ursache:** `_save_cache()`s Merge-Logik übernimmt einen neuen Eintrag nur,
wenn sein `"ts"` ≥ dem bereits auf der Platte stehenden `"ts"` ist (Schutz
gegen Lost-Updates zwischen zwei parallelen Instanzen). `"ts"` stammt aber aus
`cache_store.latest_result_timestamp()` -- dem jüngsten DB-Datensatz
(Provider-Treffer/Transkript), NICHT der Wanduhr-Zeit des Schreibvorgangs.
Für "Helikopter": `latest_result_timestamp()` lieferte `2026-07-16T11:25:55`
(letzte Provider-Abfrage; Whisper wird bei Early-Stop nie persistiert, siehe
nächster Eintrag), der Platten-Eintrag stand aber schon auf `2026-07-17
T18:08:25`. Jeder frisch berechnete Eintrag -- inklusive der neuen, korrekten
`sig` -- war damit laut DB-Zeitstempel "älter" als der vorhandene und wurde
von `_save_cache()` verworfen, bevor er je auf die Platte kam. Der Song blieb
für immer "veraltet" und löste bei jedem Lauf erneut die volle
Whisper-Auswertung aus, ohne dass sich je etwas dauerhaft änderte. Betraf in
einem einzigen Test-Ordner mindestens 17 von 46 Tracks.

**Fix:** `_save_cache()` akzeptiert einen neuen Eintrag jetzt zusätzlich
IMMER, wenn sich seine `sig` vom Platten-Stand unterscheidet -- unabhängig
vom ts-Vergleich. Der ts-basierte Lost-Update-Schutz bleibt für den
unveränderten Fall (gleiche sig, zwei Prozesse) unverändert bestehen. 2 neue
Regressionstests (`test_geaenderte_sig_gewinnt_trotz_aelterem_ts`,
`test_gleiche_sig_mit_aelterem_ts_verliert_weiterhin`). 568/568 Tests grün,
`ruff` sauber. `lyrics_core.__version__` auf `2.0.0` erhöht.

Löst NICHT das im nächsten Eintrag beschriebene Kosten-Risiko (91%
Early-Stop, kein Transkript-Cache) -- dieser Fix sorgt nur dafür, dass eine
einmal fällige Selbstheilung auch tatsächlich EINMALIG bleibt, statt sich bei
jedem Lauf zu wiederholen.

## ✓ Bugfix: JSON-Cache erkannte Retagging nicht + ⚠ offenes Risiko bei Selbstheilung (Signatur-Snapshot)

**Auslöser:** Nutzerfrage direkt nach dem vorherigen Fix: "Prüfst du denn beim
Lauf wirklich ob sich die Songdatei geändert hat zu dem was in der Datenbank
steht? Song, Artist, Genre??? Oder nur gegen den JSON-Cache???" Antwort nach
Prüfung: nur `_db_newer_than_json_entry()`, ein reiner Zeitstempel-Vergleich
-- der bei nicht-monotonen Änderungen (Retag ohne neue DB-Zeile, Löschungen)
blind ist. Nutzerwunsch: Songdatei als Single Point of Truth, JSON-Cache soll
sich bei jeder identitätsrelevanten Änderung selbst invalidieren. Auf
ausdrücklichen Wunsch mit Opus konsultiert ("ich will richtig wirklich gute
Vorschläge").

**Lösung (Opus-Vorschlag, umgesetzt):** Neue Funktion `lyrics_core.
_current_sig(conn, artist_key, titel_key) -> [titel_key, artist_key,
is_skip]` -- ein Signatur-Snapshot der Entscheidungs-Eingaben. Wird in jedem
Cache-Eintrag mitgespeichert (`_build_cache_entry`) und bei jedem Zugriff
zuerst gegen die aktuelle Signatur verglichen (`_cache_entry_up_to_date`),
noch vor dem alten Zeitstempel-Check. Jede Abweichung -- Genre wechselt
zu/von Skip-Genre, Artist/Titel-Retag -- macht den Eintrag sofort veraltet,
unabhängig davon ob und wann neue DB-Zeilen entstanden. Zusätzlich liest
`evaluate_lyrics.evaluate_song()` das Genre jetzt direkt aus der DB und
bricht bei Skip-Genre sofort mit `kein-provider` ab -- unabhängig vom Zustand
der `ergebnisse`-Tabelle. Das macht den `DELETE FROM ergebnisse`-Teilschritt
aus dem vorigen Eintrag überflüssig; er wurde zurückgenommen (siehe dort).

Per `AskUserQuestion` explizit "Volle Selbstheilung" gewählt: fehlende
Signatur in einem alten JSON-Eintrag gilt automatisch als veraltet, kein
gesonderter Migrationsschritt nötig.

Tests: `TestCurrentSig` (5 neue Tests), `TestCacheEntryUpToDate` um 3 Tests
erweitert (fehlende Signatur, Genre-Wechsel zu Skip, unveränderte Signatur
bleibt aktuell). 566/566 Tests grün, `ruff` sauber.
`lyrics_core.__version__` auf `2.0.0` erhöht.

**⚠ Offenes Risiko, NICHT behoben:** Nach Freigabe der "vollen Selbstheilung"
lief der Nutzer `--recursive` erneut, und das Log zeigte für Songs mit
geänderter Signatur durchgehend neue Whisper-Läufe. Ich hatte vorher
unbelegt behauptet, die Selbstheilungs-Welle sei größtenteils günstig, weil
Transkripte gecacht seien -- das war eine Vermutung, kein verifizierter
Befund (Verstoß gegen "Evidenz vor Vermutung", vom Nutzer zurecht scharf
zurückgewiesen). Reale Prüfung per DB-Abfrage `SELECT early_stopped,
COUNT(*) FROM early_stop_log GROUP BY early_stopped` ergab: `0 → 324`,
`1 → 3244`. **3244 von 3568 historischen Whisper-Versuchen (91%) wurden früh
gestoppt** -- und früh gestoppte Transkripte werden bewusst NIE in
`transkripte` persistiert (Design-Grund: unvollständige Daten sollen
künftige Vergleiche nicht verunreinigen).

Konsequenz: Für ~91% der bereits Whisper-verifizierten Songs verursacht die
Selbstheilungs-Welle bei jeder Neuauswertung (Genre-/Tag-Änderung) volle
Whisper-Kosten, keine billige Cache-Nachprüfung. Die Grundannahme hinter der
Nutzer-Entscheidung "Volle Selbstheilung" -- dass eine Neubewertung günstig
ist -- trifft für den Großteil der Bibliothek nicht zu. Bewusst offen
gelassen: der Nutzer hat die Session an dieser Stelle gestoppt, um das erst
zu bewerten, bevor weitere große `--recursive`-Läufe gestartet werden.

Mögliche Ansatzpunkte für eine künftige Session (nicht umgesetzt, nur
Ideen, mit Trade-offs abzuwägen): früh gestoppte Transkripte doch
persistieren (steht dem ursprünglichen Kontaminationsgrund entgegen); Signatur-
Änderung nur bei tatsächlich Whisper-relevanten Feldern auslösen statt bei
jeder Artist/Titel-Änderung; eine separate, günstigere Vorprüfung ob sich die
Datei überhaupt hörbar geändert hat, vor einem vollen Re-Whisper.

## ✓ Bugfix: Genre-Retagging kam nie in der DB an + Kontext-Aufbau zu früh ("Big City Beats"-Fall)

**Auslöser:** Nutzer taggte Party-/Club-Remix-Tracks ohne sinnvollen Songtext
als Genre `Club Remix Instrumental` (siehe `--is_skip_genre`-Mechanismus),
damit sie beim `--abfragen`-Schritt übersprungen werden. Bei einem realen
Lauf über `_Various Artists` liefen zwei Dinge trotzdem falsch: (1) der
kontrastive Hintergrund-Kontext (teurer Aufbau, ~34k IDF-Dokumente) wurde
sofort beim ersten bewerteten Song gebaut, obwohl dieser genre-geskippt war
und nie Whisper brauchte; (2) Track "45 (Olav Basoski Remix)" (Big City Beats
Vol. 14) durchlief trotz korrektem aktuellem Genre-Tag weiterhin volle
Whisper-Auswertung.

**Untersuchung, Befund 1 (Kontext-Timing):** `lyrics_core._note_contrastive_
evaluation()` wurde bisher in `evaluate_all()`s Schleife für JEDEN
*bewerteten* Song aufgerufen -- unabhängig davon, ob dieser Song überhaupt
bis zum Whisper-Zweig kommt. Ein genre-geskippter Song (0 Kandidaten, landet
immer bei `kein-provider`) zählte trotzdem als "bewerteter Song" und konnte
so den teuren Aufbau auslösen, nur weil er zufällig der erste im Lauf war.

**Untersuchung, Befund 2 (stale Genre + stale Provider-Cache), per DB-Abfrage
verifiziert:** Track "45" hatte in der DB noch `genre='Dance & DJ'`
gespeichert -- vom allerersten Scan (16.07.), bevor die Datei umgetaggt
wurde. Ursache: `cache_store._get_or_create_song()` setzte ein Genre nur
beim *erstmaligen* Anlegen (`COALESCE(songs.genre, excluded.genre)` bevorzugt
den ALTEN Wert) -- ein späteres Retagging der Datei kam in der DB nie an.
Zusätzlich blieben die beiden bereits gefundenen Provider-Treffer (lrclib,
netease, ebenfalls vom 16.07.) unabhängig vom Genre für immer gültig im
Cache -- selbst ein korrigiertes Genre hätte sie nicht entfernt.

**Nutzer-Entscheidung (zwei Design-Fragen, da Datenverlust-Trade-offs
betroffen):**
1. `--scan` überschreibt das DB-Genre künftig immer mit dem aktuellen
   Tag-Wert (nicht nur beim erstmaligen Anlegen) -- ein leerer/None-Wert
   überschreibt dabei NICHT (schützt vor Datenverlust bei einem fehlerhaften
   Tag-Lesevorgang).
2. Wechselt das Genre zu einem Skip-Genre, werden bereits vorhandene
   Provider-Treffer für diesen Song verworfen -- er verhält sich danach wie
   neu angelegt.

**Fix:**
1. `cache_store._get_or_create_song()`: `COALESCE(excluded.genre,
   songs.genre)` (Reihenfolge umgedreht -- neuer Wert hat jetzt Vorrang,
   alter nur noch Fallback bei leerem neuem Wert).
2. `fetch_providers.fetch_all()`: bei erkanntem Skip-Genre wurde zusätzlich
   `DELETE FROM ergebnisse WHERE song_id=?` ausgeführt, bevor der Song
   übersprungen wird. **Dieser Teilschritt wurde im nächsten Eintrag
   (Signatur-Snapshot) wieder zurückgenommen** -- er brach
   `_db_newer_than_json_entry()` (weniger `ergebnisse`-Zeilen ließen den
   JSON-Skip-Eintrag fälschlich "aktuell" statt "veraltet" aussehen) und
   war ohnehin unvollständig (deckte den Fall Artist/Titel-Retagging ohne
   Genre-Änderung nicht ab). Siehe dort für die endgültige Lösung.
3. `evaluate_lyrics.evaluate_song()`: `lyrics_core._note_contrastive_
   evaluation()` wird jetzt direkt im Whisper-Zweig aufgerufen (kurz bevor
   `_whisper_best()` gebraucht wird), nicht mehr pauschal in `evaluate_all()`s
   Schleife für jeden bewerteten Song.

11 Tests in `test_evaluate_lyrics.py` mussten wegen der verschobenen
Aufrufstelle von `_note_contrastive_evaluation()` eine echte Cache-
Verbindung bekommen (`lyrics_core._cache_conn = conn`, spiegelt was die
Produktion an dieser Stelle ohnehin immer schon setzt); 3 Tests, die
`evaluate_song()` komplett wegmockten, riefen `_note_contrastive_
evaluation()` jetzt selbst in ihrem Mock auf, um die neue Aufrufstelle
korrekt zu simulieren. 558/558 Tests grün, `ruff` sauber.
`lyrics_core.__version__` auf `1.13.32` erhöht.

## ✓ Bugfix: existing_lrc ohne Konkurrenz "gewann" automatisch trotz katastrophal niedrigem Score ("Pohlmann-Fall")

**Auslöser:** Live-Lauf des Nutzers zeigte eine Zeile mit `0/4: —` (kein
einziger Provider kannte den Song, Pohlmann – "Besser Glauben Wir An Uns")
und `unter Schwelle idf-jacc=0,056` — aber die bestehende `.lrc`-Datei wurde
NICHT gelöscht (`=` statt `–` am Zeilenende).

**Ursache:** `existing_is_best = has_existing and best_path == existing_lrc`
(voriger Eintrag) prüfte nur, ob existing_lrc der beste Kandidat war — bei 0
frischen Treffern ist sie zwangsläufig die EINZIGE, "gewinnt" also
automatisch gegen niemanden. Ihr tatsächlicher Score floss in diese
Schutz-Entscheidung nie ein. Nutzer-Gegenprobe, die den Bug klar belegt: wäre
derselbe Text stattdessen als einziger FRISCHER Provider-Kandidat gekommen,
hätte `_whisper_accept(0,056, ...)` ihn klar abgelehnt — kein automatisches
Speichern. Reiner Zufall der Herkunft (Provider vs. bereits auf der Platte)
entschied bisher über Löschen oder Behalten, nicht die Qualität des Textes.

**Diskutierte, aber verworfene Zwischenlösung:** existing_best zusätzlich an
`contrastive_margin >= 0` binden (nicht schlechter als der Zufalls-
Hintergrund) hätte Pohlmann korrekt behandelt, aber echte Grenzfälle
weiterhin speziell geschützt. Nutzer-Entscheidung (von Opus als Zweitmeinung
bestätigt): einfachere, härtere Regel — das Whisper-Verdikt ist IMMER final,
unabhängig davon ob der Text von einem Provider oder von der Platte kam. Der
ursprüngliche Grund für die Sonderbehandlung (Halluzinations-Fehlklassifikation
konnte `has_vocals` fälschlich `False` setzen) ist bereits in einer früheren
Session-Änderung behoben — die Marge-Rauschen-Sorge bleibt zwar real, aber
`existing_best` war dafür ohnehin viel zu grob (schützte einen
Totalausfall wie 0,056 genauso wie einen echten Grenzfall bei 0,28). Der
saubere Ort für Margen-Rauschen ist die Marge selbst (größere/stabilere
Hintergrund-Stichprobe), nicht eine Bestandsschutz-Klausel.

**Fix:** `extras["existing_best"]` ist in den Zweigen `kein-vokal` und
`unter-schwelle` jetzt IMMER `False` — ein Whisper-Verdikt gilt für
existing_lrc genauso wie für einen frischen Kandidaten. Einzig verbliebener
`True`-Fall: der Kein-Audio-Zweig (`dauer-abweichung`), weil dort mangels
Audiodatei gar kein Whisper-Verdikt existiert, das etwas widerlegen könnte.
Die `existing_best`-Infrastruktur selbst (extras-Key, Aufrufer-Check in
`write_lrc.py`/`cut.py`) bleibt bestehen — sie hat mit diesem einen
verbliebenen Fall weiterhin eine echte, nicht-redundante Funktion.

**Ehrlicher Trade-off (bewusst in Kauf genommen):** ein Grenzfall-Score nahe
der Schwelle kann bei ungünstiger Zufalls-Hintergrundziehung künftig gelöscht
statt behalten werden. Das ist selbstheilend (ein späterer Lauf mit anderer
Stichprobe oder neuen Provider-Daten schreibt ihn bei echter Übereinstimmung
neu) und bewusst der einfacheren, konsistenteren Regel vorgezogen.

2 Tests umgedreht (`test_existing_best_false_bei_kein_vokal`,
`test_existing_best_false_bei_unter_schwelle`, vormals `..._true_...`),
Klassen-Docstring und Aufrufer-Kommentare in `write_lrc.py`/`cut.py`
korrigiert. 558/558 Tests grün, `ruff` sauber. `lyrics_core.__version__` auf
`1.13.31` erhöht.

## ✓ Bugfix: starke Mehrheits-Einigkeit wurde durch die Gruppierung selbst verworfen ("Fernando-Fall")

**Auslöser:** Empirische Prüfung des Konsens-Umbaus (siehe vorheriger Eintrag)
gegen 5 echte englischsprachige Songs aus der Bibliothek. Bei ABBA "Fernando"
(4/4 frische Provider-Treffer) wurde die bestehende, korrekte `.lrc`-Datei
unerwartet durch eine leicht andere Fassung ersetzt, obwohl objektiv eine
sehr starke Übereinstimmung vorlag.

**Ursache, mit echten Zahlen belegt:** Alle 5 Quellen (existing_lrc + 4
Provider) stimmten paarweise zu 82–100% überein -- lrclib/musixmatch/netease/
existing_lrc landeten wegen dieser extremen Ähnlichkeit korrekt in EINER
Gruppe (`_group_candidates`, Zweck: keine Doppelzählung), Genius (durch seine
Kontributoren-Kopfzeile leicht verwässert, aber immer noch 82-84% ähnlich)
blieb als zweite, eigene Gruppe übrig. Macht nur noch **2 Gruppen** --
`_provider_consensus` verlangt aber `min_providers=3` **Gruppen**, bevor es
überhaupt die tatsächliche Übereinstimmung zwischen ihnen prüft. Ergebnis:
trotz 82%+ Übereinstimmung wurde der Fall als "zu wenig Kandidaten"
abgewiesen und fiel auf die (unabhängige, ältere) Dauer-Heuristik zurück, die
ihrerseits per Zeilenanzahl eine andere Fassung als die bestehende wählte.

**Kernfrage (vom Nutzer aufgeworfen):** Wozu dient Gruppierung/Dedup
eigentlich? Antwort: um zu verhindern, dass NICHT-unabhängige (gespiegelte/
korrelierte) Quellen als mehrere unabhängige Bestätigungen durchgehen -- NICHT
um echte, starke Übereinstimmung zu bestrafen. Die Mindestanzahl-Schwelle
(`min_providers`) sollte deshalb auf der **rohen, ungruppierten**
Quellenzahl prüfen (genug unabhängige Quellen überhaupt vorhanden?), während
die eigentliche Übereinstimmungs-Rechnung weiterhin auf den **gruppierten**
Repräsentanten läuft (schützt weiter vor Mehrfachzählung durch Mirrors).

**Fix:** `lyrics_core._provider_consensus()` bekommt einen neuen Parameter
`raw_count` -- die Mindestanzahl-Prüfung (`min_providers`) läuft jetzt darauf
statt auf der Länge der (ggf. gruppierten) `candidates`-Liste. Default (kein
`raw_count` übergeben) zählt weiterhin nur AUSWERTBARE (nicht-leere)
Kandidaten in `candidates` selbst -- eine leere/unlesbare Datei darf die
Schwelle nie mit erfüllen helfen, auch nicht indirekt (Regressionsschutz für
`test_leere_lrc_zählt_nicht`, bei einem ersten Entwurf dieses Fixes real
kaputtgegangen und beim Testen aufgefallen). `evaluate_lyrics.evaluate_song()`
übergibt jetzt `raw_count=lyrics_core._nonempty_candidate_count(all_candidates)`
(neue kleine Hilfsfunktion, zählt Kandidaten mit echtem Wortinhalt VOR der
Gruppierung). Fables Zirkelschluss-Schutz (vorheriger Eintrag) bleibt
unangetastet: dort bilden sich ebenfalls nur 2 Gruppen, die sich aber
UNEINIG sind -- die Übereinstimmungs-Rechnung selbst (unverändert auf den
Gruppen) verweigert weiterhin den Konsens.

**Erneut empirisch verifiziert** (dieselben 5 Songs, echte Bibliotheksdaten,
read-only): Fernando jetzt `Konsens 84%`, Datei unverändert. "2 Unlimited –
Faces" (2 frische Provider) erreicht jetzt ebenfalls Konsens (87%) statt
Heuristik -- der ursprünglich beabsichtigte Nutzen des ganzen Umbaus
(existing_lrc als dritte Stimme). "Adamski – Over Killer" (nur 1 frischer
Treffer + existing = 2 rohe Quellen) bleibt korrekt unter der Schwelle.

4 neue Tests (`test_lyrics_core.py`: `TestProviderConsensus` --
`test_raw_count_rettet_starke_einigkeit_trotz_wenig_gruppen`,
`test_raw_count_ohne_uebergabe_zaehlt_nur_auswertbare_kandidaten`,
`test_raw_count_schuetzt_weiterhin_vor_zirkelschluss`;
`test_evaluate_lyrics.py`: `test_starke_mehrheits_einigkeit_ueber_nur_zwei_gruppen`).
558/558 Tests grün, `ruff` sauber. `lyrics_core.__version__` auf `1.13.30`
erhöht.

## ✓ Umbau: existing_lrc als vollwertiger Konsens-Kandidat + Löschschutz gegen verrauschte Signale

**Auslöser:** Nutzerfrage, wie das Programm verhindert, dass eine erneute
Provider-Abfrage (`--nachholen`) eine bereits korrekte `.lrc`-Datei durch
einen schlechteren/falschen Treffer ersetzt oder grundlos löscht. Beim
Durchverfolgen von `evaluate_lyrics.py`/`write_lrc.py`/`cut.py` (mit
Zweitmeinungen von Opus und Fable) fielen mehrere zusammenhängende Lücken auf.

**Fund 1 -- Konsens-Schnellpfad umging existing_lrc komplett:**
`_provider_consensus(candidates)` prüfte nur frische Provider untereinander,
ohne jeden Vergleich mit einer bereits vorhandenen Datei. Erster Fix: ein
nachträglicher Veto-Check (Konsens-Kandidat gegen existing_lrc per Jaccard
vergleichen, bei Abweichung zu Whisper durchreichen). Nach weiterer Diskussion
verworfen zugunsten von Fund 4 (sauberere Lösung).

**Fund 2 -- "kein Vokal"/"unter Schwelle" löschten existing_lrc ohne Beleg:**
`write_lrc.py`/`cut.py` löschten eine vorhandene `.lrc` bei jedem
`found=False`, auch wenn `_whisper_best()` sie selbst intern als besten
Kandidaten gescort hatte (`best_path == existing_lrc`) -- die beiden
Signale, die `found=False` auslösen (has_vocals-Flag, kontrastive
Marge/Schwelle), sind beide verrauscht (Halluzinations-Fehlklassifikation
bzw. mit dem Cache wandernder Hintergrund-Pool). **Fix (dauerhaft):**
`evaluate_lyrics.evaluate_song()` liefert jetzt `extras["existing_best"]`
(True wenn `best_path == existing_lrc`, oder -- ohne Audiodatei, also ohne
jeden Gegenbeweis -- konservativ True wenn existing_lrc existiert).
`write_lrc.py`/`cut.py` löschen nur noch, wenn `existing_best` False ist
(`outcome: "keep"`, Cache-Eintrag bleibt `r="ok"`, spätere echte
Provider-Neuigkeiten lösen weiterhin `--nachholen`-Neubewertung aus).

**Fund 3 -- Score-Gleichstand bevorzugte fälschlich den frischen Kandidaten:**
Nutzer-Einwand: existing_lrc kann von Hand oder ursprünglich von einem
Provider stammen -- läuft aber nie durch einen Dedup-Schritt gegen frische
Kandidaten. Ist sie wort-identisch, aber byte-verschieden (andere
Zeitstempel/Formatierung) zu einem frischen Kandidaten, entscheidet
`_whisper_best()`s striktes `>` beim Scoring-Gleichstand -- der zuerst in der
Liste stehende Kandidat gewinnt. **Fix (dauerhaft):** `all_candidates` stellt
existing_lrc an den Anfang, nicht ans Ende -- bei Gleichstand gewinnt sie,
ohne dass ein strikt besserer frischer Kandidat je verdrängt würde.

**Fund 4 -- existing_lrc sollte als vollwertige Konsens-Stimme zählen dürfen:**
Nutzer-Vorschlag (mehrfach präzisiert): alle bis zu 5 Quellen (4 frische +
existing_lrc) gemeinsam deduplizieren, danach bei ≥3 verbleibenden Konsens
versuchen, sonst Whisper. Erster Sonnet-Einwand (Byte-Dedup würde die echte
Datei physisch löschen bzw. erfasse den Fall praktisch nie) traf nur die
wörtliche Byte-Implementierung, nicht die Idee (Opus-Zweitmeinung). Fable
fand danach einen echten, testbelegten Fehlerkanal: `_provider_consensus`s
C3-Ausreißer-Rettung prüft nach dem Rauswurf des schlechtesten Kandidaten
NICHT erneut gegen `min_providers` (siehe `test_ausreisser_c3_gerettet`) --
wäre existing_lrc eine ungruppierte Stimme neben ihrem eigenen (unveränderten)
Herkunfts-Provider, könnte dieses Paar einen dritten, tatsächlich korrekten
Kandidaten als vermeintlichen Ausreißer verdrängen. Nutzer-Gegenprüfung
(durch Code-Beleg bestätigt): die 4 Provider matchen alle über Fuzzy-/
Textsuche (`_looks_like_translation`-Kommentar: Genius übernimmt "ungeprüft
den ersten Suchtreffer"; Präzedenzfall im selben Projekt, MusicBrainz-Dauer-
Bug "Bohemian Rhapsody" 157s statt 355s, gefixt mit Median statt erstem
Treffer) -- eine exakte Wiederholung derselben Provider-Antwort ist NICHT der
Normalfall. Und strukturell entschärft sich Fables Angriff von selbst, wenn
(wie vorgesehen) ERST gruppiert und DANACH gezählt wird: ein existing_lrc,
die mit ihrem Herkunfts-Provider übereinstimmt, landet dann in derselben
Gruppe und zählt nur als eine Stimme -- das Ausreißer-Paar aus Fables Szenario
kann so gar nicht erst entstehen.

**Umsetzung:**
1. `lyrics_core._dedupe_by_content()` (reiner Byte-Hash, löschte
   Duplikat-Dateien) ersetzt durch `lyrics_core._group_candidates()`
   (wort-basierte Jaccard-Gruppierung, Schwelle `_GROUP_WORD_JACCARD=0,90`,
   löscht nichts -- rein logische Gruppierung, erster Kandidat in
   Prioritätsreihenfolge bleibt Repräsentant).
2. `evaluate_lyrics.evaluate_song()`: Gruppierung läuft jetzt über
   `all_candidates` (existing_lrc + alle frischen, existing zuerst), das
   Ergebnis geht direkt in `_provider_consensus()` -- der separate
   Veto-Check aus Fund 1 entfällt (jetzt redundant), `_lrc_word_jaccard()`
   dadurch ebenfalls ungenutzt und entfernt.
3. Fund 2 (existing_best) und Fund 3 (Reihenfolge) bleiben unverändert
   bestehen -- unabhängig vom Konsens-Umbau, schützen die Whisper-Stufe.

23 neue/angepasste Tests (`test_lyrics_core.py`: `TestGroupCandidates`,
`TestIsHallucination`-Nachbarschaft; `test_evaluate_lyrics.py`:
`TestEvaluateSongExistingBest`, `TestEvaluateSongKonsens`; `test_write_lrc.py`,
`test_cut.py`). 554/554 Tests grün, `ruff` sauber. `lyrics_core.__version__`
auf `1.13.29` erhöht.

## ✓ Bugfix: "kein Vokal"-Sonderfall abgeschafft + Halluzinationsfilter erkannte echte Songs mit langem Outro fälschlich als Loop

**Auslöser:** Beim Aufräumen der Neil-Diamond-Alben fiel auf, dass der
Sonderfall „bei `has_vocals=False` reicht 2-Provider-Konsens trotzdem zum
Speichern" (siehe Archiv, `_provider_consensus(candidates, min_providers=2)`
im `not has_vocals`-Zweig von `evaluate_lyrics.py`) in der echten Bibliothek
tatsächlich noch auftrat -- 23 Treffer über die gesamte Musikbibliothek
gefunden (`no_vocal: true` + `method: konsens` im JSON-Ordner-Cache). Beispiel
Carpenters „Carol Of The Bells": die gespeicherte LRC zeigte die echten,
offiziellen Songtexte, obwohl diese konkrete Aufnahme instrumental ist --
Provider-Konsens allein kann nicht unterscheiden, ob ein Songtitel offizielle
Lyrics hat oder ob die KONKRETE Aufnahme tatsächlich gesungen wird.

**Fix 1 (`evaluate_lyrics.py`):** Nutzer-Entscheidung: „wenn `no_vocal=true`,
ist keine LRC valide." Der 2-Provider-Rettungszweig komplett entfernt --
`has_vocals=False` führt jetzt immer zu `reason: "kein-vokal"`, nie mehr zu
einem automatischen Speichern. Alle 23 betroffenen `.lrc`-Dateien in der
Bibliothek identifiziert und gelöscht; die zugehörigen JSON-Ordner-Cache-
Einträge müssen nicht manuell angefasst werden -- `lyrics_core.
_cache_entry_up_to_date()` erkennt fehlende `.lrc`-Dateien automatisch und
bewertet den Track beim nächsten Lauf neu (`entry.get("r") == "ok" and not
lrc_path.exists()` → `False`).

**Fund beim Identifizieren:** 2 der 23 Treffer (Bronski Beat/Communards
„Never Can Say Goodbye", Nu Shooz „Lost Your Number") haben tatsächlich
durchgehend Gesang -- Whisper (Modell `medium`, aktuell) hatte den Text
korrekt erkannt, aber `_is_hallucination()` warf das komplette Transkript weg.
Beide Songs haben ein langes ECHTES Fade-out/Outro, in dem ein Hook-Wort real
wiederholt gesungen wird ("no" x149/36,8%, "lost" x121/38,3% aller Wörter) --
das erfüllt beide bisherigen Kriterien (unique-ratio <25% UND dominantes Wort
≥25%, siehe v1.4.20) genauso wie eine echte Halluzinationsschleife.

**Fix 2 (`lyrics_core.py`), nach Zweitmeinung von Opus konsolidiert:**
1. Neue Konstante `_HALLUCINATION_MAX_UNIQUE_WORDS = 15` als zusätzliches
   UND-Kriterium in `_is_hallucination()`: mehr als 15 einzigartige Wörter
   insgesamt → kein Alarm, unabhängig von den Ratios. Echte
   Halluzinationsschleifen ("lets go" ×20) haben nur 2-10 einzigartige
   Wörter, echte Songs mit langem Outro auch bei niedriger Ratio 50+ (94 bzw.
   51 im konkreten Fall) -- die Ratio-Kriterien allein sind blind für diesen
   Größenunterschied, da beide relativ zur Gesamtwortzahl sind.
2. Scoring von der Halluzinations-Filterung entkoppelt: `_score_against_idf`
   (und die kontrastive Marge) nutzen jetzt in beiden `_whisper_best`-Pfaden
   (Cache-Hit UND Cache-Miss) immer `raw_words` statt der gefilterten Liste.
   Begründung: der Score arbeitet ohnehin über `set(...)` -- Wiederholungs-
   häufigkeit eines Worts geht nie in den IDF-Jaccard-Score ein, das frühere
   Nullsetzen bot dort also keinen zusätzlichen Schutz vor Halluzinationen
   (eine echte kurze Loop wie `{"lets","go"}` ergibt gegen eine echte LRC
   ohnehin nur eine winzige Schnittmenge, der Score bleibt von selbst
   niedrig), konnte aber im Fehlalarmfall einen validen Treffer auf `0.0`
   zerstören. Der Halluzinations-Flag steuert jetzt ausschließlich noch
   `total_words`/`has_vocals`.

**Trade-off (laut Opus-Zweitmeinung, akzeptiert):** Eine Halluzination mit
zufällig >15 verschiedenen Müll-Wörtern würde künftig nicht mehr erkannt --
in der Praxis selten, da echte Whisper-Loops kurze Fixphrasen sind; der
Schaden bliebe dank Fix 2 ohnehin auf `has_vocals` begrenzt.

23/23 gelöschte Fälle stichprobenartig live gegengeprüft (Carol Of The Bells,
Tubular Bells etc. weiterhin korrekt "kein Vokal"; Never Can Say Goodbye,
Lost Your Number jetzt korrekt "hat Vokal"). 8 neue Tests
(`test_lyrics_core.py`, `test_evaluate_lyrics.py`), 533/533 Tests grün, `ruff`
sauber. `lyrics_core.__version__` auf `1.13.28` erhöht.

## ✓ Aufräumen: WER/Modellvergleich-Rohdaten + -Doku entfernt

Alle Rohdaten und Zwischen-Dokus der längst abgeschlossenen WER- und
Modellvergleichs-Experimente entfernt (Entscheidungen stehen bereits
zusammengefasst in diesem Dokument bzw. als Codekommentar, siehe
"Kontrastive Marge statt absoluter Schwelle" oben und "Nachtrag: large-v3
ergänzt"): `CHECKPOINT_kontrastiv.md`, `bigram_jaccard_test_ergebnis.md`,
`contrastive_reselection_check.md`, `contrastive_run_vergleich.md` (getrackt),
sowie untracked liegen gebliebene `wer_whisper_uneinigkeit.md`,
`scratch_contrastive_test_ergebnis.md`, `whisper_modellvergleich_ergebnis.md`,
`whisper_modellvergleich/`, `contrastive_experiment_log.csv`,
`large_v3_run.log`, `speed_test.log`. Tote Dateiverweise in `lyrics_core.py`-
Kommentaren, `evaluate_lyrics.py`-Docstring und `README.md` entsprechend
entfernt (Zahlen/Schlussfolgerung blieben dort ohnehin schon inline stehen).
`compare_whisper_models.py` bleibt als wiederverwendbares Werkzeug erhalten,
nur seine alte Beispiel-Ausgabe ist weg.

**Einzige echte Datenlücke:** `contrastive_experiment_log.csv` war die
einzige verbliebene Quelle für 784 rohe Whisper-Einzelentscheidungen aus dem
alten Kalibrierungslauf — die Schlussfolgerung daraus (kontrastive Marge statt
absoluter Schwelle) bleibt erhalten, die Rohdaten selbst nicht mehr.

528/528 Tests grün, `ruff` sauber.

## ✓ assemble.py: Normton-Fix, Default-Kennzeichnung, Umbenennen-Vorschlag

**Normton-Bug (analog zum `cut.py`-Fix in v1.9.6):** In der Crossfade-Vorschau
war der Normton am Ausklang viel kürzer zu hören als am Anfang. Ursache:
`ffmpeg` schrieb WAV direkt in eine Pipe an `ffplay` -- bei nicht-seekbaren
Pipes kann `ffmpeg` die WAV-Chunk-Größen nicht nachträglich patchen (Header
zeigt `0xFFFFFFFF`/unbekannt), `ffplay` kennt die reale Länge nicht und
schneidet ab. Fix wie in `cut.py`: `ffmpeg` rendert erst in eine temp-WAV,
`ffplay` spielt die Datei. Betraf `play_snippet_with_tone` und
`play_crossfade_preview`, beide jetzt mit Preview-Cache (kein Neu-Rendern bei
wiederholtem `[p]` auf gleicher Position).

Zusätzlich auf Nutzerwunsch: alle vier `[j/n]`-Prompts in `assemble.py` zeigen
jetzt wie in `cut.py` den Default groß (`[j/N]` -- alle vier defaulten auf
"nein"). Der Umbenennen-Vorschlag am Ende hängt jetzt automatisch
`-assembled` an den Dateinamen an und steht bereits vorausgefüllt im
Eingabefeld (`live_input()` in `cut_ui.py` hat dafür einen neuen optionalen
`initial`-Parameter) -- man muss den Namen nicht mehr komplett neu tippen,
nur noch anpassen.

528/528 Tests grün, `ruff` sauber. `assemble.py` auf `1.1.11` erhöht.

## ✓ Terminal-Farbschema fest verdrahtet, UI-Styles zentralisiert

**Auslöser:** Nutzer-Screenshot zeigte `cut.py`s Rich-Panel auf grünem
Terminal-Hintergrund praktisch unlesbar (blaue Rahmen, roter/grauer Text) --
weder `cut_ui.py` noch `assemble_ui.py` setzten je einen Hintergrund, die
Darstellung hing komplett vom Terminal.app-Profil des Nutzers ab und war bei
mehreren Profilen betroffen.

`Console(style="bright_white on black")` in `cut.py`/`assemble.py` erzwingt
jetzt Vordergrund UND Hintergrund für die komplette Live-Session, unabhängig
vom Terminal-Profil.

Zusätzlich auf Nutzerwunsch die bisher an 15+ Stellen wortgleich wiederholten
Style-Strings (`"grey35"`, `"blue dim"`, Status-Symbole ✓/→/○, Delta-Ampel
grün/gelb/rot) in `cut_ui.py` zentralisiert (`MUTED`, `BORDER`,
`status_symbol()`, `row_style()`, `severity_style()`, `normton_text()`) und
`assemble_ui.py` importiert sie, statt sie erneut zu duplizieren.

528/528 Tests grün, `ruff` sauber. `cut.py` auf `1.9.19`, `assemble.py` auf
`1.1.8` erhöht.

## ✓ Whisper-Early-Stop: Live-Transkription bricht bei sicherer Erkennung ab

`lyrics_core._transcribe_with_early_stop()` ersetzt `_transcribe()` im
Cache-Miss-Pfad von `_whisper_best()`: konsumiert faster-whisper's
Segment-Generator inkrementell statt per `list(...)` alles auf einmal zu
materialisieren, und bricht ab, sobald ein Kandidat über
`_EARLY_STOP_N_CONFIRM=3` aufeinanderfolgende 15s-Checkpoints stabil die
kontrastive Marge (+`_EARLY_STOP_MARGIN_BUFFER=0,10` Sicherheitspuffer) UND
einen Mindestabstand zum zweitbesten ECHTEN Kandidaten (`_EARLY_STOP_SEP_MIN`,
nach Dedupe wortidentischer Mehrfach-Provider-Texte via
`_dedupe_word_sets`/`_EARLY_STOP_DEDUPE_JACCARD=0,80`) erreicht, plus ein
hartes Mindest-Gate (`_EARLY_STOP_MIN_WORDS=20`/`_EARLY_STOP_MIN_SEC=30`).
NUR früh AKZEPTIEREN, nie früh ablehnen -- has_vocals/Ablehnung laufen
unverändert am vollen Transkript. Ein früh gestopptes (unvollständiges)
Transkript wird NICHT im Song-Cache persistiert.

An 53 echten, verifizierten Songs validiert (45 en/`medium`, 8 de/`large-v3`,
Musikbibliothek A/B): 0 Falsch-Akzeptanzen, ~61-63% Zeitersparnis im
Accept-Pfad (Details/Methodik siehe project-fetch-songtext-Memory). Realer
Bug unterwegs gefunden und gefixt: ohne Dedupe sind wortidentische
Mehrfach-Provider-Texte `best_score == second_score`, der Separations-Check
schlägt dann IMMER fehl und es wird nie früh gestoppt.

Sichtbarkeit: Pro-Song-Zeile zeigt `früh-gestoppt` wenn zutreffend
(`evaluate_lyrics.py`); am Ende jedes `--bewerten`-Laufs zusätzlich eine
Aggregat-Zeile (`songtext_pipeline.py`, aus `lyrics_core._early_stop_stats`):
Anzahl Läufe, davon früh gestoppt, geschätzte eingesparte Audiosekunden.

**v1.13.26 — dauerhaftes Log statt nur Terminal/RAM:** Neue Tabelle
`early_stop_log` (`cache_store.py`, `song_id`/`early_stopped`/`modell`/
`datum`) protokolliert JEDEN echten Whisper-Versuch (nicht nur Cache-
Treffer) per `log_early_stop_attempt()`, aufgerufen aus `_whisper_best()`.
Bewusst getrennt von `transkripte` (bleibt reiner Reuse-Cache für
vollständige Transkripte) — dieses Log ist nur Telemetrie, wird von keiner
Vergleichs-/Matching-Logik gelesen. Damit per SQL auswertbar (z.B. "wie
viele der letzten N Stunden früh gestoppt"), auch über Prozess-Neustarts
und Sessions hinweg, statt nur aus der flüchtigen Konsolenausgabe.

## ✓ IDF-Refresh-Intervall proportional statt fest (100 -> N × 5 %)

`evaluate_lyrics._IDF_REFRESH_INTERVAL` (fest 100) ersetzt durch
`lyrics_core._idf_refresh_interval(n) = max(5, round(0,05 × n))` -- n =
zuletzt bekannte texte+transkripte-Anzahl. Herleitung: IDF ist bereits eine
log(N/df)-Größe, ihr Effekt neuer Dokumente schrumpft mit 1/N -- ein fester
Prozentsatz hält die relative Veralterung konstant, statt bei kleinem Cache
zu träge und bei großem Cache unnötig oft zu prüfen. Bei aktueller
Datenmenge (~26667) macht das ~1333 statt 100 -- deutlich weniger
Neuaufbauten während eines langen Laufs, bei kleinen Caches (z.B. 100)
weiterhin engmaschig (~5).

## ✓ Bugfix: lrclib-Dump-Lookup fand Songs mit Akzent-Buchstaben nicht

LRCLib transliteriert Akzent-Buchstaben zu ASCII (João -> joao, Coração ->
coracao) -- `_strip_punctuation_for_lrclib_dump()` machte das nicht mit.
Betraf u.a. "The Girl From Ipanema" und "Para Machucar Meu Coração" (Stan
Getz & João Gilberto). Fix über neue zentrale Funktion `library.
to_ascii_fold()` (Paket `anyascii`, ISC-Lizenz, deckt mehr Zeichen ab als
das ältere GPL-lizenzierte `Unidecode`).

## ✓ Redundanz-Aufräumen Runde 3: JSON-Skip-Prädikat + whisper_analyse.py entfernt + ffprobe-Dauer

**Auslöser:** Letzter noch offener Punkt aus Runde 2 ("wichtigster Fund,
braucht mehr Sorgfalt"), plus zwei neue Nutzer-Entscheidungen währenddessen.

**Behoben:**
1. **JSON-Ordner-Cache-Skip-Prädikat** ("ist dieser Track schon aktuell,
   kann ich ihn überspringen?") war fast wortgleich dreifach unabhängig
   implementiert: inline in `write_lrc.write_all()`, inline in `cut.py`
   (bewusst OHNE den DB-Aktualitäts-Check), als eigene Funktion
   `evaluate_lyrics._skip_reevaluation()`. Neue gemeinsame Funktion
   `lyrics_core._cache_entry_up_to_date(entry, lrc_path, conn=None,
   artist_key=None, titel_key=None)` -- `conn=None` (cut.py) überspringt
   den DB-Check wie bisher, mit `conn` gesetzt (write_lrc.py,
   evaluate_lyrics.py) läuft die volle Prüfung. 7 neue Tests
   (`test_lyrics_core.py`) decken beide Modi ab.
2. **`whisper_analyse.py` gelöscht** (Nutzer-Entscheidung: "wirf weg was nur
   einfaches Werkzeug ist"): zeigte nur dieselben Zahlen wie `lrc_analyse.py`
   (Methode/Ablehnungsgrund), nur anders gruppiert ("mit/ohne Whisper" statt
   "akzeptiert/abgelehnt") -- keine echte Zusatzinfo. `lrc_analyse.py` und
   `lrc_recheck.py` bleiben (eigenständiger Nutzen).
3. **ffprobe-Dauer-Ermittlung** (Fund E aus Runde 2, war als Trade-off
   offen) -- Nutzer-Entscheidung: "library.py darf externe Programme
   benutzen und Abhängigkeiten dazu haben", die bisherige "kein
   subprocess"-Regel dort galt nicht mehr. Neue Funktion `library.
   get_audio_duration()`, wirft bei Fehlern durch (ehrliche Funktion, kein
   stiller Fallback). `fetch_metadata.get_flac_duration()` bleibt als
   dünner Wrapper, der bei Fehlern weiterhin `0.0` liefert (ihre
   bestehenden Aufrufer in `cut.py` verlassen sich darauf); `assemble.py`
   ruft die neue Funktion direkt auf, unverändertes Verhalten bei Fehlern
   (kein stiller Fallback, wie vorher). 4 neue Tests.

516/516 Tests grün, `ruff` sauber. `lyrics_core.__version__` auf `1.13.24`
erhöht.

## ✓ Redundanz-Aufräumen Runde 2: vollständiges Audit über alle 18 Module

**Auslöser:** Nutzer-Vorgabe nach Runde 1: "ich will am Ende ALLE Module auf
Redundanz geprüft wissen" -- vollständiges Audit über alle 18
Produktionsmodule (nicht nur cut.py/assemble.py/Songtext-Cluster).
Umsetzung bewusst in einem separaten Worktree (Nutzer nutzt die Programme
parallel produktiv).

**Behoben:**
1. **`_method()`/`_reject_reason()`** waren zwei- bzw. dreifach wortgleich
   in `lrc_analyse.py`, `lrc_recheck.py`, `whisper_analyse.py` dupliziert
   (`whisper_analyse.py` kommentierte es sogar selbst: "identisch mit
   lrc_analyse.py") -- UND hatten in keiner der drei Dateien eigene Tests.
   Neue Funktionen `library.method_from_cache_entry()`/
   `library.reject_reason_from_cache_entry()` (Nutzer-Entscheidung: in
   `library.py`, nicht `lyrics_core.py` -- vertikale Schichtung ist erlaubt,
   aber diese reine Klassifikationslogik über JSON-Cache-Einträge soll
   zentral, nicht fachspezifisch verortet sein). 15 neue Tests
   (`test_library.py`) decken jetzt beide Funktionen erstmals ab.
2. **`_default_db_path()`** war wortgleich in `cut.py`, `db_analyse.py`,
   `inspect_song.py`, `songtext_pipeline.py` dupliziert -- `compare_whisper_
   models.py` zeigte dabei versehentlich auf eine ANDERE Datei
   (`lyrics_core_cache.db` statt `fetch_songtext_cache.db`, laut Nutzer ein
   Kopier-Fehler). Neue Funktion `cache_store.default_cache_path()`.
   **Zusätzlich, auf Nutzerwunsch:** die Produktions-Datenbank selbst
   umbenannt: `fetch_songtext_cache.db` → `cache.db` (kürzerer, neutraler
   Name). Live am laufenden `songtext_pipeline.py --recursive`-Prozess
   verifiziert: `mv` ist für offene Dateihandles unschädlich (`lsof` zeigt
   die Handles danach unter dem neuen Namen, ohne Unterbrechung).
   `cut.py`/`db_analyse.py` hatten dafür keine Tests, die die Funktion
   monkeypatchen -- dort direkt inline durch `cache_store.
   default_cache_path()` ersetzt. `inspect_song.py`/`songtext_pipeline.py`/
   `compare_whisper_models.py` haben dagegen Tests, die `_default_db_path`
   gezielt monkeypatchen -- dort blieb die Funktion als dünner Wrapper
   erhalten (nur der Rückgabewert kommt jetzt aus `cache_store`).
   README.md/CACHE_DESIGN.md auf den neuen Dateinamen aktualisiert.

**Zurückgestellt (kein Handlungsbedarf jetzt):**
- JSON-Cache-Durchlauf-Boilerplate (ähnlich, nicht wortgleich, in
  `lrc_analyse.py`/`lrc_recheck.py`/`whisper_analyse.py` -- `lrc_analyse.py`
  liest den Baum dabei sogar 4× neu ein) -- später, gebündelt mit einer
  eigenen Änderung.
- ffprobe-Dauer-Ermittlung ähnlich in `assemble.py`/`fetch_metadata.py` --
  Trade-off (würde `library.py`s bisherige "kein subprocess"-Regel
  aufweichen), noch nicht entschieden.
- JSON-Skip-Prädikat weiterhin dreifach kopiert (`cut.py`/`write_lrc.py`/
  `evaluate_lyrics.py`) -- eigener, separater Schritt (mehrere Aufrufer
  betroffen, braucht mehr Sorgfalt).

**Sauber bestätigt, keine Funde:** `cache_store.py` selbst, `cut_ui.py`,
`assemble_ui.py`, `library.py`, `fetch_metadata.py`. Damit sind alle 18
Produktionsmodule mindestens einmal auf Redundanz geprüft.

506/506 Tests grün, `ruff` sauber. `lyrics_core.__version__` auf `1.13.23`
erhöht.

## ✓ Redundanz-Aufräumen Runde 1: cut.py-Duplikate behoben, library.py angelegt

**Auslöser:** Nutzer bemerkte beim Testen des Zeitstempel-Fixes (siehe unten),
dass `cut.py` mehrere Gigabyte RAM braucht -- Ursache war eine weitere,
bisher unentdeckte Redundanz. Daraufhin explizite Vorgabe: "wenn Funktionen
in mehreren Modulen (.py) benutzt werden, sind diese in einem zentralen
Bibliotheksmodul zu implementieren und von anderen Stellen zu nutzen,
statt redundant programmiert zu werden" -- vertikale Schichtung (mehrere
fachspezifische Bibliotheken statt einer einzigen Datei) ist dabei
ausdrücklich erlaubt.

**Behoben:**
1. **`cut.py` hatte eine eigene Kopie der JSON-Cache-Eintrag-Bau-Logik**
   (Version/Ergebnis/Zeitstempel), unabhängig von `write_lrc.py` gepflegt --
   der Zeitstempel-Fix (siehe unten) wäre sonst nur in `write_lrc.py`
   gelandet. Neue gemeinsame Funktion `lyrics_core._build_cache_entry()`,
   beide Aufrufer nutzen sie jetzt.
2. **`cut.py` lud eager das Whisper-Modell "medium"** (`lyrics_core.
   _get_whisper_model(...)`) nur um zu prüfen, ob faster-whisper überhaupt
   installiert ist -- kostete real ~1 GB RAM, auch wenn der Track gar kein
   Whisper brauchte oder das Album nicht-englisch war und nur `large-v3`
   gebraucht hätte (dann sogar BEIDE Modelle gleichzeitig geladen).
   `evaluate_lyrics.py` hatte dafür längst `lyrics_core.
   _faster_whisper_available()` (reiner Import-Check, ~200 MB) --
   `cut.py` nutzte ihn nur nicht. Live gemessen: 1021 MB (alt) vs. 202 MB
   (neu).
3. **Neue Datei `library.py`** -- zentrale, UI-unabhängige Bibliothek für
   Funktionen, die mehrere Kern-Skripte brauchen. Erster Inhalt:
   `parse_offset()`/`parse_preview_duration()`, vorher wortgleich in
   `cut.py` UND `assemble.py` dupliziert.
4. **`assemble.py`s `fmt_time()` gelöscht** -- toter Code (nirgends in
   Produktion aufgerufen), Duplikat von `cut_ui.fmt_dur()`. Tests auf
   `fmt_dur` umgestellt (dort bereits mit Vorzeichen-Fall abgedeckt).
5. **Titel-Normalisierung** (`_clean_query_title` + 2× `cache_store.
   normalize_key`) war wortgleich in `scan_songs.py` UND
   `songtext_pipeline.build_file_song_map()` -- neue gemeinsame Funktion
   `lyrics_core._song_keys(artist, title)`.

491/491 Tests grün, `ruff` sauber. `cut.py` auf `1.9.18`, `assemble.py` auf
`1.1.7`, `lyrics_core.__version__` auf `1.13.22` erhöht.

**Vollständiges Audit über alle 18 Produktionsmodule** (Nutzer-Vorgabe:
"ich will am Ende ALLE Module auf Redundanz geprüft wissen") ergab weitere
Funde, Umsetzung folgt in einem separaten Worktree:
- `_reject_reason()`/`_method()` dreifach/zweifach wortgleich in
  `lrc_analyse.py`, `lrc_recheck.py`, `whisper_analyse.py` -- Ziel laut
  Nutzer: `library.py`.
- `_default_db_path()` identisch in 4 Skripten, `compare_whisper_models.py`
  zeigte abweichend auf eine andere Datenbankdatei (Kopier-Fehler laut
  Nutzer) -- Produktions-Datenbank bereits umbenannt: `fetch_songtext_
  cache.db` → `cache.db` (unschädlich bei laufendem Prozess, per `lsof`
  bestätigt: offene Dateihandles überleben die Umbenennung unverändert).
- JSON-Cache-Durchlauf-Boilerplate in denselben drei Analyse-Tools
  (`lrc_analyse.py` liest den Baum dabei sogar 4× neu ein) -- zurückgestellt.
- ffprobe-Dauer-Ermittlung ähnlich in `assemble.py`/`fetch_metadata.py` --
  Trade-off, noch nicht entschieden.
- Sauber bestätigt, keine Funde: `cache_store.py`, `cut_ui.py`,
  `assemble_ui.py`, `library.py`, `fetch_metadata.py`.

## ✓ Optimierung: kontrastiver Kontext wird seltener und nur bei echten Änderungen neu gebaut

**Auslöser:** Beim großen `--recursive`-Nachhollauf (siehe unten) wollte der
Nutzer den Overhead des periodischen IDF-/Hintergrund-Pool-Neuaufbaus
senken, ohne die eigentliche Whisper-Zeit zu berühren (dort liegt der
Haupt-Flaschenhals, aber Optimierungen dort wurden geprüft und verworfen —
mlx-whisper lieferte laut früherem eigenen Test schlechtere Erkennung,
VAD-Filter zu wenig Gewinn für den Reifegrad-Aufwand, Whisper für den Lauf
abschalten verschiebt die Arbeit nur, spart nichts in Summe).

**Zwei Änderungen:**
1. `evaluate_lyrics._IDF_REFRESH_INTERVAL` von `50` auf `100` erhöht — der
   Kontext wird seltener überhaupt in Erwägung gezogen.
2. **Wichtiger:** Der Zähler alleine löst den teuren Neuaufbau nicht mehr
   aus. Neue Funktion `lyrics_core._contrastive_data_signature()` — billige
   `COUNT(*)`-Summe über `texte` + `transkripte` — wird bei Erreichen des
   Intervalls zuerst geprüft; der eigentliche Neuaufbau
   (`_build_contrastive_context()`, scannt alle Provider-Texte + Whisper-
   Transkripte, erkennt die Sprache jedes Songs neu) läuft nur noch, wenn
   sich diese Signatur seit dem letzten Aufbau tatsächlich verändert hat.
   Bei einem Lauf mit vielen bereits gecachten/übersprungenen Songs (wie
   dem aktuellen Nachhollauf) kamen bisher trotzdem alle `_IDF_REFRESH_
   INTERVAL` Songs neue, identische Neuaufbauten — jetzt nur noch, wenn
   wirklich neue Daten dazukamen.

2 neue Tests (`test_idf_wird_nicht_erneut_aufgefrischt_ohne_neue_daten`,
`test_idf_wird_erneut_aufgefrischt_wenn_neue_daten_dazukamen`), 1
bestehender Test ans neue Verhalten angepasst. 497/497 grün, `ruff` sauber.
`lyrics_core.__version__` auf `1.13.21` erhöht.

## ✓ Bugfix: "Kontrastiver Hintergrund-Kontext gebaut..."-Zeile löschte Statuszeile nicht sauber

**Auslöser:** Beim Live-Test des großen `--recursive`-Nachhollaufs (siehe unten)
bemerkte der Nutzer, dass die Zeile „Kontrastiver Hintergrund-Kontext
gebaut: ..." weiterhin mitten in einer anderen Zeile stehenblieb — dieselbe
Bug-Klasse wie die bereits behobene Ordner-Kopfzeile und die
Whisper-Modell-Ladung, hier an einer DRITTEN Stelle: `lyrics_core.
_build_contrastive_context()` druckte diese Zeile über einen blossen
`print()`, ohne vorher `_clear_status()` aufzurufen — landet direkt nach
einer transienten Statuszeile (z.B. `fetch_providers.py`s "i/N: ..."), blieb
deren Text stehen. Fix: `_clear_status()` davor eingefügt, exakt wie bei den
beiden anderen Fundstellen. `lyrics_core.__version__` auf `1.13.20` erhöht.

## ✓ Bugfix: JSON-Ordner-Cache hielt bereits fertige Tracks fälschlich für veraltet — bei JEDEM Lauf neu

**Auslöser:** Nutzer meldete beim Live-Test von `songtext_pipeline.py --recursive`
über die ganze Bibliothek: bereits vollständig bearbeitete Ordner (z.B.
„2 Unlimited/No Limits", die komplette ABBA-Diskografie) wurden bei JEDEM
erneuten Lauf komplett neu bewertet — auch reine Konsens-Tracks ohne
Whisper, und das wiederholte sich sogar nach dem ersten Reparaturversuch
unten weiterhin bei jedem Lauf ("das wiederholt sich bei jedem Lauf").

**Ursache, Teil 1 (bereits vermutet und geprüft):**
`lyrics_core._db_newer_than_json_entry()` vergleicht den JSON-Cache-
Zeitstempel (`ts`, per `datetime.now().isoformat(timespec="seconds")` —
Sekunden-genau, lokale Wanduhr) gegen den jüngsten Datenbank-Zeitstempel
für den Song (`cache_store.latest_result_timestamp()` — Mikrosekunden-
genau, UTC). Landet der JSON-Schreibvorgang in derselben Sekunde wie der
letzte DB-Eintrag (bei einem zügigen Durchlauf keine Seltenheit), wirkt die
sekundengenaue Wanduhr-Zeit fälschlich "früher" als der mikrosekunden-
genaue DB-Wert — der Track gilt fortan für immer als potenziell veraltet.

**Erster Fix (Teil 1):** `write_lrc.py` schreibt jetzt statt
`datetime.now()` direkt den Wert von `cache_store.latest_result_timestamp()`
als `ts` in den JSON-Eintrag — Vergleich künftig Datenbank-Zeitstempel
gegen Datenbank-Zeitstempel (dieselbe Uhr), nicht mehr Wanduhr gegen
Datenbank-Uhr.

**Ursache, Teil 2 (live entdeckt, NICHT vorher erkannt — Fix aus Teil 1
griff dadurch zunächst gar nicht):** `lyrics_core._save_cache()` und
`_load_cache()` entscheiden beim Zusammenführen zweier Cache-Einträge
für denselben Track "welcher ist neuer" ebenfalls über `entry.get("ts", "")
>= disk_cache[key].get("ts", "")` — ein reiner STRING-Vergleich. Der neue,
UTC-formatierte Zeitstempel aus Fix 1 (z.B. `"...T18:02:03.719825+00:00"`)
ist als TEXT kleiner als ein alter, lokal-formatierter Zeitstempel (z.B.
`"...T20:02:03"`), weil `'1'` < `'2'` an der Stundenstelle — unabhängig
davon, welcher Zeitpunkt real später liegt. `_save_cache()` verwarf dadurch
den frisch korrekt berechneten neuen Eintrag beim Schreiben STILLSCHWEIGEND
wieder zugunsten des alten, weiterhin fehlerhaften Eintrags auf der Platte
— Fix 1 wurde dadurch bei jedem Lauf sofort wieder rückgängig gemacht, ohne
jede Fehlermeldung.

**Fix, Teil 2:** Neue Funktion `lyrics_core._parse_cache_ts()` — parst
`ts` zu einem echten, timezone-aware `datetime` (naive/lokale Werte werden
via `astimezone()` als Ortszeit interpretiert, bereits-aware Werte bleiben
unverändert), fehlende/kaputte Werte gelten als minimal alt. `_load_cache()`
(NFC-Dubletten-Merge) und `_save_cache()` (Merge gegen Festplattenstand)
nutzen diesen Parser jetzt statt des rohen Stringvergleichs.

**Live verifiziert (nicht nur Unit-Test):** Realer Fall aus der
Produktions-Bibliothek nachgestellt (ABBA – „Dancing Queen", exakter
Zeitstempel-Konflikt `"...T20:02:03"` vs. `"...T18:02:03.719825+00:00"`) —
`_parse_cache_ts` erkennt den neuen Wert jetzt korrekt als später. Danach
zweimal echt gegen die Produktions-DB + die reale `.fetch_songtext.json`
laufen lassen: erster Lauf heilt den Eintrag (schreibt den korrekten
DB-Zeitstempel), zweiter Lauf überspringt den Track korrekt (kein erneuter
`evaluate_song()`-Aufruf, per Spy bestätigt). 490/490 Tests grün, `ruff`
sauber. `lyrics_core.__version__` auf `1.13.19` erhöht.

**Für bestehende Bibliotheken:** Kein Migrationsskript nötig — jeder Track
mit einem durch diesen Bug "verseuchten" Eintrag wird beim nächsten
Antreffen ein letztes Mal neu bewertet (heilt sich dabei selbst), danach
dauerhaft korrekt übersprungen.

## ✓ Aufräumen: welche Skripte werden noch gebraucht, welche können weg

**Ziel:** Projekt schlank halten — alles, was nicht mehr gebraucht wird, weg.

1. **✓ Skript-Inventur — erledigt.** Alle 19 Top-Level-`.py`-Skripte geprüft.
   Ergebnis:
   - **Gelöscht:** `normalize_cache.py` — bereinigte NFC/NFD-Duplikate im
     JSON-Ordner-Cache. Diese Normalisierung passiert seit längerem
     automatisch bei JEDEM `lyrics_core._load_cache()`-Aufruf (Zeile ~1432
     ff., NFC-Vereinheitlichung + Merge nach neuerem Zeitstempel) — jeder
     normale Pipeline-Lauf über einen Ordner bereinigt dessen Cache-Datei
     also schon als Nebeneffekt beim Schreiben. Das Skript duplizierte damit
     nur noch Logik, die die Produktion längst selbst übernimmt. Zusätzliches
     Staleness-Indiz: der Docstring verwies auf `whisper_sample.py` als
     Vorbild für den `find`-Trick — dieses Skript wurde bereits vorher
     bewusst entfernt (Commit `dd927b1`, "Einweg-Whisper-Untersuchungstools
     entfernt"), also derselbe Fall wie hier: einmaliges
     Untersuchungswerkzeug, dessen Job erledigt ist. Keine Tests vorhanden,
     keine andere Datei importierte es — risikolose Löschung.
   - **Behalten:** `lrc_recheck.py` — trotz Überschneidungsverdacht mit
     `--nachholen` KEIN Duplikat: `--nachholen` findet neue Provider-Treffer
     nur in der SQLite-DB; der neue Staleness-Check
     `lyrics_core._db_newer_than_json_entry()` (siehe Vergleich alt/neu,
     Befund 9) triggert danach zwar automatisch eine Neu-Bewertung, aber NUR
     wenn die DB neuere Provider-Daten hat. `lrc_recheck.py`s eigentlicher
     Zweck laut Docstring (V1/V2/V3-/C1/C3-Fixes) ist ein anderer: gezielt
     Tracks neu bewerten lassen, nachdem sich die BEWERTUNGSLOGIK selbst
     geändert hat (Bugfix in Konsens/VAD) — ohne dass sich die zugrunde
     liegenden Provider-Daten in der DB überhaupt geändert haben. Dieser Fall
     bleibt eine echte, weiterhin offene Lücke (siehe „Weiterhin offen"-Notiz
     oben, Punkt 2) — das Skript adressiert sie gezielt.
   - **Behalten:** `compare_whisper_models.py` — kein Duplikat, sondern ein
     wiederverwendbares Diagnose-Werkzeug für künftige Modell-Neubewertungen
     (z.B. bei neuen Whisper-Versionen). Dass es bereits einmal ein Ergebnis
     produziert hat (`whisper_modellvergleich_ergebnis.md`), macht es nicht
     einmalig — die aktuelle Modellwahl (medium/large-v3) beruht zwar darauf,
     das Werkzeug selbst bleibt für eine spätere Wiederholung nützlich.
   - Auch geprüft, aber klar mit eigenem, nicht überschneidendem Zweck: 
     `db_analyse.py` (SQLite-DB-Statistik), `lrc_analyse.py`
     (JSON-Cache-Statistik), `whisper_analyse.py` (Warum lief Whisper),
     `inspect_song.py` (Einzelsong-Diagnose) — alle vier bleiben.
2. **✓ Vergleich alt vs. neu (Funktionalität + Stabilität) — erledigt.** Das
   alte, monolithische `fetch_songtext.py` (letzter Stand vor Löschung:
   Commit `841e7b1`, danach gelöscht in `45b230f`) wurde der neuen Pipeline
   (`songtext_pipeline.py` + `scan_songs.py` + `fetch_providers.py` +
   `evaluate_lyrics.py` + `write_lrc.py` + `lyrics_core.py`)
   gegenübergestellt. Kern-Algorithmen (Konsens/Whisper/Heuristik,
   Rate-Limit-Backoff, `_query_provider`, Provider-Cache-Logik) sind
   unverändert übernommen. 10 Einzelbefunde insgesamt, dem Nutzer in
   einfacher Sprache vorgelegt; daraus ausgewählt für Weiterbearbeitung:
   Punkte 3–5 unten. Nebenbefund: Der Hinweis-Absatz weiter unten in diesem
   Dokument („Hinweis für eine spätere, hier noch nicht getroffene
   Entscheidung … was mit fetch_songtext.py selbst passiert") ist veraltet
   — die Löschung ist längst passiert.
3. **✓ Absturz statt sauberer Meldung, wenn `syncedlyrics` fehlt — behoben.**
   `fetch_providers.fetch_all()` fängt jetzt `FileNotFoundError` um
   `future.result()` ab (analog zum Altskript-Verhalten), räumt bereits
   erzeugte Temp-`.lrc`-Dateien anderer Anbieter auf und reicht die
   Exception weiter; `songtext_pipeline.main()` fängt sie am Ende des
   Datei-Loops ab und druckt „syncedlyrics nicht gefunden — Abbruch.“ statt
   eines rohen Tracebacks. Live verifiziert (nicht nur Unit-Tests): mit
   gefakten Providern, von denen einer nach erfolgreichen anderen
   `FileNotFoundError` wirft — alle bereits erzeugten Temp-Dateien werden
   nachweislich gelöscht (0 von 3 übrig), und ein End-to-End-Lauf über
   `songtext_pipeline.main()` kehrt sauber mit der Abbruch-Meldung zurück
   statt zu crashen.
4. **✓ Ordner-Sperre deckt jetzt den gesamten Ordner-Durchlauf ab — behoben.**
   `songtext_pipeline.main()`s Datei-Schleife beansprucht die Sperre jetzt
   selbst bei jedem Ordnerwechsel, BEVOR irgendein Schritt (scan/abfragen/
   bewerten/schreiben) für eine Datei dieses Ordners läuft — nicht mehr erst
   `write_lrc.py` beim Schreiben. Ist der Ordner belegt (andere Instanz
   aktiv), werden ALLE Dateien dieses Ordners komplett übersprungen (weder
   Anbieter-Abfrage noch Whisper noch Schreiben). `write_lrc.write_all()`
   bekommt die bereits gehaltene Sperre über den neuen Parameter
   `external_lock` durchgereicht und versucht NICHT mehr, sie ein zweites
   Mal zu beanspruchen (ein Prozess kann sich sonst mit `flock()` selbst
   aussperren, da die Sperre an die offene Dateibeschreibung gebunden ist,
   nicht an den Prozess) — Standalone-Aufrufe von `write_lrc.write_all()`
   ohne `external_lock` (z.B. Tests) behalten ihr bisheriges
   Selbst-Locking-Verhalten unverändert. Live verifiziert: eine simulierte
   zweite Instanz hält die Ordner-Sperre extern — der reale Lauf über
   `songtext_pipeline.main()` überspringt dann nachweislich beide Dateien
   des Ordners komplett (0 Aufrufe von Abfragen/Bewerten/Schreiben); im
   Normalfall (Ordner frei) wird die Sperre nachweislich GENAU EINMAL
   beansprucht (kein doppeltes Locking durch `write_lrc.py`).
5. **✓ `-V`/`--version`-Flag ergänzt.** `songtext_pipeline.py -V` und
   `--version` geben jetzt `lyrics_core.__version__` aus (argparse
   `action="version"`). Live verifiziert.
6. **✓ „Whisper transkribiert...“-Statuszeile zeigt jetzt den Grund.**
   `lyrics_core._whisper_best()` bekommt einen neuen optionalen `reason`-
   Parameter, der in die transiente Statuszeile eingeblendet wird (z.B.
   „Whisper transkribiert... (nur 1/4 Provider)“ oder „... (Konsens nur 32%
   < 40%)“). `evaluate_lyrics.evaluate_song()` berechnet den Grund direkt
   vor dem `_whisper_best()`-Aufruf aus `len(candidates)` und
   `consensus_jaccard` — unterscheidet dabei "zu wenige Provider" (Konsens-
   Prüfung lief gar nicht, `_provider_consensus` liefert dafür immer 0.0,
   das wäre sonst irreführend als "Konsens 0%" dargestellt) von "genug
   Provider, aber Übereinstimmung unter der 40%-Schwelle". Live verifiziert
   (Terminal-Änderung, siehe CLAUDE.md): Aufruf mit `reason="nur 1/4
   Provider"` erzeugt nachweislich die Statuszeile „Whisper
   transkribiert... (nur 1/4 Provider)“.

**Zurückgestellt (nicht auf TODO gepackt, nur zur Erinnerung):** Befund 2
(fehlgeschlagene Anbieter werden im Normal-Lauf nicht mehr automatisch
nachgeholt, `--nachholen` nötig), Befund 4/5 (`--retry-missing` je
Einzelprovider und mehrere alte Flags wie `--cache-only`/`--no-whisper`/
`--fast` entfallen ersatzlos), Befund 7 (Alt-Songs mit altem Whisper-Modell
`small` werden nie automatisch mit dem neuen, sprachabhängigen Modell
nachgeprüft) — vom Nutzer geprüft, aber bewusst nicht in Bearbeitung
genommen.

---

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

**✓ Nachtrag — `--phase` spricht jetzt Namen statt Zahlen:** Nutzer-Feedback
nach dem ersten echten Test: „phase ist nichtsprechend" — `--phase 2,4,5`
zwingt dazu, sich zu merken, welche Zahl welche Phase meint. Umbenannt auf
`_PHASE_NAMES` (`scan`=1, `abfragen`=2, `nachholen`=3, `bewerten`=4,
`schreiben`=5 — Reihenfolge/Wortwahl direkt aus dem Architektur-Dokument
übernommen: „scannen, Anbieter abfragen, Anbieter nachholen, bewerten, .lrc
schreiben"). Intern bleibt die Phase weiterhin eine Zahl (1-5) — nur die
CLI-Schicht (`_parse_phase_list`, Hilfetexte, Fehlermeldungen) spricht jetzt
in Namen, `main()`s Dispatch-Logik (`phase == 1/2/3/4/5`) blieb unverändert,
um das Risiko in bereits getesteter Logik gering zu halten. Beispiel:
`songtext_pipeline.py PFAD --phase abfragen,bewerten,schreiben` statt vorher
`--phase 2,4,5`. Alle betroffenen Tests in `test_songtext_pipeline.py` sowie
die CLI-Beispiele in `README.md` wurden auf die neue Syntax umgestellt.
`pytest test_songtext_pipeline.py`: 27/27 grün. Volle Suite: 458 grün +
dieselben 13 bekannten, unabhängigen Fehlschläge. `ruff check`/`ruff format`
sauber.

**✓ Nachtrag — `fetch_songtext.py` gelöscht, `lyrics_core.py` als geteilte
Bibliothek extrahiert:** Die oben offen gelassene Entscheidung („was passiert
mit `fetch_songtext.py` selbst") ist jetzt gefallen — explizite
Nutzer-Vorgabe: „fetch_songtext wird gelöscht. sorge dafür, das cut das neue
programm oder die module nutzt und das user-erlebnis sich dadurch nicht
wirklich ändert." Vorher wurde noch ein uncommitteter Debug-Hack aus einer
früheren Session entfernt (`_LRCLIB_LIVE_FALLBACK = False`, blockierte jeden
Live-Fallback für lrclib) — Ursache der 13 bis dahin bekannten, als
„unabhängig" geführten Testfehlschläge; nach Entfernen sofort 471/471 grün.

Die wiederverwendbaren Teile von `fetch_songtext.py` (Tag-Lesen, Provider-
Abfrage inkl. Rate-Limit-Handling, Whisper-Helfer, kontrastive Marge,
JSON-Ordner-Cache, `__version__`) wurden 1:1 nach `lyrics_core.py`
übernommen — bewusst OHNE `fetch_lrc()`, `_whisper_rerun_needed()` und das
alte `main()`/CLI, die mit der Phasen-Aufteilung ersatzlos entfallen sind
(deren Aufgaben übernehmen `evaluate_lyrics.py`/`write_lrc.py`/
`songtext_pipeline.py`). Alle Phasen-Module (`scan_songs.py`,
`fetch_providers.py`, `evaluate_lyrics.py`, `write_lrc.py`,
`songtext_pipeline.py`) sowie `cache_store.py`, `compare_whisper_models.py`
und `normalize_cache.py` importieren jetzt `lyrics_core` statt
`fetch_songtext` — die auf der Platte liegenden Cache-Dateinamen
(`.fetch_songtext.json`/`.fetch_songtext.lock`/`fetch_songtext_cache.db`)
blieben dabei bewusst unverändert, damit bestehende Bibliotheks-Caches
weiter funktionieren.

`cut.py` migriert: `_fetch_lyrics_for_track()` (neu) komponiert für EINEN
frisch geschnittenen Track synchron denselben Ablauf wie die Pipeline
(Song-Identität anlegen → alle 4 Provider parallel abfragen →
`evaluate_lyrics.evaluate_song()` → `.lrc` schreiben/löschen), ersetzt den
alten `fetch_songtext.fetch_lrc()`-Aufruf. Die Cache-DB wird jetzt einmal pro
Schneide-Session geöffnet (vorher gar nicht) und Whisper-Modell +
kontrastiver Kontext einmal geladen. **Nebenwirkung, kein bewusster
Funktionsumbau:** `cut.py` öffnete vorher NIE die Cache-DB — die kontrastive
Marge (braucht immer einen Hintergrund-Pool aus der DB) fiel dadurch
still auf `margin=None`/Score 0.0 zurück, Whisper-Verifikation akzeptierte
in `cut.py` also faktisch nie etwas. Mit echter Cache-DB kann Whisper jetzt
auch beim Direkt-Schneiden wirklich akzeptieren — eine ECHTE
Verhaltensänderung (mehr Songs bekommen einen `.lrc` über den Whisper-Pfad
statt zuvor gar keinen), keine reine Umbenennung. Dem Nutzer noch nicht
explizit zur Bestätigung vorgelegt.

Testmigration: `test_fetch_songtext.py` → `test_lyrics_core.py` (die 27
weiterhin gültigen Testklassen für reine Logikfunktionen), eine Klasse
(`TestRetryMissingUsesLrclibDump`) nach `test_fetch_providers.py`
verschoben und von `lyrics_core.main()`/`sys.argv`-Patching auf einen
direkten `fetch_providers.retry_missing(conn)`-Aufruf umgestellt (der alte
Bug, den dieser Test abdeckte, ist mit der Phasen-Aufteilung strukturell
unmöglich geworden — der Test bleibt trotzdem als Ende-zu-Ende-Abdeckung
sinnvoll). `fetch_songtext.py`/`test_fetch_songtext.py` per `git rm`
gelöscht. `README.md` (komplette Neufassung des Songtexte-Abschnitts:
`songtext_pipeline.py` statt `fetch_songtext.py`-Flags, Whisper-Modellwahl
medium/large-v3 statt `small`, `--no-whisper`/`--fast`/`--force`/
`--cache-only`/`--retry-missing`-Flags entfernt — existieren im neuen CLI
nicht mehr) und `CACHE_DESIGN.md` (ein stehen gebliebener Modul-Verweis
korrigiert) nachgezogen.

Volle Suite: 441/441 grün (13 vorbestehende Fehlschläge durch den
Hack-Fund vollständig aufgelöst, keine übrig). `ruff check` auf allen
geänderten/neuen Dateien sauber.

**✓ Nachtrag — Phase 2 ("abfragen") retryt fehlgeschlagene Anbieter nicht
mehr automatisch mit:** Bug, der bei einem echten Testlauf über
`/Volumes/music/musik/_aktuell` auffiel: der Nutzer bemerkte, dass Songs bei
einem zweiten Lauf erneut bei den Providern angefragt wurden statt den
gecachten Eintrag zu nutzen — „aber genau dafür gibt es doch Phase 3 =
Provider neu anfragen. das bremst doch aus." Wurde bewusst auf die
To-do-Liste gesetzt statt sofort mitgelöst (parallel lief noch die
`fetch_songtext.py`-Löschung) — jetzt nachgeholt.

**Ursache:** `lyrics_core.get_provider()` (der Cache-Lookup, den
`_query_provider` vor jeder Live-Abfrage macht) wertet `status="fehlschlag"`
absichtlich NIE als gültigen Cache-Treffer (siehe `CACHE_DESIGN.md` — ein
Fehlschlag soll ja beim nächsten Lauf grundsätzlich erneut versucht werden
dürfen, sonst würde ein gedrosselter Lauf einen Song fälschlich 90 Tage lang
als „hat keinen Text" abstempeln). `fetch_providers.fetch_all()` (Phase 2)
rief für JEDEN Song ausnahmslos alle 4 Anbieter live über `_query_provider`
auf — ein Anbieter mit gecachtem Fehlschlag wurde dadurch bei jedem
Phase-2-Lauf erneut live angefragt, obwohl genau das exklusiv die Aufgabe
von `retry_missing()` (Phase 3, „nachholen") sein soll. Das bremste jeden
Normal-Lauf unnötig aus (unnötige Live-Anfragen, potenziell erneute
Rate-Limit-Wartezeit) und unterlief die eigentliche Trennung der beiden
Phasen aus dem Architektur-Dokument.

**Fix:** `fetch_all()` liest jetzt vor der Provider-Abfrage eines Songs
dessen bereits gecachte `status='fehlschlag'`-Zeilen aus `ergebnisse`
(`SELECT quelle FROM ergebnisse WHERE song_id=? AND status='fehlschlag'`)
und lässt genau diese Anbieter beim `ThreadPoolExecutor`-Aufruf aus — pro
(Song, Anbieter) einzeln, nicht pro Song: ein Song mit z.B. 3 Treffern und 1
Fehlschlag bekommt weiterhin nur den einen fehlgeschlagenen Anbieter nicht
erneut angefragt, die anderen 3 laufen unverändert durch `_query_provider`s
eigenen Cache-Lookup (der `treffer`/`nichts` weiterhin normal
TTL-respektierend behandelt — daran ändert dieser Fix nichts). Sind für
einen Song ausnahmsweise alle 4 Anbieter bereits als Fehlschlag markiert,
wird der `ThreadPoolExecutor`-Aufruf für diesen Song ganz übersprungen
(0 Worker wären ein Fehler) und stattdessen eine eigene Ergebniszeile
ausgegeben (`bereits alle Anbieter fehlgeschlagen -- siehe --phase
nachholen`). `retry_missing()` (Phase 3) selbst ist unverändert — sie deckt
genau diese übersprungenen (Song, Anbieter)-Kombinationen weiterhin ab.

Fünf neue Tests in `test_fetch_providers.py` (`TestFetchAll`): ein gecachter
Fehlschlag bei einem von vier Anbietern wird nicht live nachgefragt (nur 3
`subprocess.run`-Aufrufe statt 4), die anderen 3 Anbieter desselben Songs
werden davon nicht beeinträchtigt, ein Song mit allen 4 Anbietern als
Fehlschlag wird komplett ohne Live-Aufruf übersprungen, und als Kehrseite:
`retry_missing()` fragt genau so einen gecachten Fehlschlag weiterhin ab.
`pytest test_fetch_providers.py`: 21/21 grün. Volle Suite: 445/445 grün.
`ruff check`/`ruff format` sauber. `lyrics_core.__version__` auf `1.13.3`
erhöht (Bugfix, siehe CLAUDE.md-Versionierungsregel).

**✓ Nachtrag — `db_analyse.py`: Aggregat-Statistiken über die Cache-DB.**
Erster der drei zuvor bewusst zurückgestellten "Weiterhin offen"-Punkte
(siehe oben, Punkt 1): `lrc_analyse.py` wertet nur den JSON-Ordner-Cache
aus, nie die eigentliche `fetch_songtext_cache.db`. Neues, eigenständiges
Skript `db_analyse.py` (kein CLI-Flag, reiner Report): liest pro Anbieter
die Treffer-/Nichts-/Fehlschlag-Quote inkl. Fehlschlag-Gründen, Songs ganz
ohne Provider-Treffer, Songs mit allen 4 Providern fehlgeschlagen
(Kandidaten für `--phase nachholen`), Whisper-Transkript-Abdeckung +
Modell-Aufschlüsselung (small/medium/large-v3) sowie Provider-Aktivität der
letzten 24h/7 Tage. Trennung von `collect_stats(conn) -> dict` (reine
SQL-Aggregation) und `print_stats(stats)` (Formatierung) macht die Zahlen
ohne stdout-Capturing testbar. Live gegen die echte Produktions-DB
gegengeprüft (9936 Songs, u.a. Musixmatch-Fehlschlagquote 78,7 % — fast
ausschließlich `gesperrt`, ein echter, vorher unsichtbarer Befund). 7 neue
Tests in `test_db_analyse.py`. `ruff check`/`ruff format` sauber.

**✓ Nachtrag — Kein Bindeglied zwischen JSON-Cache und SQLite-Cache
(Punkt 2) behoben.** Live an einem echten `--recursive`-Lauf über
`/Volumes/music/musik/_aktuell` bestätigt: Phase "bewerten" hatte an einem
Tag 56 frische Whisper-Transkripte erzeugt (u.a. ZZ Top "Stages"/"Woke Up
With Wood"), aber KEINE einzige `.fetch_songtext.json` im ganzen Baum wurde
aktualisiert. Ursache: `lyrics_core._cache_entry_valid()` prüft nur die
Skript-Version (≥ 1.7.1), kein TTL — ein einmal geschriebener JSON-Eintrag
gilt für Phase "schreiben" FÜR IMMER als aktuell, egal was Phase
"bewerten"/"nachholen" seitdem in der DB gefunden haben. `evaluate_song()`
wird für solche Tracks nie wieder aufgerufen, frische Whisper-Arbeit
verpufft ungenutzt.

**Fix:** neue `cache_store.latest_result_timestamp(conn, artist_key,
titel_key)` liefert den jüngsten `datum`-Zeitstempel über alle
`ergebnisse`- und `transkripte`-Zeilen eines Songs. `write_lrc.py`s
Skip-Check vergleicht diesen jetzt zusätzlich gegen den `ts`-Wert des
JSON-Eintrags (`_db_newer_than_json_entry`) — ist die DB neuer, wird trotz
gültigem JSON-Eintrag neu bewertet. Ein echter Zeitvergleich, kein reiner
String-Vergleich: der JSON-`ts` ist lokale Zeit ohne Zeitzone
(`datetime.now().isoformat()`), der DB-`datum` UTC-aware
(`datetime.now(timezone.utc).isoformat()`) — beide Formate sind NICHT direkt
als String vergleichbar, `_db_newer_than_json_entry` parst deshalb über
`datetime.fromisoformat()` und hängt an den naiven JSON-`ts` per
`.astimezone()` die lokale Zeitzone an, bevor verglichen wird. Fehlt/ist der
JSON-`ts` nicht parsbar, wird konservativ NICHT übersprungen (lieber einmal
zu oft neu bewerten als für immer eine veraltete Entscheidung stehen
lassen). Kein DB-Datensatz für den Song → nichts Neues → Skip bleibt
gültig. Die zusätzliche Abfrage ist ein einzelnes, über `song_id` indiziertes
`MAX(datum)` je übersprungenem Track — der eigentliche Performance-Vorteil
des JSON-Cache-Skips für tatsächlich unveränderte Songs bleibt erhalten.

Gegen die echte Produktions-DB verifiziert: der ZZ-Top-Fall wird jetzt
korrekt als "neu zu bewerten" erkannt (`latest_result_timestamp` liefert
2026-07-17T01:47 gegenüber dem JSON-`ts` vom 2026-07-11). 3 neue Tests in
`test_cache_store.py`, 3 neue Tests in `test_write_lrc.py`
(`TestWriteAllDbNeuerAlsJsonEintrag`: neuerer DB-Eintrag erzwingt
Neubewertung, unveränderte DB bleibt beim Skip, fehlender `ts` erzwingt
konservativ Neubewertung). Volle Suite: 459/459 grün. `ruff check`/
`ruff format` sauber. `lyrics_core.__version__` auf `1.13.4` erhöht
(Bugfix, siehe CLAUDE.md-Versionierungsregel).

Bewusst NICHT Teil dieses Fixes (siehe Diskussion mit dem Nutzer): ein
manueller "Plausibilitätsprüfung"-Modus, der eine bereits akzeptierte `.lrc`
auch OHNE neue DB-Daten neu bewerten kann (z.B. weil inzwischen ein besseres
Whisper-Modell zur Verfügung steht) — auf die To-do-Liste vertagt, siehe
Punkt 3 oben.

**✓ Nachtrag — `--phase LISTE` ersatzlos entfernt, jeder Schritt hat jetzt
sein eigenes Flag; `--nachholen` funktioniert jetzt mit PFAD.** Nutzer-
Feedback beim Entwurf des geplanten "Plausibilitätsprüfung"-Flags (Punkt 3
oben): "nimm doch mal alles was das tool kann und können soll und
entwickle die passenden sprechenden Flags dafür. Kein Mensch braucht im
Flag den Begriff 'phase'." Das Sammel-Flag `--phase scan,abfragen,...`
(erst kürzlich von Zahlen auf sprechende Namen umgestellt, siehe Nachtrag
weiter oben) ist damit selbst schon überholt — jeder der 5 Schritte
bekommt stattdessen sein eigenes boolesches Flag: `--scan`, `--abfragen`,
`--nachholen`, `--bewerten`, `--schreiben`. Kein Flag angegeben → kompletter
Normal-Durchlauf (alter Standard ohne `--phase`); mindestens ein Flag
angegeben → nur die gewählten Schritte, weiterhin in fester Reihenfolge.

Im selben Zug fiel eine zweite, unabhängige Einschränkung auf, die der
Nutzer direkt mitkorrigiert haben wollte: "wenn ich einen pfad mitgebe gilt
das flag auch nur für die dateien, song + artist, die darin enthalten sind.
sonst kann ich ja keinen gezielten --nachholen machen." Vorher wurde
`--nachholen`/Phase 3 bei gesetztem PFAD komplett übersprungen (siehe
Nachtrag "Fix B" weiter oben) -- `fetch_providers.retry_missing()` kannte
keinen Scope-Parameter, nur eine artist/title-Eingrenzung für genau einen
Künstler/Song (Rest von `fetch_songtext.py --retry-missing --artist/
--title`, siehe Git-Historie). **Fix:** `lyrics_core._retry_missing()`
bekommt einen neuen `song_ids`-Parameter (Vorrang vor artist/title, falls
gesetzt; eine LEERE Liste bedeutet bewusst "nichts zu tun", nicht "keine
Eingrenzung" -- sonst wäre `"... IN ()"` ungültiges SQL).
`fetch_providers.retry_missing()` bekommt denselben `scope`-Parameter wie
`fetch_all()`/`evaluate_all()` (Menge von `(artist_key, titel_key)`) und
löst ihn zu `song_ids` auf. `songtext_pipeline.py` berechnet den Scope für
`--nachholen` jetzt genauso wie für `--abfragen`/`--bewerten` (frisch pro
Schritt, nach einem eventuellen `--scan` im selben Lauf) -- die alte
Sonderregel "PFAD gesetzt → nachholen überspringen" ist komplett entfallen.

Konsolen-Ausgaben von "Phase 1 (scan_songs): ..." usw. auf schlichte
"scan: ...", "abfragen: ...", "nachholen:", "bewerten: ...", "schreiben:
..." umgestellt -- passend zum neuen Flag-Namen, ohne "Phase N"-Nummerierung.
README.md komplett auf die neuen Flags umgestellt (Tabelle statt
Phasen-Liste), `fetch_providers.py`/`scan_songs.py`/`write_lrc.py`/
`db_analyse.py` von stehen gebliebenen `--phase`-Erwähnungen bereinigt.

Design vorab in einer eigenen Textdatei (`flag_vorschlaege_lrc_recheck.txt`)
mit dem Nutzer abgestimmt, inklusive einem ersten Opus-Entwurf (auf
Nutzer-Wunsch: "denk aber nach, nimm opus zuhilfe") für den noch
zurückgestellten `--nachpruefen`-Namen (Punkt 3) -- wichtigster Fund dabei:
`--recheck` kollidiert mit dem bereits bestehenden, aber semantisch
anderen `lrc_recheck.py` und schied deshalb aus.

Testmigration: die 6 `_parse_phase_list`-Tests entfielen (Funktion
gestrichen), alle `--phase`-CLI-Tests in `test_songtext_pipeline.py` auf die
neuen Flags umgeschrieben, plus neue Tests für das Kernstück
(`test_main_nachholen_mit_pfad_grenzt_auf_pfad_songs_ein`: ein Song IM PFAD
mit gecachtem Fehlschlag wird retried, ein Song AUSSERHALB bleibt
unangetastet; `test_main_nachholen_mit_pfad_ohne_treffer_bleibt_leer_...`:
ein PFAD ohne passende Songs fällt nicht auf die ganze DB zurück). Neue
Tests auch in `test_lyrics_core.py` (`song_ids`-Parameter, Vorrang vor
artist/title, leere Liste) und `test_fetch_providers.py` (`scope`-Parameter,
Auflösung zu `song_ids`, unbekannter Scope-Eintrag wird ignoriert,
End-zu-Ende über die echte `_retry_missing`). Volle Suite: 456/456 grün
(6 alte Tests entfernt, mehr neue hinzugekommen). `ruff check`/
`ruff format` sauber.

**✓ Nachtrag — `--nachholen` läuft nicht mehr im Normal-Durchlauf mit;
Whisper-Modell wird nicht mehr unnötig geladen.** Zwei reale Befunde aus
einem echten Wiederholungslauf über "Betterov - Olympia" (derselbe PFAD
dreimal hintereinander):

1. **`--nachholen` im Normal-Durchlauf war zu aufdringlich.** Der komplette
   Durchlauf ohne jedes Flag führte `--nachholen` automatisch mit aus (seit
   dem PFAD-Scoping-Fix, siehe Nachtrag oben) -- bei JEDEM Wiederholungslauf
   desselben Albums wurden dadurch alle historisch offenen "nichts"/
   "fehlschlag"-Kombis erneut live abgefragt (im Beispiel: 16 Provider-
   Retries bei jedem einzelnen Lauf, obwohl sich nichts geändert hatte).
   Nutzer-Vorgabe: "Ich will ein 'nachholen' nur wenn das flag gesetzt ist.
   das impliziert dann auch bewerten und schreiben." **Fix:** der Normal-
   Durchlauf (kein Flag angegeben) läuft jetzt nur noch `--scan --abfragen
   --bewerten --schreiben` -- OHNE `--nachholen`. Wird `--nachholen`
   ausdrücklich angegeben, impliziert es automatisch `--bewerten` +
   `--schreiben` mit (sonst käme ein frisch gefundener Provider-Treffer
   nirgendwo an) -- auch wenn der Nutzer nur `--nachholen` allein tippt.

2. **Whisper-Modell wurde geladen, obwohl der Song längst ein gecachtes
   Transkript hatte.** Realer Befund im selben Log: ein zweiter/dritter
   Lauf über denselben Ordner lud weiterhin `medium`/`large-v3` neu ("Lade
   Whisper-Modell..."), obwohl die Transkripte aus dem ersten Lauf bereits
   in der DB standen. Ursache: `lyrics_core._whisper_best()` rief
   `_get_whisper_model()` (lädt bei Bedarf das VOLLE Modell in den
   Speicher) ganz am Funktionsanfang auf -- noch BEVOR geprüft wurde, ob
   für den Song schon ein Transkript im Cache liegt. Bei einem Cache-
   Treffer wird das geladene Modell-Objekt danach nie benutzt, nur der
   Modell-NAME als String für die Anzeige. **Fix:** der Modell-Load wandert
   hinter die Cache-Prüfung, direkt vor den echten Live-Transkriptions-
   Aufruf -- ein Song mit gültigem Transkript-Cache-Treffer lädt jetzt kein
   Modell mehr. Neuer Regressionstest `test_cache_hit_laedt_kein_whisper_
   modell` (mockt `_get_whisper_model` als `pytest.fail`-Falle).

   Bewusst NICHT Teil dieses Fixes (siehe Diskussion mit dem Nutzer): das
   Bauen des kontrastiven Hintergrund-Kontexts (`_build_contrastive_context`)
   bleibt weiterhin bei jedem `--bewerten`-Lauf nötig, sobald irgendein Song
   im Scope den Whisper-Vergleichszweig braucht (auch bei einem Cache-
   Treffer -- die Marge wird ja gegen den aktuellen Hintergrund-Pool neu
   berechnet) UND weil jeder `songtext_pipeline.py`-Aufruf ein frischer
   Prozess ist (kein Zustand überlebt zwischen zwei separaten Aufrufen).
   `bewerten` hat außerdem WEITERHIN keinerlei "hat sich seitdem etwas
   geändert"-Skip (anders als `schreiben` seit dem vorherigen Nachtrag) --
   bewertet also bei jedem Lauf jeden Song im Scope neu. Ein solcher Skip
   für `bewerten` selbst wäre der eigentlich vollständige Fix für "beim
   zweiten/dritten Lauf über einen unveränderten Pfad passiert gar nichts
   mehr" -- auf die To-do-Liste vertagt.

Tests angepasst: `test_main_ohne_flags_aktiviert_scan_abfragen_bewerten_
schreiben` (ersetzt die alte "...alle_5_schritte"-Variante, prüft jetzt
explizit `"nachholen:" not in out`), `test_main_nachholen_impliziert_
bewerten_und_schreiben` (ersetzt die alte "...allein_funktioniert_ohne_
pfad"-Variante), `test_main_pfad_ohne_flags_laesst_nachholen_aus` (Gegenprobe
mit PFAD). Volle Suite: 457/457 grün. `ruff check`/`ruff format` sauber.
`lyrics_core.__version__` auf `1.13.6` erhöht (Bugfix, siehe
CLAUDE.md-Versionierungsregel).

**✓ Nachtrag — `evaluate_all()`s Verfügbarkeits-Sonde lud unnötig das
`medium`-Modell.** Direkt beim nächsten Live-Testlauf desselben Albums
aufgefallen: trotz des vorherigen Fixes ("Whisper-Modell wird nicht mehr
unnötig geladen") erschien weiterhin `Lade Whisper-Modell (medium)...` --
obwohl das komplett deutsche Album gar keinen einzigen Song hat, der
`medium` braucht (nur `large-v3`, für Englisch wäre es `medium`). Ursache:
der vorherige Fix betraf nur `lyrics_core._whisper_best()`s EIGENEN
Modell-Load (pro Song, verzögert bis nach der Cache-Prüfung) -- eine ZWEITE,
unabhängige Stelle blieb bestehen: `evaluate_lyrics.evaluate_all()` prüft
ganz am Anfang "ist Whisper überhaupt verfügbar" per
`lyrics_core._get_whisper_model(_WHISPER_MODEL_EN)` -- das lädt bei diesem
reinen Verfügbarkeits-Check das VOLLE `medium`-Modell, unabhängig davon, ob
irgendein Song im Scope `medium` je braucht.

**Fix:** neue `lyrics_core._faster_whisper_available()` -- reiner
Import-Check (`import faster_whisper`), lädt kein Modell. `evaluate_all()`
nutzt das jetzt für seine Sonde statt `_get_whisper_model(_WHISPER_MODEL_EN)`.
Modelle werden dadurch ausschließlich noch lazy, pro tatsächlich
gebrauchtem Modellnamen, geladen (wie im Docstring immer schon behauptet,
jetzt auch tatsächlich so). Neuer Regressionstest
`test_verfuegbarkeits_check_laedt_kein_modell` (mockt `_get_whisper_model`
als Abbruch-Falle, prüft `evaluate_all()` gegen eine leere DB). Bestehender
Test `test_kein_whisper_verfuegbar_bricht_sauber_ab` auf
`_faster_whisper_available` umgestellt. Volle Suite: 458/458 grün. `ruff
check`/`ruff format` sauber. `lyrics_core.__version__` auf `1.13.7` erhöht
(Bugfix, siehe CLAUDE.md-Versionierungsregel).

**✓ Nachtrag — "bewerten" bekommt einen echten Skip für unveränderte Songs
(Punkt aus der Diskussion zum vorherigen Nachtrag, jetzt umgesetzt).**
`evaluate_lyrics.evaluate_all()` bewertete bislang JEDEN Song im Scope bei
JEDEM Lauf neu -- anders als `write_lrc.write_all()` (`--schreiben`) kannte
`bewerten` keinerlei "hat sich seitdem etwas geändert"-Skip. Live am
Beispiel "Betterov - Olympia" nachvollzogen: ein reiner Wiederholungslauf
über denselben, unveränderten Ordner bewertete trotzdem alle 13 Songs neu
(inkl. Whisper-Vergleich für 3 Songs ohne Provider-Konsens) und baute jedes
Mal den kontrastiven Hintergrund-Kontext neu, obwohl `schreiben` hinterher
korrekt "0 geschrieben, 13 übersprungen" meldete -- die eigentliche Arbeit
in `bewerten` war also für die Katz.

**Fix:** `evaluate_all()` bekommt denselben Skip wie `write_lrc.write_all()`
-- dieselbe Frage ("hat die DB seit dem JSON-Cache-Eintrag etwas Neueres?"),
dieselbe Antwort. Dafür wurde `_db_newer_than_json_entry()` aus `write_lrc.py`
nach `lyrics_core.py` verschoben (jetzt von beiden Modulen gemeinsam
genutzt, statt dupliziert) und eine neue, rein lesende
`evaluate_lyrics._skip_reevaluation(conn, audio_path, artist_key,
titel_key)` ergänzt, die denselben Vergleich anstellt wie `write_lrc.py`s
Skip -- OHNE die Ordner-Sperre von `write_lrc.write_all` (reine
Lese-Entscheidung, kein Schreibvorgang, ein Race mit einem gleichzeitigen
`--schreiben` ist unkritisch). Der Skip ist nur mit zugeordneter Audiodatei
möglich (JSON-Ordner-Cache ist datei-basiert) -- ohne PFAD (ganze Bibliothek,
kein `file_song_map`) bewertet `bewerten` weiterhin jeden Song wie bisher.

Zusätzlich wurde das Bauen des kontrastiven Hintergrund-Kontexts
(`lyrics_core._build_contrastive_context`) von "immer vor der Schleife" auf
"lazy, beim ersten tatsächlich bewerteten Song" umgestellt (inkl. des
IDF-Refresh-Intervalls, jetzt an `evaluated_count` statt am rohen
Schleifenindex gezählt) -- werden ALLE Songs im Scope übersprungen, wird der
Kontext gar nicht erst aufgebaut. Neue Zählgröße `counts["uebersprungen"]`,
in `songtext_pipeline.py`s `bewerten:`-Ausgabe mit aufgenommen.

3 neue Tests in `test_evaluate_lyrics.py`
(`TestEvaluateAllSkipUnveraendert`): ein Track mit gültigem JSON-Cache-
Eintrag (über einen echten `write_lrc.write_all()`-Lauf erzeugt, nicht
handgebaut) wird beim nächsten `evaluate_all()` nicht erneut bewertet; ein
neuer DB-Eintrag seit dem JSON-Zeitstempel erzwingt trotzdem eine
Neubewertung; ein Song ohne Datei-Zuordnung wird weiterhin immer bewertet.
Bestehender `test_idf_wird_alle_n_songs_aufgefrischt` unverändert grün
(keine Datei-Zuordnung im Test, daher nie ein Skip -- reines Verhalten wie
vorher). Volle Suite: 461/461 grün. `ruff check`/`ruff format` sauber.
`lyrics_core.__version__` auf `1.13.8` erhöht (Bugfix, siehe
CLAUDE.md-Versionierungsregel).

**✓ Nachtrag — "abfragen" meldete "Frage N Song(s) ab" und Treffer-Zeilen,
obwohl gar keine Live-Anfrage stattfand.** Direkt beim nächsten
Wiederholungslauf über "Betterov - Olympia" aufgefallen (nachdem `bewerten`
und `schreiben` schon korrekt "nichts geschrieben"/"übersprungen" meldeten):
`abfragen` zeigte weiterhin `Frage 11 Song(s) bei 4 Anbietern ab ...` und
pro Song eine Treffer-Zeile (`3/4: lrclib, musixmatch, genius`) -- obwohl
buchstäblich jeder einzelne Provider-Wert aus dem Cache kam. Ursache: der
`#11`-Fehlschlag-Fix (siehe Nachtrag "Phase 2 soll fehlschlag-Einträge nicht
automatisch mit-retryen") filterte nur `status='fehlschlag'` heraus --
gültige, nicht abgelaufene `treffer`/`nichts`-Einträge wurden weiterhin an
`_query_provider()` durchgereicht. Dort griff zwar dessen eigener
Cache-Lookup (kein echter Netzwerk-Aufwand), aber die Konsolenausgabe von
`fetch_all()` selbst wusste davon nichts und tat so, als sei live gefragt
worden.

**Fix:** `fetch_all()` bestimmt jetzt PRO SONG vorab (nicht mehr erst in der
Schleife), welche Anbieter wirklich noch offen sind -- ein Anbieter mit
gecachtem `status='fehlschlag'` (weiterhin `--nachholen`s Aufgabe) ODER
einem gültigen, nicht abgelaufenen `treffer`/`nichts`-Eintrag
(`cache_store.get_provider()`, gleiche TTL-Logik wie `_query_provider()`
selbst) zählt als "bereits erledigt". Bleibt für einen Song kein einziger
offener Anbieter übrig, wird der Song komplett aus der Anfrage-Liste
ausgeschlossen -- keine Konsolenzeile, kein `ThreadPoolExecutor`-Aufruf.
"Frage N Song(s) ab" zeigt dadurch von vornherein nur Songs mit echtem
Anfragebedarf. `fetch_all()`s Rückgabe wächst von `(queried, skipped)` auf
`(queried, skipped_genre, skipped_up_to_date)` -- alle Aufrufer (u.a.
`songtext_pipeline.py`) und Tests angepasst; `skipped_up_to_date` wird in
der `abfragen:`-Zusammenfassung als "X Song(s) bereits aktuell, nichts
abzufragen" ausgewiesen.

3 neue/angepasste Tests in `test_fetch_providers.py`: ein Song mit lauter
gültigen Treffern/Nichts-Einträgen wird komplett übersprungen (kein
`subprocess.run`-Aufruf); ein einzelner ABGELAUFENER Treffer zählt nicht als
"bereits aktuell" -- nur dieser eine Anbieter wird erneut gefragt, die
anderen (noch gültigen) nicht; der bestehende "alle 4 Anbieter fehlgeschlagen"-
Test zählt jetzt korrekt als `skipped_up_to_date` statt als `queried` mit
0 tatsächlichen Anfragen. Volle Suite: 463/463 grün. `ruff check`/
`ruff format` sauber. `lyrics_core.__version__` auf `1.13.9` erhöht
(Bugfix, siehe CLAUDE.md-Versionierungsregel).

**✓ Nachtrag — Verzeichnis-Walk + Tag-Read passiert nur noch einmal pro
Lauf, nicht mehr bis zu sechsmal.** Auslöser: ein Lauf über
`/Volumes/music/musik/_Various Artists` (viele Compilation-Alben, Netzlaufwerk)
"dauerte ewig", hing sichtbar lange bei `Scanne: ...`. Nutzer-Anfrage war
ursprünglich "Phasen pro Ordner statt global durchlaufen" (siehe unten,
weiterhin nicht umgesetzt) -- eine Codeprüfung vor der Aufwandsschätzung
ergab aber einen konkreteren, kleineren Übeltäter: ein normaler
Komplett-Lauf (`scan`+`abfragen`+`bewerten`+`schreiben`) durchsuchte den
kompletten Verzeichnisbaum **sechsmal** und las dabei jedes Mal für JEDE
Datei erneut die Tags (Datei-Zuordnungs-Vorabmeldung, `scan` selbst,
`abfragen`s Scope, `bewerten`s Scope, `bewerten`s eigene Datei-Zuordnung,
`schreiben`s Datei-Zuordnung) -- auf einem SMB-Mount mit hunderten Alben
vermutlich die eigentliche Ursache, nicht die Provider-/Whisper-Arbeit
selbst.

**Fix:** neue `scan_songs._read_tagged_files(root, recursive)` liest
Verzeichnisbaum + Tags GENAU EINMAL pro `songtext_pipeline.py`-Lauf.
`scan_songs.scan()` und `songtext_pipeline.build_file_song_map()`/
`_scope_from_root()` bekommen einen neuen optionalen `files`-Parameter --
wird er übergeben (immer der Fall, wenn `main()` sie aufruft), entfällt der
erneute Walk komplett, nur noch der günstige DB-Abgleich läuft weiterhin
mehrfach (nötig, weil frisch gescannte Songs erst nach `scan` in der
DB stehen, siehe bestehende Doku dazu). Ohne `files` (z.B. bei
eigenständiger Nutzung von `scan_songs.py`) bleibt das alte
Selbst-Einlesen als Fallback erhalten.

Übrig bleiben zwei architektonisch separate, kleinere Aufrufe: `bewerten`
und `schreiben` lesen je einmal selbst die Tags nach (über
`evaluate_lyrics._resolve_expected_dur()` für die erwartete Songdauer) --
bewusst nicht mit angefasst, weil jeder Schritt laut Architektur-Dokument
auch einzeln, ohne die anderen im selben Prozess, aufrufbar bleiben muss.
Macht insgesamt 1 (Walk) + 2 (Dauer-Auflösung) = 3 statt vorher bis zu 8
`_read_audio_tags`-Aufrufe pro Datei in einem Komplett-Lauf.

Neuer Regressionstest `test_main_liest_tags_nicht_mehr_pro_schritt_neu`
(zählt `_read_audio_tags`-Aufrufe über einen vollen Lauf, erwartet genau 3
statt der alten bis zu 8). Volle Suite: 464/464 grün. `ruff check`/
`ruff format` sauber. `lyrics_core.__version__` auf `1.13.10` erhöht
(Bugfix, siehe CLAUDE.md-Versionierungsregel).

**Weiterhin offen, NICHT umgesetzt (Nutzer-Wunsch, zurückgestellt bis
geprüft ist ob nach diesem Fix überhaupt noch Bedarf besteht): Phasen pro
Ordner statt global durchlaufen** -- statt `scan` global über den ganzen
Baum, dann `abfragen` global, dann `bewerten` global, dann `schreiben`
global, für jeden Ordner nacheinander alle gewählten Schritte durchlaufen.
Bringt (im Gegensatz zum obigen Fix) keine Reduktion der Gesamtarbeit,
sondern sichtbaren Fortschritt Ordner für Ordner und Abbruch-Sicherheit
(bei einem Absturz mitten im Lauf sind bereits fertige Ordner schon
geschrieben). Größerer Umbau von `main()`s Ablaufsteuerung, neues
Konsolenausgabe-Format, kompletter Neuentwurf der `main()`-Integrationstests
in `test_songtext_pipeline.py`.

Vom Nutzer für eine spätere Umsetzung explizit mitgegeben, was dabei
beachtet werden muss: das Whisper-Modell wird pro Lauf nur einmal geladen
(`lyrics_core._get_whisper_model()`, modulglobal gecacht in
`_whisper_models` -- gilt für den ganzen Prozess, nicht pro Aufruf) und
DARF bei einer Ordner-für-Ordner-Schleife nicht pro Ordner neu geladen
werden (bleibt automatisch so, solange alles im selben Prozess läuft, ohne
Sonderbehandlung nötig). Der kontrastive Wort-Index (Hintergrund-Kontext,
`lyrics_core._build_contrastive_context`) wird dagegen bewusst regelmäßig
neu aufgebaut -- aktuell alle `_IDF_REFRESH_INTERVAL` (50) tatsächlich
bewerteter Songs, gezählt in `evaluate_lyrics.evaluate_all()`s
`evaluated_count` (siehe Nachtrag "'bewerten' bekommt einen Skip für
unveränderte Songs" oben) -- dieser Zähler darf bei einer Ordner-für-
Ordner-Schleife NICHT pro Ordner zurückgesetzt werden, sonst würde der
Index bei vielen kleinen Ordnern viel häufiger neu gebaut als beabsichtigt
(oder bei "IDF alle 50 Songs" mit lauter 3-Song-Ordnern faktisch nie neu,
wenn versehentlich pro Ordner bei 0 neu gestartet wird). Der Zähler muss
also außerhalb der Ordner-Schleife leben, nicht pro Ordner neu.

**✓ Nachtrag — Task #15 umgesetzt: Phasen laufen jetzt pro Ordner statt
global.** Direkt im Anschluss an den obigen Walk-Fix angegangen (Nutzer:
"gehe task #15 an. im anschluss werde ich testen."), mit der oben schon
mitgegebenen Nebenbedingung zum IDF-Refresh-Zähler.

**Umbau in `songtext_pipeline.py`:** `main()`s Schritt-Dispatch (scan/
abfragen/nachholen/bewerten/schreiben) ist jetzt in eine geschlossene
Closure `_run_selected_steps(step_root, step_files)` gewandert, die EINEN
Ordner (oder global, `step_root=None`, für den PFAD-losen Fall) komplett
durchläuft. Mit PFAD: `scan_songs._read_tagged_files()` liest den ganzen
Baum EINMAL, danach werden die Dateien nach `path.parent` gruppiert und für
jeden Ordner (sortiert) `_run_selected_steps(folder, folder_files)`
aufgerufen -- inklusive einer Fortschrittszeile `Ordner i/N: <relativer
Pfad> (M Datei(en))` davor. Kein Ordner mit Audiodateien gefunden (leeres
PFAD-Verzeichnis) -> `_run_selected_steps` läuft trotzdem EINMAL mit leerer
Datei-Liste, damit z.B. `--nachholen` weiterhin seine gewohnte "nichts
gefunden"-Meldung zeigt statt komplett stillzubleiben. Ohne PFAD (z.B.
`--nachholen` allein) bleibt es beim bisherigen einmaligen, globalen Aufruf
über die ganze Cache-DB -- dort gibt es keine Ordner-Struktur.

**IDF-Refresh-Zähler aus `evaluate_lyrics.py` in `lyrics_core.py`
verschoben** (siehe oben mitgegebene Nebenbedingung): neue modulglobale
`lyrics_core._contrastive_context_built_ever`/
`_contrastive_context_evaluations_since_refresh` + neue
`lyrics_core._note_contrastive_evaluation(refresh_interval)` -- vorher lebte
dieser Zustand als lokale Variablen (`context_built`/`evaluated_count`) in
`evaluate_all()` und wäre bei einem Aufruf pro Ordner bei JEDEM Ordner auf 0
zurückgefallen, hätte den kontrastiven Kontext also viel öfter als die
beabsichtigten 50 Songs neu aufgebaut. "Wurde je gebaut" wird dabei bewusst
über ein EIGENES Flag verfolgt, nicht über `_contrastive_idf is None` --
sonst hätte ein in Tests gemocktes `_build_contrastive_context` (das
`_contrastive_idf` nicht setzt) bei jedem Song erneut als "nie gebaut"
gegolten.

4 neue Tests: `test_idf_refresh_zaehler_bleibt_ueber_mehrere_evaluate_all_
aufrufe_erhalten` (zwei `evaluate_all()`-Aufrufe mit je einem Song, Refresh-
Intervall 2 -- nur 1 statt 2 `_build_contrastive_context`-Aufrufe, weil der
zweite Song insgesamt erst der zweite ist, nicht wieder der erste eines
neuen Zählers); `test_main_verarbeitet_mehrere_ordner_nacheinander_komplett`
(zwei Alben in getrennten Unterordnern, `--recursive`, prüft Ordner-
Fortschrittszeilen UND dass jeder Ordner scan/abfragen/schreiben wirklich
einzeln durchläuft -- zweimal "scan: 1 Song(s)...", nicht einmal "scan: 2
Song(s)..."); `test_main_ohne_audiodateien_unter_pfad_laeuft_trotzdem_
einmal_durch` (leeres PFAD-Verzeichnis, kein Ordner gefunden, Schritte
laufen trotzdem einmal). Bestehende Tests liefen unverändert durch, ohne
Anpassung nötig (nutzen fast alle nur einen einzigen Ordner -- die
Ordner-Schleife iteriert dann trivial genau einmal, identisch zum alten
globalen Verhalten). Volle Suite: 467/467 grün. `ruff check`/`ruff format`
sauber. `lyrics_core.__version__` auf `1.13.11` erhöht (siehe
CLAUDE.md-Versionierungsregel).

Noch nicht live gegen die echte Produktionsbibliothek getestet -- der
Nutzer testet im Anschluss selbst.

**✓ Nachtrag — `--abfragen`/`--bewerten` laufen jetzt in Dateinamen-
Reihenfolge statt alphabetisch nach Künstler/Titel, Konsole zeigt den
Dateinamen.** Aus dem Live-Test des obigen Ordner-für-Ordner-Umbaus (Nutzer:
"ich will die durchläufe der dateien nach dateinamen sortiert, nicht bane
artist und songname. und ich will den dateinamen sehen, falls es die gibt.
das macht es für mich besser nachvollzihbar") -- die Konsolenausgabe sollte
sich mit der Tracklist im Ordner decken.

`fetch_providers.fetch_all()` bekommt einen neuen optionalen Parameter
`file_order: list[tuple[Path, str, str]] | None` (dieselbe Liste wie
`scope`, aus `songtext_pipeline.build_file_song_map()`) -- mit Wert daraus
werden die abzufragenden Songs in Datei-/Verzeichnisreihenfolge statt per
`ORDER BY artist_key, titel_key` verarbeitet, `evaluate_lyrics.evaluate_all()`
entsprechend über `file_song_map` (Dict-Einfügereihenfolge). Beide zeigen in
der Konsole `label = audio_path.name if audio_path is not None else
f"{artist_key} / {titel_key}"` -- Dateiname wenn eine Audiodatei bekannt
ist (PFAD-Lauf), sonst weiterhin den normalisierten Cache-Schlüssel als
Fallback (globaler Lauf ohne PFAD, dort gibt es keine Datei-Zuordnung).

Neue Tests: `TestFetchAllFileOrder` (4 Tests, `test_fetch_providers.py`),
`TestEvaluateAllFileOrder` (2 Tests, `test_evaluate_lyrics.py`). Volle
Suite: 473/473 grün. `ruff format` auf die eigenen neuen Zeilen angewendet
(nicht auf vorbestehende, unabhängige Formatierungs-Abweichungen in
`_open_lrclib_dump_conn`/`_heuristic_best`-Aufrufen). `lyrics_core.
__version__` auf `1.13.12` erhöht.

**✓ Nachtrag — Phasen laufen jetzt Datei für Datei statt Ordner für
Ordner.** Nutzer, direkt im Anschluss an die obige Reihenfolgen-Frage zu
Tequila (fehlender JSON-Eintrag, weil `bewerten` einen ganzen Ordner
komplett durchläuft, bevor `schreiben` überhaupt anfängt): "dann will ich,
dass die phasen für jeden einzelne datei laufen. dadurch haben die provider
auch wieder länger leerlauf und wir fallen nicht in rate-limit." Ordner-für-
Ordner (Task #15) reduzierte das Problem schon einmal (globaler Lauf über
den ganzen Baum → Ordner-Batches), löst es aber nicht: innerhalb eines
Ordners liefen weiterhin ALLE Songs gebündelt durch `--abfragen`, bevor
`--bewerten` (mit ggf. mehrminütiger Whisper-Transkription pro Song) auch
nur den ersten Song erreichte — die Anbieter-Abfragen blieben also weiterhin
dicht hintereinander.

**Umbau in `songtext_pipeline.py`:** `_run_selected_steps(step_root,
step_files)` (siehe Task #15) brauchte keine Änderung — sie akzeptiert
bereits jede beliebige `step_files`-Liste, ob Ordner-Batch oder Einzeldatei.
Nur `main()`s äußere Schleife wurde geändert: statt `all_files` nach
`path.parent` zu gruppieren und pro Ordner EINMAL `_run_selected_steps`
aufzurufen, läuft jetzt eine flache Schleife über `all_files` (bereits in
Datei-/Verzeichnisreihenfolge, siehe vorheriger Nachtrag), die
`_run_selected_steps(audio_path.parent, [entry])` mit GENAU EINEM Element
pro Aufruf ruft. Ein Ordnerwechsel (erkannt über `audio_path.parent !=
current_folder`) druckt weiterhin eine `Ordner: <relativer Pfad>`-Zeile,
zusätzlich zu `Datei i/N: <Dateiname>` vor jeder Datei.

Kein Code in `fetch_providers.py`/`evaluate_lyrics.py`/`write_lrc.py`
musste angefasst werden — alle drei akzeptierten schon vorher beliebig
lange Listen (Task #15/Dateinamen-Reihenfolge-Nachtrag), ein-elementige
Listen sind nur ein weiterer gültiger Fall. `write_lrc.write_all()` claimt/
released den Ordner-Lock jetzt zwangsläufig bei JEDER Datei neu (Vergleich
`audio_path.parent != current_parent` ist bei einer 1-Element-Liste immer
wahr) statt einmal pro Ordner-Batch -- bewusst in Kauf genommen: der
zusätzliche JSON-Lese-/Schreib-Overhead ist vernachlässigbar gegen eine
Whisper-Transkription oder eine Netzwerk-Anfrage, und mehr Leerlauf
zwischen den Anbieter-Abfragen ist hier ausdrücklich das Ziel, nicht ein
Nebeneffekt.

Bestehende Tests angepasst: `test_main_verarbeitet_mehrere_ordner_
nacheinander_komplett` → `test_main_verarbeitet_dateien_ueber_
ordnergrenzen_hinweg_einzeln` (Ausgabe-Format geändert: `Ordner: album1`
statt `Ordner 1/2: album1 (1 Datei(en))`, `Datei 1/2: ...` statt implizit
über den Ordner-Batch), `test_main_scan_abfragen_fragt_nur_pfad_songs_ab_
nicht_die_ganze_db` (zwei Songs im selben Ordner: erwartete jetzt zweimal
"scan: 1 Song(s)"/"abfragen: 1 Song(s)" statt einmal "scan: 2 Song(s)"/
"abfragen: 2 Song(s)"). Neuer Test: `test_main_verarbeitet_dateien_im_
selben_ordner_ebenfalls_einzeln` -- der eigentliche Regressionstest für
diesen Umbau: zwei Songs IM SELBEN Ordner, prüft explizit zweimal "scan: 1
Song(s)..." (NICHT einmal "scan: 2 Song(s)...") und genau EINE
"Ordner: album"-Zeile trotz zwei Dateien. Volle Suite: 474/474 grün.
`lyrics_core.__version__` auf `1.13.13` erhöht.

**✓ Nachtrag — Konsole zeigt jetzt pro Track genau EINE Zeile statt bis zu
zehn.** Live-Test des Datei-für-Datei-Umbaus gegen die echte
Produktionsbibliothek zeigte: jede einzelne Datei durchlief jetzt zwar
scan/abfragen/bewerten/schreiben separat, aber JEDER dieser vier Schritte
druckte weiterhin seine eigene Kopfzeile ("Frage 1 Song(s) ab ...", "Bewerte
1 Song(s) ...", "Schreibe/prüfe 1 Datei(en) ..."), seine eigene persistente
Ergebniszeile (abfragen und bewerten rekonstruierten dabei UNABHÄNGIG
voneinander densel­ben `prov_str`/Anbieter-Treffer-Teil) und seine eigene
Zusammenfassung ("abfragen: 1 Song(s) abgefragt.", "bewerten: 0 Konsens, ...
1 übersprungen", "schreiben: 1 geschrieben, ...") -- bis zu ~10 Zeilen für
EINEN Track. Nutzer: "die ausgabe ist 'bescheiden'. verbessere das. zeig auf
trackebene in diesem fall was passiert. pro track eine zeile. schau dir das
bei dem alten programm ab." Das alte, mittlerweile gelöschte
`fetch_songtext.py` (siehe Git-Historie, `main()`) verarbeitete jeden Track
tatsächlich in einem einzigen Rutsch und druckte genau eine `_tprint`-Zeile
pro Track: `{ts}  {rel_pfad}  {info}  {symbol}` -- exakt das Format, das
`write_lrc.write_all()`s Ergebniszeile in der neuen Architektur schon
liefert (siehe Nachtrag oben, "Dateinamen-Reihenfolge"), nur bislang von den
Zwischenzeilen der vorgelagerten Schritte verdeckt.

**Lösung: neuer `quiet`-Parameter**, durchgereicht von
`songtext_pipeline._run_selected_steps()` an `fetch_providers.fetch_all()`,
`evaluate_lyrics.evaluate_all()` und `write_lrc.write_all()` (jeweils über
die `_normal()`-Wrapper in `songtext_pipeline.py`). `quiet = run_schreiben
and step_root is not None` -- läuft `--schreiben` im selben Aufruf mit UND
ist ein PFAD gesetzt (der Normalfall bei einem Datei-für-Datei-Lauf),
unterdrücken scan/abfragen/bewerten ihre Kopf-/Zwischen-/
Zusammenfassungszeilen komplett; `write_all()`s EINE Ergebniszeile pro Song
bleibt in jedem Fall bestehen (nie hinter `quiet` versteckt -- das ist die
gewollte Zeile). Läuft ein Schritt EINZELN (z.B. nur `--abfragen`, ohne
`--schreiben` im selben Aufruf), bleibt die ausführliche Ausgabe erhalten --
`quiet` wird dann nie gesetzt, da es sonst die einzige Rückmeldung wäre.
`--nachholen` bewusst NICHT angefasst (deutlich seltener Pfad, eigene,
andersartige Fortschrittsausgabe über `lyrics_core._retry_missing` -- YAGNI,
kann bei Bedarf separat nachgezogen werden).

Zusätzlich: die Ordner-Kopfzeile (bisher `\nOrdner: <Pfad>`, aus dem
vorherigen Datei-für-Datei-Nachtrag) durch den Stil des alten Programms
ersetzt (`{ts}  ── {Pfad}`, siehe Git-Historie `fetch_songtext.main()`), die
separate "Datei i/N: <Dateiname>"-Zeile ganz entfernt (redundant zur
Ergebniszeile, die den Dateinamen ohnehin zeigt) und "N Datei(en)
gefunden." VOR die leer/nicht-leer-Verzweigung gezogen -- wird jetzt IMMER
ausgegeben, auch bei 0 Dateien, damit ein leeres PFAD-Verzeichnis (oder ein
`quiet`-geschalteter Lauf) nicht komplett still wirkt wie ein Hänger.

6 bestehende Tests angepasst (Konsolen-Assertions auf die jetzt
unterdrückten Kopf-/Zusammenfassungszeilen mussten weg; wo Datei-für-Datei-
Granularität geprüft wurde, jetzt über einen `scan_songs.scan()`-Spy
verifiziert statt über deren -- jetzt unterdrückte -- Konsolenausgabe, siehe
`test_main_verarbeitet_dateien_ueber_ordnergrenzen_hinweg_einzeln` /
`test_main_verarbeitet_dateien_im_selben_ordner_ebenfalls_einzeln`). Volle
Suite: 474/474 grün. `ruff check`/`format` sauber (verbleibende
`ruff format`-Diffs in `evaluate_lyrics.py`/`fetch_providers.py`
vorbestehend, nicht von diesem Nachtrag). `lyrics_core.__version__` auf
`1.13.14` erhöht.

**✓ Nachtrag — Verzeichnis-Walk + Tag-Read laufen jetzt lazy statt den
kompletten Baum vorab einzusammeln.** Live-Test gegen
`/Volumes/music/musik/_aktuell --recursive`: Nutzer sah nach dem Start
nur `  Scanne: Will Smith/Big Willie Style` (transiente Statuszeile) und
fragte "Programm startet trotzdem mit einem großen Scan über alle
Verzeichnisse. Muss das sein?" -- berechtigter Einwand. Ursache:
`scan_songs._read_tagged_files()` (siehe Nachtrag weiter oben, "GENAU
EINMAL pro Lauf") war eine List Comprehension -- las beim Aufruf ALLE Tags
im GESAMTEN Baum ein, bevor `songtext_pipeline.main()`s neue Datei-für-
Datei-Schleife (siehe Nachtrag "Phasen laufen jetzt Datei für Datei") auch
nur den ersten Track verarbeiten konnte. Bei einer großen, netzwerk-
gemounteten Bibliothek genau die lange, stille Anfangsphase, die der
Datei-für-Datei-Umbau eigentlich vermeiden sollte.

Vor der Umsetzung Trade-off mit AskUserQuestion geklärt: die bisherige
Vorab-Zeile "N Datei(en) gefunden." kennt die Gesamtzahl nur, wenn der ganze
Baum vorher gezählt wird (entweder weiterhin voll vorab, oder über einen
zusätzlichen, aber immerhin billigeren Nur-Pfade-Walk). Nutzer-Entscheidung:
ganz weglassen -- kein zusätzlicher Walk, Verarbeitung beginnt sofort bei
der ersten gefundenen Datei, exakt wie beim alten fetch_songtext.py (das
zeigte ebenfalls keine Vorab-Gesamtzahl).

**Umbau:** `scan_songs._read_tagged_files()` von einer Liste auf einen
Generator umgestellt (`yield` statt Listen-Comprehension) -- jede Datei wird
weiterhin nur EINMAL getaggt (kein Rückfall auf die alten bis zu sechs
Durchläufe), aber die erste Datei ist verfügbar, sobald sie gefunden ist,
statt erst nach dem kompletten Baum. `songtext_pipeline.main()`s
Datei-Schleife iteriert jetzt direkt über diesen Generator (kein
`all_files`-Zwischenspeicher mehr, kein `len()`/keine "N Datei(en)
gefunden."-Zeile); der Leer-Verzeichnis-Fallback (`_run_selected_steps(root,
[])`) läuft über ein `any_file`-Flag, das beim ersten Schleifendurchlauf
gesetzt wird, statt über eine vorab bekannte Listenlänge. Sowohl `scan()`
als auch `build_file_song_map()` nutzen `_read_tagged_files()` intern
weiterhin nur als Fallback, wenn kein `files`-Parameter übergeben wird --
beide iterieren ihre `entries` nur EINMAL in einer `for`-Schleife, ein
Generator funktioniert dort unverändert.

Manuell verifiziert (Live-Skript, zwei Alben mit je einem Song, Tag-Lese-
Spy): "Scanne: album2" erscheint jetzt erst NACH der fertigen Ergebniszeile
für album1s Song -- der Walk ist also tatsächlich mit der Verarbeitung
verzahnt, nicht mehr vorgelagert.

3 Tests angepasst (die jetzt fehlende "N Datei(en) gefunden."-Zeile war
Teil ihrer Assertions), einer davon (`test_main_ohne_audiodateien_unter_
pfad_laeuft_trotzdem_einmal_durch`) auf einen `scan()`-Spy umgestellt --
ohne die Zeile lässt sich der Leer-Verzeichnis-Fallback nicht mehr über
Konsolentext belegen. Volle Suite: 474/474 grün. `ruff check`/`format`
sauber. `lyrics_core.__version__` auf `1.13.15` erhöht.

**✓ Inzwischen behoben (siehe weiter unten "✓ Behoben. Die Ordner-Kopfzeile..."): Ordner-Kopfzeile löscht
die transiente "Scanne: ..."-Statuszeile nicht sauber, Ausgabe "beißt
sich".** Live-Test gegen `/Volumes/music/musik/_Various Artists --recursive`
(direkt im Anschluss an den lazy-Walk-Nachtrag oben): Nutzer sah in echt
verschmolzene Zeilen wie
`  Scanne: 0-9/100% Rock Classics Part Four                    14:50:20  ── 0-9/100% Rock Classics Part Four`
-- Statustext und echte Zeile auf derselben sichtbaren Zeile statt sauber
getrennt. Zweite Nutzer-Meldung direkt danach: "wird noch schlimmer mit der
formatierung" (weitere transiente Status wie `1/1: ... ...` und `Whisper
transkribiert...` reihen sich offenbar genauso ein).

**Wahrscheinliche Ursache (noch nicht live verifiziert, nur aus dem Code
hergeleitet -- vor der Umsetzung bestätigen):** `lyrics_core._print_status()`
schreibt `\r{msg:<98}` OHNE `\n` -- der Cursor bleibt nach Spalte 98 stehen,
nicht Spalte 0. `lyrics_core._tprint()` ruft davor `_clear_status()` auf
(schreibt `\r` + 100 Leerzeichen + `\r`, Cursor damit zurück auf Spalte 0)
und LÖSCHT die Statuszeile so sauber, bevor die echte Zeile gedruckt wird --
das nutzen alle bisherigen Track-Ergebniszeilen (`write_lrc.write_all()`
usw.) bereits richtig. Die NEUE Ordner-Kopfzeile in
`songtext_pipeline.py`s `main()`
(`print(f"{lyrics_core._ts()}  ── {label}")`, siehe Nachtrag "Konsole zeigt
pro Track eine Zeile") ist dagegen ein BLOSSER `print()` -- ruft
`_clear_status()` nicht auf, schreibt also direkt ab der aktuellen
Cursor-Position (Spalte 98 der vorherigen `Scanne: ...`-Statuszeile) weiter,
statt die Zeile vorher zu löschen. Naheliegender Fix: die Ordner-Kopfzeile
über `lyrics_core._tprint()` statt `print()` ausgeben (wie alle anderen
persistenten Zeilen auch).

**Vor der Umsetzung:** live gegen eine echte Bibliothek verifizieren (nicht
nur behaupten, siehe CLAUDE.md "Evidenz vor Vermutung") -- insbesondere ob
auch die `Whisper transkribiert...`/`1/1: ...`-Statuszeilen (aus
`lyrics_core.py`, an mehreren Stellen) betroffen sind oder nur die neue
Ordner-Kopfzeile.

**✓ Nachtrag — Bugfix: Kali Uchis „Telepatía" bekam den falschen (englischen
Übersetzungs-)Songtext.** Nutzer-Meldung: „kali uchis text von telepatia ist
absolut falsch". Ursachenkette rekonstruiert direkt aus der Cache-DB
(`fetch_songtext_cache.db`, song_id 51251):

1. Genius lieferte über `syncedlyrics` nicht die Original-Lyrics, sondern die
   Seite „Kali Uchis - telepatía (**English Translation**) Lyrics" — Genius'
   Suche liefert bei `syncedlyrics/providers/genius.py` ungeprüft den ERSTEN
   Suchtreffer (`data[0]["result"]["url"]`), das kann auch eine
   Übersetzungsseite sein.
2. `lyrics_core._detect_lrc_language()` erkennt die Song-Sprache, indem sie
   den Text ALLER Kandidaten (Genius' englische Übersetzung + Netease'
   spanisches Original) zu EINEM Textblock zusammenklebt, bevor `langdetect`
   läuft. Live nachgestellt: Genius allein → `en`, Netease allein → `es`,
   beide zusammen → `en` (das Original-Lied ist zudem selbst zweisprachig,
   was das zusätzlich begünstigt).
3. Dieses falsche `lrc_lang="en"` wird als erzwungener Sprach-Hinweis an
   Whisper durchgereicht (`_transcribe(..., language=lrc_lang)`) — Whisper
   bekommt die spanische Audiospur vorgesetzt, hält sie aber für Englisch.
   Ergebnis laut gecachtem Transkript: ein durchgehend englischer,
   übersetzungsartiger Text statt einer spanischen Mitschrift (bekannter
   Whisper-Effekt bei erzwungener Fehl-Sprache).
4. Der IDF-Jaccard-Vergleich matcht diesen englischen Fehltranskript
   folgerichtig am besten gegen Genius' englische Übersetzung — Netease'
   korrektes spanisches Original verliert trotz Richtigkeit.

**Fix, zwei unabhängige Teile:**

- **`lyrics_core._resolve_lrc_language()`** (neu, Drop-in-Ersatz für
  `_detect_lrc_language()` an der Whisper-Sprach-Hinweis-Stelle in
  `_whisper_best()`): erkennt die Sprache JE KANDIDAT einzeln statt als
  Textmix. Sind sich alle Kandidaten mit erkannter Sprache einig, wird diese
  zurückgegeben; bei Widerspruch `None` — Whisper bekommt dann keinen
  Sprach-Hinweis und erkennt selbst aus dem Audio.
- **`lyrics_core._looks_like_translation()`** (neu): erkennt
  Übersetzungsseiten am Fetch-Text (Klammer-Zusatz wie „(English
  Translation)"/„(Traducción al Español)" in beliebiger Wortreihenfolge,
  sowie Genius' „Translations"-Sprachauswahl-Kopfzeile), provider-
  unabhängig geprüft. Angewendet an drei Stellen in `_query_provider()`
  (Live-Fetch, eigener Cache-Replay, lrclib-Dump-Replay) — verhindert neue
  Vergiftung UND heilt alte Cache-Einträge beim nächsten `--abfragen`/
  `--nachholen` selbst. Zusätzlich in `evaluate_lyrics._load_candidate_texts()`
  angewendet, weil `--bewerten` Kandidaten direkt per SQL aus der DB liest,
  NICHT über `_query_provider` — nur dort wird auch ein bereits (vor diesem
  Fix) als `treffer` gespeicherter Übersetzungs-Fund wirksam ignoriert, ohne
  die DB-Zeile selbst anfassen zu müssen.

Bei der Umsetzung Redundanz vermieden: `evaluate_lyrics.py` hat mit
`_select_whisper_model()` (Modul-Docstring: „ROADMAP.md, Nachtrag: large-v3
ergänzt + Entscheidung für den Produktivbetrieb") bereits einen eigenen
Modell-Wahl-Mechanismus (Englisch → `medium`, alles andere inkl. `None` →
`large-v3`), der `lyrics_core._WHISPER_MODEL` vor jedem `_whisper_best()`-
Aufruf kurzzeitig überschreibt. Ein Widerspruch (`None`) fällt dort
automatisch unter „nicht Englisch" und erzwingt so bereits das große Modell
— dafür genügte es, `_select_whisper_model()` von `_detect_lrc_language()`
auf `_resolve_lrc_language()` umzustellen, ganz ohne einen zweiten,
eigenen Eskalationsmechanismus in `lyrics_core.py` selbst (ein erster
Anlauf mit eigener `_WHISPER_MODEL_ESCALATED`-Konstante + genereller
Cache-Invalidierung bei Modell-Mismatch wurde verworfen: er hätte alle 1269
noch mit dem alten `small`-Modell gecachten Transkripte in der Produktions-
DB bei jedem Lauf neu transkribiert — genau das zurückgestellte Verhalten,
siehe „Befund 7" oben, das der Nutzer bereits bewusst nicht umgesetzt haben
wollte).

Konkreter Song (Kali Uchis – Telepatía) zusätzlich manuell bereinigt: das
veraltete, verunreinigte Whisper-Transkript (`modell=medium`, Cache-DB
`transkripte`-Zeile zu song_id 51251) gelöscht, damit der nächste Lauf frisch
transkribiert — dafür existiert kein CLI-Flag (`--nachholen` betrifft nur
`status='nichts'/'fehlschlag'`, nie `'treffer'`; ein generisches
`--force`/`--refresh-cache` ist in `songtext_pipeline.py` aktuell nicht
verdrahtet). Live gegen die echte Cache-DB nachgewiesen: `_load_candidate_texts`
liefert für song_id 51251 jetzt nur noch `netease`, `_resolve_lrc_language`
→ `es`, `_select_whisper_model` → `large-v3`.

17 neue Tests (`TestResolveLrcLanguage`, `TestLooksLikeTranslation`, zwei
neue `TestProviderCache`-Fälle in `test_lyrics_core.py`; je ein neuer Fall in
`TestLoadCandidateTexts`/`TestSelectWhisperModel` in `test_evaluate_lyrics.py`),
2 bestehende Tests in `test_cache_store.py` angepasst (`cache_store.
get_transcript()` liefert jetzt zusätzlich das Feld `"modell"`, war bisher
nicht im Rückgabewert obwohl die Spalte längst existierte). Volle Suite:
487/487 grün. `ruff check`/`format` sauber. `lyrics_core.__version__` auf
`1.13.16` erhöht.

**✓ Behoben.** Die Ordner-Kopfzeile in `songtext_pipeline.py`s `main()` nutzt
jetzt `lyrics_core._tprint()` statt `print()`, genau wie vermutet. Die
anderen transienten Statuszeilen (`Scanne: ...`, `Whisper transkribiert...`,
`i/N: ...`) waren NICHT zusätzlich betroffen: sie überschreiben sich beim
nächsten Aufruf immer selbst per führendem `\r` (das ist der Zweck von
`_print_status()`) -- das Problem trat ausschließlich auf, wenn direkt danach
eine PERSISTENTE Zeile ohne vorheriges `_clear_status()` gedruckt wurde, und
das war nur bei der neuen Ordner-Kopfzeile der Fall. Live per Rohbyte-Vergleich
verifiziert (nicht nur behauptet): die Sequenz nach dem Fix ist exakt
`\r` + Statustext + `\r` + 100 Leerzeichen + `\r` + Ordner-Kopfzeile + `\n` --
dieselbe Lösch-Sequenz, die die bestehenden Track-Ergebniszeilen schon
korrekt verwenden.

**Nachtrag — zweite, verwandte Fundstelle beim Live-Test des obigen Fixes
entdeckt (nicht Teil der ursprünglichen Vermutung):** Nutzer sah live
`  1/1: 48 Soda Pop.mp3 ...                          18:00:30  Lade
Whisper-Modell (medium)... bereit.  18:00:30` -- wieder dieselbe Klasse
Bug, an einer dritten Stelle. Ursache: `lyrics_core._get_whisper_model()`
druckt "Lade Whisper-Modell (...)..." (Zeile ~714) über einen BLOSSEN
`print(..., end=" ")` -- exakt derselbe Fehler wie bei der Ordner-Kopfzeile,
nur diesmal beim erstmaligen Laden eines Whisper-Modells mitten in einem
Lauf. **Fix:** `_clear_status()` direkt davor eingefügt, analog zum
Ordner-Kopfzeile-Fix. Live per Rohbyte-Vergleich verifiziert: Sequenz ist
jetzt `\r` + Statustext + `\r` + 100 Leerzeichen + `\r` + "Lade
Whisper-Modell..." + "bereit." + `\n`.

**Zusätzlich vom Nutzer angemerkt:** Der `i/total:`-Zähler in den
transienten Statuszeilen (`fetch_providers.py`, `evaluate_lyrics.py`,
`write_lrc.py`) zeigte im kombinierten Datei-für-Datei-Lauf aus
songtext_pipeline.py IMMER `1/1:` (da dort stets genau eine Datei pro
Aufruf verarbeitet wird) -- reine Redundanz ohne Information ("was soll
dieses unnötige 1/1 Soda Pop.mp3?"). **Fix:** Der Zähler wird jetzt nur noch
angezeigt, wenn `total > 1` (echte Mehrfach-Läufe, z.B. eigenständiges
`--abfragen` über eine ganze Bibliothek) -- an allen drei Stellen konsistent
geändert, neuer Test `test_statuszeile_zeigt_zaehler_bei_mehreren_songs`
für den `total > 1`-Fall ergänzt.

**Nachtrag — Merge mit dem parallel auf `main` entstandenen Kali-Uchis-Fix
(siehe Eintrag oben):** Beide Zweige erhöhten unabhängig voneinander
`lyrics_core.__version__` auf `1.13.16` — echte Versionsnummern-Kollision
zwischen zwei unabhängigen Änderungen. Beim Merge auf `1.13.18` aufgelöst
(der Kali-Uchis-Fix behält seine historische `1.13.16`-Bezeichnung im
Commit, dieser TODO-Abarbeitungs-Zweig wird als `1.13.17`/`1.13.18`
weitergezählt). `evaluate_lyrics._load_candidate_texts()` hatte dieselbe
Funktion in beiden Zweigen verändert (Kali-Uchis-Fix: Übersetzungsseiten-
Filter; dieser Zweig: unverändert) — beim Merge behalten, kein inhaltlicher
Widerspruch.

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

**Bugfix — derselbe Künstler tauchte mehrfach in einer Stichprobe auf:**
Ein echter Testlauf zeigte, dass `select_language_pools()` denselben
Künstler mehrfach zog. Ursache: die Cache-DB ist extrem schief verteilt —
von nur ~420 verschiedenen Künstlern haben einzelne hunderte gecachte Songs
(Prince 392, Gary Numan 238, Green Day 105, …) — eine rein zufällige Ziehung
pro (Artist, Titel)-Paar trifft solche Künstler überproportional oft, was
den Modellvergleich unnötig auf deren Vokabular/Stimme verengt statt eine
breite Stichprobe zu liefern. Fix: `select_language_pools()` merkt sich
`artist_key` bereits aufgenommener Kandidaten (`used_artists`, über BEIDE
Pools hinweg) und überspringt jeden weiteren Kandidaten desselben Künstlers
— unabhängig von Sprache oder Puffer-Status. Neuer Parameter
`exclude_artists` nimmt zusätzlich die Künstler der Pflicht-Songs entgegen
(`main()` übergibt sie), damit z.B. „Nina Hagen Band" nicht zusätzlich per
Zufall nochmal mit einem anderen Song gezogen wird. Am realen Cache-Bestand
verifiziert (`--n 20`, Cache-DB mit 5758 klassifizierbaren Kandidaten): vorher
kamen bei stichprobenartigen Läufen Wiederholungen vor, danach 48/48 bzw.
12/12 unterschiedliche Künstler in beiden Pools, keine einzige Dopplung.

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

### ✓ Nachtrag: `large-v3` ergänzt + Entscheidung für den Produktivbetrieb

**`--models`-Flag:** `compare_whisper_models.py` konnte bisher nur die fest
verdrahteten drei Modelle (`small`/`medium`/`turbo`) laufen lassen. Neuer
Parameter `--models MODELL1,MODELL2,...` (Standard weiterhin
`small,medium,turbo`) erlaubt ein beliebiges zusätzliches Modell auf
derselben Stichprobe. Existiert für einen Song schon eine Datei aus einem
früheren Lauf im selben `--output-dir`, hängt `_existing_output_path()` den
neuen Modell-Abschnitt an diese Datei an, statt (wie bisher) eine neue Datei
mit `_2`-Suffix anzulegen — so lässt sich ein Modell nachträglich auf exakt
derselben Stichprobe ergänzen, ohne die anderen drei neu zu rechnen.

**Vierter Testlauf mit `large-v3`:** Auf denselben 21 Songs ergänzt
(`--models large-v3`). Manuelle Auswertung (siehe
`whisper_modellvergleich_ergebnis.md`) zunächst nur im Modell-zu-Modell-
Vergleich, danach gegen echte `.lrc`-Songtexte von der Bibliothek geprüft
(20 von 21 Songs hatten eine Referenz — bei "Joco – Cloud" existiert keine
`.lrc`, der einzige gecachte Provider-Kandidat ist ein komplett anderer Song
und wurde von der App zu Recht unterhalb der Score-Schwelle abgelehnt).

Kernergebnis der Gegenprüfung: kein Modell ist frei von ausgelassenen
Songabschnitten oder Halluzinationen — auch `medium` lässt an zwei Songs
(Nina Hagen, Spliff) ganze Anfangszeilen weg, `small` verschluckt bei Nina
Hagen sogar das komplette letzte Songdrittel. `large-v3` löst Turbos
Aussetzer bei fehlenden Anfängen, hat aber ein eigenes Beispiel dafür
(George Michael – Faith) und halluziniert genauso oft wie Turbo, nur mit
anderen Phrasen. Der klare Unterschied zeigt sich bei der Sprache:

- **Bei rein englischen und rein deutschen Songs** liegen `medium`,
  `turbo` und `large-v3` nah beieinander — `large-v3` gewinnt zwar die
  meisten Einzelvergleiche (trifft öfter das exakte Wort), aber der
  Abstand ist klein und nicht bei jedem Song stabil.
- **Beim Sprachwechsel innerhalb eines Songs** (Testfall "Ja, Panik",
  Englisch/Deutsch gemischt) ist `large-v3` klar und deutlich am
  genauesten — trifft mehrere komplette deutsche Zeilen wortgenau, wo
  `medium`/`turbo`/`small` nur falsche englische Paraphrasen liefern.
  `small` erkennt den Sprachwechsel gar nicht.
- **Geschwindigkeit, isoliert gemessen** (5 Songs, `medium` und `large-v3`
  je einzeln auf derselben Stichprobe, ohne Bibliotheks-Scan/Modell-
  Ladezeit): `medium` ≈ 97 Sek/Song, `large-v3` ≈ 136 Sek/Song —
  `large-v3` braucht **rund 40 % länger pro Song**.

**Entscheidung für den Produktivbetrieb:** Englischsprachige Songs nutzen
weiterhin `medium` (Qualitätsunterschied zu `large-v3` zu gering, um die
40 % Mehrkosten zu rechtfertigen). Nicht-englische Songs (Sprach-Hint ≠
`en`, insbesondere Deutsch und gemischtsprachige Songs) nutzen künftig
`large-v3` — dort ist der Qualitätsgewinn real und deutlich. Umsetzung
(Modellwahl in `fetch_songtext.py` anhand des Sprach-Hints verzweigen) ist
noch offen.

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

## ✓ v1.1.6 — UI an cut.py angeglichen: Farbschema + Vorschaudauer live änderbar

Auf Nutzerwunsch an `cut.py`/`cut_ui.py` angeglichen:

- **Farbschema:** `assemble_ui.py` nutzte durchgängig das rich-Attribut
  `style="dim"` (+ `[dim]`/`[/dim]`-Markup in Subtitles), `cut_ui.py`
  stattdessen die explizite Farbe `"grey35"` (`"blue dim"` als Border-Farbe
  und `"dim yellow"` für noch-nicht-erreichte Status-Symbole blieben in
  beiden Dateien unverändert — nur der reine `"dim"`-Fall wurde ersetzt).
  Alle 26 betroffenen Stellen in `assemble_ui.py` umgestellt, per
  `sed`-Ersetzung + Gegenprobe (Anzahl vorher/nachher gezählt).
- **Vorschaudauer live änderbar (`p<Sek>`):** `cut.py` erlaubt, die
  Snippet-Länge während der Bedienung per `p18` etc. zu ändern
  (`parse_preview_duration()`, Grenzen 2–30s). `assemble.py` hatte dafür
  bisher gar keinen Mechanismus — Phase 1 (Punkt-Vorschau) lief immer mit
  der festen `DEFAULT_PLAY_DURATION`, Phase 2 (Crossfade-Vorschau) nur mit
  dem einmal beim Start gesetzten `--preview`-Wert. `parse_preview_duration()`
  1:1 aus `cut.py` übernommen; **beide** Phasen (nicht nur Phase 1, das
  einzige Äquivalent in cut.py) haben jetzt eigene, live per `p<Sek>`
  änderbare Werte — Phase 1 und Phase 2 sind in `assemble.py` zwei
  unterschiedliche Vorschau-Mechanismen ohne 1:1-Entsprechung in cut.py,
  deshalb beide konsistent nachgezogen (Nutzerentscheidung).
- **Anzeige:** Die Steuerzeile in beiden Panels zeigt jetzt
  `[p] {dauer}s abspielen  [p<Sek>] Dauer ändern (2-30s)` — genau wie in
  `cut_ui.py`. `skip_play`-Mechanik ergänzt (wie in `cut.py`): eine
  Dauer-Änderung spielt nicht sofort erneut ab, sondern nur auf explizites
  `[p]` danach.

Manuell mit `rich.Console` gerendert (keine echte Audiodatei nötig) zur
Kontrolle auf Rendering-Fehler — echter interaktiver Test mit Audiodatei
steht noch aus (siehe CLAUDE.md: UI-Änderungen gelten erst nach Bestätigung
im laufenden Programm als abgeschlossen).

## ✓ v1.1.5 — Bugfix: UI zeigte noch "-0.1 dBFS" statt echtem Zielwert

In v1.1.4 vergessen: die Phase-4-Statuszeile ("DC-Offset + Peak-
Normalisierung auf ...") war als String-Literal fest auf "-0.1 dBFS"
codiert, unabhängig vom tatsächlich verwendeten Zielwert. Nutzer meldete
den Widerspruch direkt aus der laufenden UI. Fix: Zeile liest jetzt
`PEAK_NORMALIZE_TARGET_DBFS` statt eines hartcodierten Strings — Anzeige
und tatsächliches Verhalten können dadurch nicht mehr auseinanderlaufen.

## ✓ v1.1.4 — Zurück zu reiner Peak-Normalisierung auf -1 dBFS

`normalize()` nutzte seit v1.1.2 ffmpeg `loudnorm` (Lautheitsangleichung auf
-23 LUFS + True-Peak-Limit -1.0 dBTP). Auf Nutzerwunsch zurückgebaut auf
reine Peak-Normalisierung wie vor v1.1.1 (damals sox, jetzt ffmpeg): Pass 1
misst mit `volumedetect` den Spitzenpegel nach DC-Offset-Filter und
optionalem Kanalausgleich (`max_volume` aus stderr geparst), Pass 2 wendet
den nötigen Gain über `volume=XdB` an, sodass der Spitzenpegel exakt bei
`PEAK_NORMALIZE_TARGET_DBFS = -1.0` dBFS landet. Kanalausgleich (optionaler
`pan`-Filter vor der Messung) bleibt unverändert erhalten. Kein LUFS-Ziel
mehr, keine Lautheitsangleichung — nur noch Spitzenpegel. Verifiziert an
einer echten Datei: `ffmpeg volumedetect` nach der Normalisierung bestätigt
`max_volume: -1.0 dB`, `ffprobe` bestätigt weiterhin 16 Bit (siehe v1.1.3).

## ✓ v1.1.3 — Bugfix: `_final.flac` doppelt so groß wie nötig (24 statt 16 Bit)

`normalize()` schrieb die Ausgabe ohne festes `-sample_fmt` — der `loudnorm`-
Filter rechnet intern in 32-Bit-Float, und ffmpegs FLAC-Encoder wählte ohne
Vorgabe eigenständig 24 Bit statt die ursprüngliche 16-Bit-Tiefe der Quelle
beizubehalten. Die zusätzlichen 8 Bit sind bei einer 16-Bit-Quelle praktisch
Rauschen und kaum komprimierbar — die Datei wurde dadurch fast doppelt so
groß wie `_prepared.flac` (kein Klangunterschied, nur Speicherplatz-
Verschwendung). Fund: reale Datei (Kali Uchis – Orquideas Parte 2) über
`ffprobe` geprüft, `bits_per_raw_sample` 16 → 24 zwischen `_prepared.flac`
und `_final.flac` bestätigt. Fix: `-sample_fmt s16` zum finalen ffmpeg-Aufruf
in `normalize()` ergänzt — verifiziert an derselben Datei, Ausgabe jetzt
wieder 16 Bit, Größe entsprechend wieder im erwarteten Bereich.

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
