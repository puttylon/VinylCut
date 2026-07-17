"""Tests für compare_whisper_models.py (manueller Modellqualitätsvergleich small/medium/turbo)."""

import argparse
from pathlib import Path

import cache_store as cs
import compare_whisper_models as cwm
import lyrics_core


def _put_song_with_transcript(conn, artist, title, transcript="alt"):
    artist_key = cs.normalize_key(artist)
    titel_key = cs.normalize_key(title)
    cs.put_transcript(conn, artist_key, titel_key, transcript, 0.1, -0.5)
    return artist_key, titel_key


def test_select_all_candidate_pairs_braucht_provider_treffer_nicht_transkript(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    # Hat Provider-Treffer UND Transkript -- zählt.
    artist_key1, titel_key1 = _put_song_with_transcript(
        conn, "Nina Hagen", "Naturträne"
    )
    cs.put_provider(conn, "genius", artist_key1, titel_key1, "treffer", "Text eins")
    # Hat NUR einen Provider-Treffer, KEIN Transkript -- zählt trotzdem, ob
    # Whisper schon mal gelaufen ist, ist für die Auswahl unerheblich.
    artist_key2 = cs.normalize_key("Kraftwerk")
    titel_key2 = cs.normalize_key("Autobahn")
    cs.put_provider(conn, "genius", artist_key2, titel_key2, "treffer", "Text zwei")
    # Hat NUR ein Transkript, KEINEN Provider-Treffer -- darf NICHT
    # auftauchen (keine Sprache klassifizierbar).
    _put_song_with_transcript(conn, "No Provider", "Song")

    pairs = cwm.select_all_candidate_pairs(conn)
    assert set(pairs) == {(artist_key1, titel_key1), (artist_key2, titel_key2)}


def test_get_candidate_texts_liefert_nur_treffer_texte(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    artist_key = cs.normalize_key("Nina Hagen")
    titel_key = cs.normalize_key("Naturträne")
    cs.put_provider(conn, "genius", artist_key, titel_key, "treffer", "Text eins")
    cs.put_provider(conn, "netease", artist_key, titel_key, "treffer", "Text zwei")
    cs.put_provider(conn, "lrclib", artist_key, titel_key, "nichts", None)

    texts = cwm.get_candidate_texts(conn, artist_key, titel_key)

    assert set(texts) == {"Text eins", "Text zwei"}


def test_get_candidate_texts_leer_ohne_provider_treffer(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    texts = cwm.get_candidate_texts(conn, "unknown", "song")
    assert texts == []


def test_detect_language_hint_ohne_kandidaten_liefert_none(tmp_path):
    conn = cs.open_cache(tmp_path / "cache.db")
    result = cwm.detect_language_hint(conn, "unknown", "song")
    assert result is None


def test_detect_language_hint_schreibt_temp_dateien_und_raeumt_auf(
    tmp_path, monkeypatch
):
    conn = cs.open_cache(tmp_path / "cache.db")
    artist_key = cs.normalize_key("Nina Hagen")
    titel_key = cs.normalize_key("Naturträne")
    cs.put_provider(conn, "genius", artist_key, titel_key, "treffer", "Text eins")
    cs.put_provider(conn, "netease", artist_key, titel_key, "treffer", "Text zwei")

    captured_paths = []

    def fake_detect(candidates):
        # Zum Zeitpunkt des Aufrufs müssen die Temp-Dateien existieren und
        # den jeweiligen Kandidatentext enthalten.
        captured_paths.extend(candidates)
        contents = {p.read_text(encoding="utf-8") for p in candidates}
        assert contents == {"Text eins", "Text zwei"}
        return "de"

    monkeypatch.setattr(lyrics_core, "_detect_lrc_language", fake_detect)

    result = cwm.detect_language_hint(conn, artist_key, titel_key)

    assert result == "de"
    assert len(captured_paths) == 2
    assert all(p.suffix == ".lrc" for p in captured_paths)
    # Aufgeräumt: Temp-Dateien existieren nach dem Aufruf nicht mehr.
    assert all(not p.exists() for p in captured_paths)


# --- select_language_pools: 80/20-Stratifizierung -----------------------


def _put_classified_song(conn, artist, title, language_marker):
    """Legt einen Song mit Transkript + einem Provider-Text an, der über
    fake_detect (siehe Tests unten) eindeutig als "en"/"de" erkennbar ist."""
    artist_key, titel_key = _put_song_with_transcript(conn, artist, title)
    cs.put_provider(conn, "genius", artist_key, titel_key, "treffer", language_marker)
    return artist_key, titel_key


def _fake_detect_by_marker(candidates):
    """Ersatz für lyrics_core._detect_lrc_language: liest den Marker aus
    dem (einzigen) Kandidatentext statt echte Spracherkennung laufen zu
    lassen -- deterministisch und unabhängig von langdetect-Konfidenzwerten."""
    content = candidates[0].read_text(encoding="utf-8")
    if content == "ENGLISH_MARKER":
        return "en"
    if content == "GERMAN_MARKER":
        return "de"
    return None


def test_select_language_pools_stratifiziert_en_de_und_ignoriert_unbekannte(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(lyrics_core, "_detect_lrc_language", _fake_detect_by_marker)
    conn = cs.open_cache(tmp_path / "cache.db")

    pairs = []
    for i in range(8):
        pairs.append(
            _put_classified_song(
                conn, f"En Artist {i}", f"En Title {i}", "ENGLISH_MARKER"
            )
        )
    for i in range(4):
        pairs.append(
            _put_classified_song(
                conn, f"De Artist {i}", f"De Title {i}", "GERMAN_MARKER"
            )
        )
    # Kandidat ohne erkennbare Sprache -- zählt zu keinem Pool.
    pairs.append(_put_song_with_transcript(conn, "Unknown Artist", "Unknown Title"))

    pools = cwm.select_language_pools(conn, pairs, n=10, seed=1)

    en_pool, en_target = pools["en"]
    de_pool, de_target = pools["de"]
    assert en_target == 8
    assert de_target == 2
    assert len(en_pool) == 8
    assert len(de_pool) == 4
    assert set(en_pool) == {p for p in pairs[:8]}
    assert set(de_pool) == {p for p in pairs[8:12]}


def test_select_language_pools_hoechstens_ein_song_pro_kuenstler(tmp_path, monkeypatch):
    """Realer Bug (siehe ROADMAP.md): ein im Cache stark vertretener Künstler
    (z.B. Prince mit 392, Gary Numan mit 238 gecachten Songs bei nur ~420
    Künstlern insgesamt) wurde rein zufällig mehrfach in dieselbe Stichprobe
    gezogen -- ohne Sperre nichts, was das verhindert hätte."""
    monkeypatch.setattr(lyrics_core, "_detect_lrc_language", _fake_detect_by_marker)
    conn = cs.open_cache(tmp_path / "cache.db")

    pairs = []
    # Ein Künstler mit 5 Songs -- darf höchstens EINMAL im Pool landen.
    for i in range(5):
        pairs.append(
            _put_classified_song(conn, "Repeat Artist", f"Song {i}", "ENGLISH_MARKER")
        )
    # 6 weitere, unterschiedliche Künstler -- genug, um den Puffer trotz der
    # Künstlersperre voll zu bekommen.
    for i in range(6):
        pairs.append(
            _put_classified_song(
                conn, f"Distinct Artist {i}", f"Title {i}", "ENGLISH_MARKER"
            )
        )

    # n=2 -> en_target=round(2*0.8)=2, Puffer=2*3=6.
    pools = cwm.select_language_pools(conn, pairs, n=2, seed=1)
    en_pool, _en_target = pools["en"]

    artists_in_pool = [artist_key for artist_key, _titel_key in en_pool]
    assert len(en_pool) == 6  # Puffer trotz Sperre voll erreicht
    assert len(set(artists_in_pool)) == len(artists_in_pool)  # keine Duplikate
    assert artists_in_pool.count(cs.normalize_key("Repeat Artist")) <= 1


def test_select_language_pools_exclude_artists_wird_nie_gezogen(tmp_path, monkeypatch):
    monkeypatch.setattr(lyrics_core, "_detect_lrc_language", _fake_detect_by_marker)
    conn = cs.open_cache(tmp_path / "cache.db")

    excluded = _put_classified_song(conn, "Excluded Artist", "Song", "ENGLISH_MARKER")
    included = _put_classified_song(conn, "Included Artist", "Song", "ENGLISH_MARKER")

    pools = cwm.select_language_pools(
        conn,
        [excluded, included],
        n=1,
        seed=1,
        exclude_artists={cs.normalize_key("Excluded Artist")},
    )
    en_pool, _en_target = pools["en"]

    assert excluded not in en_pool
    assert included in en_pool


def test_select_language_pools_bricht_ab_sobald_puffer_erreicht(tmp_path, monkeypatch):
    monkeypatch.setattr(lyrics_core, "_detect_lrc_language", _fake_detect_by_marker)
    conn = cs.open_cache(tmp_path / "cache.db")

    pairs = []
    for i in range(30):
        pairs.append(
            _put_classified_song(
                conn, f"En Artist {i}", f"En Title {i}", "ENGLISH_MARKER"
            )
        )

    call_count = {"n": 0}
    real_detect_language_hint = cwm.detect_language_hint

    def counting_detect_language_hint(conn, artist_key, titel_key):
        call_count["n"] += 1
        return real_detect_language_hint(conn, artist_key, titel_key)

    monkeypatch.setattr(cwm, "detect_language_hint", counting_detect_language_hint)

    # n=2 -> en_target=round(2*0.8)=2, Puffer = 2*3=6 -- die restlichen 24
    # Kandidaten dürfen NICHT mehr klassifiziert werden.
    pools = cwm.select_language_pools(conn, pairs, n=2, seed=1)

    en_pool, en_target = pools["en"]
    assert en_target == 2
    assert len(en_pool) == 6
    assert call_count["n"] < len(pairs)


# --- resolve_all_songs: einmaliger Durchlauf, Früh-Abbruch, Ersatzkandidaten ---


def test_resolve_all_songs_bricht_frueh_ab_sobald_alles_gefunden_ist(
    tmp_path, monkeypatch
):
    lib = tmp_path / "lib"
    lib.mkdir()

    wanted_path = lib / "wanted.flac"
    wanted_path.write_bytes(b"fake")
    filler_paths = []
    for i in range(50):
        p = lib / f"filler_{i}.flac"
        p.write_bytes(b"fake")
        filler_paths.append(p)

    # Reihenfolge fest vorgeben (unabhängig von echter Dateisystem-Sortierung):
    # der gesuchte Song liegt ganz am Anfang des simulierten Durchlaufs.
    ordered_paths = [wanted_path] + filler_paths
    monkeypatch.setattr(Path, "rglob", lambda self, pattern: iter(ordered_paths))

    tags_by_path = {wanted_path: ("Wanted Artist", "Wanted Title", "")}
    read_calls = []

    def fake_read_tags(path):
        read_calls.append(path)
        return tags_by_path.get(path, ("", "", ""))

    monkeypatch.setattr(lyrics_core, "_read_audio_tags", fake_read_tags)
    monkeypatch.setattr(cwm, "_read_duration_sec", lambda path: 100.0)

    wanted_key = (cs.normalize_key("Wanted Artist"), cs.normalize_key("Wanted Title"))
    language_pools = {"en": ([wanted_key], 1), "de": ([], 0)}

    forced_found, forced_missing, random_found, skipped = cwm.resolve_all_songs(
        lib, [], language_pools
    )

    assert len(random_found) == 1
    assert random_found[0]["artist"] == "Wanted Artist"
    assert random_found[0]["language"] == "en"
    assert forced_found == []
    assert forced_missing == []

    # Früh-Abbruch: nicht alle 51 Dateien wurden über _read_audio_tags gelesen
    # -- der Durchlauf endet direkt nachdem der gesuchte Song (an Position 1)
    # gefunden wurde.
    assert len(read_calls) == 1
    assert len(read_calls) < len(ordered_paths)


def test_resolve_all_songs_findet_pflicht_und_pool_song_in_einem_durchlauf(
    tmp_path, monkeypatch
):
    lib = tmp_path / "lib"
    lib.mkdir()
    forced_path = lib / "forced.flac"
    reserve_path = lib / "reserve.flac"
    forced_path.write_bytes(b"fake")
    reserve_path.write_bytes(b"fake")

    tags_by_path = {
        forced_path: ("Nina Hagen Band", "Rangehn", ""),
        reserve_path: ("Reserve Artist", "Reserve Title", ""),
    }
    monkeypatch.setattr(
        lyrics_core,
        "_read_audio_tags",
        lambda p: tags_by_path.get(p, ("", "", "")),
    )
    monkeypatch.setattr(cwm, "_read_duration_sec", lambda p: 150.0)

    # Primärkandidat hat KEINE Datei in der Bibliothek -- der Ersatzkandidat
    # (reserve_key) muss automatisch für die Quote einspringen.
    primary_key = (
        cs.normalize_key("Missing Primary Artist"),
        cs.normalize_key("Missing Primary Title"),
    )
    reserve_key = (
        cs.normalize_key("Reserve Artist"),
        cs.normalize_key("Reserve Title"),
    )
    language_pools = {"en": ([primary_key, reserve_key], 1), "de": ([], 0)}

    forced_found, forced_missing, random_found, skipped = cwm.resolve_all_songs(
        lib, [("Nina Hagen Band", "Rangehn")], language_pools
    )

    assert len(forced_found) == 1
    assert forced_found[0]["forced"] is True
    assert forced_found[0]["artist"] == "Nina Hagen Band"
    assert forced_missing == []

    assert len(random_found) == 1
    assert random_found[0]["artist"] == "Reserve Artist"
    assert random_found[0]["language"] == "en"
    # Quote via Ersatzkandidat erreicht -> kein echtes Fehlen zu melden.
    assert skipped == []


def test_resolve_all_songs_meldet_fehlende_poolkandidaten_nach_vollem_scan(
    tmp_path, monkeypatch
):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "irrelevant.flac").write_bytes(b"fake")
    monkeypatch.setattr(lyrics_core, "_read_audio_tags", lambda p: ("", "", ""))
    monkeypatch.setattr(cwm, "_read_duration_sec", lambda p: 100.0)

    missing_key = (cs.normalize_key("Ghost Artist"), cs.normalize_key("Ghost Title"))
    language_pools = {"en": ([missing_key], 1), "de": ([], 0)}

    forced_found, forced_missing, random_found, skipped = cwm.resolve_all_songs(
        lib, [], language_pools
    )

    assert random_found == []
    # Kandidat wurde über den kompletten (kleinen) Scan gesucht und nicht
    # gefunden -> echtes Fehlen, wird gemeldet.
    assert skipped == [missing_key]


def test_resolve_all_songs_meldet_fehlenden_pflicht_song_nach_vollem_scan(
    tmp_path, monkeypatch
):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "irrelevant.flac").write_bytes(b"fake")
    monkeypatch.setattr(lyrics_core, "_read_audio_tags", lambda p: ("", "", ""))
    monkeypatch.setattr(cwm, "_read_duration_sec", lambda p: 100.0)

    forced_found, forced_missing, random_found, skipped = cwm.resolve_all_songs(
        lib, [("Nina Hagen Band", "Rangehn")], {"en": ([], 0), "de": ([], 0)}
    )

    assert forced_found == []
    assert forced_missing == [("Nina Hagen Band", "Rangehn")]


def test_transcribe_song_with_model_ruft_transcribe_mit_hint_auf(monkeypatch):
    calls = []

    def fake_transcribe(path, start, context_sec, model_name, language=None):
        calls.append((path, start, context_sec, model_name, language))
        return ([f"wort_{model_name}"], 0.1, -0.5)

    monkeypatch.setattr(lyrics_core, "_transcribe", fake_transcribe)
    monkeypatch.setattr(lyrics_core, "_whisper_context_sec", lambda dur_s: 480.0)

    entry = {"path": Path("/lib/a.flac"), "duration": 200.0}
    result = cwm.transcribe_song_with_model(entry, "medium", "de")

    assert result == "wort_medium"
    assert calls == [(Path("/lib/a.flac"), 0.0, 480.0, "medium", "de")]


def test_transcribe_song_with_model_leeres_ergebnis_wird_als_kein_text_erkannt_markiert(
    monkeypatch,
):
    monkeypatch.setattr(lyrics_core, "_transcribe", lambda *a, **kw: ([], 1.0, 0.0))
    entry = {"path": Path("/lib/a.flac"), "duration": 0.0}
    result = cwm.transcribe_song_with_model(entry, "small", None)
    assert result == "(kein Text erkannt)"


def test_run_model_over_songs_transkribiert_und_haengt_sofort_an_datei_an(
    tmp_path, monkeypatch
):
    calls = []

    def fake_transcribe(path, start, context_sec, model_name, language=None):
        calls.append((path, model_name, language))
        return ([f"wort_{model_name}"], 0.1, -0.5)

    monkeypatch.setattr(lyrics_core, "_transcribe", fake_transcribe)
    monkeypatch.setattr(lyrics_core, "_get_whisper_model", lambda name: object())
    monkeypatch.setattr(lyrics_core, "_whisper_context_sec", lambda dur_s: 480.0)

    found = [
        {"path": Path("/lib/a.flac"), "duration": 200.0, "artist": "A", "title": "1"},
        {"path": Path("/lib/b.flac"), "duration": 200.0, "artist": "B", "title": "2"},
    ]
    language_hints = ["de", None]
    output_paths = [tmp_path / "a.txt", tmp_path / "b.txt"]
    for p in output_paths:
        p.write_text("Kopf\n\n", encoding="utf-8")

    result = cwm.run_model_over_songs("small", found, language_hints, output_paths)

    assert result is True
    assert calls == [
        (Path("/lib/a.flac"), "small", "de"),
        (Path("/lib/b.flac"), "small", None),
    ]
    assert output_paths[0].read_text(encoding="utf-8") == (
        "Kopf\n\n=== small ===\nwort_small\n\n"
    )
    assert output_paths[1].read_text(encoding="utf-8") == (
        "Kopf\n\n=== small ===\nwort_small\n\n"
    )


def test_run_model_over_songs_gibt_false_zurueck_wenn_modell_fehlt(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(lyrics_core, "_get_whisper_model", lambda name: None)
    found = [
        {"path": Path("/lib/a.flac"), "duration": 1.0, "artist": "A", "title": "1"}
    ]
    output_paths = [tmp_path / "a.txt"]

    result = cwm.run_model_over_songs("medium", found, [None], output_paths)

    assert result is False
    assert "konnte nicht geladen werden" in capsys.readouterr().err
    # Kein Transkriptionsversuch, wenn das Modell gar nicht geladen werden konnte.
    assert not output_paths[0].exists()


def test_unload_model_entfernt_modell_aus_cache_und_gibt_speicher_frei(monkeypatch):
    fake_models = {"small": object(), "medium": object()}
    monkeypatch.setattr(lyrics_core, "_whisper_models", fake_models)

    cwm._unload_model("small")

    assert "small" not in fake_models
    assert "medium" in fake_models

    # Kein Fehler, wenn das Modell gar nicht (mehr) geladen ist.
    cwm._unload_model("small")


def test_write_song_header_dann_append_model_transcript_format_und_unique_path(
    tmp_path,
):
    entry = {"artist": "Nina Hagen", "title": "Naturträne"}

    out_path = cwm.write_song_header(entry, tmp_path, "de")
    assert out_path.name == "Nina_Hagen_Naturträne_modellvergleich.txt"
    # Kopf ist sofort da, noch bevor irgendein Modell gelaufen ist.
    assert out_path.read_text(encoding="utf-8") == (
        "Artist: Nina Hagen\nTitel: Naturträne\nSprache (Hint): de\n\n"
    )

    cwm.append_model_transcript(out_path, "small", "text s")
    cwm.append_model_transcript(out_path, "medium", "text m")
    cwm.append_model_transcript(out_path, "turbo", "text t")

    content = out_path.read_text(encoding="utf-8")
    expected = (
        "Artist: Nina Hagen\n"
        "Titel: Naturträne\n"
        "Sprache (Hint): de\n"
        "\n"
        "=== small ===\n"
        "text s\n"
        "\n"
        "=== medium ===\n"
        "text m\n"
        "\n"
        "=== turbo ===\n"
        "text t\n"
        "\n"
    )
    assert content == expected

    # Zweiter Aufruf für denselben Song darf die Datei nicht überschreiben.
    out_path2 = cwm.write_song_header(entry, tmp_path, "de")
    assert out_path2.name == "Nina_Hagen_Naturträne_modellvergleich_2.txt"


def test_write_song_header_ohne_erkannte_sprache(tmp_path):
    entry = {"artist": "Kraftwerk", "title": "Autobahn"}

    out_path = cwm.write_song_header(entry, tmp_path, None)
    content = out_path.read_text(encoding="utf-8")

    assert "Sprache (Hint): nicht erkannt" in content


def test_write_index_file_listet_bearbeitete_und_uebersprungene(tmp_path):
    entry = {"artist": "Nina Hagen", "title": "Naturträne"}
    out_path = tmp_path / "Nina_Hagen_Naturträne_modellvergleich.txt"
    processed = [(entry, out_path)]
    skipped = [("unknown artist", "unknown title")]

    index_path = cwm.write_index_file(processed, skipped, tmp_path)
    content = index_path.read_text(encoding="utf-8")

    assert "1 Songs bearbeitet" in content
    assert "1 übersprungen" in content
    assert (
        "Nina Hagen - Naturträne -> Nina_Hagen_Naturträne_modellvergleich.txt"
        in content
    )
    assert "artist_key='unknown artist', titel_key='unknown title'" in content
    assert "Pflicht-Songs NICHT in Bibliothek gefunden" in content
    assert "(keine)" in content


def test_write_index_file_markiert_pflicht_songs_und_fehlende(tmp_path):
    entry = {"artist": "Nina Hagen Band", "title": "Rangehn", "forced": True}
    out_path = tmp_path / "Nina_Hagen_Band_Rangehn_modellvergleich.txt"
    processed = [(entry, out_path)]

    index_path = cwm.write_index_file(
        processed, [], tmp_path, forced_missing=[("Kraftwerk", "Fehlt")]
    )
    content = index_path.read_text(encoding="utf-8")

    assert (
        "Nina Hagen Band - Rangehn -> Nina_Hagen_Band_Rangehn_modellvergleich.txt [Pflicht-Song]"
        in content
    )
    assert "Kraftwerk - Fehlt" in content


def test_write_index_file_zeigt_sprach_hint_je_song(tmp_path):
    entry = {
        "artist": "En Artist",
        "title": "En Title",
        "language_hint": "en",
    }
    out_path = tmp_path / "En_Artist_En_Title_modellvergleich.txt"
    processed = [(entry, out_path)]

    index_path = cwm.write_index_file(processed, [], tmp_path)
    content = index_path.read_text(encoding="utf-8")

    assert (
        "En Artist - En Title -> En_Artist_En_Title_modellvergleich.txt [en]" in content
    )


def test_main_end_to_end_schreibt_dateien(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "cache.db"
    conn = cs.open_cache(db_path)
    artist_key, titel_key = _put_song_with_transcript(conn, "Nina Hagen", "Naturträne")
    cs.put_provider(conn, "genius", artist_key, titel_key, "treffer", "ENGLISH_MARKER")
    conn.close()

    lib = tmp_path / "lib"
    lib.mkdir()
    song_path = lib / "song.flac"
    song_path.write_bytes(b"fake")

    output_dir = tmp_path / "out"

    monkeypatch.setattr(cwm, "_default_db_path", lambda: db_path)
    monkeypatch.setattr(
        lyrics_core,
        "_read_audio_tags",
        lambda path: ("Nina Hagen", "Naturträne", ""),
    )
    monkeypatch.setattr(cwm, "_read_duration_sec", lambda path: 200.0)
    monkeypatch.setattr(lyrics_core, "_get_whisper_model", lambda name: object())
    monkeypatch.setattr(lyrics_core, "_detect_lrc_language", _fake_detect_by_marker)

    calls = []

    def fake_transcribe(path, start, context_sec, model_name, language=None):
        calls.append(model_name)
        return ([f"wort_{model_name}"], 0.1, -0.5)

    monkeypatch.setattr(lyrics_core, "_transcribe", fake_transcribe)

    monkeypatch.setattr(
        "sys.argv",
        [
            "compare_whisper_models.py",
            "--n",
            "1",
            "--library",
            str(lib),
            "--output-dir",
            str(output_dir),
            "--seed",
            "1",
        ],
    )

    cwm.main()

    assert calls == ["small", "medium", "turbo"]
    report_path = output_dir / "Nina_Hagen_Naturträne_modellvergleich.txt"
    assert report_path.exists()
    # Sprache wurde bereits bei der Stratifizierung erkannt (en) und direkt
    # als Hint weiterverwendet -- kein zweiter detect_language_hint-Aufruf.
    assert "Sprache (Hint): en" in report_path.read_text(encoding="utf-8")
    assert (output_dir / "modellvergleich_index.txt").exists()
    out = capsys.readouterr().out
    assert "Fertig: 1 Songs verglichen" in out


def test_main_ermittelt_sprachhint_und_gibt_ihn_an_alle_drei_modelle_weiter(
    tmp_path, monkeypatch
):
    """Belegt beide Fixes im Zusammenspiel: EIN Sprach-Hint pro Song (aus der
    Sprachstratifizierung), an small/medium/turbo IDENTISCH übergeben (fairer
    Vergleich, siehe Modul-Docstring)."""
    db_path = tmp_path / "cache.db"
    conn = cs.open_cache(db_path)
    artist_key, titel_key = _put_song_with_transcript(conn, "Nina Hagen", "Naturträne")
    cs.put_provider(
        conn,
        "genius",
        artist_key,
        titel_key,
        "treffer",
        "GERMAN_MARKER",
    )
    conn.close()

    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "song.flac").write_bytes(b"fake")

    output_dir = tmp_path / "out"

    monkeypatch.setattr(cwm, "_default_db_path", lambda: db_path)
    monkeypatch.setattr(
        lyrics_core,
        "_read_audio_tags",
        lambda path: ("Nina Hagen", "Naturträne", ""),
    )
    monkeypatch.setattr(cwm, "_read_duration_sec", lambda path: 200.0)
    monkeypatch.setattr(lyrics_core, "_get_whisper_model", lambda name: object())
    monkeypatch.setattr(lyrics_core, "_detect_lrc_language", _fake_detect_by_marker)

    calls = []

    def fake_transcribe(path, start, context_sec, model_name, language=None):
        calls.append((model_name, language))
        return ([f"wort_{model_name}"], 0.1, -0.5)

    monkeypatch.setattr(lyrics_core, "_transcribe", fake_transcribe)

    monkeypatch.setattr(
        "sys.argv",
        [
            "compare_whisper_models.py",
            # n=5 -> de_target = 5 - round(5*0.8) = 1, damit der einzige
            # (als "de" klassifizierte) Kandidat überhaupt zur Quote zählt.
            "--n",
            "5",
            "--library",
            str(lib),
            "--output-dir",
            str(output_dir),
            "--seed",
            "1",
        ],
    )

    cwm.main()

    assert len(calls) == 3
    assert {model for model, _lang in calls} == {"small", "medium", "turbo"}
    assert all(
        language == "de" for _model, language in calls
    )  # gleicher Hint für alle drei

    content = (output_dir / "Nina_Hagen_Naturträne_modellvergleich.txt").read_text(
        encoding="utf-8"
    )
    assert "Sprache (Hint): de" in content


def test_main_bricht_ab_wenn_modell_fehlt(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "cache.db"
    conn = cs.open_cache(db_path)
    artist_key, titel_key = _put_song_with_transcript(conn, "Nina Hagen", "Naturträne")
    cs.put_provider(conn, "genius", artist_key, titel_key, "treffer", "ENGLISH_MARKER")
    conn.close()

    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "song.flac").write_bytes(b"fake")

    monkeypatch.setattr(cwm, "_default_db_path", lambda: db_path)
    monkeypatch.setattr(
        lyrics_core,
        "_read_audio_tags",
        lambda path: ("Nina Hagen", "Naturträne", ""),
    )
    monkeypatch.setattr(cwm, "_read_duration_sec", lambda path: 200.0)
    monkeypatch.setattr(lyrics_core, "_get_whisper_model", lambda name: None)
    monkeypatch.setattr(lyrics_core, "_detect_lrc_language", _fake_detect_by_marker)

    monkeypatch.setattr(
        "sys.argv",
        [
            "compare_whisper_models.py",
            "--n",
            "1",
            "--library",
            str(lib),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )

    import pytest

    with pytest.raises(SystemExit) as exc_info:
        cwm.main()
    assert exc_info.value.code == 1
    assert "konnte nicht geladen werden" in capsys.readouterr().err


def test_parse_include_zerlegt_artist_titel():
    assert cwm._parse_include("Nina Hagen Band:Rangehn") == (
        "Nina Hagen Band",
        "Rangehn",
    )
    # Whitespace um Artist/Titel wird getrimmt.
    assert cwm._parse_include(" Kraftwerk : Autobahn ") == ("Kraftwerk", "Autobahn")


def test_parse_include_lehnt_fehlendes_format_ab():
    import pytest

    with pytest.raises(argparse.ArgumentTypeError):
        cwm._parse_include("Nina Hagen Band ohne Trenner")


def test_parse_include_lehnt_leere_teile_ab():
    import pytest

    with pytest.raises(argparse.ArgumentTypeError):
        cwm._parse_include(":Rangehn")
    with pytest.raises(argparse.ArgumentTypeError):
        cwm._parse_include("Nina Hagen Band:")


def test_dedupe_forced_songs_entfernt_normalisierte_duplikate():
    forced = [
        ("Nina Hagen Band", "Rangehn"),
        ("nina hagen band", "rangehn"),  # gleicher normalisierter Schlüssel
        ("Kraftwerk", "Autobahn"),
    ]
    unique = cwm.dedupe_forced_songs(forced)
    assert unique == [("Nina Hagen Band", "Rangehn"), ("Kraftwerk", "Autobahn")]


def test_main_pflicht_song_wird_garantiert_verarbeitet_auch_ohne_zufallstreffer(
    tmp_path, monkeypatch, capsys
):
    """Nina Hagen Band/Rangehn muss auch dann in der Stichprobe landen, wenn
    die Zufallsauswahl sie gar nicht ziehen konnte."""
    db_path = tmp_path / "cache.db"
    conn = cs.open_cache(db_path)
    # Kraftwerk/Autobahn ist als "en" klassifiziert und wird per Stratifizierung
    # gezogen -- Rangehn wird nicht darüber gefunden (kommt separat als
    # Pflicht-Song).
    artist_key, titel_key = _put_song_with_transcript(conn, "Kraftwerk", "Autobahn")
    cs.put_provider(conn, "genius", artist_key, titel_key, "treffer", "ENGLISH_MARKER")
    conn.close()

    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "kraftwerk.flac").write_bytes(b"fake")
    (lib / "rangehn.flac").write_bytes(b"fake")

    tags_by_path = {
        lib / "kraftwerk.flac": ("Kraftwerk", "Autobahn", ""),
        lib / "rangehn.flac": ("Nina Hagen Band", "Rangehn", ""),
    }

    monkeypatch.setattr(cwm, "_default_db_path", lambda: db_path)
    monkeypatch.setattr(
        lyrics_core,
        "_read_audio_tags",
        lambda path: tags_by_path.get(path, ("", "", "")),
    )
    monkeypatch.setattr(cwm, "_read_duration_sec", lambda path: 200.0)
    monkeypatch.setattr(lyrics_core, "_get_whisper_model", lambda name: object())
    monkeypatch.setattr(lyrics_core, "_detect_lrc_language", _fake_detect_by_marker)

    calls = []

    def fake_transcribe(path, start, context_sec, model_name, language=None):
        calls.append((path, model_name))
        return ([f"wort_{model_name}"], 0.1, -0.5)

    monkeypatch.setattr(lyrics_core, "_transcribe", fake_transcribe)

    output_dir = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv",
        [
            "compare_whisper_models.py",
            "--n",
            "1",
            "--library",
            str(lib),
            "--output-dir",
            str(output_dir),
            "--seed",
            "1",
        ],
    )

    cwm.main()

    # Beide Songs wurden verarbeitet: der stratifiziert gezogene (Kraftwerk)
    # UND der garantierte Pflicht-Song (Nina Hagen Band/Rangehn), obwohl --n 1 ist.
    assert (output_dir / "Kraftwerk_Autobahn_modellvergleich.txt").exists()
    assert (output_dir / "Nina_Hagen_Band_Rangehn_modellvergleich.txt").exists()
    index_content = (output_dir / "modellvergleich_index.txt").read_text(
        encoding="utf-8"
    )
    assert "[Pflicht-Song]" in index_content
    out = capsys.readouterr().out
    assert "Fertig: 2 Songs verglichen" in out


def test_main_include_flag_fuegt_zusaetzlichen_pflicht_song_hinzu(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "cache.db"
    conn = cs.open_cache(db_path)
    conn.close()  # komplett leere Cache-DB -- keine zufälligen Kandidaten

    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "song.flac").write_bytes(b"fake")

    monkeypatch.setattr(cwm, "_default_db_path", lambda: db_path)
    monkeypatch.setattr(
        lyrics_core,
        "_read_audio_tags",
        lambda path: ("Kraftwerk", "Autobahn", ""),
    )
    monkeypatch.setattr(cwm, "_read_duration_sec", lambda path: 200.0)
    monkeypatch.setattr(lyrics_core, "_get_whisper_model", lambda name: object())
    monkeypatch.setattr(
        lyrics_core,
        "_transcribe",
        lambda path, start, context_sec, model_name, language=None: (
            [f"wort_{model_name}"],
            0.1,
            -0.5,
        ),
    )

    output_dir = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv",
        [
            "compare_whisper_models.py",
            "--n",
            "1",
            "--library",
            str(lib),
            "--output-dir",
            str(output_dir),
            "--include",
            "Kraftwerk:Autobahn",
        ],
    )

    # Cache-DB ist komplett leer (keine zufälligen Kandidaten), trotzdem muss
    # der per --include angeforderte Pflicht-Song verarbeitet werden -- er
    # braucht nur einen Bibliothekstreffer, keinen Cache-Eintrag.
    cwm.main()

    assert (output_dir / "Kraftwerk_Autobahn_modellvergleich.txt").exists()


def test_main_songs_ohne_bibliothekstreffer_werden_uebersprungen(
    tmp_path, monkeypatch, capsys
):
    db_path = tmp_path / "cache.db"
    conn = cs.open_cache(db_path)
    _put_song_with_transcript(conn, "Not In Library", "Missing Song")
    conn.close()

    lib = tmp_path / "lib"
    lib.mkdir()  # leere Bibliothek

    monkeypatch.setattr(cwm, "_default_db_path", lambda: db_path)

    monkeypatch.setattr(
        "sys.argv",
        [
            "compare_whisper_models.py",
            "--n",
            "1",
            "--library",
            str(lib),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )

    import pytest

    with pytest.raises(SystemExit) as exc_info:
        cwm.main()
    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert (
        "Weder Pflicht-Songs noch zufällig gezogene Songs wurden in der "
        "Bibliothek gefunden" in out
    )
