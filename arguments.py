"""Shared help text for the settings.

Both the Tkinter GUI (hover hints) and the CLI (``--help`` text) pull their
descriptions from ``HINTS`` so the two interfaces stay in sync.
"""

HINTS: dict[str, str] = {
    "audio": (
        "The input audio file or folder of audio files."
        "Audio file timestamps must follow the YYMMDD_HHMM format."
    ),
    "results": (
        "A folder of buzzdetect results, a single buzzdetect results file,"
        "or multiple files joined together with an 'ident' column to distinguish  files."
    ),
    "output": (
        "Where to write the extracted clips. A folder when outputs aren't joined,"
        "a file when output is set to Join all."
    ),
    "threshold": "Minimum activation_ins_buzz value for a frame to be included.",
    "buffer": "Include extra audio on either side of each frame."
    "Note: overlapping buffers will be merged into a single extracted segment.",
    "deadtime": "A period of silence inserted between extracted segments so that their boundaries are distinct.",
    "join_mode": (
        "How to group output clips. 'file': one file per recording; "
        "'recorder': one file per recorder directory; "
        "'all': a single combined file."
    ),
    "output_format": "Audio container for the written clips: flac, mp3 or wav.",
    "time_from": (
        "Start of the detection time window (HH:MM). Combine with --time-to "
        "to keep only detections within the window; the window may wrap midnight."
    ),
    "time_to": "End of the detection time window (HH:MM). See --time-from.",
    "date_filter": "Keep only detections on this date (YYYY-MM-DD).",
    "datetime_format": (
        "strptime format used to parse the recording start datetime from "
        "filenames (default: %y%m%d_%H%M). Only used when filtering by time or date."
    ),
    "audio_format": (
        "File extension of the input audio recordings (e.g. mp3, wav, wma). "
        "Used to locate audio files when results are a folder or an ident CSV. "
        "wma is decoded via PyAV since soundfile can't read it."
    ),
    "frame_n": "Cap the number of detection frames kept per recording.",
    "frame_select": (
        "When limiting frames, which to keep: 'top' (highest score) or "
        "'random'."
    ),
}
