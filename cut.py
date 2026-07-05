#!/usr/bin/env python3
import sys
import json
import os
import subprocess
from pathlib import Path

import fetch_metadata as mf
from rich.console import Console
from rich.live import Live

from cut_ui import build_cutting_panel, build_metadata_panel, live_input

__version__ = "1.9.0"

DEFAULT_PLAY_DURATION_SEC = 3.0

console = Console()


def parse_offset(s: str) -> float:
    s = s.strip()
    sign = 1.0
    if s.startswith("+"):
        s, sign = s[1:], 1.0
    elif s.startswith("-"):
        s, sign = s[1:], -1.0
    if ":" in s:
        m, sec = s.split(":", 1)
        return sign * (int(m) * 60 + float(sec))
    return sign * float(s)


def estimate_start(i: int, tracks: list, starts: list, last_gap: float) -> float:
    if i == 0:
        return 0.0
    if "dur_s" in tracks[i - 1]:
        return starts[i - 1] + tracks[i - 1]["dur_s"] + last_gap
    return starts[i - 1]


def save_progress(progress_path: Path, history: list) -> None:
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump({"history": history}, f)


def play_snippet(flac_path: Path, start_time: float, duration: float) -> None:
    subprocess.run(
        [
            "ffplay",
            "-nodisp",
            "-autoexit",
            "-v",
            "quiet",
            "-ss",
            f"{start_time:.3f}",
            "-t",
            str(duration),
            str(flac_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def play_snippet_with_tone(flac_path: Path, start_time: float, duration: float) -> None:
    filter_complex = (
        "[0:a]aformat=channel_layouts=stereo[tone];"
        "[1:a]aformat=channel_layouts=stereo[audio];"
        "[tone][audio]concat=n=2:v=0:a=1[out]"
    )
    cmd = [
        "ffmpeg",
        "-v",
        "quiet",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=220:duration=0.25",
        "-ss",
        f"{start_time:.3f}",
        "-t",
        str(duration),
        "-i",
        str(flac_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[out]",
        "-f",
        "wav",
        "pipe:1",
    ]
    ffmpeg = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    subprocess.run(
        ["ffplay", "-nodisp", "-autoexit", "-v", "quiet", "-"],
        stdin=ffmpeg.stdout,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ffmpeg.wait()


def cut_and_tag(
    flac_path, out_file, track_num, title, artist, album, start_s, length_s, cover_path
):
    comment = f"cut.py v{__version__}"
    subprocess.run(
        ["sox", str(flac_path), str(out_file), "trim", f"{start_s}s", f"{length_s}s"],
        capture_output=True,
    )
    subprocess.run(
        [
            "metaflac",
            "--remove-all-tags",
            f"--set-tag=ARTIST={artist}",
            f"--set-tag=ALBUM={album}",
            f"--set-tag=TITLE={title}",
            f"--set-tag=TRACKNUMBER={track_num}",
            f"--set-tag=COMMENT={comment}",
            str(out_file),
        ],
        capture_output=True,
    )
    if cover_path.exists():
        subprocess.run(
            ["metaflac", f"--import-picture-from={cover_path}", str(out_file)],
            capture_output=True,
        )


def run_metadata_search(live, flac_path: Path, out_dir: Path, token: str) -> dict:
    stem = flac_path.stem
    artist, album = stem.split(" - ", 1) if " - " in stem else ("Unknown", stem)
    status: list[str] = []
    best_cand = None
    all_cands: list = []

    def refresh(cand=None, error=None):
        live.update(build_metadata_panel(artist, album, status, cand, error))
        live.refresh()

    flac_total = mf.get_flac_duration(flac_path)
    status.append(f"Dateidauer: {flac_total / 60:.1f} min — suche Discogs...")
    refresh()

    # Discogs search
    import urllib.parse
    import urllib.request

    results = []
    for page in range(1, 3):
        query = urllib.parse.quote(f"{artist} {album}")
        data = mf._get_json(
            f"{mf.DISCOGS_API}/database/search?type=release&q={query}&per_page=50&page={page}",
            token,
        )
        if not data:
            break
        results += data.get("results", [])
        if page >= data.get("pagination", {}).get("pages", 1):
            break

    plausible = [
        r
        for r in results
        if mf._name_matches(r.get("title", "").split(" - ")[-1], album)
    ]
    plausible.sort(
        key=lambda r: (
            0 if "vinyl" in " ".join(r.get("format", [])).lower() else 1,
            -r.get("community", {}).get("have", 0),
        )
    )

    best_score = 9999.0

    if not plausible:
        status.append("Keine Discogs-Treffer — suche MusicBrainz...")
        refresh()
        all_cands = mf.search_musicbrainz(artist, album, flac_total)
        if not all_cands:
            live_input(
                live,
                build_metadata_panel(
                    artist,
                    album,
                    status,
                    error="Kein passendes Release gefunden (weder Discogs noch MusicBrainz).",
                ),
                "[Enter] zum Beenden",
            )
            sys.exit(1)
        best_cand = min(all_cands, key=lambda c: mf.score_release(c, flac_total, album))
        status.append(f"✓ MusicBrainz: {len(all_cands)} Release(s) gefunden.")
        refresh(best_cand)
    else:
        n_check = min(len(plausible), mf.DEFAULT_MAX_RELEASES)
        status.append(
            f"Discogs: {len(results)} Ergebnisse, {len(plausible)} plausibel — prüfe bis zu {n_check}..."
        )
        status.append("")  # Platzhalter — wird pro Pressung überschrieben
        refresh()

        for idx, res in enumerate(plausible[: mf.DEFAULT_MAX_RELEASES], 1):
            rel_id = res.get("id")
            status[-1] = f"  Pressung {idx}/{n_check} (ID: {rel_id})..."
            refresh(best_cand)

            full = mf._get_json(f"{mf.DISCOGS_API}/releases/{rel_id}", token)
            if not full:
                continue
            tracks = [
                {
                    "title": (t.get("title") or "Track").strip(),
                    "dur_s": mf._parse_discogs_duration(t.get("duration", "")),
                }
                for t in full.get("tracklist", [])
                if t.get("type_") in (None, "track")
            ]
            if not tracks:
                continue
            fmts = ", ".join(f.get("name", "") for f in full.get("formats", []))
            cand = {
                "id": str(rel_id),
                "title": full.get("title", ""),
                "format": fmts,
                "is_vinyl": "vinyl" in fmts.lower(),
                "tracks": tracks,
                "cover_url": (full.get("images") or [{}])[0].get("uri"),
                "community_have": (
                    res.get("community", {}).get("have", 0)
                    if isinstance(res.get("community"), dict)
                    else 0
                ),
            }
            all_cands.append(cand)
            score = mf.score_release(cand, flac_total, album)
            if score < best_score:
                best_score = score
                best_cand = cand
                refresh(best_cand)
            if score <= 5.0 and cand["is_vinyl"]:
                status[-1] = f"✓ Perfekter Match (Score: {score:.1f})."
                refresh(best_cand)
                break

        if not best_cand:
            live_input(
                live,
                build_metadata_panel(
                    artist, album, status, error="Konnte keine validen Tracks laden."
                ),
                "[Enter] zum Beenden",
            )
            sys.exit(1)

        # MB fallback wenn keine Tracklängen
        if not any(t.get("dur_s") for t in best_cand["tracks"]):
            status.append("Keine Tracklängen — suche MusicBrainz...")
            refresh(best_cand)
            mb_cands = mf.search_musicbrainz(artist, album, flac_total)
            if mb_cands:
                mb_best = min(
                    mb_cands, key=lambda c: mf.score_release(c, flac_total, album)
                )
                if any(t.get("dur_s") for t in mb_best["tracks"]):
                    status.append("✓ MusicBrainz-Alternative mit Tracklängen.")
                    best_cand = mb_best
                    for c in mb_cands:
                        if not any(x["id"] == c["id"] for x in all_cands):
                            all_cands.append(c)
                    refresh(best_cand)

    # Interaktiver Override-Loop
    current_cand = best_cand
    while True:
        ans = live_input(
            live,
            build_metadata_panel(artist, album, status, current_cand),
            "[Enter] Akzeptieren, Discogs-ID oder MB-ID: ",
        )
        if not ans:
            break
        if mf._is_mbid(ans):
            status.append(f"Lade MB-Release {ans[:8]}...")
            refresh(current_cand)
            new_cand = mf.fetch_musicbrainz_by_id(ans)
        else:
            status.append(f"Lade Discogs-ID {ans}...")
            refresh(current_cand)
            new_cand = mf.fetch_discogs_by_id(ans, token)
        if new_cand:
            current_cand = new_cand
            if not any(c["id"] == new_cand["id"] for c in all_cands):
                all_cands.append(new_cand)
        else:
            status.append("✗ Fehler: ID nicht gefunden oder keine validen Tracks.")

    best_cand = current_cand
    for t in best_cand["tracks"]:
        if not t.get("dur_s"):
            t.pop("dur_s", None)

    # release.json schreiben
    with open(out_dir / "release.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "artist": artist,
                "album": album,
                "release_id": best_cand["id"],
                "tracks": best_cand["tracks"],
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    # Cover herunterladen
    cover_sorted = sorted(
        all_cands, key=lambda c: (0 if c["is_vinyl"] else 1, -c["community_have"])
    )
    for c in cover_sorted:
        if not c.get("cover_url"):
            continue
        try:
            with urllib.request.urlopen(
                urllib.request.Request(
                    c["cover_url"], headers={"User-Agent": mf.DISCOGS_UA}
                ),
                timeout=20,
            ) as r:
                (out_dir / "cover.jpg").write_bytes(r.read())
                status.append("✓ Cover gespeichert.")
            break
        except Exception:
            continue
    else:
        status.append("⚠ Cover-Download fehlgeschlagen.")

    refresh(current_cand)
    return {
        "artist": artist,
        "album": album,
        "release_id": best_cand["id"],
        "tracks": best_cand["tracks"],
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(
            f"VinylCut Interactive Cutter v{__version__}\n"
            "\nNutzung:\n"
            '  python3 cut.py "Pfad/zur/Artist - Album.flac"\n'
            "\nOptionen:\n"
            "  -h, --help          Diese Hilfe anzeigen\n"
            "  -V, --version       Versionsnummer ausgeben\n"
            "  --no-songtext       Songtext-Suche am Ende überspringen\n"
            "  --out <Verzeichnis> Ausgabeverzeichnis für geschnittene Tracks\n"
            "  --preview <Sek>     Snippet-Länge in Sekunden (Standard: 3)\n"
            "\nInteraktive Befehle während des Schneidens:\n"
            "  [p]         Snippet nochmal abspielen\n"
            "  [+] / [-]   Start ±0,5 s verschieben\n"
            "  [++]/[--]   Start ±2,0 s verschieben\n"
            "  [ok]        Startpunkt bestätigen, nächster Track\n"
            "  [u]         Letztes ok rückgängig machen\n"
            "  [n]         Normton (220 Hz, 0,25 s) vor Snippet aus-/einschalten (Standard: EIN)\n"
            "  Zahl/±m:ss  Start um Offset verschieben (z.B. +2:34 oder -30)"
        )
        sys.exit(0 if len(sys.argv) >= 2 else 1)

    if sys.argv[1] in ("-V", "--version"):
        print(f"cut.py {__version__}")
        sys.exit(0)

    args = sys.argv[1:]
    no_songtext = "--no-songtext" in args
    args = [a for a in args if a != "--no-songtext"]

    out_arg = None
    if "--out" in args:
        idx = args.index("--out")
        if idx + 1 >= len(args):
            print("Fehler: --out benötigt ein Verzeichnis.")
            sys.exit(1)
        out_arg = args[idx + 1]
        args = args[:idx] + args[idx + 2 :]

    preview_duration = DEFAULT_PLAY_DURATION_SEC
    if "--preview" in args:
        idx = args.index("--preview")
        if idx + 1 >= len(args):
            print("Fehler: --preview benötigt eine Sekundenangabe.")
            sys.exit(1)
        try:
            preview_duration = float(args[idx + 1])
        except ValueError:
            print("Fehler: --preview erwartet eine Zahl.")
            sys.exit(1)
        args = args[:idx] + args[idx + 2 :]

    if not args:
        print("Fehler: Kein FLAC-Pfad angegeben.")
        sys.exit(1)

    flac_path = Path(args[0]).resolve()
    out_dir = flac_path.parent / flac_path.stem
    track_out_dir = Path(out_arg).resolve() if out_arg else out_dir
    track_out_dir.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("DISCOGS_TOKEN", "")

    with Live(console=console, screen=True, auto_refresh=False) as live:
        # --- Metadaten ---
        data = run_metadata_search(live, flac_path, out_dir, token)

        # FLAC-Samplerate
        probe = json.loads(
            subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-select_streams",
                    "a:0",
                    "-show_entries",
                    "stream=sample_rate",
                    "-of",
                    "json",
                    str(flac_path),
                ],
                capture_output=True,
                text=True,
            ).stdout
        )
        sr = int(probe["streams"][0]["sample_rate"])

        n = len(data["tracks"])
        progress_path = out_dir / "progress.json"
        history: list = []
        starts: list = []
        last_gap = 0.0
        i = 0
        normton = True
        current_start = 0.0

        if progress_path.exists():
            with open(progress_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            history = saved["history"]
            starts = [h["start"] for h in history]
            last_gap = history[-1]["last_gap"] if history else 0.0
            i = len(starts)
            est = estimate_start(i, data["tracks"], starts, last_gap)
            live.update(
                build_cutting_panel(
                    data["artist"],
                    data["album"],
                    data["tracks"],
                    starts,
                    i,
                    est,
                    normton,
                    last_gap,
                    est,
                )
            )
            live.refresh()
            ans = live_input(
                live,
                build_cutting_panel(
                    data["artist"],
                    data["album"],
                    data["tracks"],
                    starts,
                    i,
                    est,
                    True,
                    last_gap,
                    est,
                ),
                f"Fortschritt gefunden ({i}/{n} Tracks). Fortsetzen? [j/n]: ",
            ).lower()
            if ans != "j":
                history, starts, last_gap, i = [], [], 0.0, 0
                progress_path.unlink()

        def panel(phase="cutting", export_status=None, lrc_status=None):
            est = (
                estimate_start(i, data["tracks"], starts, last_gap)
                if phase == "cutting"
                else 0.0
            )
            return build_cutting_panel(
                data["artist"],
                data["album"],
                data["tracks"],
                starts,
                i,
                current_start if phase == "cutting" else 0.0,
                normton,
                last_gap,
                est,
                phase,
                export_status,
                lrc_status,
            )

        # --- Schneiden ---
        while i < n:
            current_start = estimate_start(i, data["tracks"], starts, last_gap)

            while True:
                live.update(panel())
                live.refresh()

                if normton:
                    play_snippet_with_tone(flac_path, current_start, preview_duration)
                else:
                    play_snippet(flac_path, current_start, preview_duration)

                action = live_input(live, panel(), "> ")

                if action == "p":
                    continue
                elif action == "+":
                    current_start += 0.5
                elif action == "-":
                    current_start = max(0.0, current_start - 0.5)
                elif action == "++":
                    current_start += 2.0
                elif action == "--":
                    current_start = max(0.0, current_start - 2.0)
                elif action == "n":
                    normton = not normton
                elif action == "u":
                    if i > 0:
                        history.pop()
                        starts = [h["start"] for h in history]
                        last_gap = history[-1]["last_gap"] if history else 0.0
                        save_progress(progress_path, history)
                        i -= 1
                        break
                elif action == "ok":
                    starts.append(current_start)
                    if i > 0 and "dur_s" in data["tracks"][i - 1]:
                        last_gap = current_start - (
                            starts[i - 1] + data["tracks"][i - 1]["dur_s"]
                        )
                    history.append({"start": current_start, "last_gap": last_gap})
                    save_progress(progress_path, history)
                    i += 1
                    break
                else:
                    try:
                        current_start = max(0.0, current_start + parse_offset(action))
                    except ValueError:
                        pass

        progress_path.unlink(missing_ok=True)

        # --- Export ---
        export_status = [""] * n
        for idx, track in enumerate(data["tracks"]):
            live.update(panel("export", export_status))
            live.refresh()
            start_smp = round(starts[idx] * sr)
            if idx < len(starts) - 1:
                len_smp = round((starts[idx + 1] - starts[idx]) * sr)
            else:
                total_dur_s = float(
                    subprocess.check_output(
                        [
                            "ffprobe",
                            "-v",
                            "error",
                            "-show_entries",
                            "format=duration",
                            "-of",
                            "default=noprint_wrappers=1:nokey=1",
                            str(flac_path),
                        ]
                    )
                )
                len_smp = round((total_dur_s * sr) - start_smp)
            cut_and_tag(
                flac_path,
                track_out_dir
                / f"{idx + 1:02d} - {track['title'].replace('/', '_')}.flac",
                idx + 1,
                track["title"],
                data["artist"],
                data["album"],
                start_smp,
                len_smp,
                out_dir / "cover.jpg",
            )
            export_status[idx] = "✓"

        live.update(panel("export", export_status))
        live.refresh()

        # --- Songtexte ---
        lrc_status = None
        if not no_songtext:
            lrc_status = [""] * n
            live.update(panel("songtext", export_status, lrc_status))
            live.refresh()

            token_path = Path(__file__).parent / "genius_token"
            env = os.environ.copy()
            if token_path.exists():
                token = token_path.read_text().strip()
                if token:
                    env["GENIUS_ACCESS_TOKEN"] = token

            artist = data.get("artist", "")
            for idx, track in enumerate(data["tracks"]):
                safe = track["title"].replace("/", "_")
                lrc_path = track_out_dir / f"{idx + 1:02d} - {safe}.lrc"
                query = f"{artist} {track['title']}".strip()
                lrc_status[idx] = "…"
                live.update(panel("songtext", export_status, lrc_status))
                live.refresh()
                try:
                    subprocess.run(
                        ["syncedlyrics", query, "-o", str(lrc_path)],
                        capture_output=True,
                        text=True,
                        env=env,
                    )
                except FileNotFoundError:
                    lrc_status[idx] = "✗"
                    for j in range(idx + 1, n):
                        lrc_status[j] = "✗"
                    break
                lrc_status[idx] = "✓" if lrc_path.exists() else "✗"
                live.update(panel("songtext", export_status, lrc_status))
                live.refresh()

        last_phase = "songtext" if not no_songtext else "export"
        last_es = export_status
        last_lrc = lrc_status
        live_input(live, panel(last_phase, last_es, last_lrc), "[Enter] zum Beenden")


if __name__ == "__main__":
    main()
