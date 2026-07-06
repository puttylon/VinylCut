# assemble.py — UI-Vorschläge

Ziel: Rich Live(screen=True) wie in cut.py. Gleiche Bibliothek, gleiche Schichtentrennung
(assemble_ui.py analog zu cut_ui.py). Drei Varianten zur Auswahl.

---

## Variante A — Maximale Ähnlichkeit zu cut.py  [ja, gewünscht]

Vollbild-Panel, identischer Aufbau: Tabelle oben, Info-Block unten, Eingabezeile ganz unten
via live_input(). Alle vier Phasen laufen im selben Panel-Container, nur der Inhalt wechselt.

### Phase 1 — Punkte setzen

```
╭──────────────────────── Artist - Album-raw · Vorbereitung ────────────────────────────╮
│  #   Beschreibung                       Position    Vorschlag    Δ                     │
│  ─────────────────────────────────────────────────────────────────────────────────     │
│  01  Trim Start · Anfang Seite A         0:03.20     0:03.20              ✓            │
│  02  Ende Seite A · Grenze A→B          18:45.10    18:44.50    +0.60s   ✓            │
│→ 03  Anfang Seite B · Grenze A→B        20:02.30    20:01.80    +0.50s   →            │
│  04  Ende Seite B · Grenze B→C         ~38:20.00    38:18.90             ○            │
│  05  Anfang Seite C · Grenze B→C       ~40:00.00    39:59.10             ○            │
│  06  Trim Ende · Ende Seite C          ~58:30.00    58:28.40             ○            │
│  ─────────────────────────────────────────────────────────────────────────────────     │
│  Schritt 03/06 · Anfang Seite B · Grenze A→B                                          │
│  Position: 20:02.30   Vorschlag: 20:01.80   Δ +0.50s                                  │
│  Normton: EIN                                                                          │
│                                                                                        │
│  [p] abspielen  [+/-] ±0.5s  [++/--] ±2s  [ok] bestätigen  [u] rückgängig  [n] Ton  │
╰────────────────────────────────────────────────────────────────────────────────────────╯
```

### Phase 2 — Crossfade-Vorschau

```
╭──────────────────────── Artist - Album-raw · Crossfade ───────────────────────────────╮
│  Grenze   Ende Seite      Anfang Seite     Lücke         Status                        │
│  ─────────────────────────────────────────────────────────────────────────────────     │
│  A → B    18:45.10        20:02.30         1:17.20       ✓                             │
│→ B → C    38:20.00        40:00.00         1:40.00       →   [B Anfang aktiv]          │
│  ─────────────────────────────────────────────────────────────────────────────────     │
│  Grenze B→C  (2/2)                                                                     │
│  Ende Seite B:    38:20.00                                                             │
│  Anfang Seite C:  40:00.00  ←  aktiv                                                   │
│  Herausgeschnitten: 1:40.00   Normton: EIN                                             │
│                                                                                        │
│  [a] Fokus Ende  [b] Fokus Anfang  [+/-] ±0.5s  [++/--] ±2s  [ok]  [u]  [n]         │
╰────────────────────────────────────────────────────────────────────────────────────────╯
```

### Phase 3 — Schneiden & Verbinden

```
╭──────────────────────── Artist - Album-raw · Export ──────────────────────────────────╮
│  #   Segment     Start       Ende        Dauer       Status                            │
│  ─────────────────────────────────────────────────────────────────────────────────     │
│  01  Seite A      0:03.20    18:45.10    18:41.90    ✓                                 │
│  02  Seite B     20:02.30    38:20.00    18:17.70    ✓                                 │
│→ 03  Seite C     40:00.00    58:28.40    18:28.40    …                                 │
│  ─────────────────────────────────────────────────────────────────────────────────     │
│  Schneide Segment 3/3 · dann verbinden mit 0.5s Crossfade                              │
╰────────────────────────────────────────────────────────────────────────────────────────╯
```

### Phase 4 — Normalisierung

```
╭──────────────────────── Artist - Album-raw · Normalisierung ──────────────────────────╮
│  Links:   -0.42 dBFS                                                                   │
│  Rechts:  -0.65 dBFS                                                                   │
│  Differenz: +0.23 dB                                                                   │
│  ─────────────────────────────────────────────────────────────────────────────────     │
│  DC-Offset entfernen + Peak-Normalisierung auf -0.1 dBFS                               │
│  Kanalausgleich anwenden? [j/n]:                                                       │
╰────────────────────────────────────────────────────────────────────────────────────────╯
```

**Aufwand:** mittel — assemble_ui.py anlegen, alle print/input ersetzen.
**Vorteil:** konsistentes Look & Feel mit cut.py, vollständig testbar via Console(force_terminal=False).

---

## Variante B — Kompakt mit Seitenüberblick oben  [?]

Feste Header-Zeile mit Seiten-Status, darunter der aktuelle Schritt. Weniger Tabellen-Zeilen,
dafür mehr Kontext pro Schritt. Gut wenn die Anzahl Seiten groß wird.

### Phase 1

```
╭──────────────────────── Artist - Album-raw · Vorbereitung ────────────────────────────╮
│  Seiten:  [A ✓] [B →] [C ○] [D ○]                                3 Grenzen · 8 Punkte │
│  ─────────────────────────────────────────────────────────────────────────────────     │
│                                                                                        │
│  Schritt 03/08 — Anfang Seite B (Grenze A→B)                                          │
│                                                                                        │
│  Vorschlag:   20:01.80                                                                 │
│  Aktuell:     20:02.30   Δ +0.50s                                                      │
│  Normton:     EIN                                                                      │
│                                                                                        │
│  [p] abspielen  [+/-] ±0.5s  [++/--] ±2s  [ok] bestätigen  [u] rückgängig  [n] Ton  │
╰────────────────────────────────────────────────────────────────────────────────────────╯
```

### Phase 2

```
╭──────────────────────── Artist - Album-raw · Crossfade B→C ───────────────────────────╮
│  Grenzen:  [A→B ✓] [B→C →] [C→D ○]                                         2/3 fertig │
│  ─────────────────────────────────────────────────────────────────────────────────     │
│                                                                                        │
│  Seite B endet:    38:20.00                                                            │
│  Seite C beginnt:  40:00.00  ←  aktiv                                                  │
│  Lücke:             1:40.00                                                            │
│  Normton:          EIN                                                                 │
│                                                                                        │
│  [a] Ende B  [b] Anfang C  [+/-] ±0.5s  [++/--] ±2s  [ok]  [u]  [n]                 │
╰────────────────────────────────────────────────────────────────────────────────────────╯
```

**Aufwand:** mittel — etwas einfacher als A, kein großes Tabellen-Rendering.
**Vorteil:** klarer Fokus auf den aktuellen Schritt, Seiten-Status als Chips.
**Nachteil:** Man sieht nicht alle Punkte auf einmal.

---

## Variante C — Minimaler Eingriff  [?]

Nur die zwei interaktiven Phasen (1 + 2) bekommen Rich Live-Panels.
Phase 3 + 4 bleiben print/input — dort ist keine Eingabe nötig außer dem Kanalausgleich.
Analyse (Stille-Erkennung) und Seitenanzahl-Frage bleiben print/input (laufen vor Live-Start).

```
# Startphase: wie bisher (print/input für Analyse + Seitenanzahl)
python3 assemble.py "Artist - Album-raw.flac"
> Analysiere...
> Erkannte Grenzen: 3 (= 4 Seiten)
> Wie viele Seiten hat die Vinyl? [4]:

# Dann: Live-Panel für Phase 1 (Punkte setzen) — wie Variante A
# Dann: Live-Panel für Phase 2 (Crossfade) — wie Variante A
# Dann: print() für Phase 3 + 4 (kein Vollbild mehr nötig)
```

**Aufwand:** gering — nur Phase 1+2 umprogrammieren.
**Vorteil:** schnell umsetzbar, geringes Risiko, bewährt durch cut.py.
**Nachteil:** inkonsistenter Übergang zwischen Rich-Fullscreen und normalem Terminal.

---

## Empfehlung  [Variante A]

**Variante A** wenn du einen vollständig konsistenten Look willst (wie cut.py).
**Variante C** wenn du schnell fertig sein möchtest und den Bruch Print→Rich akzeptierst.

Variante B ist ein Mittelweg, aber der Zusatzaufwand gegenüber A ist gering.

---

## Offene Fragen  [antworte ich inline]

1. Soll die Seitenanzahl-Frage ("Wie viele Seiten?") ins Panel oder bleibt sie davor? A: Dazu baust du ein Bild in Phase 0 (vor phase 1)
2. Phase 4 (Normalisierung): Kanalausgleich-Frage als live_input() im Panel, oder reicht print/input? A: verstehe den unterschied nicht. aus sicht des useres egal, oder?
3. assemble_ui.py als neues Modul (analog cut_ui.py), oder gemeinsames assemble_ui.py in cut_ui.py? 
   (Gemeinsam würde fmt_dur() teilen, aber die Panels sind sehr verschieden.) A: unterschiedlich
