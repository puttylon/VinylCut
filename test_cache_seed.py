import unicodedata
from pathlib import Path

import pytest

import cache_seed


class FakeCacheStore:
    """Minimaler Ersatz für cache_store.py (liegt hier evtl. noch nicht)."""

    def __init__(self):
        self.calls = []  # Liste von (quelle, kuenstler_key, titel_key, status, content)
        self.opened_with = None

    def open_cache(self, db_path):
        self.opened_with = db_path
        return "fake-conn"

    def normalize_key(self, text):
        return unicodedata.normalize("NFC", text).strip().lower()

    def put_provider(self, conn, quelle, kuenstler_key, titel_key, status, content):
        assert conn == "fake-conn"
        self.calls.append((quelle, kuenstler_key, titel_key, status, content))


@pytest.fixture
def fake_store(monkeypatch):
    store = FakeCacheStore()
    monkeypatch.setattr(cache_seed, "cache_store", store)
    return store


def test_seed_reads_lrc_with_audio_tags(tmp_path, fake_store, monkeypatch):
    folder = tmp_path / "Artist Ordner"
    folder.mkdir()
    lrc = folder / "01 Song.lrc"
    lrc.write_text("[00:01.00]Hallo Welt\n", encoding="utf-8")
    audio = folder / "01 Song.flac"
    audio.write_bytes(b"")  # Existenz reicht - _read_audio_tags wird unten gemockt

    # _read_audio_tags via mutagen selbst zu testen ist Sache von
    # fetch_songtext; hier nur sicherstellen, dass cache_seed die
    # zurückgelieferten Tags korrekt verwertet.
    def fake_read_audio_tags(path):
        assert path == audio
        return "Die Band", "Der Titel (Live)", ""

    monkeypatch.setattr(cache_seed, "_read_audio_tags", fake_read_audio_tags)

    eingelesen, uebersprungen = cache_seed.seed(tmp_path, tmp_path / "cache.db")

    assert eingelesen == 1
    assert uebersprungen == 0
    assert len(fake_store.calls) == 1
    quelle, kuenstler_key, titel_key, status, content = fake_store.calls[0]
    assert quelle == "lokal"
    assert status == "treffer"
    assert content == "[00:01.00]Hallo Welt\n"
    # Titel-Bereinigung: Klammerzusatz muss weg sein (siehe _clean_query_title)
    assert kuenstler_key == "die band"
    assert titel_key == "der titel"


def test_seed_falls_back_to_filename_and_folder_without_audio(tmp_path, fake_store):
    folder = tmp_path / "Fallback Artist"
    folder.mkdir()
    lrc = folder / "Ein Song Ohne Audio.lrc"
    lrc.write_text("[00:02.00]Nur Text\n", encoding="utf-8")

    eingelesen, uebersprungen = cache_seed.seed(tmp_path, tmp_path / "cache.db")

    assert eingelesen == 1
    assert uebersprungen == 0
    quelle, kuenstler_key, titel_key, status, content = fake_store.calls[0]
    assert kuenstler_key == "fallback artist"  # Ordnername als Fallback
    assert titel_key == "ein song ohne audio"  # Dateiname-Stamm als Fallback


def test_seed_uses_release_json_artist_fallback(tmp_path, fake_store):
    folder = tmp_path / "Any Folder Name"
    folder.mkdir()
    (folder / "release.json").write_text(
        '{"artist": "Release Artist", "tracks": []}', encoding="utf-8"
    )
    lrc = folder / "Some Title.lrc"
    lrc.write_text("[00:03.00]Text\n", encoding="utf-8")

    cache_seed.seed(tmp_path, tmp_path / "cache.db")

    _, kuenstler_key, _, _, _ = fake_store.calls[0]
    assert kuenstler_key == "release artist"


def test_seed_skips_empty_lrc(tmp_path, fake_store):
    folder = tmp_path / "X"
    folder.mkdir()
    (folder / "leer.lrc").write_text("", encoding="utf-8")
    (folder / "nur_whitespace.lrc").write_text("   \n\n", encoding="utf-8")

    eingelesen, uebersprungen = cache_seed.seed(tmp_path, tmp_path / "cache.db")

    assert eingelesen == 0
    assert uebersprungen == 2
    assert fake_store.calls == []


def test_seed_skips_unreadable_lrc_without_aborting(tmp_path, fake_store, monkeypatch):
    folder = tmp_path / "Y"
    folder.mkdir()
    good = folder / "gut.lrc"
    good.write_text("[00:01.00]OK\n", encoding="utf-8")
    bad = folder / "kaputt.lrc"
    bad.write_text("[00:01.00]Auch da\n", encoding="utf-8")

    real_read_text = Path.read_text

    def flaky_read_text(self, *args, **kwargs):
        if self.name == "kaputt.lrc":
            raise OSError("kaputte Datei, simuliert")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    eingelesen, uebersprungen = cache_seed.seed(tmp_path, tmp_path / "cache.db")

    assert eingelesen == 1
    assert uebersprungen == 1
    assert len(fake_store.calls) == 1


def test_seed_opens_cache_at_given_db_path(tmp_path, fake_store):
    db_path = tmp_path / "custom.db"
    cache_seed.seed(tmp_path, db_path)
    assert fake_store.opened_with == db_path


def test_seed_raises_without_cache_store(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_seed, "cache_store", None)
    with pytest.raises(RuntimeError):
        cache_seed.seed(tmp_path, tmp_path / "cache.db")
