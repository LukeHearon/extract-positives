"""Command-line interface for extract_positives.

Help text is shared with the GUI via ``arguments.HINTS``.
"""

import argparse
import sys
from datetime import date, time
from pathlib import Path

from arguments import HINTS
from extract_positives import Config, extract_positives


def _parse_time(s: str) -> time:
    try:
        h, m = s.strip().split(":")
        return time(int(h), int(m))
    except Exception:
        raise argparse.ArgumentTypeError(f"'{s}' must be HH:MM (e.g. 22:00)")


def _parse_date(s: str) -> date:
    try:
        return date.fromisoformat(s.strip())
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{s}' must be YYYY-MM-DD (e.g. 2026-06-12)")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="extract_positives",
        description="Extract audio clips at buzz-detection positives.",
    )
    p.add_argument("audio", help=HINTS["audio"])
    p.add_argument("results", help=HINTS["results"])
    p.add_argument("output", help=HINTS["output"])

    p.add_argument("--threshold", type=float, default=-1.2, help=HINTS["threshold"])
    p.add_argument("--buffer", type=float, default=0.25, help=HINTS["buffer"])
    p.add_argument("--deadtime", type=float, default=0.1, help=HINTS["deadtime"])
    p.add_argument(
        "--join-mode", choices=["file", "recorder", "all"], default="file",
        help=HINTS["join_mode"],
    )
    p.add_argument(
        "--output-format", choices=["flac", "mp3", "wav"], default="flac",
        help=HINTS["output_format"],
    )
    p.add_argument("--time-from", type=_parse_time, help=HINTS["time_from"])
    p.add_argument("--time-to", type=_parse_time, help=HINTS["time_to"])
    p.add_argument("--date", dest="date_filter", type=_parse_date, help=HINTS["date_filter"])
    p.add_argument("--frame-n", type=int, help=HINTS["frame_n"])
    p.add_argument(
        "--frame-select", choices=["top", "random"], default="top",
        help=HINTS["frame_select"],
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if (args.time_from is None) != (args.time_to is None):
        print("error: --time-from and --time-to must be given together.", file=sys.stderr)
        return 2

    cfg = Config(
        threshold=args.threshold,
        buffer=args.buffer,
        deadtime=args.deadtime,
        join_mode=args.join_mode,
        output_format=args.output_format,
        time_from=args.time_from,
        time_to=args.time_to,
        date_filter=args.date_filter,
        frame_select=args.frame_select if args.frame_n is not None else None,
        frame_n=args.frame_n,
    )

    audio = Path(args.audio)
    results = Path(args.results)

    extract_positives(
        audio_dir=args.audio,
        results_dir=args.results,
        output_dir=args.output,
        audio_is_file=audio.is_file(),
        results_is_file=results.is_file(),
        cfg=cfg,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
