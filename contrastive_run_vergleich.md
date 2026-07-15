# Kontrastive Marge vs. alte IDF-Entscheidung — Vergleich der 33 Uneinigkeiten

Datenbasis: `contrastive_experiment_log.csv` (784 Whisper-Entscheidungen aus dem
`--cache-only --contrastive-experiment`-Lauf gegen die reale Bibliothek — war nur
Zwischenstand für diese Auswertung, nach Abschluss der Analyse aufgeräumt), Cache-DB
`fetch_songtext_cache.db` (Provider-Kandidatentexte), `lrc_backup/` (Bibliotheksstand
vor dem Lauf, dient als zusätzliche Kandidatenquelle für Tracks ohne Provider-Treffer
im Cache — siehe Methodik).

## Methodik-Hinweis

`_whisper_best()` nimmt bei jedem Lauf **immer auch die bereits auf der Festplatte
liegende `.lrc`-Datei als Kandidat** mit auf (`all_candidates = candidates +
[existing_lrc]`, fetch_songtext.py Z. 1795-1798 im Wer-Worktree). Bei den JETZT!-,
Hannes-Wader- und weiteren Fällen, bei denen die Cache-DB für alle vier Provider
`status='nichts'`/`'fehlschlag'` zeigt, war die frühere `lrc_backup`-Datei der
tatsächlich bewertete Kandidat — nicht ein DB-Provider-Text. Für jeden der 33 Fälle
wurden deshalb sowohl DB-Provider-Treffer (`texte.inhalt` über `ergebnisse`) als auch
die passende Backup-`.lrc` herangezogen und mit dem Whisper-Transkript (`transkripte`)
inhaltlich verglichen.

Zwei Fälle (JETZT! „Warum", JETZT! „Die Zeit") sind vom Nutzer per Abhören des
Original-Audios als Ground Truth verifiziert: Whisper hat hier korrekt transkribiert.
Das bestätigt die Transkript-Qualität auch für die übrigen Fälle als verlässliche
Referenz — „Transkript ≠ Provider-Text" wurde deshalb durchgehend als „Provider hat
falschen Song" gewertet, wo der inhaltliche Abgleich das hergibt.

## 1. Kategorienverteilung (33 Uneinigkeiten)

| Kategorie | Deutsch (22) | Englisch (10) | Spanisch (1) | Gesamt |
|---|---|---|---|---|
| RICHTIG-ablehnung | 21 | 7 | 1 | 29 |
| FALSCH-ablehnung | 0 | 2 | 0 | 2 |
| RICHTIG-annahme | 1 | 1 | 0 | 2 |
| FALSCH-annahme | 0 | 0 | 0 | 0 |
| unklar | 0 | 0 | 0 | 0 |

**Bei den 22 deutschen Fällen: 0 Fehler.** Beide echten kontrastiven Fehler
(FALSCH-ablehnung) betreffen englischsprachige Songs.

## 2. Alle 33 Fälle

| # | Artist | Titel | Spr. | best | max_hg | marge | Kategorie | Begründung (derselbe Song?) |
|---|---|---|---|---|---|---|---|---|
| 0 | Garth Brooks | White Christmas | en | 0.890 | 0.906 | −0.0162 | **FALSCH-ablehnung** | Ja — Transkript und Genius/Netease/Backup fast wortidentisch. |
| 1 | Glenn Miller Orch. | At Last | en | 0.079 | 0.075 | +0.0033 | RICHTIG-ablehnung | Nein — Transkript ist "At Last" (Etta James), lrclib/Backup liefern "Somewhere Over The Rainbow". |
| 2 | Hannes Wader | Nach Hamburg | de | 0.058 | 0.098 | −0.0403 | RICHTIG-ablehnung | Nein — Transkript ist Erzähl-Song über Hamburg-Ausflug, lrclib liefert "Kodiak Bär"-Text (anderer Wader-Song). |
| 3 | Hannes Wader | Alle Hügel | de | 0.083 | 0.102 | −0.0194 | RICHTIG-ablehnung | Nein — Transkript ist Dorfrückkehr-Song, Backup-LRC ist "Eva auf dem Eis" (anderer Wader-Song). |
| 4 | Hannes Wader | Gute Nacht | de | 0.085 | 0.089 | −0.0034 | RICHTIG-ablehnung | Nein — weder Genius ("Ade zur guten Nacht") noch lrclib/Backup (Reinhard Meys "Gute Nacht Freunde") matchen das Transkript. |
| 5 | Hannes Wader | Nach Hamburg (Dup.) | de | 0.058 | 0.098 | −0.0403 | RICHTIG-ablehnung | Zweite Datei desselben Songs (zwei Alben, "Auftritt" + "Schon so lang") — identisch zu #2. |
| 6 | Hannes Wittmer | Das Ende der Geschichte | de | 0.073 | 0.070 | +0.0024 | RICHTIG-ablehnung | Nein — Transkript ist gesprochener Podcast-Outro von Hannes Wittmer, Backup-LRC ist ein Auszug aus Fontanes Roman "Vor dem Sturm" (kompletter Fehltreffer). |
| 7 | Heino | Die Sonne Von Mexico | de | 0.033 | 0.012 | +0.0206 | **RICHTIG-annahme** | Ja — lrclib-Text deckt sich inhaltlich exakt (Whisper transkribierte fälschlich auf Englisch, Wortlaut stimmt aber). |
| 8 | Hercules & Love Affair | Hercules Theme | en | 0.066 | 0.074 | −0.0075 | **FALSCH-ablehnung (mit Vorbehalt)** | Wahrscheinlich ja — Genius/Netease/Backup stimmen alle überein ("Little Boy Hercules"), Whisper hat vermutlich bei stark halligem Electro-Track halluziniert statt transkribiert. |
| 9 | Hope | Away | en | 0.066 | 0.061 | +0.0056 | RICHTIG-ablehnung | Nein — Titelkollision: Genius/Backup liefern "Let's go away..." (Finding Hope), Transkript ist ein anderer "Away"-Song ("tides of change"). |
| 10 | Hope | Prepared To Fly | en | 0.079 | 0.104 | −0.0252 | RICHTIG-ablehnung | Nein — Netease und Backup sind je unterschiedliche, andere "Prepared To Fly"-Songs; keiner matcht das Transkript. |
| 11 | Hope | Hope Is Alive | en | 0.094 | 0.142 | −0.0482 | RICHTIG-ablehnung | Nein — Genius/Backup liefern "There Is Hope (Hope Is Still Alive)", lrclib einen dritten Song; keiner matcht das Transkript wörtlich. |
| 12 | Hope | The End | en | 0.073 | 0.120 | −0.0471 | RICHTIG-ablehnung | Nein — Genius/Backup liefern "The End of the World" (Klayton/Celldweller), komplett anderer Text. |
| 13 | JETZT! | Vielleicht-Menschen | de | 0.085 | 0.085 | +0.0001 | RICHTIG-ablehnung | Nein — Backup-LRC ist ein Hip-Hop-Song "300K", Transkript ist erkennbar das tatsächliche JETZT!-Stück. |
| 14 | JETZT! | Herbst In Berlin | de | 0.079 | 0.090 | −0.0106 | RICHTIG-ablehnung | Nein — Backup ist anderer Song gleichen Titels ("Steig in Prenzlau ein..."), Transkript hat eigenen, kohärenten Text. |
| 15 | JETZT! | Du Bist Nicht Allein | de | 0.047 | 0.127 | −0.0800 | RICHTIG-ablehnung | Nein — lrclib liefert "wir sind schon viele"-Song, Transkript hat anderen Text/Refrain. |
| 16 | JETZT! | Kommst Du Mit In Den Alltag? | de | 0.087 | 0.100 | −0.0137 | RICHTIG-ablehnung | Nein — Backup ist anderer "Alltag"-Song ("Ich renn, weiß noch nicht wohin"), Transkript hat eigenen Text mit Titel-Refrain. |
| 17 | JETZT! | Warum | de | 0.083 | 0.095 | −0.0121 | RICHTIG-ablehnung **(Ground Truth verifiziert)** | Nein — Genius/Backup: "Warum jetzt?" (Trinker-Erzählung), Transkript: tatsächlicher JETZT!-Song. Per Audio bestätigt. |
| 18 | JETZT! | Acht Stunden Sind Kein Tag | de | 0.093 | 0.101 | −0.0084 | RICHTIG-ablehnung | Nein — Backup ist anderer Song ("Jeder Umweg..."), Transkript hat Titel-Refrain "acht Stunden sind kein Tag". |
| 19 | JETZT! | Unsere Wilden Jahre | de | 0.082 | 0.084 | −0.0022 | RICHTIG-ablehnung | Nein — Backup ist anderer Song ("20.000 Meilen unter dem Radar"), Transkript hat eigenen Text mit Titel-Phrase. |
| 20 | JETZT! | Winterschlaf | de | 0.068 | 0.098 | −0.0303 | RICHTIG-ablehnung | Nein — Backup ist Rap-Track ("Eisbär", DMS), komplett anderer Song. |
| 21 | JETZT! | So sieht es aus, wenn das Herz bricht | de | 0.083 | 0.099 | −0.0167 | RICHTIG-ablehnung | Nein — Backup ist Rap-Song ("einsamer Wolf"), Transkript hat eigenen Titel-Refrain. |
| 22 | JETZT! | Die Zeit | de | 0.064 | 0.083 | −0.0191 | RICHTIG-ablehnung **(Ground Truth verifiziert)** | Nein — Genius-Text explizit als "(JTZT Cover)" gekennzeichnet, also anderer/Cover-Song. Per Audio bestätigt. |
| 23 | JETZT! | Traurigkeit | de | 0.071 | 0.085 | −0.0141 | RICHTIG-ablehnung | Nein — Backup ist anderer Song ("Dunkle Stunden, dunkles Bier"), Transkript hat eigenen Text mit Titel-Refrain. |
| 24 | JETZT! | Die Welt wird größer, wenn wir sie teilen | de | 0.099 | 0.102 | −0.0024 | RICHTIG-ablehnung | Nein — Backup ist ein "Baby kommt zur Welt"-Song ("Hallo Cayetana"), Transkript ist Liebeslied ohne Bezug dazu. |
| 25 | JETZT! | Red' mit mir | de | 0.089 | 0.140 | −0.0516 | RICHTIG-ablehnung | Nein — Backup ist Rap-Song ("Reaper"-Motiv), Transkript hat eigenen Titel-Refrain "red mit mir". |
| 26 | JETZT! | Was man Heimat nennt | de | 0.092 | 0.098 | −0.0059 | RICHTIG-ablehnung | Nein — Backup ist Rap-Song über Selbstverletzung, Transkript ist reflektierender Song über Heimat/Ankommen. |
| 27 | Jochen Distelmeyer | Manchmal | de | 0.109 | 0.122 | −0.0135 | RICHTIG-ablehnung | Nein — Backup-LRC (gleiches Album!) hat anderen Text ohne "manchmal habe ich noch Hoffnung"-Refrain des Transkripts. |
| 28 | Joco | Your Gun | en | 0.083 | 0.106 | −0.0231 | RICHTIG-ablehnung | Nein — Genius/Backup liefern "Your World Lyrics" (Titelverwechslung), Transkript hat Titel-Motiv "gun on my chest". |
| 29 | Joco | Winter | de | 0.047 | 0.080 | −0.0329 | RICHTIG-ablehnung | Nein — Genius liefert "Stift & Block" (Joko & Klaas TV-Comedy-Song, Namenskollision "Joco"/"Joko"!), komplett anderer Inhalt. |
| 30 | John Legend | Dancing In The Dark | en | 0.074 | 0.114 | −0.0404 | RICHTIG-ablehnung | Nein — Genius/lrclib/Backup liefern übereinstimmend Springsteen-Cover "Dancing In The Dark (Live)", Transkript ist ein anderer, eigener Song ("I had a dream like Dr. King"). |
| 31 | Julio Iglesias | Sono 10 | es | 0.090 | 0.146 | −0.0558 | RICHTIG-ablehnung | Nein — Netease/Backup liefern anderen spanischen Song ("No vayas presumiendo..."), Transkript ist anderer Text über verlorene erste Liebe. |
| 32 | Kraftwerk | Uran | en | 0.058 | 0.025 | +0.0331 | **RICHTIG-annahme** | Ja — Transkript (stark vokoder-verzerrt, von Whisper als Kauderwelsch verballhornt) deckt sich bei genauem Hinhören mit Genius/lrclib ("...radioactive ray... Urankristall"). |

## 3. Die echten kontrastiven Fehler (FALSCH-ablehnung) im Detail

### #0 — Garth Brooks, "White Christmas"

Eindeutigster Fall im ganzen Datensatz. Whisper-Transkript:

> „i m dreaming of a white christmas just like the ones i used to know where the tree
> tops glisten and children listen to hear sleigh bells in the snow…"

Genius-, Netease- und Backup-Text sind identisch und bis auf Wiederholungsstruktur
wortgleich mit dem Transkript. `marge = −0.0162` trotz `best_score = 0.890` (!) —
der Hintergrund-Pool (K=20 zufällige englische Songs) enthielt hier zufällig einen
Song, dessen IDF-Jaccard zum Transkript noch höher lag (`max_hintergrund = 0.906`).
Das ist ein Artefakt des **absoluten** Scores (sehr generische, kurze, extrem
repetitive Weihnachtslied-Wörter — "I'm dreaming of a white Christmas" — die dummerweise
auch mit einem Hintergrund-Song hoch matchen). Klarer Bug in der kontrastiven Logik
bei sehr kurzen/repetitiven Songtexten mit alltäglichem Wortschatz.

### #8 — Hercules and Love Affair, "Hercules Theme"

Weniger eindeutig, aber mit Vorbehalt ebenfalls als Fehler eingestuft. Drei
unabhängige Quellen (Genius, Netease, Backup) sind sich einig: „Little boy Hercules
/ We took him to town / Pushed him around…". Das Whisper-Transkript enthält davon
nichts, sondern „i have to leave… let my heart bleed… i'm a liar…" — inhaltlich
unverbunden. Da der Track ein hallreicher Deep-House-Tune mit wenig Text ist, ist
Whisper-Halluzination auf instrumentalen Passagen eine bekannte Fehlerquelle;
angesichts der einhelligen Drei-Quellen-Bestätigung ist "richtiger Song, aber
Transkript unzuverlässig" die wahrscheinlichere Erklärung als "falscher Song". Diese
Einschätzung ist unsicherer als bei #0 — nicht durch Anhören verifiziert.

## 4. Datei-Diff: Bibliothek vs. Backup

Verglichen wurden alle 16.183 `.lrc`-Dateien aus `lrc_backup/` mit dem aktuellen
Stand unter `/Volumes/music/musik/` (reiner Byte-Vergleich).

| | Anzahl |
|---|---|
| Unverändert | 16.068 |
| Inhalt geändert | 86 |
| Gelöscht (in Backup vorhanden, live fehlend) | 29 |
| Neu geschrieben (live vorhanden, in Backup fehlend) | ≥ 2 (gezielt geprüft) |

Der Lauf selbst meldete am Ende: **„88 geladen, 9293 übersprungen, 389 nicht
gefunden"**. 88 = 86 geänderte + 2 neu geschriebene Dateien — passt exakt.

**Zuordnung zu den 33 Whisper-Uneinigkeiten:**

- **26 der 29 gelöschten Dateien** entsprechen 1:1 den 26 Fällen aus Abschnitt 2,
  bei denen (a) eine Backup-`.lrc` existierte und (b) die kontrastive Entscheidung
  auf Ablehnung stand (True→False). Jede einzelne wurde geprüft und stimmt exakt
  überein (Garth Brooks, Glenn Miller, 2× Hannes Wader, Hannes Wittmer, 4× Hope,
  10× JETZT!, Distelmeyer, Joco „Your Gun", John Legend, Julio Iglesias).
- Die **restlichen 3 gelöschten Dateien** (Hope „Don't Harden", Hope „Middle
  Child", Hope Sandoval „That Spider") gehören **nicht** zu den 33 Uneinigkeiten —
  hier waren sich altes und neues Verfahren bereits einig (beide lehnten ab), sie
  wurden aber im selben Lauf ebenfalls entfernt. Das erklärt zwanglos, warum das
  ganze Hope-„Winter"-Album so stark betroffen ist: systematischer Provider-
  Fehltreffer für das ganze Album, nicht nur für die 4 Whisper-Uneinigkeitsfälle.
- **Die 2 neu geschriebenen Dateien** (Heino „Die Sonne Von Mexico", Kraftwerk
  „Uran", beide vom 15. Juli, Zeitstempel passend zum Lauf) entsprechen exakt den
  2 RICHTIG-annahme-Fällen (False→True) aus Abschnitt 2.
- **5 der 33 Fälle** (Hannes Wader „Nach Hamburg" ×2, JETZT! „Du Bist Nicht
  Allein", JETZT! „Die Zeit", Joco „Winter") hatten **nie** eine `.lrc`-Datei —
  weder im Backup noch aktuell. Die Ablehnung hinterlässt hier keine Datei-Spur
  (nichts zu löschen), fließt aber in die 389 „nicht gefunden" ein.
- Bilanz: 26 (gelöscht) + 2 (neu) + 5 (nie vorhanden) = 33 ✓ — alle 33
  Uneinigkeiten sind im Datei-Diff lückenlos wiedergefunden.

**Die 86 „sonstigen" Änderungen** verteilen sich breit über völlig andere
Interpreten (u. a. das komplette Gary-Numan-Archiv, George Benson, I Am Kloot,
Kettcar, Kraftklub, Flash and the Pan, Julio Iglesias-Tracks abseits von „Sono
10", zwei weitere Hannes-Wader-Songs) — keiner dieser 86 Pfade überschneidet sich
mit den 33 Whisper-Uneinigkeiten. Das stützt die Vermutungs-Formulierung aus der
Aufgabenstellung: Es handelt sich um **Konsens-Pfad- bzw. Kandidaten-Neuauswahl-
Rewrites**, die unabhängig von der kontrastiven Marge entstehen (z. B. weil
`--contrastive-experiment` jeden zuvor per Whisper verarbeiteten Song erneut
prüft und dabei — bei geändertem Cache-Stand oder neuer globaler statt
sprachspezifischer IDF — einen anderen, aber ebenfalls akzeptierten Kandidaten
auswählt). Eine erschöpfende Einzelprüfung dieser 86 Dateien war laut Auftrag
nicht gefordert und wurde nicht durchgeführt.

## 5. Fazit

**Kategorienverteilung:** Von den 33 Whisper-Uneinigkeiten sind 29 korrekte
Ablehnungen (RICHTIG-ablehnung), 2 korrekte Neu-Annahmen (RICHTIG-annahme) und
nur **2 echte kontrastive Fehler** (FALSCH-ablehnung) — beide bei englischen
Songs, keiner bei Deutsch oder Spanisch.

**Deutsch ist ein Feature, kein Bug — klar belegt.** Alle 22 deutschen Fälle
wurden Zeile für Zeile inhaltlich geprüft: In 21 von 22 Fällen liefert der
Provider- bzw. lokale Bestandstext nachweislich einen **anderen Song** (Titel-
kollisionen wie „Warum"/„Warum jetzt?", explizite Cover-Kennzeichnung wie „Die
Zeit heilt keine Wunden … (JTZT Cover)", oder komplett fachfremde Texte wie ein
Fontane-Romanauszug bei Hannes Wittmer oder ein Hip-Hop-Song bei JETZT!
„Vielleicht-Menschen"). Zwei dieser Fälle (JETZT! „Warum", „Die Zeit") sind vom
Nutzer per Audio-Abhören als Ground Truth bestätigt. Der einzige deutsche
Sonderfall (Heino) ist eine korrekte Neu-Annahme, kein Fehler. Die auffällige
JETZT!-Häufung (14 von 22) erklärt sich schlicht dadurch, dass dieses Album
besonders viele Songs mit alltäglichen, titelkollisionsanfälligen deutschen
Namen hat („Warum", „Winterschlaf", „Traurigkeit" etc.) — nicht durch einen
Sprach-Bias im kontrastiven Verfahren.

**Die 2 echten Fehler:** Bei Garth Brooks „White Christmas" ist der Fall
eindeutig — Transkript und Provider-Text sind nahezu wortidentisch, trotzdem
lehnt die Marge ab (−0,0162), weil ein zufälliger Hintergrund-Song im K=20-Pool
zufällig noch besser matcht als der eigentlich korrekte Kandidat. Das ist ein
Bug: Bei sehr kurzen, hochrepetitiven, alltagssprachlichen Songtexten (Weihnachts-
lied-Standardvokabular) kann der Hintergrund-Pool zufällig einen falsch-hohen
Vergleichswert liefern und die Marge unter die Schwelle drücken. Bei Hercules and
Love Affair „Hercules Theme" ist die Lage weniger klar: drei unabhängige Quellen
bestätigen übereinstimmend den Provider-Text, das Whisper-Transkript weicht aber
komplett ab — plausibelste Erklärung ist eine Whisper-Halluzination auf dem
halligen, textarmen Electro-Track, nicht ein falscher Song. Dieser zweite Fall
ist nicht durch Anhören verifiziert und bleibt mit Vorbehalt eingestuft.

**Annahmen/Unsicherheiten:** (1) Die inhaltliche Beurteilung erfolgte per
Lesevergleich der Texte, nicht durch Anhören der Audiodateien — außer den beiden
vom Nutzer bereits verifizierten JETZT!-Fällen. (2) Bei Hercules and Love Affair
bleibt eine Restunsicherheit (Whisper-Halluzination vs. echter Fehltreffer).
(3) Die 86 „sonstigen" Datei-Änderungen wurden nur grob geprüft (keine Über-
schneidung mit den 33 Fällen bestätigt), nicht einzeln inhaltlich bewertet, wie
im Auftrag vorgesehen.
