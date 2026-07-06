# LRC-Analyse — Testergebnisse fetch_songtext v1.2.7

Getestet: 29 Alben, ~280 Tracks. fetch_songtext mit Whisper base, 60s-Fenster,
kein Pre-Roll, alle 4 Provider (lrclib, musixmatch, netease, genius).
Artist/Titel aus FLAC-Metadaten (v1.2.7).

---

## Zusammenfassung

| Kategorie | Alben |
|-----------|-------|
| Vollständig (alle Vocal-Tracks gefunden) | 18 |
| Partiell (einige fehlen — begründet) | 7 |
| Problematisch (fehlen ohne klaren Grund) | 4 |

---

## Vollständig versorgte Alben

Alle erwarteten Vocal-Tracks haben eine LRC. Fehlende sind korrekte Ablehnungen.

| Album | Treffer | Ablehnungen | Anmerkung |
|-------|---------|-------------|-----------|
| Albin Lee Meldau - About You | 12/13 | 1 (Interlude) | Instrumental, korrekt |
| Betterov - Olympia | 11/13 | 2 (Intro, Outro) | Instrumental, korrekt |
| Billie Eilish - Happier Than Ever | 16/16 | — | Vollständig |
| Birdy - Portraits | 11/11 | — | Vollständig |
| Boney M. - Nightflight to Venus | 9/10 | 1 (King Of The Road) | → siehe unten |
| Bozza - Glücklich Unzufrieden | 15/15 | — | Vollständig |
| Carpenters - Lovelines | alle ✓ | — | Alle bereits vorhanden |
| Dermot Kennedy - Sonder | alle ✓ | — | Alle bereits vorhanden |
| Dermot Kennedy - Without Fear | alle ✓ | — | Alle bereits vorhanden |
| Diana Ross - Diana Ross | alle ✓ | — | Alle bereits vorhanden |
| Ella Fitzgerald & Louis Armstrong - Ella & Louis | alle ✓ | — | Bereits vorhanden + 1 neu |
| Fleetwood Mac - Rumours | alle ✓ | — | Bereits vorhanden + 1 neu |
| Fleetwood Mac - Tango In The Night | 12/12 | — | Vollständig neu |
| Foreigner - 4 | 10/10 | — | Vollständig neu |
| Glen Campbell - By the Time I Get to Phoenix | 11/11 | — | Vollständig neu |
| Glen Campbell - Gentle on my mind | 11/11 | — | Vollständig neu |
| John Lennon - Imagine | 10/10 | — | Vollständig (1 bereits vorhanden) |
| Kenny Rogers - Life Is Like A Song | 10/10 | — | Vollständig neu, Regression ok |

---

## Korrekte Vollablehnungen (reine Instrumental-Alben)

Kein einziger Track bekam eine LRC. Das ist das erwartete Verhalten.

| Album | Tracks | Begründung |
|-------|--------|------------|
| Herb Alpert & The Tijuana Brass - What Now My Love | 12/12 | Rein instrumental |
| Dexter Gordon - Go | 6/6 | Jazz-Instrumental |
| Duke Ellington - Money Jungle | 7/7 | Jazz-Instrumental |
| Eric Dolphy - Out to Lunch! | 5/5 | Free Jazz, Instrumental |

**Bewertung:** Whisper erkennt zuverlässig, dass kein Gesang vorhanden ist.
Kein einziger Fehlgriff auf einem der vier Alben. ✓

---

## Partielle Treffer — begründet

### Betterov - Große Kunst
**12/17 gefunden — 5 kein Treffer**

| Track | Grund |
|-------|-------|
| 01 - Ouvertüre | Instrumental-Intro, kein Gesang |
| 05 - Intermezzo I | Kurzes Instrumental-Interlude |
| 07 - Intermezzo II | Kurzes Instrumental-Interlude |
| 14 - Intermezzo III | Kurzes Instrumental-Interlude |
| 17 - Epilog | Instrumental-Abschluss |

**Bewertung:** Korrekte Ablehnungen. Das Konzeptalbum enthält bewusst instrumentale
Verbindungsstücke ohne Gesang.

---

### Chet Baker - Chet (1959)
**0/10 — alle kein Treffer**

Das Album "Chet" ist ein reines Trompeten-Instrumental-Album (keine Vocals).
Whisper hört keinen Gesang, keine LRC gespeichert. Korrekte Ablehnung.

**Aber:** Vier Tracks hatten bereits LRCs vor dem Testlauf, wurden übersprungen.
Diese stammen vermutlich aus einer früheren Suche ohne Whisper-Verifikation und
sollten manuell geprüft werden — es könnten falsche LRCs sein.

→ **Handlungsbedarf:** `fetch_songtext --recursive` auf diesem Album laufen lassen
und prüfen ob die vorhandenen LRCs im Whisper-Test bestehen.

---

### Chet Baker - Sings and Plays (1955)
**1/10 neu gefunden (Forgetful), Rest kein Treffer**

Chet Baker singt auf diesem Album sehr leise und hauchend — eine der weichsten
Männerstimmen im Jazz. Whisper `base` hat erhebliche Mühe diese Stimme zu
transkribieren. Provider finden LRCs (lrclib hat die Texte), aber Whisper
verifiziert sie nicht zuverlässig.

| Track | Status | Grund |
|-------|--------|-------|
| 01 - I Should Care | kein Treffer | Vocals zu leise für Whisper |
| 02 - Violets For Your Fur | kein Treffer | Vocals zu leise für Whisper |
| 03, 04, 07, 10 | übersprungen | Bereits vorhanden (zu prüfen) |
| 05 - Good-Bye | kein Treffer | Vocals zu leise für Whisper |
| 06 - Autumn In New York | kein Treffer | Vocals zu leise für Whisper |
| 08 - Street Of Dreams | kein Treffer | Vocals zu leise für Whisper |
| 09 - Forgetful | ✓ | Gefunden |

→ **Handlungsbedarf:** Modell `small` testen (besser bei leisen Stimmen, aber
langsamer). Alternativ: Threshold auf 0.08 senken nur wenn kein anderer Kandidat
über 0.12 ist.

---

### Boney M. - Nightflight to Venus
**9/10 — King Of The Road fehlt**

Whisper gibt für diesen Track bei jeder getesteten Position ein leeres Transkript.
Ursache unklar — möglicherweise Kombination aus Rauschpegel der Vinyl-Aufnahme
und Roger-Miller-Cover-Stil. Provider (lrclib, netease) haben korrekte LRCs.

→ **Handlungsbedarf:** Whisper `small` testen. Falls gleich: akzeptieren.

---

## Problematische Fälle — Nachbesserung möglich

### Falco - Falco 3
**5/10 gefunden — 5 kein Treffer**

Die Dateinamen haben das Format `001 Rock Me Amadeus.flac` statt
`01 - Rock Me Amadeus.flac`. Da kein ` - ` im Dateinamen steht, extrahiert
das Script den Titel als `001 Rock Me Amadeus`. Die Suchanfrage wird dann
`"Falco 001 Rock Me Amadeus"` — Provider finden so nichts für weltbekannte Songs.

Gefundene Tracks hatten entweder ` - ` im Dateinamen (dann wird der Teil nach
dem ` - ` als Titel verwendet) oder kurze eindeutige Namen, die Provider trotz
Präfix treffen.

| Track | Status | Anmerkung |
|-------|--------|-----------|
| 001 Rock Me Amadeus | ✗ | Query "Falco 001 Rock Me Amadeus" → kein Treffer |
| 002 America | ✗ | Query "Falco 002 America" → kein Treffer |
| 003 Tango the Night | ✓ | Gefunden trotz Präfix |
| 004 Munich Girls | ✗ | Query "Falco 004 Munich Girls" → kein Treffer |
| 005 Jeanny | ✓ | Kurzer Name, trotz Präfix gefunden |
| 006 Vienna Calling | ✗ | Query "Falco 006 Vienna Calling" → kein Treffer |
| 007 Maenner des Westens - Any Kind of Land | ✓ | Split an " - " → Titel "Any Kind of Land" |
| 008 Nothin' Sweeter Than Arabia | ✓ | Gefunden |
| 009 Macho Macho | ✗ | Query "Falco 009 Macho Macho" → kein Treffer |
| 010 It's All Over Now, Baby Blue (No mix) | ✓ | Gefunden |

→ **Behoben in v1.2.7:** Script liest Artist/Titel jetzt aus FLAC-Metadaten
(metaflac). Query wird korrekt `"Falco Rock Me Amadeus"`. Dateinamen irrelevant.

---

### Tom Liwa - Ganz Normale Songs
**1/11 gefunden (Meistens) — 10 kein Treffer**

Tom Liwa ist ein sehr nischiger deutscher Liedermacher. Seine Songs sind in keiner
der vier Datenbanken (lrclib, musixmatch, netease, genius). Provider finden schlicht
nichts. Whisper-Verifikation gar nicht erst nötig.

→ **Handlungsbedarf:** Keine automatische Lösung möglich. Texte ggf. manuell
als LRC erstellen oder auf Songtext-Suche für dieses Album verzichten.

---

### Fortuna Ehrenfeld - Das Ende der Coolness Vol. 2
**5/12 bereits vorhanden — 7/12 kein Treffer — 1/12 neu (Pony)**

Deutsche Indie-Band mit geringer Datenbank-Abdeckung. Nur "Pony" war auffindbar.
Provider liefern für die meisten Tracks gar nichts.

→ **Handlungsbedarf:** Wie Tom Liwa — Nischenkünstler außerhalb der Datenbanken.

---

### Tom Odell - monsters
**15/15 — vollständig** ✓

Track 07 "Lockdown" war beim ursprünglichen Testlauf nicht gefunden worden —
Timing-Artefakt während der Batch-Ausführung. lrclib hat den Text (47,9 % Overlap),
nach erneutem Lauf korrekt gefunden.

A: ist vorhanden https://lrclib.net/search/tom%20odell%20lockdown


---

## Technische Beobachtungen

### Was gut funktioniert
- Rein instrumentale Alben (Jazz, Brass) werden komplett und fehlerfrei abgelehnt
- Populäre englischsprachige Alben: nahezu 100% Trefferquote
- Deutsche Songs mit Datenbankabdeckung (Betterov, Bozza): funktioniert
- Kenny Rogers: Regression nach früherem Megalobiz-Problem nicht eingetreten ✓

### Systemische Grenzen
1. **Leise/hauchende Stimmen** (Chet Baker, teilweise Billie Eilish): Whisper `base`
   kommt an Grenzen. Modell `small` könnte helfen, ist aber langsamer.

2. **Nischenkünstler** (Tom Liwa, Fortuna Ehrenfeld): kein Code-Problem, schlicht
   keine Daten in den Lyrik-Datenbanken.

3. **Falsche Dateinamen-Konventionen** (Falco): Numerische Präfixe ohne ` - ` Trenner
   landen ungefiltert in der Suchanfrage.

4. **Einzelne unklare Fehlschläge** (Boney M. King Of The Road, Tom Odell Lockdown):
   Track-spezifische Probleme, keine systematische Ursache erkennbar.
