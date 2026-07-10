"""Unit-Tests für whisper_sample.py — reine Logikfunktionen (kein Netzwerk)."""

import json
from pathlib import Path

from whisper_sample import _find_candidates, _reject_reason


class TestRejectReason:
    def test_explizites_reason_feld(self):
        assert _reject_reason({"reason": "unter-schwelle"}) == "unter-schwelle"

    def test_legacy_kein_provider(self):
        assert _reject_reason({"providers": 0}) == "kein-provider"

    def test_legacy_kein_vokal(self):
        entry = {"providers": 2, "words": 0, "score": 0.0}
        assert _reject_reason(entry) == "kein-vokal"

    def test_legacy_unter_schwelle(self):
        entry = {"providers": 2, "words": 40, "score": 0.15}
        assert _reject_reason(entry) == "unter-schwelle"

    def test_legacy_kein_whisper(self):
        entry = {"providers": 2, "score": None}
        assert _reject_reason(entry) == "kein-whisper"


def _write_cache(dir_: Path, data: dict) -> None:
    (dir_ / ".fetch_songtext.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


class TestFindCandidates:
    def test_filtert_nach_reason_und_min_providers(self, tmp_path):
        album = tmp_path / "Artist" / "Album"
        album.mkdir(parents=True)
        _write_cache(
            album,
            {
                "01 kein-vokal 2P.flac": {
                    "r": "nf", "reason": "kein-vokal", "providers": 2, "words": 0
                },
                "02 kein-vokal 1P.flac": {
                    "r": "nf", "reason": "kein-vokal", "providers": 1, "words": 0
                },
                "03 unter-schwelle.flac": {
                    "r": "nf", "reason": "unter-schwelle", "providers": 3
                },
                "04 ok.flac": {"r": "ok", "reason": None, "providers": 3},
            },
        )
        result = _find_candidates(tmp_path, min_providers=2)
        tracks = {track for _, track, _ in result}
        assert tracks == {"01 kein-vokal 2P.flac"}

    def test_legacy_eintrag_ohne_reason_feld(self, tmp_path):
        album = tmp_path / "Artist" / "Album"
        album.mkdir(parents=True)
        _write_cache(
            album,
            {
                "01 legacy.flac": {
                    "r": "nf", "providers": 2, "words": 0, "score": 0.0
                },
            },
        )
        result = _find_candidates(tmp_path, min_providers=2)
        assert len(result) == 1
        assert result[0][1] == "01 legacy.flac"

    def test_kein_cache_kein_ergebnis(self, tmp_path):
        assert _find_candidates(tmp_path, min_providers=2) == []
