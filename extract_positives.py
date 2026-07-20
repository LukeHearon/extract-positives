import soundfile as sf
import pandas as pd
import numpy as np
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal


SAMPLERATE = 44100
FRAME_DURATION = 0.96
DEFAULT_DATETIME_FORMAT = "%y%m%d_%H%M"
DEFAULT_AUDIO_FORMAT = "mp3"
# Extensions soundfile (libsndfile) can't decode; these are read via PyAV instead.
AV_ONLY_FORMATS = {"wma"}

JoinMode = Literal["file", "recorder", "all"]
FrameSelect = Literal["random", "top"]


@dataclass
class Config:
    threshold: float
    buffer: float
    deadtime: float
    join_mode: JoinMode
    output_format: str
    time_from: time | None = None
    time_to: time | None = None
    date_filter: date | None = None
    frame_select: FrameSelect | None = None
    frame_n: int | None = None
    datetime_format: str = DEFAULT_DATETIME_FORMAT
    audio_format: str = DEFAULT_AUDIO_FORMAT
    workers: int = 4

    @property
    def silence(self) -> np.ndarray:
        return np.zeros(int(self.deadtime * SAMPLERATE))

    @property
    def needs_datetime(self) -> bool:
        """Whether any filter actually depends on the recording start datetime."""
        return self.time_from is not None or self.date_filter is not None


def _parse_file_datetime(stem: str, fmt: str = DEFAULT_DATETIME_FORMAT) -> datetime | None:
    """Parse the recording start datetime from a stem like YYMMDD_HHMM[_suffix]."""
    try:
        parts = stem.split("_")
        n_parts = fmt.count("_") + 1
        return datetime.strptime("_".join(parts[:n_parts]), fmt)
    except Exception:
        return None


def _open_track(path: Path):
    """Open an audio file for seek/read, soundfile.SoundFile-alike.

    libsndfile (soundfile's backend) can't decode WMA/ASF; those go through
    the PyAV-based driver in drivers/wma.py instead.
    """
    if path.suffix.lower().lstrip(".") in AV_ONLY_FORMATS:
        from drivers.wma import Driver

        return Driver(str(path))
    return sf.SoundFile(str(path))


def _in_window(t: time, time_from: time, time_to: time) -> bool:
    if time_from <= time_to:
        return time_from <= t < time_to
    return t >= time_from or t < time_to


# Rough bitrate assumption for size estimation of lossy formats (soundfile's
# libsndfile-backed mp3 encoder defaults to ~128 kbps CBR).
_MP3_BITRATE_BPS = 128_000
# FLAC is lossless but variable; ~0.6x of PCM16 is a reasonable ballpark for
# speech/field-recording content.
_FLAC_RATIO = 0.6


def _human_size(n_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024 or unit == "GB":
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:.1f} GB"


def _estimate_bytes(total_seconds: float, channels: int, fmt: str) -> float:
    pcm16_bytes = total_seconds * SAMPLERATE * channels * 2
    if fmt == "wav":
        return pcm16_bytes
    if fmt == "flac":
        return pcm16_bytes * _FLAC_RATIO
    if fmt == "mp3":
        return total_seconds * (_MP3_BITRATE_BPS / 8) * channels
    return pcm16_bytes


def _merge_segments(starts: list[float]) -> list[tuple[float, float]]:
    """Merge overlapping / adjacent 0.96 s frames into (start, end) pairs."""
    if not starts:
        return []
    segs: list[tuple[float, float]] = []
    cur_s = starts[0]
    cur_e = starts[0] + FRAME_DURATION
    for s in starts[1:]:
        e = s + FRAME_DURATION
        if s <= cur_e + 0.01:
            cur_e = max(cur_e, e)
        else:
            segs.append((cur_s, cur_e))
            cur_s, cur_e = s, e
    segs.append((cur_s, cur_e))
    return segs


def _plan_recording(
    ident: str,
    frames_df: pd.DataFrame,
    file_dt: datetime,
    cfg: Config,
) -> list[tuple[float, float]] | None:
    """Apply time/frame filters and return merged (start, end) segments, or None if empty."""

    def frame_matches(offset: float, _dt=file_dt) -> bool:
        detection_dt = _dt + timedelta(seconds=float(offset))
        if cfg.date_filter is not None and detection_dt.date() != cfg.date_filter:
            return False
        if cfg.time_from is not None and not _in_window(detection_dt.time(), cfg.time_from, cfg.time_to):
            return False
        return True

    if cfg.time_from is None and cfg.date_filter is None:
        window_df = frames_df
    else:
        window_df = frames_df[frames_df["start"].apply(frame_matches)]

    if window_df.empty:
        return None

    if cfg.frame_select is not None and cfg.frame_n is not None and cfg.frame_n < len(window_df):
        if cfg.frame_select == "random":
            window_df = window_df.sample(n=cfg.frame_n)
        else:
            window_df = window_df.nlargest(cfg.frame_n, "activation_ins_buzz")

    return _merge_segments(sorted(window_df["start"].tolist()))


def _group_key_and_path(
    ident: str,
    out_root: Path,
    cfg: Config,
    out_file_override: Path | None,
) -> tuple[str, Path]:
    ident_path = Path(ident)
    recorder_rel = ident_path.parent
    ext = f".{cfg.output_format}"

    if out_file_override is not None:
        return ident, out_file_override
    if cfg.join_mode == "recorder":
        return str(recorder_rel), out_root / recorder_rel / f"positives{ext}"
    if cfg.join_mode == "all":
        return "", out_root
    return ident, out_root / ident_path.parent / f"{ident_path.name}_positives{ext}"


def _process_recordings(
    recordings: list[tuple[str, Path, pd.DataFrame, datetime]],
    out_root: Path,
    cfg: Config,
    out_file_override: Path | None = None,
) -> None:
    # First pass: apply filters and compute segments per recording without
    # touching audio, so we can preview output size and group durations
    # before any decoding/writing happens.
    plans: list[tuple[str, Path, list[tuple[float, float]], str, Path]] = []
    group_seconds: dict[str, float] = defaultdict(float)
    group_channels: dict[str, int] = {}
    group_outpath: dict[str, Path] = {}

    for ident, mp3_path, frames_df, file_dt in recordings:
        print(f"\n{ident}")
        segments = _plan_recording(ident, frames_df, file_dt, cfg)
        if not segments:
            print("  none in time window")
            continue
        print(f"  {len(segments)} segment(s)")

        key, out_path = _group_key_and_path(ident, out_root, cfg, out_file_override)
        group_outpath.setdefault(key, out_path)

        seg_seconds = sum((e - s) + cfg.buffer * 2 + cfg.deadtime for s, e in segments)
        group_seconds[key] += seg_seconds
        if key not in group_channels:
            try:
                group_channels[key] = _open_track(Path(mp3_path)).channels
            except Exception:
                group_channels[key] = 1

        plans.append((ident, mp3_path, segments, key, out_path))

    if not plans:
        print("\nNo detections found in the time window.")
        return

    print("\nEstimated output size:")
    for key, seconds in group_seconds.items():
        size = _estimate_bytes(seconds, group_channels.get(key, 1), cfg.output_format)
        print(f"  {group_outpath[key]}: ~{_human_size(size)} ({seconds:.1f}s)")

    # Second pass: stream clips straight to disk via an open SoundFile writer
    # per output group, instead of buffering every clip in memory.
    writers: dict[str, sf.SoundFile] = {}
    written_keys: set[str] = set()

    def get_writer(key: str, channels: int) -> sf.SoundFile:
        w = writers.get(key)
        if w is None:
            out_file = group_outpath[key]
            out_file.parent.mkdir(parents=True, exist_ok=True)
            w = sf.SoundFile(str(out_file), mode="w", samplerate=SAMPLERATE, channels=channels, format=cfg.output_format.upper())
            writers[key] = w
        return w

    def read_clips(plan: tuple[str, Path, list[tuple[float, float]], str, Path]) -> tuple[list[np.ndarray], list[str], int]:
        """Runs in a worker thread. Seeks/reads are sequential within one track
        (segments are already time-sorted), so this preserves the seek-friendly
        access pattern per file while letting independent files' I/O overlap."""
        ident, mp3_path, segments, _key, _out_path = plan
        logs: list[str] = []
        try:
            track = _open_track(Path(mp3_path))
        except Exception as e:
            return [], [f"\n{ident}\n  cannot open audio: {e}"], 1

        silence = cfg.silence
        clips: list[np.ndarray] = []
        try:
            for seg_s, seg_e in segments:
                seek = max(0, int((seg_s - cfg.buffer) * SAMPLERATE))
                n = int(((seg_e - seg_s) + cfg.buffer * 2) * SAMPLERATE)
                try:
                    track.seek(seek)
                    samples = track.read(n)
                    if len(samples) < n:
                        pad_shape = (n - len(samples), track.channels) if track.channels > 1 else (n - len(samples),)
                        samples = np.concatenate([samples, np.zeros(pad_shape)])
                    clips.append(samples)
                    clips.append(silence)
                except Exception as e:
                    logs.append(f"\n{ident}\n  error at {seg_s:.2f}s: {e}")
            return clips, logs, track.channels
        finally:
            track.close()

    try:
        with ThreadPoolExecutor(max_workers=cfg.workers) as pool:
            # map preserves plan order for the writer below while letting the
            # pool run several files' reads concurrently in the background.
            for (ident, mp3_path, segments, key, out_path), (clips, logs, channels) in zip(
                plans, pool.map(read_clips, plans)
            ):
                for log in logs:
                    print(log)
                if clips:
                    writer = get_writer(key, channels)
                    for clip in clips:
                        writer.write(clip)
                    written_keys.add(key)

                if cfg.join_mode == "file" and key in writers:
                    writers.pop(key).close()
                    if key in written_keys:
                        print(f"  Written: {out_path}")
    finally:
        for w in writers.values():
            w.close()

    if not written_keys:
        print("\nNo detections found in the time window.")
    else:
        if cfg.join_mode != "file":
            for key in written_keys:
                print(f"Written: {group_outpath[key]}")
        print("\nDone.")


def extract_positives(
    audio_dir: str,
    results_dir: str,
    output_dir: str,
    cfg: Config,
    audio_is_file: bool = False,
    results_is_file: bool = False,
) -> None:
    """
    Extract detections within a time window and write audio clips.

    results_dir may be either:
      • a folder  – walked for *_buzzdetect.csv / *_buzzpart.csv files
      • a CSV file – must have columns: ident, start, activation_ins_buzz
                     where ident is a path relative to audio_dir (no extension)

    Output join_mode (set via cfg):
      "file"     – one output file per input recording  (default)
                   output_dir is a folder
      "recorder" – one output file per recorder subdirectory
                   output_dir is a folder
      "all"      – single file combining everything
                   output_dir is that file's path
    """
    results_path = Path(results_dir)
    audio_path = Path(audio_dir)
    out_root = Path(output_dir)

    if results_is_file:
        _extract_from_ident_csv(results_path, audio_path, out_root, cfg, audio_is_file)
    else:
        _extract_from_folder(results_path, audio_path, out_root, cfg)


def _extract_from_folder(
    results_path: Path,
    audio_path: Path,
    out_root: Path,
    cfg: Config,
) -> None:
    csv_files = sorted(
        list(results_path.rglob("*_buzzdetect.csv"))
        + list(results_path.rglob("*_buzzpart.csv"))
    )

    if not csv_files:
        print("No *_buzzdetect.csv or *_buzzpart.csv files found.")
        return

    print(f"Found {len(csv_files)} result file(s).  Join mode: {cfg.join_mode}")

    recordings = []
    for csv_path in csv_files:
        rel_dir = csv_path.parent.relative_to(results_path)
        stem = csv_path.stem                   # e.g. 260430_1142_buzzdetect
        base = "_".join(stem.split("_")[:2])   # e.g. 260430_1142
        ident = str(rel_dir / base)

        file_dt = None
        if cfg.needs_datetime:
            file_dt = _parse_file_datetime(stem, cfg.datetime_format)
            if file_dt is None:
                print(f"\nSkipping {csv_path.name}: cannot parse datetime.")
                continue

        mp3_path = audio_path if audio_path.is_file() else audio_path / rel_dir / f"{base}.{cfg.audio_format}"
        if not mp3_path.exists():
            print(f"\nSkipping {csv_path.name}: audio not found at {mp3_path}")
            continue

        df = pd.read_csv(csv_path, usecols=["start", "activation_ins_buzz"])
        df = df[df["activation_ins_buzz"] > cfg.threshold]

        if df.empty:
            print(f"\n{rel_dir / csv_path.name}")
            print("  no detections above threshold")
            continue

        recordings.append((ident, mp3_path, df[["start", "activation_ins_buzz"]], file_dt))

    _process_recordings(recordings, out_root, cfg)


def _extract_from_ident_csv(
    csv_file: Path,
    audio_path: Path,
    out_root: Path,
    cfg: Config,
    audio_is_file: bool = False,
) -> None:
    if audio_is_file:
        _extract_single_audio_from_csv(csv_file, audio_path, out_root, cfg)
        return

    df = pd.read_csv(csv_file, usecols=["ident", "start", "activation_ins_buzz"])
    df = df[df["activation_ins_buzz"] > cfg.threshold].copy()

    if df.empty:
        print("No detections above threshold in the CSV.")
        return

    idents = df["ident"].unique()
    print(f"Found {len(idents)} recording(s) in ident CSV.  Join mode: {cfg.join_mode}")

    recordings = []
    for ident in idents:
        stem = Path(ident).name
        file_dt = None
        if cfg.needs_datetime:
            file_dt = _parse_file_datetime(stem, cfg.datetime_format)
            if file_dt is None:
                print(f"\nSkipping {ident}: cannot parse datetime from '{stem}'.")
                continue

        mp3_path = audio_path / f"{ident}.{cfg.audio_format}"
        if not mp3_path.exists():
            print(f"\nSkipping {ident}: audio not found at {mp3_path}")
            continue

        rows = df[df["ident"] == ident]
        recordings.append((ident, mp3_path, rows[["start", "activation_ins_buzz"]], file_dt))

    _process_recordings(recordings, out_root, cfg)


def _extract_single_audio_from_csv(
    csv_file: Path,
    audio_path: Path,
    out_root: Path,
    cfg: Config,
) -> None:
    audio_stem = audio_path.stem
    cols = pd.read_csv(csv_file, nrows=0).columns.tolist()
    has_ident = "ident" in cols

    if has_ident:
        df = pd.read_csv(csv_file, usecols=["ident", "start", "activation_ins_buzz"])
        df = df[df["activation_ins_buzz"] > cfg.threshold].copy()
        df = df[df["ident"].apply(lambda x: Path(x).name) == audio_stem]
    else:
        df = pd.read_csv(csv_file, usecols=["start", "activation_ins_buzz"])
        df = df[df["activation_ins_buzz"] > cfg.threshold].copy()

    if df.empty:
        print(f"No detections above threshold for {audio_stem}.")
        return

    file_dt = None
    if cfg.needs_datetime:
        file_dt = _parse_file_datetime(audio_stem, cfg.datetime_format)
        if file_dt is None:
            print(f"Cannot parse datetime from audio filename '{audio_stem}'.")
            return

    print(f"Found {len(df)} detection(s) for {audio_stem}.")
    _process_recordings(
        [(audio_stem, audio_path, df[["start", "activation_ins_buzz"]], file_dt)],
        out_root,
        cfg,
        out_file_override=out_root,
    )
