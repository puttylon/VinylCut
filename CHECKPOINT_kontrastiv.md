# Checkpoint — Kontrastive Whisper-Verifikation (WER-Nachfolger)

Stand: 2026-07-15, Nachmittag. Wiedereinstiegspunkt, laufend aktualisiert.

## Ergebnis bisher (kurz)

- **WER als IDF-Ersatz: verworfen.** Auf dem Whisper-Pfad längenempfindlich (26/38 Falsch-Ablehnungen bei echten Treffern, nur 9 echte Korrekturen). Konsens-Pfad war ok, aber Gesamturteil negativ. Siehe `/Users/guido/Documents/GitHub/VinylCut/wer_whisper_uneinigkeit.md`.
- **Reines Jaccard: nicht getestet, verworfen mit Beleg.** Nutzer erinnerte an v1.7.7-Analyse: reines Jaccard trennte "hauchdünn" (Stopword-Rauschen), IDF-Jaccard klar. Kein Test nötig, mathematisch klar warum (IDF aus 1 Song degeneriert zu Jaccard).
- **Bigramm-Jaccard (2-Wort-Tupel, ungewichtet) als Ersatz für IDF-Jaccard: getestet, verworfen.** Schneidet auf dem harten 33-Fälle-Testset SCHLECHTER ab als IDF-Jaccard (27/33 statt 31/33 richtig; 3 neue Fehl-Akzeptanzen, 1 neue Fehl-Ablehnung). Wichtigste Erkenntnis dabei: Der Garth-Brooks-Fehlerfall (s.u.) liegt NICHT an generischem Vokabular-Zufall, sondern an einer **Datenkontamination im Hintergrund-Pool** — der Hintergrund-Song "Michael Bublé – Christmas" hat einen Musixmatch-Kandidatentext, der wortwörtlich "White Christmas" ist (Provider-Fehltreffer bei fremdem Song), und die kontrastive Marge nimmt den MAX über alle Kandidatentexte eines Hintergrund-Songs — trifft also zwangsläufig den kontaminierten Text. Das ist ein Pool-Problem, keine Schwäche der Ähnlichkeitsmetrik; keine Metrik (auch keine sequenzbewusste) kann das umgehen. Details: `bigram_jaccard_test_ergebnis.md`, ROADMAP.md (Abschnitt "✗ Bigramm-Jaccard … — getestet, verworfen"). **Entscheidung: IDF-Jaccard bleibt die Ähnlichkeitsmetrik, kein Metrik-Wechsel.**
- **Kontrastive Marge: GO, mit einer offenen Frage.** Statt fester Schwelle: Marge = best_score − max(K=20 Zufalls-Hintergrund gleicher Sprache aus Cache). IDF-Tabelle wird GLOBAL aus Cache-Texten gebaut (keine Datei, kein `--rebuild-idf`, keine Sprach-Teiltabellen).
  - Synthetischer DB-Test: AUC 0,967 (vs. 0,949 Baseline), Deutsch-Bias-Sorge geprüft und **widerlegt** (AUC-Diff global vs. sprachrichtige IDF nur −0,0007). Siehe `/Users/guido/Documents/GitHub/VinylCut/scratch_contrastive_test_ergebnis.md`.
  - **Echter Bibliothekslauf** (`--cache-only --contrastive-experiment`, Worktree `VinylCut-wer`, Branch `experiment/wer-production`) durch: 784 Whisper-Entscheidungen (Skip-Gate-Umgehung funktioniert), 33 Uneinigkeiten mit alter IDF-Entscheidung.
  - **Inhaltlich geprüft** (Cache-Transkript vs. Provider-Text): **31/33 kontrastiv korrekt**, nur **2 echte Fehler** (beide Englisch): Garth Brooks "White Christmas" (0,89-Score fälschlich abgelehnt — Ursache jetzt geklärt: Hintergrund-Pool-Kontamination, s.o., nicht reiner Zufalls-Ausreißer wie ursprünglich vermutet) und Hercules and Love Affair "Hercules Theme" (vermutlich Whisper-Halluzination, kein Logik-Fehler). **Deutsch = Feature, nicht Bug** — 2 Fälle vom Nutzer per Audio-Anhören verifiziert (JETZT! "Warum", "Die Zeit": Provider-Text war jeweils ein anderer Song, Whisper hatte recht). Siehe `/Users/guido/Documents/GitHub/VinylCut-wer/contrastive_run_vergleich.md`.
  - **86 zusätzliche Datei-Änderungen (Kandidaten-Neuauswahl, gleiche Ja/Nein-Entscheidung wie IDF):** GEPRÜFT, siehe Abschnitt unten. **84 HARMLOS, 2 VERBESSERUNG, 0 REGRESSION, 0 unklar.**
  - **Vorgeschlagene Verbesserung (noch nicht gebaut, jetzt AKTUELLER NÄCHSTER SCHRITT):** Hybrid — akzeptiere wenn `best_score ≥ ~0,3` (hoher absoluter Boden, fängt White-Christmas-Fall unabhängig von der Hintergrund-Pool-Kontamination) ODER `Marge ≥ 0,0115`. Löst die einzige gefundene Schwäche.

## 86-Fälle-Regressionscheck — ABGESCHLOSSEN

Sonnet-Agent-Auftrag (nach vorherigem Session-Limit-Fehlschlag) erfolgreich neu gestartet und durchgelaufen. Ergebnis in `/Users/guido/Documents/GitHub/VinylCut-wer/contrastive_reselection_check.md`: **84 HARMLOS** (gleicher Song, nur Formatierung/Quelle/Encoding unterschiedlich), **2 VERBESSERUNG** (Gary Numan "Your Fascination" — ALT fehlte eine ganze, vom Transkript bestätigte Strophe; Julio Iglesias "Un Canto a Galicia" — NEU wählt die tatsächlich gesungene galicische Sprachversion statt der spanischen Übersetzung), **0 REGRESSION**, **0 unklar**. Beide VERBESSERUNG-Fälle vom Nutzer/mir stichprobenartig direkt am Dateiinhalt gegengeprüft (Gary Numan bestätigt). Fazit: Umstellung auf globale Cache-IDF für die Kandidaten-Rangfolge ist für diese 86 Fälle unbedenklich.

## Zustand der echten Bibliothek (WICHTIG)

`/Volumes/music/musik/` ist **aktuell NICHT im Backup-Zustand** — der Kontrastiv-Lauf hat 88 `.lrc` real geschrieben/gelöscht (33 Entscheidungsfälle + 86 Neuauswahl-Fälle, teils überlappend in der Zählung, siehe `contrastive_run_vergleich.md` für exakte Aufschlüsselung 86 geändert/29 gelöscht/2 neu). **Vollständiges Backup liegt unangetastet in** `/Users/guido/Documents/GitHub/VinylCut-wer/lrc_backup/` (16.183 Dateien, exakte Ordnerstruktur, verifiziert). Falls Rücksetzen nötig:
```
rsync -rm --include='*/' --include='*.lrc' --exclude='*' /Users/guido/Documents/GitHub/VinylCut-wer/lrc_backup/ /Volumes/music/musik/
```

## Repo-/Worktree-Zustand

- `/Users/guido/Documents/GitHub/VinylCut` — Branch `experiment/wer-calibration`, nur untracked Scratch-Analyseskripte (nichts committet, nichts zu verlieren).
- `/Users/guido/Documents/GitHub/VinylCut-wer` — Branch `experiment/wer-production`, **modifiziert** (`fetch_songtext.py`, `test_fetch_songtext.py`, `.gitignore`) mit beiden Experiment-Flags (`--wer-experiment` verworfen, `--contrastive-experiment` aktueller Kandidat). 332 Tests grün (Stand vor dem echten Lauf). **Nichts committet.**
- `main` — sauber, unverändert.

## Nächste Schritte (in Reihenfolge)

1. ~~86-Fälle-Regressionscheck~~ — ERLEDIGT (s. o.): 84 HARMLOS, 2 VERBESSERUNG, 0 REGRESSION.
2. ~~Bigramm-Jaccard als Alternative testen~~ — ERLEDIGT: verworfen (s. o.), IDF-Jaccard bleibt.
3. **AKTUELL:** Hybrid-Boden einbauen: `best_score ≥ 0,3 ODER Marge ≥ 0,0115` in `_whisper_accept`/`_contrastive_margin_and_decision`, kurz gegen die bekannten 33+86 Fälle gegenprüfen (insbesondere Garth Brooks muss jetzt akzeptiert werden, keiner der bisher korrekten 29+84+2 Fälle darf kippen).
4. Produktiv-Umbau: kontrastiv als Standard, `--contrastive-experiment`-Flag entfernt (wird Normalverhalten), `fetch_songtext_idf.json` + `--rebuild-idf` + `_build_idf`/`_idf_table_for`-Sprachlogik raus, Doku/README/ROADMAP/Version, volle Testsuite.
5. Finaler echter Lauf als Sanity-Gate (kein Metrik-Beweis mehr nötig, nur Integrationstest) — vorher Bibliothek aus Backup zurücksetzen für sauberen Diff.
6. **Merge nach main + IDF-Löschung erst nach expliziter Nutzer-Freigabe** (einziger vereinbarter Rückfragepunkt).

## Randnotiz für später (nicht Teil dieses Vorhabens)

Hintergrund-Pool-Kontamination (s. o., Michael Bublé/Musixmatch-Fehltreffer) ist strukturell und kann bei anderen Songs erneut auftreten — jeder Cache-Song mit einem Provider-Fehltreffer kann fälschlich als Hintergrund-Störsignal wirken. Möglicher künftiger Fix: Hintergrund-Kandidaten vor Aufnahme in den Pool per Provider-Konsens filtern. Nicht dringend, da der Hybrid-Boden (Schritt 3) den einzigen bisher gefundenen konkreten Schadensfall bereits unabhängig davon löst.
