import json
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from datetime import date, time
from pathlib import Path

from extract_positives import Config, extract_positives

_CACHE_PATH = Path.home() / ".extract_positives_last_run.json"


# ── helpers ──────────────────────────────────────────────────────────────────

def _pick_folder(var: tk.StringVar, callback=None) -> None:
    path = filedialog.askdirectory()
    if path:
        var.set(path)
        if callback:
            callback(path)


def _parse_time(s: str, label: str) -> time:
    try:
        h, m = s.strip().split(":")
        return time(int(h), int(m))
    except Exception:
        raise ValueError(f"{label} must be HH:MM (e.g. 22:00)")


def _parse_date(s: str) -> date:
    s = s.strip()
    try:
        d = date.fromisoformat(s)
    except ValueError:
        raise ValueError("Date must be YYYY-MM-DD (e.g. 2026-06-12)")
    if len(s) != 10 or s[4] != "-" or s[7] != "-":
        raise ValueError("Date must be YYYY-MM-DD (e.g. 2026-06-12)")
    return d


def _parse_float(s: str, label: str) -> float:
    try:
        return float(s.strip())
    except Exception:
        raise ValueError(f"{label} must be a number")


# ── log redirect ─────────────────────────────────────────────────────────────

class _LogStream:
    def __init__(self, widget: scrolledtext.ScrolledText) -> None:
        self._w = widget

    def write(self, text: str) -> None:
        self._w.after(0, self._append, text)

    def _append(self, text: str) -> None:
        self._w.configure(state="normal")
        self._w.insert(tk.END, text)
        self._w.see(tk.END)
        self._w.configure(state="disabled")

    def flush(self) -> None:
        pass


# ── main window ──────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Positives Extractor")
        self.resizable(False, False)

        self._audio_dir    = tk.StringVar()
        self._audio_mode   = tk.StringVar(value="folder")
        self._results_dir  = tk.StringVar()
        self._results_mode = tk.StringVar(value="folder")
        self._output_dir   = tk.StringVar()
        self._output_is_file = False
        self._threshold  = tk.StringVar(value="-1.2")
        self._filter_time = tk.BooleanVar(value=False)
        self._time_from   = tk.StringVar(value="22:00")
        self._time_to     = tk.StringVar(value="05:00")
        self._filter_date = tk.BooleanVar(value=False)
        self._date_filter = tk.StringVar(value="")
        self._buffer     = tk.StringVar(value="0.25")
        self._deadtime    = tk.StringVar(value="0.1")
        self._join_mode      = tk.StringVar(value="file")
        self._output_format  = tk.StringVar(value="flac")
        self._limit_frames   = tk.BooleanVar(value=False)
        self._frame_n        = tk.StringVar(value="50")
        self._frame_select   = tk.StringVar(value="top")

        self._build_ui()
        self._load_cache()

    # ── cache ────────────────────────────────────────────────────────────────

    def _save_cache(self) -> None:
        data = {
            "audio_dir":     self._audio_dir.get(),
            "audio_mode":    self._audio_mode.get(),
            "results_dir":   self._results_dir.get(),
            "results_mode":  self._results_mode.get(),
            "output_dir":    self._output_dir.get(),
            "threshold":     self._threshold.get(),
            "filter_time":   self._filter_time.get(),
            "time_from":     self._time_from.get(),
            "time_to":       self._time_to.get(),
            "filter_date":   self._filter_date.get(),
            "date_filter":   self._date_filter.get(),
            "buffer":        self._buffer.get(),
            "deadtime":      self._deadtime.get(),
            "join_mode":     self._join_mode.get(),
            "output_format": self._output_format.get(),
            "limit_frames":  self._limit_frames.get(),
            "frame_n":       self._frame_n.get(),
            "frame_select":  self._frame_select.get(),
        }
        try:
            _CACHE_PATH.write_text(json.dumps(data, indent=2))
        except OSError:
            pass

    def _load_cache(self) -> None:
        try:
            data = json.loads(_CACHE_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return
        self._audio_dir.set(data.get("audio_dir", ""))
        self._audio_mode.set(data.get("audio_mode", "folder"))
        self._results_dir.set(data.get("results_dir", ""))
        self._results_mode.set(data.get("results_mode", "folder"))
        self._output_dir.set(data.get("output_dir", ""))
        self._threshold.set(data.get("threshold", "-1.2"))
        self._filter_time.set(data.get("filter_time", False))
        self._time_from.set(data.get("time_from", "22:00"))
        self._time_to.set(data.get("time_to", "05:00"))
        self._filter_date.set(data.get("filter_date", False))
        self._date_filter.set(data.get("date_filter", ""))
        self._buffer.set(data.get("buffer", "0.25"))
        self._deadtime.set(data.get("deadtime", "0.1"))
        self._join_mode.set(data.get("join_mode", "file"))
        self._output_format.set(data.get("output_format", "flac"))
        self._limit_frames.set(data.get("limit_frames", False))
        self._frame_n.set(data.get("frame_n", "50"))
        self._frame_select.set(data.get("frame_select", "top"))

    # ── auto-fill siblings ───────────────────────────────────────────────────

    def _on_audio_dir_set(self, path: str) -> None:
        """After audio path is picked, look for a sibling 'results' dir."""
        p = Path(path)
        # For a file inside e.g. .../1_145/audio/, go up two levels to find
        # the sibling results/ next to audio/. For a folder, one level suffices.
        base = p.parent.parent if p.is_file() else p.parent
        sibling_results = base / "results"
        if sibling_results.is_dir() and not self._results_dir.get():
            if p.is_file():
                ident = p.stem
                matches = list(sibling_results.glob(f"{ident}*.csv"))
                if matches:
                    self._results_mode.set("file")
                    self._results_dir.set(str(matches[0]))
                    if not self._output_dir.get():
                        ext = self._output_format.get()
                        self._output_dir.set(str(base / "positives" / f"{ident}.{ext}"))
                    return
            self._results_dir.set(str(sibling_results))
            self._on_results_dir_set(str(sibling_results))

    def _on_results_dir_set(self, path: str) -> None:
        """Default output to 'nightpositives' next to the results dir/file."""
        if not self._output_dir.get():
            p = Path(path)
            base = p.parent if p.is_file() else p.parent
            self._output_dir.set(str(base / "positives"))

    # ── layout ───────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        pad = {"padx": 4, "pady": 3}
        f = ttk.Frame(self, padding=12)
        f.grid(sticky="nsew")

        self._audio_row(f, 0)
        self._results_row(f, 1)
        self._output_row(f, 2)

        ttk.Separator(f, orient="horizontal").grid(
            row=3, column=0, columnspan=3, sticky="ew", pady=8
        )

        params = ttk.Frame(f)
        params.grid(row=4, column=0, columnspan=3, sticky="w")

        def param(label, var, col):
            ttk.Label(params, text=label).grid(row=0, column=col * 2, sticky="w", **pad)
            ttk.Entry(params, textvariable=var, width=8).grid(
                row=0, column=col * 2 + 1, **pad
            )

        param("Threshold",     self._threshold, 0)
        param("Buffer (s)",    self._buffer,    1)
        param("Dead time (s)", self._deadtime,  2)

        time_frame = ttk.Frame(f)
        time_frame.grid(row=5, column=0, columnspan=3, sticky="w", padx=4, pady=(0, 4))

        ttk.Checkbutton(
            time_frame, text="Filter by time", variable=self._filter_time
        ).pack(side="left", padx=(0, 10))

        ttk.Label(time_frame, text="From").pack(side="left")
        self._time_from_entry = ttk.Entry(time_frame, textvariable=self._time_from, width=8)
        self._time_from_entry.pack(side="left", padx=(4, 10))

        ttk.Label(time_frame, text="To").pack(side="left")
        self._time_to_entry = ttk.Entry(time_frame, textvariable=self._time_to, width=8)
        self._time_to_entry.pack(side="left", padx=4)

        def _sync_time_filter(*_):
            state = "normal" if self._filter_time.get() else "disabled"
            self._time_from_entry.configure(state=state)
            self._time_to_entry.configure(state=state)

        self._filter_time.trace_add("write", _sync_time_filter)
        _sync_time_filter()  # set initial state

        date_frame = ttk.Frame(f)
        date_frame.grid(row=6, column=0, columnspan=3, sticky="w", padx=4, pady=(0, 4))

        ttk.Checkbutton(
            date_frame, text="Filter by date", variable=self._filter_date
        ).pack(side="left", padx=(0, 10))

        ttk.Label(date_frame, text="Date (YYYY-MM-DD)").pack(side="left")
        self._date_filter_entry = ttk.Entry(date_frame, textvariable=self._date_filter, width=12)
        self._date_filter_entry.pack(side="left", padx=(4, 0))

        def _sync_date_filter(*_):
            state = "normal" if self._filter_date.get() else "disabled"
            self._date_filter_entry.configure(state=state)

        self._filter_date.trace_add("write", _sync_date_filter)
        _sync_date_filter()

        frame_limit_frame = ttk.Frame(f)
        frame_limit_frame.grid(row=7, column=0, columnspan=3, sticky="w", padx=4, pady=(0, 4))

        ttk.Checkbutton(
            frame_limit_frame, text="Limit frames", variable=self._limit_frames
        ).pack(side="left", padx=(0, 10))

        ttk.Label(frame_limit_frame, text="N").pack(side="left")
        self._frame_n_entry = ttk.Entry(frame_limit_frame, textvariable=self._frame_n, width=6)
        self._frame_n_entry.pack(side="left", padx=(4, 12))

        self._frame_select_random = ttk.Radiobutton(
            frame_limit_frame, text="Random", variable=self._frame_select, value="random"
        )
        self._frame_select_random.pack(side="left", padx=(0, 4))
        self._frame_select_top = ttk.Radiobutton(
            frame_limit_frame, text="Top by score", variable=self._frame_select, value="top"
        )
        self._frame_select_top.pack(side="left")

        def _sync_frame_limit(*_):
            state = "normal" if self._limit_frames.get() else "disabled"
            self._frame_n_entry.configure(state=state)
            self._frame_select_random.configure(state=state)
            self._frame_select_top.configure(state=state)

        self._limit_frames.trace_add("write", _sync_frame_limit)
        _sync_frame_limit()

        ttk.Separator(f, orient="horizontal").grid(
            row=8, column=0, columnspan=3, sticky="ew", pady=8
        )

        join_frame = ttk.Frame(f)
        join_frame.grid(row=9, column=0, columnspan=3, pady=(0, 6))
        ttk.Label(join_frame, text="Join output:").pack(side="left", padx=(0, 10))
        self._join_btns: list[ttk.Radiobutton] = []
        for label, value in [("By file", "file"), ("By recorder", "recorder"), ("Join all", "all")]:
            btn = ttk.Radiobutton(
                join_frame, text=label, variable=self._join_mode, value=value
            )
            btn.pack(side="left", padx=6)
            self._join_btns.append(btn)

        def _sync_audio_file_mode(*_):
            is_single = self._audio_mode.get() == "file"
            join_state = "disabled" if is_single else "normal"
            for btn in self._join_btns:
                btn.configure(state=join_state)
            if is_single:
                self._join_mode.set("all")
                self._results_mode.set("file")
                self._output_label.configure(text="Output file")
                self._output_is_file = True
            else:
                self._output_label.configure(text="Output folder")
                self._output_is_file = False
            for btn in self._results_mode_btns:
                btn.configure(state="disabled" if is_single else "normal")

        self._audio_mode.trace_add("write", _sync_audio_file_mode)

        fmt_frame = ttk.Frame(f)
        fmt_frame.grid(row=10, column=0, columnspan=3, pady=(0, 6))
        ttk.Label(fmt_frame, text="Output format:").pack(side="left", padx=(0, 10))
        for label, value in [("FLAC", "flac"), ("MP3", "mp3"), ("WAV", "wav")]:
            ttk.Radiobutton(
                fmt_frame, text=label, variable=self._output_format, value=value
            ).pack(side="left", padx=6)

        self._run_btn = ttk.Button(f, text="Run", command=self._run)
        self._run_btn.grid(row=11, column=0, columnspan=3, pady=(0, 8))

        self._log = scrolledtext.ScrolledText(
            f, width=80, height=18, state="disabled",
            font=("Menlo", 11), bg="#1e1e1e", fg="#d4d4d4",
            insertbackground="white",
        )
        self._log.grid(row=12, column=0, columnspan=3, sticky="nsew")

    def _audio_row(self, parent, row: int) -> None:
        pad = {"padx": 4, "pady": 3}
        ttk.Label(parent, text="Audio", width=16, anchor="w").grid(
            row=row, column=0, sticky="w", **pad
        )
        ttk.Entry(parent, textvariable=self._audio_dir, width=52).grid(
            row=row, column=1, sticky="ew", **pad
        )

        btn_frame = ttk.Frame(parent)
        btn_frame.grid(row=row, column=2, sticky="w", **pad)

        def browse():
            if self._audio_mode.get() == "file":
                path = filedialog.askopenfilename(
                    filetypes=[("Audio files", "*.mp3 *.wav *.flac"), ("All files", "*.*")]
                )
            else:
                path = filedialog.askdirectory()
            if path:
                self._audio_dir.set(path)
                self._on_audio_dir_set(path)

        ttk.Button(btn_frame, text="Browse…", width=8, command=browse).pack(side="left")

        toggle_frame = ttk.Frame(btn_frame)
        toggle_frame.pack(side="left", padx=(6, 0))
        ttk.Radiobutton(
            toggle_frame, text="Folder", variable=self._audio_mode, value="folder"
        ).pack(side="left")
        ttk.Radiobutton(
            toggle_frame, text="File", variable=self._audio_mode, value="file"
        ).pack(side="left")

    def _results_row(self, parent, row: int) -> None:
        pad = {"padx": 4, "pady": 3}
        ttk.Label(parent, text="Results", width=16, anchor="w").grid(
            row=row, column=0, sticky="w", **pad
        )
        ttk.Entry(parent, textvariable=self._results_dir, width=52).grid(
            row=row, column=1, sticky="ew", **pad
        )

        btn_frame = ttk.Frame(parent)
        btn_frame.grid(row=row, column=2, sticky="w", **pad)

        def browse():
            if self._results_mode.get() == "file":
                path = filedialog.askopenfilename(
                    filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
                )
            else:
                path = filedialog.askdirectory()
            if path:
                self._results_dir.set(path)
                self._on_results_dir_set(path)

        self._results_browse_btn = ttk.Button(btn_frame, text="Browse…", width=8, command=browse)
        self._results_browse_btn.pack(side="left")

        toggle_frame = ttk.Frame(btn_frame)
        toggle_frame.pack(side="left", padx=(6, 0))
        self._results_mode_btns: list[ttk.Radiobutton] = []
        for text, value in [("Folder", "folder"), ("File", "file")]:
            rb = ttk.Radiobutton(
                toggle_frame, text=text, variable=self._results_mode, value=value
            )
            rb.pack(side="left")
            self._results_mode_btns.append(rb)

    def _output_row(self, parent, row: int) -> None:
        pad = {"padx": 4, "pady": 3}
        self._output_label = ttk.Label(parent, text="Output folder", width=16, anchor="w")
        self._output_label.grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(parent, textvariable=self._output_dir, width=52).grid(
            row=row, column=1, sticky="ew", **pad
        )

        def browse():
            if self._output_is_file:
                fmt = self._output_format.get()
                ext = f".{fmt}"
                type_label = fmt.upper() + " files"
                path = filedialog.asksaveasfilename(
                    defaultextension=ext,
                    filetypes=[(type_label, f"*{ext}"), ("All files", "*.*")],
                )
            else:
                path = filedialog.askdirectory()
            if path:
                self._output_dir.set(path)

        ttk.Button(parent, text="Browse…", width=8, command=browse).grid(
            row=row, column=2, **pad
        )

    # ── run ──────────────────────────────────────────────────────────────────

    def _run(self) -> None:
        errors = []
        if not self._audio_dir.get():
            label = "Audio file" if self._audio_mode.get() == "file" else "Audio folder"
            errors.append(f"{label} is required.")
        if not self._results_dir.get():
            label = "Results file" if self._results_mode.get() == "file" else "Results folder"
            errors.append(f"{label} is required.")
        if not self._output_dir.get():
            label = "Output file" if self._audio_mode.get() == "file" else "Output folder"
            errors.append(f"{label} is required.")
        if errors:
            messagebox.showerror("Missing inputs", "\n".join(errors))
            return

        try:
            threshold = _parse_float(self._threshold.get(), "Threshold")
            if self._filter_time.get():
                time_from = _parse_time(self._time_from.get(), "Time from")
                time_to   = _parse_time(self._time_to.get(),   "Time to")
            else:
                time_from = None
                time_to   = None
            if self._filter_date.get():
                date_filter = _parse_date(self._date_filter.get())
            else:
                date_filter = None
            buffer   = _parse_float(self._buffer.get(),   "Buffer")
            deadtime = _parse_float(self._deadtime.get(), "Dead time")
            if self._limit_frames.get():
                try:
                    frame_n = int(self._frame_n.get().strip())
                    if frame_n < 1:
                        raise ValueError()
                except Exception:
                    raise ValueError("N must be a positive integer")
                frame_select = self._frame_select.get()
            else:
                frame_n = None
                frame_select = None
        except ValueError as exc:
            messagebox.showerror("Invalid input", str(exc))
            return

        self._save_cache()
        self._run_btn.configure(state="disabled")
        self._clear_log()

        def worker():
            old_stdout = sys.stdout
            sys.stdout = _LogStream(self._log)
            try:
                extract_positives(
                    audio_dir=self._audio_dir.get(),
                    results_dir=self._results_dir.get(),
                    output_dir=self._output_dir.get(),
                    cfg=Config(
                        threshold=threshold,
                        buffer=buffer,
                        deadtime=deadtime,
                        join_mode=self._join_mode.get(),
                        output_format=self._output_format.get(),
                        time_from=time_from,
                        time_to=time_to,
                        date_filter=date_filter,
                        frame_select=frame_select,
                        frame_n=frame_n,
                    ),
                )
            except Exception as exc:
                print(f"\nERROR: {exc}")
            finally:
                sys.stdout = old_stdout
                self.after(0, lambda: self._run_btn.configure(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    def _clear_log(self) -> None:
        self._log.configure(state="normal")
        self._log.delete("1.0", tk.END)
        self._log.configure(state="disabled")


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
