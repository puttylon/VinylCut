# CLAUDE.md – Richtlinien für persönliche Python-Projekte

Dieses Dokument dient als Leitfaden für die KI-Unterstützung in diesem Repository. Da es sich um ein reines Soloprojekt handelt, stehen Pragmatismus, Lesbarkeit und schnelle Iteration im Vordergrund. Kein Over-Engineering.

## 1. Kernphilosophie
- **Pragmatismus vor Perfektion:** Schreibe sauberen, verständlichen Code, aber vermeide unnötige Abstraktionsschichten oder komplexe Design-Patterns.
- **YAGNI (You Aren't Gonna Need It):** Implementiere nur das, was akut angefordert ist. Keine Vorbereitungen für hypothetische zukünftige Features.
- **Direkte Lösungen:** Löse Probleme kompakt und effizient, nutze bevorzugt die Python-Standardbibliothek, es sei denn, externe Pakete bringen massiven Mehrwert.
- **Evidenz vor Vermutung:** Bei technischer Unsicherheit (Bibliotheksverhalten, Bug-Ursache, Performance-Annahmen) nicht raten oder aus dem Gedächtnis behaupten — nachweisen: Quellcode der Bibliothek lesen, live testen/messen, recherchieren. Keine Zahlen oder Verhaltensweisen als Fakt hinstellen, die nicht durch einen Beleg gedeckt sind. Bei mehreren plausiblen Lösungsansätzen: Optionen mit Trade-offs vorlegen statt eine Wahl vorwegzunehmen.
- **Token-Effizienz:** Tokens sind kein Fundgeld, das man einfach verbraucht — effizient einsetzen. Kein Wiederholen bereits bekannter Informationen, keine unnötig langen Recherchen/Dateilesungen, wenn ein gezielterer Weg reicht. Gilt für die KI-Arbeitsweise selbst, nicht für den produzierten Code.

## 2. Entwicklung & Testing
- **Testpflicht vor Commit:** Bevor du (die KI) Code-Änderungen als abgeschlossen meldest oder committest, musst du selbstständig die Tests lokal ausführen.
- **Sinnvolles Testen:** Fokussiere Unit-Tests auf Kernlogik und fehleranfällige Funktionen. Triviale Funktionen benötigen keine erzwungene Testabdeckung.
- **Wichtige Befehle:**
  - Virtuelle Umgebung: Standard `venv` (`source .venv/bin/activate` bzw. `.venv\Scripts\activate`)
  - Abhängigkeiten: `pip install -r requirements.txt`
  - Tests ausführen: `pytest`
  - Code prüfen/formatieren: `ruff check --fix` und `ruff format`
- **Dokumentation nachziehen** — Reihenfolge: **Roadmap → Code → Tests → Dokumentation → Commit**
    - Implementiertes Feature in ROADMAP.md als erledigt markieren
    - README.md auf aktuelle Flags, Befehle und Abhängigkeiten aktualisieren
	-- help auf dann aktuelle Funktionen und Änderung anpassen
	- jeder Bugfix erhöht die Versionsnummer x.y.n n um eins.
- **Parallele Claude-Code-Sessions:** Wenn absehbar mehrere Sessions gleichzeitig an diesem Repo arbeiten, jede Session per `git worktree` in ein eigenes Verzeichnis + Branch isolieren (ein Branch allein reicht nicht — Working Tree und Index sind pro Verzeichnis, nicht pro Branch). Für einzelne Sessions: kein Branch-Zwang, direkt auf `main`.
	

## 3. Bibliotheken & Terminal-Code

- **Recherche vor Integration:** Bevor eine neue Bibliothek oder ein neues Framework eingebaut wird, relevante Dokumentation und bekannte Fallstricke prüfen — insbesondere für Terminal-, UI- und I/O-Bibliotheken. Nicht blind draufloscodieren.
- **Scratch-Skript zuerst:** Neues Bibliotheks-Verhalten (z.B. `rich.Live`, `tty`, ANSI-Escapes) zuerst in einem isolierten Wegwerf-Skript verstehen und manuell testen. Erst danach in Produktionscode integrieren.
- **Terminal/UI-Änderungen brauchen manuellen Test:** `pytest` prüft keine Terminal-Ausgabe. Änderungen an `rich`, `tty`, `termios` oder ANSI-Escapes sind erst abgeschlossen, wenn der User sie im laufenden Programm bestätigt hat — nicht wenn die Tests grün sind.
- **Bekannte Rich-Fallen (screen=True):**
  - `tty.setcbreak()` verwenden, nie `tty.setraw()` — `setraw()` deaktiviert `OPOST`, `\n` wird nicht mehr zu `\r\n` übersetzt, Rich rendert leer.
  - `input()` und `console.input()` funktionieren nicht korrekt im `Live(screen=True)`-Kontext.
  - Zeichenweise Eingabe: `sys.stdin.buffer.read(1)` in einer `setcbreak`-Session, Echo manuell im Panel rendern.
- **UI-Bugfixes erhöhen ebenfalls x.y.N** — auch wenn `pytest` gar nichts davon mitbekommt.

## 4. Code-Stil & Qualität
- **Stil:** Halte dich an PEP 8 (Standard-Formatierung via Ruff). Nutze sprechende Variablen- und Funktionsnamen in `snake_case`.
- **Typisierung:** Verwende Type-Hints, um die Lesbarkeit und Wartbarkeit zu verbessern, aber erzwinge sie nicht dogmatisch bei trivialen Skripten.
- **Dokumentation:** Schreibe Docstrings und Kommentare nur für komplexe, nicht selbsterklärende Logik. Keine Zeit mit dem Dokumentieren von Offensichtlichkeiten verschwenden.
- **Fehlerbehandlung:** Fehler abfangen und sinnvoll loggen oder ins Terminal ausgeben. Keine leeren `except: pass`-Blöcke, es sei denn, es ist explizit begründet.

## 5. Dokumentations-Struktur
- **README.md** — Bedienung (Flags, Befehle, Abhängigkeiten).
- **ROADMAP.md** — Feature-Status + geplante Features; verlinkt die Design-Dokumente.
- **`*_DESIGN.md`** — Spezifikationen einzelner Module (z. B. `CACHE_DESIGN.md`).
- **CLAUDE.md** — Arbeitsregeln (dieses Dokument).