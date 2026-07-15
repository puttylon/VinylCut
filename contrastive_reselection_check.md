# Kandidaten-Neuauswahl bei unveränderter Ja/Nein-Entscheidung — Prüfung der 86 „sonstigen" Änderungen

Diese Analyse ergänzt `contrastive_run_vergleich.md` (dort: 33 Fälle, bei denen sich die
Akzeptieren/Ablehnen-Entscheidung selbst geändert hat). Hier geht es um die **86 übrigen
`.lrc`-Änderungen** aus demselben `--cache-only --contrastive-experiment`-Lauf: Die
Ja/Nein-Entscheidung blieb identisch, aber der **gewählte Kandidatentext** hat sich
geändert — Ursache ist die globale, cache-weite IDF-Tabelle (`_contrastive_idf`,
`fetch_songtext.py` Z. ~1484–1488) statt der sprachspezifischen Datei-IDF-Tabelle
(`_idf_table_for(lrc_lang, idf_data)`), die bei mehreren angebotenen Kandidaten
(Genius, Netease, lrclib, Musixmatch, „lokal"/Backup) zu einer anderen Rangfolge führen
kann.

## Methodik

1. Für jede Datei: ALT-Text (`lrc_backup/<pfad>`) und NEU-Text (`/Volumes/music/musik/<pfad>`)
   gelesen, mit `_extract_lrc_words` tokenisiert (Zeitstempel/Metadaten entfernt).
2. **Objektives Vorprüf-Signal:** Jaccard-Ähnlichkeit (ungewichtet, kein IDF) zwischen
   ALT- und NEU-Wortmenge. Ein Wert nahe 1.0 zeigt praktisch identischen Wortbestand
   (reine Formatierungs-/Zeitstempel-/Kopfzeilen-Variante) — direkt HARMLOS.
3. Für alle übrigen Fälle: Artist/Titel per Audio-Tag (`_read_audio_tags`) ermittelt,
   Whisper-Referenztranskript aus der Cache-DB (`transkripte`, per `songs.artist_key`/
   `titel_key`) geladen. Berechnet wurde der **ungewichtete Recall** — Anteil der
   Transkript-Wörter, die auch im jeweiligen Kandidatentext vorkommen — für ALT und NEU
   getrennt. Ein nahezu identischer Recall beider Seiten bedeutet: Transkript matched
   ALT und NEU etwa gleich gut, kein Korrektheits-Unterschied.
4. Alle Fälle mit auffälligem Signal (Jaccard < 0.85, Recall-Differenz > 0.03, absoluter
   Recall < 0.4 auf einer Seite, oder kein Cache-Transkript verfügbar — 22 von 86) wurden
   **zusätzlich per Wort-für-Wort-Diff und Textlektüre** geprüft (Auszüge siehe Abschnitt
   „Detailfälle" unten). Die restlichen 64 Fälle hatten so eindeutige Zahlenlage (Jaccard
   ≥ 0.85, Recall-Differenz ≤ 0.03), dass eine zusätzliche Textlektüre keinen
   Erkenntnisgewinn mehr gebracht hätte; mehrere davon wurden stichprobenartig verifiziert
   (z. B. Fälle mit sehr hohem Wortüberlapp trotz stark unterschiedlicher Zeilenzahl, s. u.).
5. Für die 9 Dateien ohne Cache-Transkript-Eintrag wurde direkt ALT gegen NEU gelesen
   (kein Whisper-Vergleich möglich, aber Songwechsel wäre auch ohne Transkript an groben
   inhaltlichen Abweichungen erkennbar gewesen — war in keinem der 9 Fälle so).

## Kategorienverteilung

| Kategorie | Anzahl |
|---|---|
| HARMLOS | 84 |
| VERBESSERUNG | 2 |
| REGRESSION | 0 |
| unklar | 0 |
| **Gesamt** | **86** |

**Keine Regression gefunden.** Die beiden VERBESSERUNG-Fälle sind kein Songwechsel
(„falscher Song → richtiger Song"), sondern echte Content-Qualitätsverbesserungen
innerhalb desselben Songs (fehlende Strophe ergänzt bzw. richtige Sprachversion
gewählt) — Details unten.

## Alle 86 Fälle

| # | Artist | Titel | Klassifikation | Begründung |
|---|---|---|---|---|
| 1 | Flash & The Pan | Man In The Middle | HARMLOS | Hoher Wortüberlapp (Jaccard=0.957); Transkript-Recall ALT=0.80 vs NEU=0.81 — kein inhaltlicher Unterschied. |
| 2 | Flash & The Pan | Down Among The Dead Men | HARMLOS | Hoher Wortüberlapp (Jaccard=0.904); Recall ALT=0.61 vs NEU=0.61. |
| 3 | Flash & The Pan | Restless | HARMLOS | Hoher Wortüberlapp (Jaccard=0.955); Recall ALT=0.73 vs NEU=0.73. |
| 4 | Garth Brooks | This Ain't Tennessee | HARMLOS | Hoher Wortüberlapp (Jaccard=0.956); Recall ALT=0.92 vs NEU=0.92. |
| 5 | Garth Brooks | Longneck Bottle | HARMLOS | Hoher Wortüberlapp (Jaccard=0.918); Recall ALT=0.76 vs NEU=0.77. |
| 6 | Garth Brooks | A Friend to Me | HARMLOS | Hoher Wortüberlapp (Jaccard=0.952); Recall ALT=0.94 vs NEU=0.93. |
| 7 | Tubeway Army | The Life Machine | HARMLOS | Hoher Wortüberlapp (Jaccard=0.943); Recall ALT=0.91 vs NEU=0.93. |
| 8 | Tubeway Army | Something's In The House | HARMLOS | Hoher Wortüberlapp (Jaccard=0.970); Recall ALT=0.88 vs NEU=0.88. |
| 9 | Tubeway Army | Zero Bars (Mr.Smith) | HARMLOS | Direkter Textvergleich: identischer Songtext, ALT nur mit zusätzlicher Genius-Kopfzeile („5 Contributors"). |
| 10 | Tubeway Army | You Don't Know Me | HARMLOS | Identischer Wortbestand (Jaccard=1.0) — reine Formatierungsvariante. |
| 11 | Tubeway Army | Only A Downstat | HARMLOS | Identischer Wortbestand (Jaccard=1.0). |
| 12 | Gary Numan | Remind Me To Smile | HARMLOS | Hoher Wortüberlapp (Jaccard=0.969); Recall ALT=0.61 vs NEU=0.61 (NEU enthält zusätzlichen wiederholten Refrain-Durchlauf, vgl. #13). |
| 13 | Gary Numan | The Joy Circuit | HARMLOS | Identischer Wortbestand (Jaccard=1.0); NEU ist vollständigere Version (Strophen 2× statt 1×, per Volltext-Lektüre verifiziert) desselben Songtexts. |
| 14 | Gary Numan | Exhibition | HARMLOS | Identischer Wortbestand (Jaccard=1.0). |
| 15 | Gary Numan | The Secret | HARMLOS | Hoher Wortüberlapp (Jaccard=0.948); Recall ALT=0.47 vs NEU=0.46. |
| 16 | Gary Numan | She Cries | HARMLOS | Direkter Textvergleich: identischer Songtext, nur Interpunktion/Unsicherheitsmarker („?") in ALT entfernt. |
| 17 | Gary Numan | Your Fascination | **VERBESSERUNG** | ALT fehlt eine ganze Strophe („No more features / for now, now, now / …") — grosse Zeitlücken im LRC deuten auf unvollständige Version hin; Transkript bestätigt exakt diese Strophe. NEU vollständiger, Recall +0.056. Siehe Detailfall unten. |
| 18 | Gary Numan | Creatures | HARMLOS | Hoher Wortüberlapp (Jaccard=0.926); Recall ALT=0.48 vs NEU=0.47. |
| 19 | Gary Numan | Tricks | HARMLOS | Hoher Wortüberlapp (Jaccard=0.937); Recall ALT=0.55 vs NEU=0.58. |
| 20 | Gary Numan | I Still Remember | HARMLOS | Hoher Wortüberlapp (Jaccard=0.971); Recall ALT=0.80 vs NEU=0.80. |
| 21 | Gary Numan | Child With The Ghost | HARMLOS | Hoher Wortüberlapp (Jaccard=0.929); Recall ALT=0.62 vs NEU=0.63. |
| 22 | Gary Numan | Prophecy | HARMLOS | Hoher Wortüberlapp (Jaccard=0.968); Recall ALT=0.69 vs NEU=0.69. |
| 23 | Gary Numan | My Jesus | HARMLOS | Hoher Wortüberlapp (Jaccard=0.953); Recall ALT=0.49 vs NEU=0.49. |
| 24 | Gary Numan | Everyday I Die | HARMLOS | Identischer Wortbestand (Jaccard=1.0). |
| 25 | Gary Numan | Are Friends Electric? | HARMLOS | Hoher Wortüberlapp (Jaccard=0.983); Recall ALT=0.63 vs NEU=0.63. |
| 26 | Gary Numan | We Are the Lost | HARMLOS | Hoher Wortüberlapp (Jaccard=0.897); Recall ALT=0.49 vs NEU=0.49. |
| 27 | Gary Numan | Pray For The Pain You Serve | HARMLOS | Hoher Wortüberlapp (Jaccard=0.932); Recall ALT=0.77 vs NEU=0.77. |
| 28 | Gary Numan | My Breathing | HARMLOS | Hoher Wortüberlapp (Jaccard=0.979); Recall ALT=0.61 vs NEU=0.61. |
| 29 | Gary Numan | You Walk In My Soul | HARMLOS | Hoher Wortüberlapp (Jaccard=0.963); Recall ALT=0.58 vs NEU=0.58. |
| 30 | Gary Numan | Absolution | HARMLOS | Identischer Wortbestand (Jaccard=1.0). |
| 31 | Genesis | Illegal Alien | HARMLOS | Hoher Wortüberlapp (Jaccard=0.867); Recall ALT=0.81 vs NEU=0.79. |
| 32 | George Benson | Star Of A Story (X) | HARMLOS | Direkter Textvergleich: identischer Songtext, ALT nur mit zusätzlicher Genius-Kopfzeile. |
| 33 | George Benson | Nature Boy | HARMLOS | Identischer Wortbestand (Jaccard=1.0). |
| 34 | George Benson | You Can Do It, Baby | HARMLOS | Identischer Wortbestand (Jaccard=1.0). |
| 35 | The Glenn Miller Orchestra | Cinderella (Stay In My Arms) | HARMLOS | Direkter Textvergleich: identischer Songtext, NEU mit korrekten Apostrophen statt ALT ohne (Encoding-Unterschied). |
| 36 | Hannes Wader | Kokain | HARMLOS | Identischer Wortbestand (Jaccard=1.0). |
| 37 | Hannes Wader | Schon Morgen | HARMLOS | Hoher Wortüberlapp (Jaccard=0.986); Recall ALT=0.71 vs NEU=0.71. |
| 38 | Heart | How Can I Refuse | HARMLOS | Hoher Wortüberlapp (Jaccard=0.949); Recall ALT=0.79 vs NEU=0.80. |
| 39 | Heaven 17 | Height Of The Fighting (He-La-Hu) | HARMLOS | Direkter Textvergleich: Zeilen 1–12 wortidentisch; kein Cache-Transkript, kein Hinweis auf Songwechsel. |
| 40 | Heaven 17 | Trouble | HARMLOS | Identischer Wortbestand (Jaccard=1.0). |
| 41 | Heaven 17 | Come Live With Me | HARMLOS | Hoher Wortüberlapp (Jaccard=0.946); Recall ALT=0.89 vs NEU=0.88. |
| 42 | Herbert Grönemeyer | Neuland | HARMLOS | Hoher Wortüberlapp (Jaccard=0.916); Recall ALT=0.46 vs NEU=0.48. |
| 43 | Herbert Grönemeyer | Lache, Wenn Es Nicht Zum Weinen Reicht | HARMLOS | Identischer Wortbestand (Jaccard=1.0). |
| 44 | Herbert Grönemeyer | Blick Zurück | HARMLOS | Direkter Textvergleich: identischer Songtext, ALT mit zusätzlichen Genius-Übersetzungs-Kopfzeilen und einer fehlenden Zeile („weil du dir genügst"); Kernstrophen decken sich. |
| 45 | Hercules and Love Affair | Blind | HARMLOS | Hoher Wortüberlapp (Jaccard=0.853); Recall ALT=0.79 vs NEU=0.81. |
| 46 | Hot Chocolate | I Believe (In Love) | HARMLOS | Direkter Textvergleich: identischer Songtext, ALT nur mit zusätzlicher Genius-Kopfzeile. |
| 47 | I Am Kloot | A Strange Arrangement of Colour | HARMLOS | Hoher Wortüberlapp (Jaccard=0.939); Recall ALT=0.96 vs NEU=0.96. |
| 48 | I Am Kloot | Cuckoo | HARMLOS | Hoher Wortüberlapp (Jaccard=0.961); Recall ALT=0.71 vs NEU=0.71. |
| 49 | I Am Kloot | Sold as Seen | HARMLOS | Direkter Textvergleich: identischer Songtext, nur Kleinigkeiten („favourite"/„faded", „There"/„They're") unterschiedlich transkribiert. |
| 50 | I Am Kloot | Hold Back the Night | HARMLOS | Hoher Wortüberlapp (Jaccard=0.965); Recall ALT=0.90 vs NEU=0.92. |
| 51 | I Am Kloot | Some Better Day | HARMLOS | Identischer Wortbestand (Jaccard=1.0). |
| 52 | I Am Kloot | Forgive Me These Reminders | HARMLOS | Hoher Wortüberlapp (Jaccard=0.943); Recall ALT=0.87 vs NEU=0.89. |
| 53 | INXS | What You Need | HARMLOS | Direkter Textvergleich: identischer Songtext, nur einzelne Wörter („why"/„what") und Kontraktionen unterschiedlich. |
| 54 | Ideal | Da Leg Ich Mich Doch Lieber Hin | HARMLOS | Identischer Wortbestand (Jaccard=1.0). |
| 55 | Ilona | Allo Allo | HARMLOS | Hoher Wortüberlapp (Jaccard=0.914); Recall ALT=0.70 vs NEU=0.71. |
| 56 | Ilona | Retourner à l'école | HARMLOS | Hoher Wortüberlapp (Jaccard=0.890); Recall ALT=0.66 vs NEU=0.67. |
| 57 | Iron & Wine | The Desert Babbler | HARMLOS | Wort-für-Wort-Diff: identischer Song, kleine Transkriptions-Varianten („Bug eyes"/„Buckeyes" etc.) und unterschiedlich verschriftete Scat-Passage. |
| 58 | Ja, Panik | Alles hin, hin, hin | HARMLOS | Direkter Textvergleich: identischer Songtext, ALT mit zusätzlicher Genius-Erklärzeile (Falco-Zitat), Kernstrophen identisch. |
| 59 | Ja, Panik | Nevermore | HARMLOS | Direkter Textvergleich: identischer Songtext, nur unterschiedliche Kopfzeile (Poe-Hinweis). |
| 60 | Jimi Hendrix | Cross Town Traffic | HARMLOS | Hoher Wortüberlapp (Jaccard=0.901); Recall ALT=0.81 vs NEU=0.83. |
| 61 | Joy Division | These Days | HARMLOS | Hoher Wortüberlapp (Jaccard=0.968); Recall ALT=0.67 vs NEU=0.67. |
| 62 | Julio Iglesias | Goodbye Amore Mio | HARMLOS | Hoher Wortüberlapp (Jaccard=0.962); Recall ALT=0.84 vs NEU=0.85. |
| 63 | Julio Iglesias | Wenn Ein Schiff Vorüberfährt (un Canto a Galicia) | HARMLOS | Direkter Textvergleich: identischer Songtext (deutsche Version), ALT nur mit zusätzlicher Genius-Kopfzeile. |
| 64 | Julio Iglesias | Du in Deiner Welt (Rio Rebelde) | HARMLOS | Direkter Textvergleich: identischer Songtext, ALT nur mit zusätzlicher Genius-Kopfzeile. |
| 65 | Julio Iglesias | Komm Wieder Madonna | HARMLOS | Hoher Wortüberlapp (Jaccard=0.951); Recall ALT=0.62 vs NEU=0.64. |
| 66 | Julio Iglesias | Un Canto a Galicia | **VERBESSERUNG** | ALT ist die spanische Übersetzung („Yo te quiero tanto"), NEU der galicische Originaltext („Eu queroche tanto"). Transkript folgt eindeutig dem Galicischen. Siehe Detailfall unten. |
| 67 | Karat | Falscher Glanz | HARMLOS | Hoher Wortüberlapp (Jaccard=0.967); Recall ALT=0.62 vs NEU=0.62. |
| 68 | Kenny Chesney | Never Gonna Feel Like That Again | HARMLOS | Identischer Wortbestand (Jaccard=1.0). |
| 69 | Kettcar | Money Left To Burn | HARMLOS | Direkter Textvergleich: identischer Songtext, ALT nur mit zusätzlicher Genius-Kopfzeile/Kommasetzung. |
| 70 | Kettcar | Agnostik für Anfänger | HARMLOS | Hoher Wortüberlapp (Jaccard=0.936); Recall ALT=0.57 vs NEU=0.58. |
| 71 | Kettcar | Der Apokalyptische Reiter Und Das Besorgte Pferd | HARMLOS | Hoher Wortüberlapp (Jaccard=0.985); Recall ALT=0.83 vs NEU=0.83. |
| 72 | Kettcar | Zurück Aus Ohlsdorf | HARMLOS | Hoher Wortüberlapp (Jaccard=0.961); Recall ALT=0.89 vs NEU=0.90. |
| 73 | Kettcar | Wagenburg | HARMLOS | Hoher Wortüberlapp (Jaccard=0.959); Recall ALT=0.61 vs NEU=0.61. |
| 74 | Kettcar | Sommer '89 (Er schnitt Löcher in den Zaun) | HARMLOS | Direkter Textvergleich: identischer Songtext, nur Kopfzeilen-/Zeilenumbruch-Unterschiede. |
| 75 | Kettcar | Straßen unseres Viertels | HARMLOS | Hoher Wortüberlapp (Jaccard=0.967); Recall ALT=0.73 vs NEU=0.73. |
| 76 | Kinks | Till The End Of The Day | HARMLOS | Hoher Wortüberlapp (Jaccard=0.884); Recall ALT=0.71 vs NEU=0.71. |
| 77 | Kinks | Village Green Preservation Society | HARMLOS | Direkter Textvergleich: identischer Songtext, ALT mit zusätzlicher Genius-Erklärzeile („This iconic Kinks song…"), die in NEU fehlt. |
| 78 | Kool & The Gang | Straight Ahead | HARMLOS | Hoher Wortüberlapp (Jaccard=0.949); Recall ALT=0.85 vs NEU=0.85. |
| 79 | Kool & The Gang | Misled | HARMLOS | Direkter Textvergleich: identischer Songtext, nur Kleinigkeiten unterschiedlich transkribiert. |
| 80 | Kraftklub | Kein Gott, kein Staat, nur Du (feat. Mia Morgan) | HARMLOS | Direkter Textvergleich: identischer Songtext, ALT mit Genius-Strophen-Markup, NEU als Fliesstext. |
| 81 | Kraftklub | Der Zeit bist du egal | HARMLOS | Hoher Wortüberlapp (Jaccard=0.881); Recall ALT=0.87 vs NEU=0.86. |
| 82 | Kraftklub | Leben ruinieren | HARMLOS | Hoher Wortüberlapp (Jaccard=0.936); Recall ALT=0.80 vs NEU=0.81. |
| 83 | Kraftklub | Melancholie | HARMLOS | Hoher Wortüberlapp (Jaccard=0.872); Recall ALT=0.70 vs NEU=0.69. |
| 84 | Kraftwerk | Radioaktivität | HARMLOS | Direkter Textvergleich: identischer Songtext, NEU enthält zusätzlich die legitime Morsecode-Zeile (Teil des Original-Songs). Cache-Transkript wirkt für diesen Track unzuverlässig (siehe Detailfall unten). |
| 85 | Kraftwerk | Antenne | HARMLOS | Hoher Wortüberlapp (Jaccard=0.886); Recall ALT=0.61 vs NEU=0.61. |
| 86 | Karat | Jede Stunde | HARMLOS | Identischer Wortbestand (Jaccard=1.0). |

## Detailfälle (VERBESSERUNG + auffällige Sonderfälle)

### #17 — Gary Numan, „Your Fascination"

ALT-LRC (Auszug, mit Original-Zeitstempeln):

```
[00:40.46] And I'll fall down
[01:28.83] I don't need it
[01:30.84] If you're gonna leave then do it soon
[01:37.61] I don't need it
...
[02:05.08] I don't suppose you ever laughed at me
[02:11.31] Your fascination leaves me cold
[02:23.07] I don't suppose you ever loved at all
[03:06.85] I don't need it
[03:15.46] I don't need it
[04:08.93] If you want it
```

Auffällig: riesige Zeitlücken ohne jede Textzeile (00:40 → 01:28, 02:23 → 03:06,
03:15 → 04:08 bei einem >4-minütigen Song) — ALT ist erkennbar eine **unvollständige**
LRC-Datei, der eine ganze Strophe fehlt.

NEU-LRC enthält an der entsprechenden Stelle zusätzlich:

```
No more features
For now, now, now
No more features
For now, now, now
I don't suppose you ever laughed at me
```

Whisper-Transkript (Auszug): *„…don't fall down no more pictures no no no no more
pictures for now for now for now i don't suppose you ever left me your fascination
lays me…"*

Das Transkript bestätigt exakt die in ALT fehlende Strophe („no more pictures" ≈
„No more features", „for now for now for now" wortgleich). NEU ist also nicht nur
formatierungs-, sondern **inhaltlich vollständiger und korrekter** — Recall gegen
Transkript steigt von 0.556 (ALT) auf 0.611 (NEU). Kein Songwechsel (beide sind
zweifelsfrei „Your Fascination"), aber eine echte Qualitätsverbesserung des Kandidaten.

### #66 — Julio Iglesias, „Un Canto a Galicia"

ALT (spanische Übersetzung):

```
Yo te quiero tanto
Y aun no lo sabes
Yo te quiero tanto
Tierra de mi padre
Quiero tus riberas
Que hacen recordar
Y tus ojos tristes
Que hacen llorar
```

NEU (galicischer Originaltext):

```
Eu queroche tanto,
E ainda non o sabes...
Eu queroche tanto,
Terra do meu pai.
Quero as tuas ribeiras
Que me fan lembrare
Os teus ollos tristes
Que me fan chorare.
```

Whisper-Transkript (Auszug): *„el queroche tanto el queroche tanto e ainda no no
sabes el queroche tanto terrado me ufa e quiero as tu asriveira que me fa lembare
os te uso y os triste dejanme chorare…"*

Das Transkript folgt hörbar der galicischen Aussprache/dem galicischen Text
(„queroche", „terrado me [ufa]" ≈ „terra do meu pai") — nicht der spanischen
Übersetzung in ALT. Der numerische Recall ist für beide Seiten niedrig (ALT 0.223,
NEU 0.234, kaum Unterschied), weil Whisper das Galicische durchgehend eigenwillig
verschriftet (auch die NEU-Wörter werden nicht 1:1 getroffen) — das Recall-Signal
ist hier also **nicht zuverlässig**, deshalb war die manuelle Textlektüre nötig. Der
qualitative Befund ist trotzdem eindeutig: NEU trifft die tatsächlich gesungene
Sprachversion, ALT ist eine (im Prinzip korrekte, aber sprachlich abweichende)
Übersetzung desselben Songs. Eingestuft als Verbesserung, nicht als reiner Songwechsel.

### #84 — Kraftwerk, „Radioaktivität" (Sonderfall, kein Fehler)

ALT und NEU sind textlich identisch (englische + deutsche Strophen), NEU enthält
zusätzlich eine Morsecode-Zeile (`.-. .- -.. .. --- .- -.-. - .. ...- .. - -.--` =
„RADIOACTIVITY" in Morse), die tatsächlicher Bestandteil des Original-Songs „Radio-
Activity" von Kraftwerk ist — kein Fehltext. Das Cache-Transkript für diesen Track
liefert für ALT wie NEU einen identisch niedrigen Recall (0.31) und enthält teils
unplausible Fragmente (u. a. japanisch anmutende Silben) — vermutlich eine
Whisper-Fehlleistung auf dem stark vocodierten/robotischen Kraftwerk-Gesang. Das
Recall-Signal ist hier nicht aussagekräftig, aber der direkte ALT/NEU-Vergleich zeigt
zweifelsfrei denselben Song — daher HARMLOS.

## Fazit

Von den 86 „sonstigen" Datei-Änderungen (Ja/Nein-Entscheidung unverändert, aber
anderer gewählter Kandidatentext) sind **84 nachweislich harmlos** — derselbe Song,
lediglich andere Formatierung, Kopfzeilen (Genius „N Contributors"-Zeilen), Encoding
oder minimale Transkriptions-Varianten desselben Textes. Bei **64 dieser 84** Fälle
ist das bereits am nahezu identischen Wortbestand (Jaccard ≥ 0.85) und/oder am
praktisch gleichen Transkript-Recall beider Kandidaten (Differenz ≤ 0.03) klar
ersichtlich; die übrigen 20 wurden per direktem Textvergleich verifiziert.

**Zwei Fälle sind Verbesserungen, keine Regressionen:** Bei Gary Numan „Your
Fascination" war die ALT-Datei nachweislich unvollständig (eine ganze, vom Transkript
bestätigte Strophe fehlte), NEU ergänzt sie korrekt. Bei Julio Iglesias „Un Canto a
Galicia" wählt NEU die tatsächlich gesungene galicische Sprachversion statt der
spanischen Übersetzung in ALT. In beiden Fällen handelt es sich nicht um einen
Songwechsel (falscher vs. richtiger Song), sondern um eine echte inhaltliche
Verbesserung des gewählten Kandidaten für denselben Song.

**Keine einzige Regression gefunden.** Anders als bei den 33 Whisper-Uneinigkeiten
(dort ging es um echte Falsch-Song-Risiken bei Titelkollisionen) betreffen diese 86
Fälle ausschliesslich Konsens-Kandidaten desselben, bereits korrekt identifizierten
Songs — die globale statt sprachspezifische IDF-Tabelle wirkt sich hier erkennbar
nur auf **welche Quelle/Formatierung** gewählt wird, nicht auf **welcher Song**
gewählt wird. Die Umstellung auf die globale kontrastive IDF-Tabelle ist für diese
86 Fälle also unbedenklich.
