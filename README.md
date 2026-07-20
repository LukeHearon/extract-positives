# extract-positives

A little helper tool to extract the audio corresponding to positives in [buzzdetect](https://github.com/OSU-Bee-Lab/buzzdetect) results. It's a little ugly, but she runs!

## Requirements

```         
soundfile
pandas
numpy
```

Optional, only needed for reading `.wma` audio (not decodable by soundfile):

```
av
```

## Usage

### GUI

```         
python ui.py
```

Point it at your audio folder or file, the corresponding buzzdetect folder or results, and where you want the extracted audio to be written. Choose a threshold at which to call detections, then run.

A number of settings can filter your extractions:

- Use the buffer to add context on either side of each frame (it can be hard to recognize the sound in a short frame without hearing what comes before and after)

- Filter by time of day and/or by date to focus in on interesting (or suspicious) spikes in detections

- Extract a random subset of frames or the highest-activation frames to get a smaller snapshot

### CLI

```         
python cli.py AUDIO RESULTS OUTPUT [options]
```

`AUDIO`, `RESULTS` and `OUTPUT` may be folders or single files (auto-detected). Run `python cli.py --help` for the full option list — the help text matches the GUI's hover hints.

### Python API

``` python
from extract_positives import Config, extract_positives

cfg = Config(
    threshold=-1.2,
    buffer=0.25,
    deadtime=0.1,
    join_mode="file",       # "file" | "recorder" | "all"
    output_format="flac",   # "flac" | "mp3" | "wav"
    time_from=None,         # datetime.time or None
    time_to=None,
    date_filter=None,       # datetime.date or None
    frame_select=None,      # "top" | "random" | None
    frame_n=None,           # int or None
    datetime_format="%y%m%d_%H%M",  # strptime format for filename stems
    audio_format="mp3",     # input audio file extension; "wma" is read via PyAV
)

extract_positives(
    audio_dir="/path/to/audio",
    results_dir="/path/to/results",   # folder or single CSV
    output_dir="/path/to/output",
    cfg=cfg,
)
```