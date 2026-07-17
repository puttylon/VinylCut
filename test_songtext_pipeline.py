"""Tests für songtext_pipeline.py (Steuer-Skript).

Seit dem --phase-Umbau (siehe ROADMAP.md, "kein Mensch braucht im Flag den
Begriff 'phase'") hat jeder Schritt sein eigenes Flag: --scan, --abfragen,
--nachholen, --bewerten, --schreiben. Ohne jedes dieser Flags läuft der
komplette Normal-Durchlauf (alter Standard ohne --phase). --nachholen darf
jetzt zusammen mit PFAD benutzt werden und wird dann auf PFAD eingegrenzt --
vorher wurde --nachholen bei gesetztem PFAD komplett übersprungen.
"""

from pathlib import Path

import cache_store as cs
import lyrics_core
import songtext_pipeline


# --- CLI: Schritt-Flags und Standard-Durchlauf ---------------------------


def test_main_ohne_flags_aktiviert_scan_abfragen_bewerten_schreiben(
    tmp_path, monkeypatch, capsys
):
    """Der Normal-Durchlauf ohne jedes Flag läuft OHNE --nachholen (Nutzer-
    Feedback: ein Wiederholungslauf soll nicht bei jedem Mal erneut alle
    historisch offenen "nichts"/"fehlschlag"-Kombis live nachfragen -- das
    ist ein bewusster, expliziter Schritt, siehe ROADMAP.md)."""
    # eigene DB in tmp_path -- sonst würde main() die echte Produktions-Cache-DB
    # öffnen (siehe _default_db_path). Die DB ist leer (keine Songs, keine
    # Ergebnisse), --abfragen fragt daher real, aber ohne einen einzigen
    # (Song, Provider) -- keine Live-Netzwerk-Abfrage findet statt.
    # _get_whisper_model gemockt -- sonst würde --bewerten ein echtes
    # faster-whisper-Modell laden (langsam, nicht Testgegenstand hier).
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(songtext_pipeline, "_default_db_path", lambda: db_path)
    monkeypatch.setattr(lyrics_core, "_get_whisper_model", lambda name: object())
    monkeypatch.setattr(lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None)
    monkeypatch.setattr("sys.argv", ["songtext_pipeline.py", str(tmp_path)])
    try:
        songtext_pipeline.main()

        out = capsys.readouterr().out
        assert "scan: 0 Song(s) gescannt/aktualisiert." in out
        assert "abfragen: 0 Song(s) abgefragt." in out
        assert "nachholen:" not in out
        assert (
            "bewerten: 0 Konsens, 0 Whisper akzeptiert, 0 abgelehnt, 0 ohne Provider, 0 übersprungen (unverändert)."
            in out
        )
        assert "schreiben: 0 geschrieben, 0 übersprungen, 0 nicht gefunden." in out
    finally:
        _reset_lyrics_core_globals()


def test_main_nachholen_impliziert_bewerten_und_schreiben(
    tmp_path, monkeypatch, capsys
):
    """--nachholen läuft nie allein: ohne --bewerten/--schreiben käme ein
    frisch gefundener Provider-Treffer nirgendwo an (siehe ROADMAP.md)."""
    # eigene, leere DB -- sonst würde main() die echte Produktions-Cache-DB
    # öffnen und fetch_providers.retry_missing() könnte live abfragen.
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(songtext_pipeline, "_default_db_path", lambda: db_path)
    monkeypatch.setattr(lyrics_core, "_get_whisper_model", lambda name: object())
    monkeypatch.setattr("sys.argv", ["songtext_pipeline.py", "--nachholen"])
    try:
        songtext_pipeline.main()

        out = capsys.readouterr().out
        assert "nachholen:" in out
        assert "Keine passenden Cache-Einträge gefunden" in out
        assert "scan:" not in out
        assert "abfragen:" not in out
        assert (
            "bewerten: 0 Konsens, 0 Whisper akzeptiert, 0 abgelehnt, 0 ohne Provider, 0 übersprungen (unverändert)."
            in out
        )
        assert "schreiben: kein PFAD angegeben, nichts zu schreiben." in out
    finally:
        _reset_lyrics_core_globals()


def test_main_einzelne_flags_nur_diese_schritte(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(songtext_pipeline, "_default_db_path", lambda: db_path)
    monkeypatch.setattr(lyrics_core, "_get_whisper_model", lambda name: object())
    monkeypatch.setattr(lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None)
    monkeypatch.setattr(
        "sys.argv",
        [
            "songtext_pipeline.py",
            str(tmp_path),
            "--abfragen",
            "--bewerten",
            "--schreiben",
        ],
    )
    try:
        songtext_pipeline.main()

        out = capsys.readouterr().out
        assert "scan:" not in out
        assert "abfragen: 0 Song(s) abgefragt." in out
        assert "nachholen:" not in out
        assert (
            "bewerten: 0 Konsens, 0 Whisper akzeptiert, 0 abgelehnt, 0 ohne Provider, 0 übersprungen (unverändert)."
            in out
        )
        assert "schreiben: 0 geschrieben, 0 übersprungen, 0 nicht gefunden." in out
    finally:
        _reset_lyrics_core_globals()


def test_main_scan_ohne_pfad_meldet_und_ueberspringt(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(songtext_pipeline, "_default_db_path", lambda: db_path)
    monkeypatch.setattr("sys.argv", ["songtext_pipeline.py", "--scan"])
    songtext_pipeline.main()

    out = capsys.readouterr().out
    assert "scan: kein PFAD angegeben, nichts zu scannen." in out


# --- --scan Ende-zu-Ende: Song landet wirklich in der DB -----------------


def test_main_scan_end_to_end_song_landet_in_songs_tabelle(
    tmp_path, monkeypatch, capsys
):
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(songtext_pipeline, "_default_db_path", lambda: db_path)

    audio_file = tmp_path / "01 - Naturtraene.flac"
    audio_file.write_bytes(b"")

    monkeypatch.setattr(
        songtext_pipeline.lyrics_core,
        "_read_audio_tags",
        lambda path: ("Nina Hagen", "Naturtraene", "Punk"),
    )
    monkeypatch.setattr("sys.argv", ["songtext_pipeline.py", str(tmp_path), "--scan"])

    songtext_pipeline.main()

    out = capsys.readouterr().out
    assert "scan: 1 Song(s) gescannt/aktualisiert." in out

    conn = cs.open_cache(db_path)
    row = conn.execute("SELECT artist_key, titel_key, genre FROM songs").fetchone()
    assert row == (
        cs.normalize_key("Nina Hagen"),
        cs.normalize_key("Naturtraene"),
        "Punk",
    )


# --- build_file_song_map --------------------------------------------------


def test_build_file_song_map_ordnet_bekannte_datei_zu_und_ueberspringt_rest(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "cache.db"
    conn = cs.open_cache(db_path)
    cs.put_provider(
        conn,
        "genius",
        cs.normalize_key("Nina Hagen"),
        cs.normalize_key("Naturtraene"),
        "treffer",
        "Ein Text",
    )

    known_file = tmp_path / "01 - Naturtraene.flac"
    known_file.write_bytes(b"")
    unknown_file = tmp_path / "02 - Unknown Song.flac"
    unknown_file.write_bytes(b"")
    no_tags_file = tmp_path / "03 - NoTags.flac"
    no_tags_file.write_bytes(b"")

    tags_by_path = {
        known_file: ("Nina Hagen", "Naturtraene", ""),
        unknown_file: ("Someone Else", "Some Song", ""),
        no_tags_file: ("", "", ""),
    }

    def fake_read_audio_tags(path):
        return tags_by_path.get(path, ("", "", ""))

    monkeypatch.setattr(
        songtext_pipeline.lyrics_core, "_read_audio_tags", fake_read_audio_tags
    )

    mapping = songtext_pipeline.build_file_song_map(
        tmp_path, recursive=False, conn=conn
    )

    assert mapping == [
        (known_file, cs.normalize_key("Nina Hagen"), cs.normalize_key("Naturtraene"))
    ]


def test_build_file_song_map_bereinigt_titel_klammerzusatz(tmp_path, monkeypatch):
    db_path = tmp_path / "cache.db"
    conn = cs.open_cache(db_path)
    cs.put_provider(
        conn,
        "genius",
        cs.normalize_key("Artist"),
        cs.normalize_key("Song Title"),
        "treffer",
        "Ein Text",
    )

    live_file = tmp_path / "01 - Song Title (Live Version).flac"
    live_file.write_bytes(b"")

    def fake_read_audio_tags(path):
        return ("Artist", "Song Title (Live Version)", "")

    monkeypatch.setattr(
        songtext_pipeline.lyrics_core, "_read_audio_tags", fake_read_audio_tags
    )

    mapping = songtext_pipeline.build_file_song_map(
        tmp_path, recursive=False, conn=conn
    )

    assert mapping == [
        (live_file, cs.normalize_key("Artist"), cs.normalize_key("Song Title"))
    ]


def test_build_file_song_map_leere_db_liefert_leere_liste(tmp_path, monkeypatch):
    db_path = tmp_path / "cache.db"
    conn = cs.open_cache(db_path)

    audio_file = tmp_path / "01 - Song.flac"
    audio_file.write_bytes(b"")

    monkeypatch.setattr(
        songtext_pipeline.lyrics_core,
        "_read_audio_tags",
        lambda path: ("Artist", "Song", ""),
    )

    mapping = songtext_pipeline.build_file_song_map(
        tmp_path, recursive=False, conn=conn
    )
    assert mapping == []


# --- --abfragen/--nachholen: fetch_providers-Anbindung --------------------
#
# Netzwerk/subprocess werden IMMER gemockt -- niemals echte Live-Provider-
# Abfragen in Tests. _open_lrclib_dump_conn wird ebenfalls auf None gemockt,
# damit die Tests unabhängig davon laufen, ob der externe LRCLib-Datenbank-
# Abzug auf der jeweiligen Maschine gemountet ist (reiner Beschleuniger,
# siehe fetch_providers._prepare_lyrics_core_globals-Docstring).


def _fake_subprocess_run(responses: dict[str, str] | None = None):
    responses = responses or {}

    class _Result:
        stderr = ""

    calls: list[tuple[str, str]] = []

    def _run(cmd, **kwargs):
        query, provider = cmd[1], cmd[-1]
        calls.append((query, provider))
        out_path = Path(cmd[3])
        for needle, content in responses.items():
            if needle in query:
                out_path.write_text(content, encoding="utf-8")
                return _Result()
        return _Result()

    _run.calls = calls
    return _run


def _reset_lyrics_core_globals():
    """fetch_providers setzt beim Aufruf über main() lyrics_core-Modul-
    Globals direkt (nicht über monkeypatch, siehe fetch_providers.
    _prepare_lyrics_core_globals) -- ohne manuellen Reset bliebe nach dem
    Test eine geschlossene tmp_path-Connection als _cache_conn stehen und
    könnte andere Tests im selben pytest-Lauf stören."""
    lyrics_core._cache_conn = None
    lyrics_core._cache_refresh = False
    lyrics_core._cache_only = False
    lyrics_core._lrclib_dump_conn = None


def test_main_abfragen_fragt_songs_ab_und_schreibt_in_cache_db(
    tmp_path, monkeypatch, capsys
):
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(songtext_pipeline, "_default_db_path", lambda: db_path)
    conn = cs.open_cache(db_path)
    cs._get_or_create_song(conn, "artist a", "title a", None)
    conn.commit()
    conn.close()

    monkeypatch.setattr(lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None)
    # Neutralisiert den uncommitteten lokalen Debug-Hack in lyrics_core.py
    # (_LRCLIB_LIVE_FALLBACK=False) -- dieser Test prüft das COMMITTETE
    # Verhalten (alle 4 Anbieter werden live gefragt), nicht den Hack.
    monkeypatch.setattr(lyrics_core, "_LRCLIB_LIVE_FALLBACK", True, raising=False)
    fake_run = _fake_subprocess_run({"artist a": "[00:01.00]hallo"})
    monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["songtext_pipeline.py", "--abfragen"])

    try:
        songtext_pipeline.main()

        out = capsys.readouterr().out
        assert "abfragen: 1 Song(s) abgefragt." in out
        assert len(fake_run.calls) == 4  # ein Song x 4 Anbieter
        assert {p for _, p in fake_run.calls} == set(lyrics_core._ALL_PROVIDERS)

        conn = cs.open_cache(db_path)
        assert cs.get_provider(conn, "lrclib", "artist a", "title a") == {
            "status": "treffer",
            "content": "[00:01.00]hallo",
        }
    finally:
        _reset_lyrics_core_globals()


def test_main_nachholen_holt_nur_nichts_fehlschlag_kombis_nach(
    tmp_path, monkeypatch, capsys
):
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(songtext_pipeline, "_default_db_path", lambda: db_path)
    conn = cs.open_cache(db_path)
    cs.put_provider(conn, "lrclib", "artist a", "title a", "nichts", None)
    cs.put_provider(conn, "genius", "artist a", "title a", "treffer", "[00:01.00]x")
    conn.close()

    monkeypatch.setattr(lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None)
    # Neutralisiert den uncommitteten lokalen Debug-Hack in lyrics_core.py
    # (_LRCLIB_LIVE_FALLBACK=False) -- dieser Test prüft das COMMITTETE
    # Verhalten (lrclib wird live gefragt), nicht den Hack.
    monkeypatch.setattr(lyrics_core, "_LRCLIB_LIVE_FALLBACK", True, raising=False)
    fake_run = _fake_subprocess_run({"artist a": "[00:01.00]neu"})
    monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["songtext_pipeline.py", "--nachholen"])

    try:
        songtext_pipeline.main()

        out = capsys.readouterr().out
        assert "nachholen:" in out
        assert "jetzt gefunden" in out
        # nur der lrclib-nichts-Eintrag wurde retried, nicht der genius-Treffer
        assert len(fake_run.calls) == 1
        assert fake_run.calls[0][1] == "lrclib"

        conn = cs.open_cache(db_path)
        assert cs.get_provider(conn, "lrclib", "artist a", "title a") == {
            "status": "treffer",
            "content": "[00:01.00]neu",
        }
    finally:
        _reset_lyrics_core_globals()


# --- Regressionstest: --abfragen muss auf den PFAD-Umfang eingrenzen -----
#
# Realer Produktions-Bug (siehe ROADMAP.md): ein Lauf über ein einzelnes
# Album fragte in --abfragen JEDEN Song ab, der jemals in der Cache-DB
# gelandet war -- tausende Songs aus Jahren lyrics_core.py-Nutzung, nicht
# nur die Songs des aktuellen Albums.


def test_main_scan_abfragen_fragt_nur_pfad_songs_ab_nicht_die_ganze_db(
    tmp_path, monkeypatch, capsys
):
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(songtext_pipeline, "_default_db_path", lambda: db_path)

    album_dir = tmp_path / "album"
    album_dir.mkdir()

    # simuliert einen frueheren Lauf ueber ein komplett anderes Album --
    # diese Zeile stand schon VOR diesem Lauf in der Cache-DB.
    conn = cs.open_cache(db_path)
    cs._get_or_create_song(
        conn, cs.normalize_key("Andere Band"), cs.normalize_key("Anderer Song"), None
    )
    conn.commit()
    conn.close()

    file_a = album_dir / "01 - Song A.flac"
    file_a.write_bytes(b"")
    file_b = album_dir / "02 - Song B.flac"
    file_b.write_bytes(b"")
    tags_by_path = {
        file_a: ("Album Artist", "Song A", ""),
        file_b: ("Album Artist", "Song B", ""),
    }
    monkeypatch.setattr(
        songtext_pipeline.lyrics_core,
        "_read_audio_tags",
        lambda path: tags_by_path.get(path, ("", "", "")),
    )
    monkeypatch.setattr(lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None)
    monkeypatch.setattr(lyrics_core, "_LRCLIB_LIVE_FALLBACK", True, raising=False)
    fake_run = _fake_subprocess_run({})
    monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)
    monkeypatch.setattr(
        "sys.argv",
        ["songtext_pipeline.py", str(album_dir), "--scan", "--abfragen"],
    )

    try:
        songtext_pipeline.main()

        out = capsys.readouterr().out
        assert "scan: 2 Song(s) gescannt/aktualisiert." in out
        assert "abfragen: 2 Song(s) abgefragt." in out

        # 2 Album-Songs x 4 Provider -- NICHT der dritte, vorbestehende Song
        assert len(fake_run.calls) == 8
        assert all("andere band" not in q for q, _p in fake_run.calls)
        assert all("anderer song" not in q for q, _p in fake_run.calls)

        conn = cs.open_cache(db_path)
        assert (
            cs.get_provider(
                conn,
                "musixmatch",
                cs.normalize_key("Andere Band"),
                cs.normalize_key("Anderer Song"),
            )
            is None
        )
    finally:
        _reset_lyrics_core_globals()


def test_main_abfragen_ohne_pfad_fragt_weiterhin_die_ganze_db_ab(
    tmp_path, monkeypatch, capsys
):
    """Gegenprobe: --abfragen OHNE PFAD ist weiterhin die bewusste "ganze
    Bibliothek nachziehen"-Absicht -- fragt also auch Songs aus früheren,
    anderen Läufen ab."""
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(songtext_pipeline, "_default_db_path", lambda: db_path)
    conn = cs.open_cache(db_path)
    cs._get_or_create_song(
        conn, cs.normalize_key("Andere Band"), cs.normalize_key("Anderer Song"), None
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None)
    monkeypatch.setattr(lyrics_core, "_LRCLIB_LIVE_FALLBACK", True, raising=False)
    fake_run = _fake_subprocess_run({})
    monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["songtext_pipeline.py", "--abfragen"])

    try:
        songtext_pipeline.main()

        out = capsys.readouterr().out
        assert "abfragen: 1 Song(s) abgefragt." in out
        assert any("andere band" in q for q, _p in fake_run.calls)
    finally:
        _reset_lyrics_core_globals()


# --- --nachholen mit PFAD: jetzt eingegrenzt statt übersprungen ----------
#
# Kernstück dieses Umbaus (Nutzer-Feedback): "wenn ich einen Pfad mitgebe
# gilt das Flag auch nur für die Dateien, song + artist, die darin enthalten
# sind. sonst kann ich ja keinen gezielten --nachholen machen." Vorher wurde
# --nachholen bei gesetztem PFAD komplett übersprungen (siehe Git-Historie).


def test_main_nachholen_mit_pfad_grenzt_auf_pfad_songs_ein(
    tmp_path, monkeypatch, capsys
):
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(songtext_pipeline, "_default_db_path", lambda: db_path)

    album_dir = tmp_path / "album"
    album_dir.mkdir()

    conn = cs.open_cache(db_path)
    # Song IM Album -- soll retried werden.
    cs._get_or_create_song(
        conn, cs.normalize_key("Album Artist"), cs.normalize_key("Song A"), None
    )
    cs.put_provider(
        conn,
        "lrclib",
        cs.normalize_key("Album Artist"),
        cs.normalize_key("Song A"),
        "fehlschlag",
        None,
        "timeout",
    )
    # Song AUSSERHALB des Albums, ebenfalls fehlgeschlagen -- soll NICHT
    # retried werden, wenn PFAD gesetzt ist.
    cs.put_provider(
        conn,
        "lrclib",
        cs.normalize_key("Andere Band"),
        cs.normalize_key("Anderer Song"),
        "fehlschlag",
        None,
        "timeout",
    )
    conn.commit()
    conn.close()

    file_a = album_dir / "01 - Song A.flac"
    file_a.write_bytes(b"")
    monkeypatch.setattr(
        songtext_pipeline.lyrics_core,
        "_read_audio_tags",
        lambda path: ("Album Artist", "Song A", ""),
    )
    monkeypatch.setattr(lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None)
    monkeypatch.setattr(lyrics_core, "_LRCLIB_LIVE_FALLBACK", True, raising=False)
    # --nachholen impliziert jetzt --bewerten + --schreiben (siehe ROADMAP.md)
    # -- Whisper hier bewusst "nicht verfügbar", damit dieser Test isoliert
    # nur die Nachhol-Scope-Eingrenzung prüft, ohne einen echten
    # ffmpeg/subprocess-Transkriptions-Umweg über denselben gemockten
    # subprocess.run auszulösen.
    monkeypatch.setattr(lyrics_core, "_get_whisper_model", lambda name: None)
    fake_run = _fake_subprocess_run({"album artist": "[00:01.00]neu"})
    monkeypatch.setattr(lyrics_core.subprocess, "run", fake_run)
    monkeypatch.setattr(
        "sys.argv", ["songtext_pipeline.py", str(album_dir), "--nachholen"]
    )

    try:
        songtext_pipeline.main()

        out = capsys.readouterr().out
        assert "nachholen:" in out
        # nur der Album-Song wurde live nachgefragt
        assert len(fake_run.calls) == 1
        assert "album artist" in fake_run.calls[0][0]
        assert all("andere band" not in q for q, _p in fake_run.calls)

        conn = cs.open_cache(db_path)
        assert cs.get_provider(
            conn, "lrclib", cs.normalize_key("Album Artist"), cs.normalize_key("Song A")
        ) == {"status": "treffer", "content": "[00:01.00]neu"}
        # der Song außerhalb von PFAD blieb unangetastet
        assert (
            cs.get_provider(
                conn,
                "lrclib",
                cs.normalize_key("Andere Band"),
                cs.normalize_key("Anderer Song"),
            )
            is None  # fehlschlag zaehlt nie als Cache-Treffer, siehe get_provider
        )
        row = conn.execute(
            "SELECT status FROM ergebnisse e JOIN songs s ON s.id=e.song_id "
            "WHERE s.artist_key=? AND s.titel_key=?",
            (cs.normalize_key("Andere Band"), cs.normalize_key("Anderer Song")),
        ).fetchone()
        assert row == ("fehlschlag",)  # unveraendert, nicht retried
    finally:
        _reset_lyrics_core_globals()


def test_main_nachholen_mit_pfad_ohne_treffer_bleibt_leer_kein_fallback_auf_ganze_db(
    tmp_path, monkeypatch, capsys
):
    """Ein PFAD ohne passende Songs darf NICHT auf die ganze Bibliothek
    zurückfallen -- sonst wäre die Eingrenzung wertlos."""
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(songtext_pipeline, "_default_db_path", lambda: db_path)

    empty_album = tmp_path / "leeres_album"
    empty_album.mkdir()

    conn = cs.open_cache(db_path)
    cs.put_provider(
        conn, "lrclib", "andere band", "anderer song", "fehlschlag", None, "timeout"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None)

    def _fail_if_called(*a, **k):
        raise AssertionError("PFAD ohne Treffer darf nie live abfragen")

    monkeypatch.setattr(lyrics_core.subprocess, "run", _fail_if_called)
    monkeypatch.setattr(
        "sys.argv", ["songtext_pipeline.py", str(empty_album), "--nachholen"]
    )

    try:
        songtext_pipeline.main()
        out = capsys.readouterr().out
        assert "Keine passenden Cache-Einträge gefunden" in out
    finally:
        _reset_lyrics_core_globals()


def test_main_pfad_ohne_flags_laesst_nachholen_aus(tmp_path, monkeypatch, capsys):
    """Gegenprobe zu test_main_nachholen_impliziert_bewerten_und_schreiben:
    der komplette Normal-Durchlauf (kein Flag angegeben) MIT PFAD führt
    --nachholen NICHT aus -- das braucht immer ein ausdrückliches Flag."""
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(songtext_pipeline, "_default_db_path", lambda: db_path)
    monkeypatch.setattr(lyrics_core, "_get_whisper_model", lambda name: object())
    monkeypatch.setattr(lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None)
    monkeypatch.setattr("sys.argv", ["songtext_pipeline.py", str(tmp_path)])

    try:
        songtext_pipeline.main()
        out = capsys.readouterr().out
        assert "nachholen:" not in out
    finally:
        _reset_lyrics_core_globals()


# --- Voller Pipeline-Lauf: scan -> abfragen -> bewerten -> schreiben ------
# prüft die reale Verdrahtung zwischen den Modulen (nicht nur einzeln
# gemockt wie in den Modul-eigenen Testdateien).

LRC_KONSENS_A = "[00:10.00]Girl you know it's true I love you\n[00:15.00]I'm in love with you girl\n"
LRC_KONSENS_B = "[00:10.00]Girl you know it's true yes I love you\n[00:15.00]I'm in love girl cause you're on my mind\n"
LRC_KONSENS_C = "[00:10.00]You know it's true I love you girl oh\n[00:15.00]In love with you girl cause you're my mind\n"


def test_voller_lauf_scan_provider_bewerten_schreiben(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(songtext_pipeline, "_default_db_path", lambda: db_path)

    album_dir = tmp_path / "album"
    album_dir.mkdir()
    audio = album_dir / "01 - Song.flac"
    audio.write_bytes(b"")

    monkeypatch.setattr(
        songtext_pipeline.lyrics_core,
        "_read_audio_tags",
        lambda path: ("Test Artist", "Test Song", ""),
    )
    monkeypatch.setattr(lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None)
    monkeypatch.setattr(lyrics_core, "_LRCLIB_LIVE_FALLBACK", True, raising=False)
    monkeypatch.setattr(lyrics_core, "_get_whisper_model", lambda name: object())

    def _fail_if_whisper_called(*a, **kw):
        raise AssertionError(
            "Whisper sollte bei 3-Provider-Konsens nicht aufgerufen werden"
        )

    monkeypatch.setattr(lyrics_core, "_whisper_best", _fail_if_whisper_called)

    fake_run = _fake_subprocess_run(
        {
            "test artist test song": LRC_KONSENS_A,  # lrclib
        }
    )

    # 3 unterschiedliche Provider müssen übereinstimmen -- _fake_subprocess_run
    # liefert nur EINEN Text pro Query-Needle, deshalb je Provider einzeln
    # über die Kommandozeile (letztes Element = Provider-Name) unterscheiden.
    def _run(cmd, **kwargs):
        query, provider = cmd[1], cmd[-1]
        fake_run.calls.append((query, provider))

        class _Result:
            stderr = ""

        content_by_provider = {
            "lrclib": LRC_KONSENS_A,
            "musixmatch": LRC_KONSENS_B,
            "netease": LRC_KONSENS_C,
        }
        content = content_by_provider.get(provider)
        if content is not None:
            Path(cmd[3]).write_text(content, encoding="utf-8")

        return _Result()

    monkeypatch.setattr(lyrics_core.subprocess, "run", _run)

    monkeypatch.setattr(
        "sys.argv", ["songtext_pipeline.py", str(album_dir), "--recursive"]
    )

    try:
        songtext_pipeline.main()
    finally:
        _reset_lyrics_core_globals()

    out = capsys.readouterr().out
    assert "scan: 1 Song(s) gescannt/aktualisiert." in out
    assert "bewerten: 1 Konsens" in out
    assert "schreiben: 1 geschrieben" in out

    conn = cs.open_cache(db_path)
    song_row = conn.execute(
        "SELECT id FROM songs WHERE artist_key=? AND titel_key=?",
        (cs.normalize_key("Test Artist"), cs.normalize_key("Test Song")),
    ).fetchone()
    assert song_row is not None

    lrc_path = audio.with_suffix(".lrc")
    assert lrc_path.exists()
    content = lrc_path.read_text(encoding="utf-8")
    assert "love you" in content


def test_voller_lauf_zweiter_durchlauf_ist_idempotent(tmp_path, monkeypatch, capsys):
    """Wiederholbarkeit (siehe workflow für songexte.txt, "generell: Jeder
    Schritt muss wiederholt werden können"): ein zweiter identischer Lauf
    darf die .lrc-Datei nicht erneut anfassen (JSON-Cache-Skip in
    --schreiben)."""
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(songtext_pipeline, "_default_db_path", lambda: db_path)

    album_dir = tmp_path / "album"
    album_dir.mkdir()
    audio = album_dir / "01 - Song.flac"
    audio.write_bytes(b"")

    monkeypatch.setattr(
        songtext_pipeline.lyrics_core,
        "_read_audio_tags",
        lambda path: ("Test Artist", "Test Song", ""),
    )
    monkeypatch.setattr(lyrics_core, "_open_lrclib_dump_conn", lambda no_cache: None)
    monkeypatch.setattr(lyrics_core, "_LRCLIB_LIVE_FALLBACK", True, raising=False)
    monkeypatch.setattr(lyrics_core, "_get_whisper_model", lambda name: object())
    monkeypatch.setattr(
        lyrics_core,
        "_whisper_best",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("kein Whisper bei Konsens erwartet")
        ),
    )

    def _run(cmd, **kwargs):
        provider = cmd[-1]

        class _Result:
            stderr = ""

        content_by_provider = {
            "lrclib": LRC_KONSENS_A,
            "musixmatch": LRC_KONSENS_B,
            "netease": LRC_KONSENS_C,
        }
        content = content_by_provider.get(provider)
        if content is not None:
            Path(cmd[3]).write_text(content, encoding="utf-8")
        return _Result()

    monkeypatch.setattr(lyrics_core.subprocess, "run", _run)
    monkeypatch.setattr(
        "sys.argv", ["songtext_pipeline.py", str(album_dir), "--recursive"]
    )

    try:
        songtext_pipeline.main()
        lrc_path = audio.with_suffix(".lrc")
        mtime_after_first_run = lrc_path.stat().st_mtime_ns

        songtext_pipeline.main()
        assert lrc_path.stat().st_mtime_ns == mtime_after_first_run
    finally:
        _reset_lyrics_core_globals()
