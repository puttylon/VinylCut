# Bigramm-Jaccard als Ersatz für IDF-Jaccard in der kontrastiven Marge — Testergebnis

Rein lesende Analyse gegen `fetch_songtext_cache.db` (Stand wie im Repo). Keine
Änderung an `fetch_songtext.py`, keiner Cache-DB, keinen `.lrc`-Dateien.

## 1. Methodik

Neue Test-Scoring-Funktion (nur im Test-Skript, ungewichteter Jaccard auf
überlappenden 2-Wort-Tupeln):

```python
def bigram_jaccard(words_a, words_b):
    def bigrams(words):
        return set(zip(words, words[1:]))
    ba, bb = bigrams(words_a), bigrams(words_b)
    if not ba or not bb:
        return 0.0
    return len(ba & bb) / len(ba | bb)
```

Tokenisierung identisch zum Original: `_extract_lrc_words()` für Kandidatentexte,
`.split()` für das (bereits normalisierte) Whisper-Transkript aus der Cache-DB.

Der Hintergrund-Pool (Sprache, K=20, Seed) wurde exakt wie im Original gezogen:
`ft._contrastive_lang_pools`, `ft._CONTRASTIVE_SEED`, `ft._CONTRASTIVE_BACKGROUND_K`,
`ft._CONTRASTIVE_MIN_BACKGROUND`, `rng = random.Random(f"{SEED}:{lang}:{song_id}")`.
Für jeden Hintergrund-Song wurde `ft._song_candidate_words(song_id)` verwendet
und der MAX-Bigramm-Score über dessen Kandidatentexte gebildet (analog zum
Original in `_contrastive_margin_and_decision`).

**Wichtige Methodik-Korrektur gegenüber der Aufgabenstellung:** Die Sprache
eines Songs wurde NICHT aus der Mitgliedschaft in `_contrastive_lang_pools`
abgeleitet (das sagt nur, ob ein Song als HINTERGRUND für andere in Frage
kommt, nicht welche Sprache sein EIGENER Kandidat hat), sondern jeweils frisch
per `_detect_lrc_language()` auf dem eigenen Kandidatentext bestimmt — exakt
wie es die Produktionslogik in `_whisper_best()` für den aktuell verarbeiteten
Song tut.

**Testkorpus A** (Breite Stichprobe): Alle Songs mit Whisper-Transkript in
`transkripte` UND mindestens einem Provider-Treffer in `ergebnisse`
(`status='treffer'`) — das ist dieselbe Grundgesamtheit wie
`_contrastive_song_texts`. **696 Songs** erfüllen beide Bedingungen (von 3258
Cache-Songs mit Provider-Treffer insgesamt — nur ein Teil davon hat auch ein
Whisper-Transkript, weil Whisper nur für Bibliotheks-Dateien läuft, die
tatsächlich noch vorhanden sind). Ergebnis: `bigram_jaccard_log.csv`
(696 Zeilen, Spalten `song_id, artist_key, titel_key, sprache,
best_score_bigram, max_hintergrund_bigram, margin_bigram, n_own_candidates`;
war nur Zwischenstand für Abschnitt 2 unten, nach Abschluss der Analyse
aufgeräumt).

**Bekannte Ground-Truth-Fälle:** Alle 33 Fälle aus `contrastive_run_vergleich.md`
und alle 86 Fälle aus `contrastive_reselection_check.md` wurden per
Artist/Titel-Lookup in der DB aufgelöst (`songs.artist_key`/`titel_key`,
Normalisierung wie `cache_store.normalize_key` plus Fuzzy-Matching für
Encoding-/Formatierungsabweichungen). Für 14 Fälle ohne Provider-Treffer
(Hannes Wader „Alle Hügel", Hannes Wittmer, 11× JETZT!, Jochen Distelmeyer)
wurde die passende `lrc_backup/…`-Datei als einziger eigener Kandidat
verwendet (Pfade jeweils per `find` verifiziert). Alle 33+86 = 119 Fälle
konnten aufgelöst werden; 2 davon (Ja, Panik „Alles hin, hin, hin", Kraftwerk
„Radioaktivität") haben keinen von `langdetect` erkannten Sprachtag — bei
diesen liefert `_detect_lrc_language` `None` (zu kurzer/uneindeutiger Text),
identisch zum Verhalten der Produktionslogik, die dann auf die alte absolute
Schwelle zurückfallen würde. Diese 2 sind daher „nicht auswertbar" markiert.

## 2. Verteilung von `margin_bigram` in Testkorpus A (671 von 696 Songs mit Hintergrund-Pool)

| Sprache | n | Min | Q1 | Median | Q3 | Max | Anteil margin < 0 |
|---|---|---|---|---|---|---|---|
| en | 478 | −0.041 | 0.212 | 0.456 | 0.635 | 0.960 | 7.1 % |
| de | 174 | −0.014 | 0.291 | 0.394 | 0.498 | 0.703 | 4.6 % |
| es | 11 | −0.004 | 0.308 | 0.499 | 0.600 | 0.713 | 9.1 % |
| fr | 4 | −0.008 | 0.066 | 0.341 | 0.395 | 0.395 | 25.0 % |
| id | 3 | 0.000 | – | 0.000 | – | 0.010 | 0.0 % |
| it | 1 | 0.203 | – | 0.203 | – | 0.203 | 0.0 % |
| **alle** | **671** | **−0.041** | **0.238** | **0.424** | **0.589** | **0.960** | **6.6 %** |

Auffällig: Die Verteilung ist **massiv nach rechts verschoben** verglichen mit
der alten IDF-Marge (die meist im Bereich ±0.05 lag) — Median 0.42 statt
typischerweise nahe 0. Das ist erwartungsgemäß: Bei einem echten Treffer teilen
Transkript und Kandidat lange exakte Wortfolgen (viele Bigramme), während zwei
zufällige Songs praktisch nie dieselbe 2-Wort-Reihenfolge produzieren — der
Hintergrund-Max liegt fast immer nahe 0 (typisch 0.01–0.05). Für die **breite,
unproblematische Masse der Songs** trennt Bigramm-Jaccard also sehr klar.
Das Bild kippt jedoch bei den unten analysierten **Grenzfällen** (Abschnitt 3–4).

## 3. Bekannte Ground-Truth-Fälle: Trennt `margin_bigram > 0` richtig von falsch?

### 3.1 Die 33 Whisper-Uneinigkeiten (`contrastive_run_vergleich.md`)

Alt-Kategorie-Legende: RICHTIG-ablehnung = altes Verfahren lag richtig mit
Ablehnung (Ground Truth: anderer Song) · FALSCH-ablehnung = altes Verfahren
lag falsch, Ground Truth ist eigentlich derselbe Song (sollte akzeptiert
werden) · RICHTIG-annahme = richtig akzeptiert.

| # | Artist | Titel | Sprache | old_marge (IDF) | Kategorie (alt) | best_bigram | max_hg_bigram | margin_bigram | neue Metrik richtig? |
|---|---|---|---|---|---|---|---|---|---|
| 0 | Garth Brooks | White Christmas | en | -0.0162 | FALSCH-ablehnung | 0.7619 | 0.8000 | -0.0381 | **NEIN** |
| 1 | Glenn Miller Orch. | At Last | en | 0.0033 | RICHTIG-ablehnung | 0.0062 | 0.0274 | -0.0212 | ja |
| 2 | Hannes Wader | Nach Hamburg | de | -0.0403 | RICHTIG-ablehnung | 0.0059 | 0.0110 | -0.0051 | ja |
| 3 | Hannes Wader | Alle Hügel | de | -0.0194 | RICHTIG-ablehnung | 0.0000 | 0.0108 | -0.0108 | ja |
| 4 | Hannes Wader | Gute Nacht | de | -0.0034 | RICHTIG-ablehnung | 0.0068 | 0.0118 | -0.0050 | ja |
| 5 | Hannes Wader | Nach Hamburg (Dup.) | de | -0.0403 | RICHTIG-ablehnung | 0.0059 | 0.0110 | -0.0051 | ja |
| 6 | Hannes Wittmer | Das Ende der Geschichte | de | 0.0024 | RICHTIG-ablehnung | 0.0195 | 0.0143 | 0.0052 | **NEIN** |
| 7 | Heino | Die Sonne Von Mexico | de | 0.0206 | RICHTIG-annahme | 0.0000 | 0.0060 | -0.0060 | **NEIN** |
| 8 | Hercules and Love Affair | Hercules Theme | en | -0.0075 | FALSCH-ablehnung (Vorbehalt) | 0.0211 | 0.0333 | -0.0123 | **NEIN** |
| 9 | Hope | Away | en | 0.0056 | RICHTIG-ablehnung | 0.0214 | 0.0219 | -0.0005 | ja |
| 10 | Hope | Prepared To Fly | en | -0.0252 | RICHTIG-ablehnung | 0.0062 | 0.0245 | -0.0183 | ja |
| 11 | Hope | Hope Is Alive | en | -0.0482 | RICHTIG-ablehnung | 0.0286 | 0.0517 | -0.0231 | ja |
| 12 | Hope | The End | en | -0.0471 | RICHTIG-ablehnung | 0.0316 | 0.0501 | -0.0185 | ja |
| 13 | JETZT! | Vielleicht-Menschen | de | 0.0001 | RICHTIG-ablehnung | 0.0027 | 0.0116 | -0.0089 | ja |
| 14 | JETZT! | Herbst In Berlin | de | -0.0106 | RICHTIG-ablehnung | 0.0128 | 0.0250 | -0.0122 | ja |
| 15 | JETZT! | Du Bist Nicht Allein | de | -0.0800 | RICHTIG-ablehnung | 0.0230 | 0.0175 | 0.0056 | **NEIN** |
| 16 | JETZT! | Kommst Du Mit In Den Alltag? | de | -0.0137 | RICHTIG-ablehnung | 0.0060 | 0.0201 | -0.0142 | ja |
| 17 | JETZT! | Warum | de | -0.0121 | RICHTIG-ablehnung (Ground Truth) | 0.0037 | 0.0135 | -0.0097 | ja |
| 18 | JETZT! | Acht Stunden Sind Kein Tag | de | -0.0084 | RICHTIG-ablehnung | 0.0129 | 0.0214 | -0.0085 | ja |
| 19 | JETZT! | Unsere Wilden Jahre | de | -0.0022 | RICHTIG-ablehnung | 0.0000 | 0.0093 | -0.0093 | ja |
| 20 | JETZT! | Winterschlaf | de | -0.0303 | RICHTIG-ablehnung | 0.0081 | 0.0177 | -0.0096 | ja |
| 21 | JETZT! | So sieht es aus, wenn das Herz bricht | de | -0.0167 | RICHTIG-ablehnung | 0.0075 | 0.0189 | -0.0115 | ja |
| 22 | JETZT! | Die Zeit | de | -0.0191 | RICHTIG-ablehnung (Ground Truth) | 0.0087 | 0.0171 | -0.0083 | ja |
| 23 | JETZT! | Traurigkeit | de | -0.0141 | RICHTIG-ablehnung | 0.0034 | 0.0118 | -0.0084 | ja |
| 24 | JETZT! | Die Welt wird größer, wenn wir sie teilen | de | -0.0024 | RICHTIG-ablehnung | 0.0083 | 0.0163 | -0.0079 | ja |
| 25 | JETZT! | Red' mit mir | de | -0.0516 | RICHTIG-ablehnung | 0.0144 | 0.0201 | -0.0057 | ja |
| 26 | JETZT! | Was man Heimat nennt | de | -0.0059 | RICHTIG-ablehnung | 0.0276 | 0.0229 | 0.0047 | **NEIN** |
| 27 | Jochen Distelmeyer | Manchmal | de | -0.0135 | RICHTIG-ablehnung | 0.0179 | 0.0200 | -0.0021 | ja |
| 28 | Joco | Your Gun | en | -0.0231 | RICHTIG-ablehnung | 0.0195 | 0.0407 | -0.0212 | ja |
| 29 | Joco | Winter | de | -0.0329 | RICHTIG-ablehnung | 0.0031 | 0.0175 | -0.0144 | ja |
| 30 | John Legend | Dancing In The Dark | en | -0.0404 | RICHTIG-ablehnung | 0.0285 | 0.0394 | -0.0109 | ja |
| 31 | Julio Iglesias | Sono 10 | es | -0.0558 | RICHTIG-ablehnung | 0.0183 | 0.0224 | -0.0041 | ja |
| 32 | Kraftwerk | Uran | en | 0.0331 | RICHTIG-annahme | 0.0238 | 0.0000 | 0.0238 | ja |

**Ergebnis: 27 von 33 richtig (81.8 %) bei Schwelle `margin_bigram > 0`.**
Das alte IDF-Marge-Verfahren lag bei genau diesen 33 Fällen bei **31 von 33
richtig (93.9 %)** — Bigramm-Jaccard schneidet auf diesem harten Testset also
**schlechter** ab als die bestehende Methode, nicht besser.

Die 6 Fehlklassifikationen im Detail:
- **#0 Garth Brooks** — der Fall, der die ganze Untersuchung ausgelöst hat,
  bleibt **ungelöst** (siehe Abschnitt 4).
- **#8 Hercules and Love Affair** — bleibt wie vorher ungelöst (kein Fix,
  aber auch keine neue Verschlechterung, da dieser Fall ohnehin als „mit
  Vorbehalt" eingestuft war).
- **#7 Heino** — **neue Verschlechterung**: War beim alten Verfahren korrekt
  akzeptiert (`+0.0206`), kippt unter Bigramm auf `best_score_bigram = 0.0`
  (keine einzige gemeinsame 2-Wort-Folge zwischen Transkript und Kandidat!)
  und wird fälschlich abgelehnt.
- **#6 Hannes Wittmer, #15 JETZT! „Du Bist Nicht Allein", #26 JETZT! „Was man
  Heimat nennt"** — **drei neue falsche Annahmen (Regressionen)**: Diese
  Fälle waren beim alten Verfahren korrekt abgelehnt, kippen unter Bigramm
  knapp über 0 (margins zwischen +0.0047 und +0.0056) und würden neu
  fälschlich akzeptiert.

Wichtig zur Einordnung: Sortiert man alle 33 Fälle nach `margin_bigram`, liegen
die 4 „Ground-Truth-Accept"-Fälle (Garth Brooks −0.038, Hercules −0.012, Heino
−0.006, Kraftwerk +0.024) **verstreut mitten im Wertebereich der 29
"Ground-Truth-Reject"-Fälle** (−0.048 bis +0.006) — es gibt **keinen
Schwellwert**, der hier eine saubere Trennung ermöglichen würde. Das gilt auch
für den absoluten `best_score_bigram` (ohne Hintergrund-Abzug): Garth Brooks
sticht mit 0.76 klar heraus, aber Heino (0.0), Hercules (0.021) und Kraftwerk
(0.024) liegen ebenfalls mitten im Rauschband der 29 falschen Kandidaten
(0.0–0.032).

### 3.2 Die 86 Kandidaten-Reselektionen (`contrastive_reselection_check.md`)

Volle Tabelle (84 HARMLOS + 2 VERBESSERUNG — alle Ground Truth „sollte
akzeptiert werden", da kein Songwechsel, nur andere Formatierung/Quelle):

| # | Artist | Titel | Kategorie (alt) | best_bigram | max_hg_bigram | margin_bigram | neue Metrik richtig? |
|---|---|---|---|---|---|---|---|
| 1 | Flash & The Pan | Man In The Middle | HARMLOS | 0.5942 | 0.0209 | 0.5733 | ja |
| 2 | Flash & The Pan | Down Among The Dead Men | HARMLOS | 0.3742 | 0.0176 | 0.3566 | ja |
| 3 | Flash & The Pan | Restless | HARMLOS | 0.3750 | 0.0318 | 0.3432 | ja |
| 4 | Garth Brooks | This Ain't Tennessee | HARMLOS | 0.7644 | 0.0280 | 0.7364 | ja |
| 5 | Garth Brooks | Longneck Bottle | HARMLOS | 0.4589 | 0.0347 | 0.4243 | ja |
| 6 | Garth Brooks | A Friend to Me | HARMLOS | 0.7651 | 0.0470 | 0.7181 | ja |
| 7 | Tubeway Army | The Life Machine | HARMLOS | 0.6784 | 0.0214 | 0.6570 | ja |
| 8 | Tubeway Army | Something's In The House | HARMLOS | 0.7037 | 0.0350 | 0.6687 | ja |
| 9 | Tubeway Army | Zero Bars (Mr.Smith) | HARMLOS | 0.5667 | 0.0220 | 0.5447 | ja |
| 10 | Tubeway Army | You Don't Know Me | HARMLOS | 0.0707 | 0.0403 | 0.0304 | ja |
| 11 | Tubeway Army | Only A Downstat | HARMLOS | 0.3038 | 0.0179 | 0.2859 | ja |
| 12 | Gary Numan | Remind Me To Smile | HARMLOS | 0.2239 | 0.0359 | 0.1880 | ja |
| 13 | Gary Numan | The Joy Circuit | HARMLOS | 0.3977 | 0.0204 | 0.3773 | ja |
| 14 | Gary Numan | Exhibition | HARMLOS | 0.1394 | 0.0158 | 0.1236 | ja |
| 15 | Gary Numan | The Secret | HARMLOS | 0.1495 | 0.0264 | 0.1231 | ja |
| 16 | Gary Numan | She Cries | HARMLOS | 0.1690 | 0.0235 | 0.1455 | ja |
| 17 | Gary Numan | Your Fascination | VERBESSERUNG | 0.3355 | 0.0413 | 0.2942 | ja |
| 18 | Gary Numan | Creatures | HARMLOS | 0.1732 | 0.0218 | 0.1513 | ja |
| 19 | Gary Numan | Tricks | HARMLOS | 0.2174 | 0.0478 | 0.1696 | ja |
| 20 | Gary Numan | I Still Remember | HARMLOS | 0.4143 | 0.0264 | 0.3879 | ja |
| 21 | Gary Numan | Child With The Ghost | HARMLOS | 0.3228 | 0.0311 | 0.2916 | ja |
| 22 | Gary Numan | Prophecy | HARMLOS | 0.3959 | 0.0255 | 0.3705 | ja |
| 23 | Gary Numan | My Jesus | HARMLOS | 0.1130 | 0.0190 | 0.0941 | ja |
| 24 | Gary Numan | Everyday I Die | HARMLOS | 0.1871 | 0.0345 | 0.1526 | ja |
| 25 | Gary Numan | Are Friends Electric? | HARMLOS | 0.2146 | 0.0418 | 0.1728 | ja |
| 26 | Gary Numan | We Are the Lost | HARMLOS | 0.1972 | 0.0229 | 0.1742 | ja |
| 27 | Gary Numan | Pray For The Pain You Serve | HARMLOS | 0.6159 | 0.0365 | 0.5794 | ja |
| 28 | Gary Numan | My Breathing | HARMLOS | 0.2389 | 0.0362 | 0.2027 | ja |
| 29 | Gary Numan | You Walk In My Soul | HARMLOS | 0.3300 | 0.0455 | 0.2846 | ja |
| 30 | Gary Numan | Absolution | HARMLOS | 0.5766 | 0.0462 | 0.5303 | ja |
| 31 | Genesis | Illegal Alien | HARMLOS | 0.3787 | 0.0310 | 0.3477 | ja |
| 32 | George Benson | Star Of A Story (X) | HARMLOS | 0.7679 | 0.0429 | 0.7250 | ja |
| 33 | George Benson | Nature Boy | HARMLOS | 0.4775 | 0.0217 | 0.4558 | ja |
| 34 | George Benson | You Can Do It, Baby | HARMLOS | 0.4918 | 0.0350 | 0.4568 | ja |
| 35 | The Glenn Miller Orchestra | Cinderella (Stay In My Arms) | HARMLOS | 0.8169 | 0.0450 | 0.7719 | ja |
| 36 | Hannes Wader | Kokain | HARMLOS | 0.5053 | 0.0093 | 0.4960 | ja |
| 37 | Hannes Wader | Schon Morgen | HARMLOS | 0.4644 | 0.0113 | 0.4532 | ja |
| 38 | Heart | How Can I Refuse | HARMLOS | 0.5980 | 0.0222 | 0.5758 | ja |
| 39 | Heaven 17 | Height Of The Fighting (He-La-Hu) | HARMLOS | 0.2588 | 0.0192 | 0.2396 | ja |
| 40 | Heaven 17 | Trouble | HARMLOS | 0.6753 | 0.0455 | 0.6298 | ja |
| 41 | Heaven 17 | Come Live With Me | HARMLOS | 0.5827 | 0.0334 | 0.5493 | ja |
| 42 | Herbert Grönemeyer | Neuland | HARMLOS | 0.1935 | 0.0107 | 0.1829 | ja |
| 43 | Herbert Grönemeyer | Lache, Wenn Es Nicht Zum Weinen Reicht | HARMLOS | 0.2771 | 0.0115 | 0.2656 | ja |
| 44 | Herbert Grönemeyer | Blick Zurück | HARMLOS | 0.4339 | 0.0093 | 0.4246 | ja |
| 45 | Hercules and Love Affair | Blind | HARMLOS | 0.6424 | 0.0287 | 0.6137 | ja |
| 46 | Hot Chocolate | I Believe (In Love) | HARMLOS | 0.6132 | 0.0338 | 0.5795 | ja |
| 47 | I Am Kloot | A Strange Arrangement of Colour | HARMLOS | 0.7065 | 0.0240 | 0.6825 | ja |
| 48 | I Am Kloot | Cuckoo | HARMLOS | 0.3687 | 0.0203 | 0.3484 | ja |
| 49 | I Am Kloot | Sold as Seen | HARMLOS | 0.5938 | 0.0185 | 0.5752 | ja |
| 50 | I Am Kloot | Hold Back the Night | HARMLOS | 0.7214 | 0.0203 | 0.7011 | ja |
| 51 | I Am Kloot | Some Better Day | HARMLOS | 0.8750 | 0.0337 | 0.8413 | ja |
| 52 | I Am Kloot | Forgive Me These Reminders | HARMLOS | 0.6647 | 0.0295 | 0.6352 | ja |
| 53 | INXS | What You Need | HARMLOS | 0.2692 | 0.0325 | 0.2368 | ja |
| 54 | Ideal | Da Leg Ich Mich Doch Lieber Hin | HARMLOS | 0.2865 | 0.0185 | 0.2679 | ja |
| 55 | Ilona | Allo Allo | HARMLOS | 0.4064 | 0.0110 | 0.3953 | ja |
| 56 | Ilona | Retourner à l'école | HARMLOS | 0.4050 | 0.0116 | 0.3934 | ja |
| 57 | Iron & Wine | The Desert Babbler | HARMLOS | 0.4423 | 0.0261 | 0.4162 | ja |
| 58 | Ja, Panik | Alles hin, hin, hin | HARMLOS | 0.4839 | – | – | n/a (kein Sprachpool) |
| 59 | Ja, Panik | Nevermore | HARMLOS | 0.3183 | 0.0090 | 0.3093 | ja |
| 60 | Jimi Hendrix | Cross Town Traffic | HARMLOS | 0.4931 | 0.0395 | 0.4536 | ja |
| 61 | Joy Division | These Days | HARMLOS | 0.3750 | 0.0236 | 0.3514 | ja |
| 62 | Julio Iglesias | Goodbye Amore Mio | HARMLOS | 0.6867 | 0.0314 | 0.6553 | ja |
| 63 | Julio Iglesias | Wenn Ein Schiff Vorüberfährt (un Canto a Galicia) | HARMLOS | 0.2944 | 0.0108 | 0.2835 | ja |
| 64 | Julio Iglesias | Du in Deiner Welt (Rio Rebelde) | HARMLOS | 0.3073 | 0.0102 | 0.2970 | ja |
| 65 | Julio Iglesias | Komm Wieder Madonna | HARMLOS | 0.3057 | 0.0087 | 0.2970 | ja |
| 66 | Julio Iglesias | Un Canto a Galicia | VERBESSERUNG | 0.0711 | 0.0126 | 0.0585 | ja |
| 67 | Karat | Falscher Glanz | HARMLOS | 0.2582 | 0.0118 | 0.2464 | ja |
| 68 | Kenny Chesney | Never Gonna Feel Like That Again | HARMLOS | 0.5872 | 0.0343 | 0.5529 | ja |
| 69 | Kettcar | Money Left To Burn | HARMLOS | 0.4905 | 0.0122 | 0.4783 | ja |
| 70 | Kettcar | Agnostik für Anfänger | HARMLOS | 0.2167 | 0.0150 | 0.2017 | ja |
| 71 | Kettcar | Der Apokalyptische Reiter Und Das Besorgte Pferd | HARMLOS | 0.6292 | 0.0159 | 0.6133 | ja |
| 72 | Kettcar | Zurück Aus Ohlsdorf | HARMLOS | 0.6822 | 0.0243 | 0.6579 | ja |
| 73 | Kettcar | Wagenburg | HARMLOS | 0.3039 | 0.0114 | 0.2925 | ja |
| 74 | Kettcar | Sommer '89 (Er schnitt Löcher in den Zaun) | HARMLOS | 0.5323 | 0.0084 | 0.5239 | ja |
| 75 | Kettcar | Straßen unseres Viertels | HARMLOS | 0.4573 | 0.0137 | 0.4436 | ja |
| 76 | Kinks | Till The End Of The Day | HARMLOS | 0.4505 | 0.0319 | 0.4186 | ja |
| 77 | Kinks | Village Green Preservation Society | HARMLOS | 0.2412 | 0.0190 | 0.2222 | ja |
| 78 | Kool & The Gang | Straight Ahead | HARMLOS | 0.5669 | 0.0214 | 0.5455 | ja |
| 79 | Kool & The Gang | Misled | HARMLOS | 0.4106 | 0.0413 | 0.3692 | ja |
| 80 | Kraftklub | Kein Gott, kein Staat, nur Du (feat. Mia Morgan) | HARMLOS | 0.5612 | 0.0187 | 0.5425 | ja |
| 81 | Kraftklub | Der Zeit bist du egal | HARMLOS | 0.6756 | 0.0150 | 0.6606 | ja |
| 82 | Kraftklub | Leben ruinieren | HARMLOS | 0.5330 | 0.0148 | 0.5182 | ja |
| 83 | Kraftklub | Melancholie | HARMLOS | 0.4109 | 0.0160 | 0.3949 | ja |
| 84 | Kraftwerk | Radioaktivität | HARMLOS | 0.1169 | – | – | n/a (kein Sprachpool) |
| 85 | Kraftwerk | Antenne | HARMLOS | 0.2308 | 0.0186 | 0.2121 | ja |
| 86 | Karat | Jede Stunde | HARMLOS | 0.3544 | 0.0121 | 0.3423 | ja |

**Ergebnis: 84 von 84 auswertbaren Fällen richtig (100 %).** Das ist
erwartbar: Diese Fälle sind unstrittige Konsens-Treffer mit hohem
Wortüberlapp — genau das Szenario, in dem Bigramm-Jaccard sein Versprechen
einlöst (Kandidat und Transkript teilen lange exakte Wortfolgen, der
Hintergrund bleibt bei ~0.01–0.05). Diese Gruppe war aber nie das eigentliche
Problem; sie testet nicht die Trennschärfe bei echten Titelkollisionen.

## 4. Detailanalyse: Die beiden Fälle, die die Untersuchung motiviert haben

### 4.1 Garth Brooks, „White Christmas" (`song_id=214`)

- Alt (IDF-Jaccard): `best=0.890`, `max_hintergrund=0.906`, `marge=−0.0162` → abgelehnt (Bug).
- Neu (Bigramm-Jaccard): `best_score_bigram=0.762`, `max_hintergrund_bigram=0.800`,
  `margin_bigram=−0.0381` → **weiterhin abgelehnt**.

Bigramm-Jaccard löst diesen Fall **nicht**. Der Root Cause ist ein anderer als
angenommen: Es ist **kein zufälliger Vokabular-Zufallstreffer** zwischen zwei
inhaltlich verschiedenen Weihnachtsliedern. Der Hintergrund-Song, der den
hohen Score erzeugt (`song_id=15349`, Michael Bublé, `titel_key='christmas'`),
hat **vier gecachte Provider-Kandidatentexte**, darunter einen lrclib-Treffer,
der wortwörtlich **„I'm dreaming of a white Christmas…"** ist — also
tatsächlich derselbe Song „White Christmas", nur unter Bublés Cache-Eintrag
`(michael bublé, christmas)` fehlgematcht (die anderen drei Provider-Texte
für diesen Eintrag sind korrekt „Christmas (Baby Please Come Home)" bzw.
„Holly Jolly Christmas"). Der Hintergrund-Pool `_song_candidate_words()`
nimmt den MAX über alle Kandidatentexte eines Songs — trifft also
zwangsläufig genau diesen einen kontaminierten Kandidaten.

Mit anderen Worten: Der Bug liegt nicht in der Scoring-Metrik (IDF vs.
Bigramm), sondern in einer **Datenkontamination im Hintergrund-Pool selbst**
— ein falsch zugeordneter Provider-Text für einen ANDEREN Song im
Hintergrund-Sample, der zufällig tatsächlich (fast) derselbe Songtext ist.
Keine Ähnlichkeitsmetrik, die den Hintergrund-Song anhand seines besten
verfügbaren Kandidatentextes bewertet, kann das umgehen, solange der
Hintergrund-Pool selbst fehlerhafte Zuordnungen enthalten kann.

### 4.2 Hercules and Love Affair, „Hercules Theme" (`song_id=5445`)

- Alt (IDF-Jaccard): `best=0.066`, `max_hintergrund=0.074`, `marge=−0.0075` → abgelehnt (mit Vorbehalt als Fehler eingestuft, da 3 Quellen übereinstimmend „Little Boy Hercules" liefern, Whisper aber komplett anderen Text transkribierte — vermutlich Halluzination auf hallreichem Track).
- Neu (Bigramm-Jaccard): `best_score_bigram=0.0211`, `max_hintergrund_bigram=0.0333`,
  `margin_bigram=−0.0123` → **weiterhin abgelehnt**.

Auch hier keine Verbesserung — aber auch keine neue Verschlechterung, da
dieser Fall ohnehin unsicher war. Der `best_score_bigram` von nur 0.021 zeigt:
Selbst wenn „Little Boy Hercules" der korrekte Songtext ist, teilt das
Whisper-Transkript praktisch keine 2-Wort-Folgen damit — konsistent mit der
Hypothese, dass Whisper hier tatsächlich halluziniert hat und nicht bloß
ungenau transkribiert. Bigramm-Jaccard bringt für diesen Fall keine neue
Erkenntnis, bestätigt aber die bisherige Einschätzung.

## 5. Empfehlung

**Bigramm-Jaccard löst das Garth-Brooks-Problem nicht — und ist auf dem
harten 33-Fälle-Testset (dem eigentlich relevanten Benchmark für
Titelkollisionen) sogar schwächer als das bestehende IDF-Jaccard-Verfahren:
27/33 (81.8 %) korrekt gegenüber bisher 31/33 (93.9 %).**

Konkret:
- **Kein Fix für den auslösenden Fall:** Garth Brooks „White Christmas" bleibt
  mit Bigramm-Jaccard genauso fälschlich abgelehnt wie mit IDF-Jaccard. Der
  Grund ist keine generische Vokabular-Kollision (wie ursprünglich vermutet),
  sondern eine Datenkontamination im Hintergrund-Pool (ein Provider-Fehltreffer
  bei einem ANDEREN Song, der zufällig denselben Songtext liefert). Das ist
  ein Problem der **Pool-Zusammensetzung**, kein Problem der **Ähnlichkeitsmetrik**
  — Bigramme lösen es nicht, weil `max()` über die Kandidatentexte des
  Hintergrund-Songs weiterhin den kontaminierten Text findet.
- **3 neue Regressionen** unter den bisher korrekt abgelehnten 29 Fällen
  (Hannes Wittmer „Das Ende der Geschichte", JETZT! „Du Bist Nicht Allein",
  JETZT! „Was man Heimat nennt") — alle mit `margin_bigram` knapp über 0
  (+0.005 bis +0.006). Bei so kurzen, teils Backup-only Kandidatentexten
  ohne Provider-Treffer ist die absolute Bigramm-Anzahl klein, wodurch der
  Quotient sehr rauschanfällig wird — ein einzelnes zufälliges 2-Wort-Match
  reicht, um die Marge über 0 zu drücken.
- **1 neue Verschlechterung** bei einem bisher korrekt akzeptierten Fall
  (Heino „Die Sonne Von Mexico"): `best_score_bigram = 0.0`, obwohl der
  Kandidat inhaltlich korrekt ist — vermutlich weil Whisper hier fälschlich
  auf Englisch transkribierte und dadurch keine einzige exakte 2-Wort-Folge
  mit dem spanischen/deutschen Original-Kandidatentext mehr teilt, obwohl
  einzelne (IDF-gewichtete) Wörter noch matchen.
- **Für die einfachen Fälle funktioniert es hervorragend:** In Testkorpus A
  (696 Songs, breite Stichprobe) und bei den 86 Kandidaten-Reselektionen
  trennt Bigramm-Jaccard nahezu perfekt (Median-Marge 0.42, praktisch alle
  echten Treffer weit über 0, Hintergrund fast immer unter 0.05). Das
  bestätigt: Für **eindeutige** Fälle ist die ursprüngliche Intuition (2-Wort-
  Kollisionen sind viel seltener als 1-Wort-Kollisionen) grundsätzlich richtig.
- **Das Problem sitzt genau in den harten Grenzfällen** — dort, wo die
  Whisper-Transkription selbst unvollständig, fehlerhaft oder
  sprachlich abweichend ist. Genau in diesen Fällen ist die absolute Anzahl
  gemeinsamer Bigramme so klein (oft einstellig), dass der Quotient extrem
  rauschempfindlich wird — ein einziges zufälliges 2-Wort-Match kippt das
  Ergebnis. Das ist bei IDF-gewichtetem Einzelwort-Jaccard mit mehr
  „Messpunkten" (mehr Wörter als Bigramme bei kurzen Texten) etwas robuster,
  wie das bessere 93.9-%-Ergebnis zeigt.

**Fazit, ohne Beschönigung:** Der getestete Ansatz funktioniert nicht besser
als das bestehende Verfahren — er ist auf dem einzigen Testset, das die
eigentlichen Titelkollisionsfälle abbildet, sogar schlechter. Weder ein
einfacher Schwellwert `margin_bigram > 0` noch ein anderer naheliegender
Cutoff trennt die 4 Ground-Truth-Accept-Fälle sauber von den 29
Ground-Truth-Reject-Fällen — die Werte liegen ineinander verschachtelt. Der
Garth-Brooks-Bug hat eine andere Ursache (Datenkontamination im
Hintergrund-Pool durch einen Provider-Fehltreffer bei einem fremden Song) als
die ursprüngliche Hypothese (generische Vokabular-Kollision) annahm — eine
Lösung müsste dort ansetzen (z. B. Hintergrund-Kandidaten stärker
validieren/deduplizieren, oder die kontrastive Marge grundsätzlich anders
konstruieren), nicht bei der Wahl der Ähnlichkeitsmetrik.

## Dateien

- `bigram_jaccard_log.csv` — Testkorpus A, 696 Songs (671 mit Hintergrund-Pool). War nur Zwischenstand für diese Auswertung, nach Abschluss der Analyse aufgeräumt (Ergebnisse vollständig in Abschnitt 2 oben).
- Dieser Bericht: `bigram_jaccard_test_ergebnis.md`.
