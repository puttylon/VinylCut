# VinylCut — Zielarchitektur

Dieses Dokument beschreibt den geplanten Umbau der Anwendung.
Bitte kommentiere mit Ja/Nein/Ändern hinter den markierten Punkten [?].

---

## 1. Schichten-Modell

```
┌────────────────────────────────────────────────────────┐
│ SCHICHT 3 — Orchestrierung                             │
│   interactive_cutter.py                               │
│   main(), run_metadata_search(), Schneid-Loop,        │
│   Export, Songtext-Aufruf                             │
├────���────────────────────────────────────────���──────────┤
│ SCHICHT 2 — Terminal-UI  (alles Rich/tty hier)         │
│   cutter_ui.py  (neue Datei)                          │
│   build_cutting_panel()  → Panel   (pure, kein I/O)   │
│   build_metadata_panel() → Panel   (pure, kein I/O)   │
│   live_input(live, panel_fn, prompt) → str            │
├────────────────────────────────────────────��───────────┤
│ SCHICHT 1 — Daten & Logik  (kein Rich, kein Terminal)  │
│   parse_offset(), fmt_dur(), estimate_start()         │
│   cut_and_tag(), play_snippet*(), save_progress()     │
│   metadata_fetcher.py  (Discogs/MB API)               │
└─────────────────────────────────────────��──────────────┘
```

Regel: Schicht 3 darf Schicht 2 und 1 importieren.
       Schicht 2 darf Schicht 1 importieren.
       Schicht 1 importiert nichts aus diesem Projekt.

---

## 2. Alle Module — Ist → Soll

### `interactive_cutter.py`  [?]
- Jetzt: 671 Zeilen, alles gemischt (Panels, Input, Logik, API-Calls)
- Soll: ~350 Zeilen — nur noch Orchestrierung + Logik-Funktionen
  (parse_offset, fmt_dur, estimate_start, cut_and_tag, play_snippet*)
- Rich und tty werden vollständig nach cutter_ui.py ausgelagert

### `cutter_ui.py`  (neue Datei)  [?]
- Enthält: build_cutting_panel(), build_metadata_panel(), live_input()
- Keine API-Calls, kein Dateisystem, kein subprocess
- Einzige Rich/tty-Abhängigkeit im gesamten Projekt (außer preparer)

### `metadata_fetcher.py`  [?]
- Jetzt: 321 Zeilen, API + Daten-Transformation
- Soll: unverändert
- Anmerkung: search_musicbrainz() hat interne print()-Aufrufe die stören
  könnten — bleibt aber für jetzt so (kein Breaking Change)

### `preparer.py`  [?]
- Soll: unverändert
- Hat eigene Terminal-Interaktion (print/input) — kein Rich, kein Problem

### `songtext.py`  [?]
- Soll: unverändert

---

## 3. Tests — Ist → Soll

### `test_preparer.py`  [?]
- Soll: unverändert (25 Tests, laufen sauber)

### `test_interactive_cutter.py`  [?]
- Soll: bleibt, ggf. Import-Pfad anpassen wenn fmt_dur nach cutter_ui wandert

### `test_cutter_ui.py`  (neue Datei)  [?]
- Testet Panel-Builder ohne echtes Terminal
- Technik: Console(force_terminal=False) + console.capture()
  → prüft dass Panels ohne Exception rendern und Pflichtfelder enthalten
- Beispiel:
    panel = build_cutting_panel("Gary Numan", "Warriors", tracks, ...)
    console = Console(force_terminal=False)
    with console.capture() as cap:
        console.print(panel)
    assert "Gary Numan" in cap.get()

### `test_smoke.py`  (neue Datei)  [?]
- Startet das Programm als subprocess, prüft Exit-Code und Ausgabe
- Abgedeckt: --version, --help
- NICHT abgedeckt: echte Terminal-Interaktion (würde pexpect brauchen)
- pexpect: neue Abhängigkeit → erst recherchieren, nicht jetzt  [?]

---

## 4. Render/Input-Trennung

### Problem heute
_live_ask(live, renderable, prompt) macht beides:
  - rendert ein statisches Panel
  - liest zeichenweise von stdin (tty.setcbreak)
→ Ein Rich-Bug und ein Input-Bug sehen gleich aus.

### Lösung: live_input() in cutter_ui.py  [?]
```python
# Caller baut das Panel (reine Funktion, separat testbar):
panel = build_cutting_panel(artist, album, tracks, ...)

# live_input rendert Group(panel_fn(), Rule, eingabe-zeile) + liest zeichenweise:
action = live_input(live, lambda: panel, "> ")
```

live_input() Signatur:
  live_input(live: Live, panel_fn: Callable[[], RenderableType], prompt: str) -> str

panel_fn ist ein Callable (keine Argumente, gibt Renderable zurück).
Für statische Panels: lambda: panel
Für dynamische Panels (z.B. Live-Update während Suche): normale Funktion

---

## 5. Rich recherchieren  [?]

### Option A — Scratch-Skript (empfohlen)
- Einmaliges scratch/test_live.py das konkret prüft:
  Wo steht der Cursor nach live.refresh() mit screen=True?
- Du führst es aus, ich lese die Ausgabe (~15 Minuten, einmalig)
- Ergebnis: 2 S��tze in CLAUDE.md
- Scratch-Datei danach löschen

### Option B — Weglassen
- Was wir wissen steht bereits in CLAUDE.md Abschnitt 3
- Kein zusätzlicher Aufwand
- Risiko: nächste unbekannte Rich-Falle trifft uns unvorbereitet

---

## 6. Reihenfolge der Umsetzung  [?]

Schritt 1: cutter_ui.py anlegen — Panel-Builder + live_input() rein
Schritt 2: interactive_cutter.py aufräumen — importiert aus cutter_ui
Schritt 3: test_cutter_ui.py schreiben
Schritt 4: test_smoke.py schreiben
Schritt 5: alle Tests grün, manuell testen, commit

Alternativ alles in einem Commit wenn du das bevorzugst.  [?]

---

## 7. Was diese Architektur NICHT löst

- metadata_fetcher.py hat print()-Aufrufe die in einem Live-Kontext stören
  (würde saubere Callback-Architektur brauchen — YAGNI für jetzt)
- Keine E2E-Tests für echte Benutzerinteraktion (pexpect-Entscheidung ausstehend)
- preparer.py hat noch keine Rich-UI (separates Thema)
