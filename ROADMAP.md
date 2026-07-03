# VinylCut Roadmap

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

## Zurückgestellt
- **Android/Termux-Port** — auf unbestimmte Zeit verschoben.

---

# preparer.py — Roadmap

Werkzeug zur non-destruktiven Vorbereitung einer Roh-FLAC (alle Seiten in einer Datei) vor dem Schneiden mit `interactive_cutter.py`. Die Original-FLAC wird nie verändert. Alle Schnittdaten landen in `preparer.json`, Zwischenergebnisse in neuen Dateien.

## ✓ v0.1 — Stille-Erkennung
Nimmt eine FLAC, erkennt lange Stillepausen via `ffmpeg silencedetect`, gibt vorgeschlagene A/B-Punkte für jede Nahtstelle im Terminal aus. Noch keine Interaktion — nur prüfen ob die Erkennung brauchbare Ergebnisse liefert.

## ✓ v0.2 — Interaktives Grob-Beschneiden
Für jede Nahtstelle: Punkt A (Ende Musik Seite N) und Punkt B (Anfang Musik Seite N+1) per Playback interaktiv setzen und in `preparer.json` speichern. Fortschritt wird nach jeder Bestätigung gespeichert, Session kann fortgesetzt werden.

## ✓ v0.3 — Crossfade-Vorschau + Feinschneiden
Jeden Übergang abhören: temporärer Crossfade (8 s Fenster, 0,5 s Blende) wird on-the-fly generiert und abgespielt. A/B per Fokus-Modell ([a]/[b] + [+]/[-]) verschieben. Nutzer gibt Anzahl Seiten an — beste Kandidaten nach Stillelänge gewählt.

## ✓ v0.4 — Schneiden + Zusammenfügen
Ausgabe: `<Name>_prepared.flac` mit Crossfades an allen Nahtstellen. Original-FLAC bleibt unangetastet.

## ✓ v0.5 — Normalisierung + DC-Offset
DC-Offset (highpass 5 Hz) + Peak-Normalisierung auf -0,1 dBFS via sox. Optionaler Kanalausgleich nach Pegelmessung. Ergebnis in `<Name>_final.flac`.

## ✓ v0.6 — Polish
Tests für `get_segments`, ROADMAP aktualisiert.

## v1.0 — Stabile Version
Dokumentation nachgezogen, in Gesamtworkflow integriert.
