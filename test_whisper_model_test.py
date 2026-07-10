"""Unit-Tests für whisper_model_test.py — reine Logikfunktionen (kein Netzwerk/Whisper)."""

from whisper_model_test import _load_json, _save_json


class TestLoadSaveJson:
    def test_roundtrip(self, tmp_path):
        path = tmp_path / "out.json"
        data = {"a.flac": {"small": {"score": 0.5, "words": 10}}}
        _save_json(path, data)
        assert _load_json(path, {}) == data

    def test_load_missing_gibt_default(self, tmp_path):
        path = tmp_path / "fehlt.json"
        assert _load_json(path, {}) == {}
        assert _load_json(path, None) is None

    def test_load_kaputtes_json_gibt_default(self, tmp_path):
        path = tmp_path / "kaputt.json"
        path.write_text("{nicht valides json", encoding="utf-8")
        assert _load_json(path, {}) == {}


class TestResumeLogik:
    """Simuliert die 'welche Modelle fehlen noch' Logik aus main()."""

    def test_alle_modelle_fehlen_bei_leerem_eintrag(self):
        entry = {}
        models = ["small", "medium"]
        missing = [m for m in models if m not in entry]
        assert missing == ["small", "medium"]

    def test_nur_fehlendes_modell_wird_erkannt(self):
        entry = {"small": {"score": 0.5, "words": 10}}
        models = ["small", "medium"]
        missing = [m for m in models if m not in entry]
        assert missing == ["medium"]

    def test_kein_modell_fehlt_wenn_vollstaendig(self):
        entry = {"small": {"score": 0.5, "words": 10}, "medium": {"score": 0.6, "words": 12}}
        models = ["small", "medium"]
        missing = [m for m in models if m not in entry]
        assert missing == []
