"""Whisperer window: Start / Put Here / YouTube list buttons and a progress bar around batch_transcribe.py.

Run with `pyw Whisperer.pyw` (or the Whisperer shortcut). The script runs as a child process; its output
goes to logs\\run.log exactly like the PowerShell command in CLAUDE.md, so the usual status commands work.
"""
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "batch_transcribe.py"
SOURCE_DIR = HERE / "Source Files"
LANG_FOLDERS = ("uk", "auto")  # created under Source Files when the folder is opened
LINKS_FILE = HERE / "YouTube List.txt"
OUT_DIR = HERE / "transcripts"
OUT_SOURCE = OUT_DIR / "Source Files"
LOG_FILE = HERE / "logs" / "run.log"

LINKS_HEADER = (
    "# One link per line; lines starting with # are ignored. Playlists work too.\n"
    "# Optional language code before the link: uk, en, ... or auto (detect).\n"
    "# Example: uk https://www.youtube.com/watch?v=VIDEO_ID\n"
)

# Lines printed by batch_transcribe.py
RE_COUNT = re.compile(r"^(?:Source files|YouTube videos): \d+, already done: (\d+), to process: (\d+)")
RE_FILE = re.compile(r"^\[(Source|YouTube) \d+/\d+\] (.*?)\s+\(language: (\w+)\)$")
RE_PCT = re.compile(r"^\s+(\d+\.\d)%\s")
RE_DONE = re.compile(r"^\s+✓ Done in ([\d.]+) min")
FILE_FAILED = ("  ! Transcription failed", "! Download failed")


def short(s, n=58):
    return s if len(s) <= n else s[: n - 1] + "…"


def minutes(sec):
    return f"{sec / 60:.0f} min" if sec >= 90 else f"{sec:.0f} s"


class App:
    def __init__(self, root):
        self.root = root
        root.title("Whisperer")
        root.resizable(False, False)
        self.proc = None
        self.lines = queue.Queue()  # lines from the reader thread; an int marks the exit code
        self.state = "idle"  # idle | running | result
        self._reset()

        frm = ttk.Frame(root, padding=(14, 12, 14, 14))
        frm.pack(fill="both", expand=True)
        self.status = ttk.Label(frm, text="", font=("Segoe UI", 10, "bold"), width=54, anchor="w")
        self.status.pack(fill="x")
        self.detail = ttk.Label(frm, text="", foreground="#666666", anchor="w")
        self.detail.pack(fill="x", pady=(1, 8))
        self.bar = ttk.Progressbar(frm, maximum=1000)
        self.bar.pack(fill="x")
        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(12, 0))
        self.start_btn = ttk.Button(btns, text="Start", width=11, command=self.toggle)
        self.start_btn.pack(side="left")
        ttk.Button(btns, text="Put Here", width=11, command=self.open_source).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="YouTube list", width=11, command=self.open_links).pack(side="left", padx=(6, 0))
        self.result_btn = ttk.Button(btns, text="Result", width=11, command=self.open_results, state="disabled")
        self.result_btn.pack(side="right")

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind("<FocusIn>", lambda e: self.show_idle() if self.state == "idle" else None)
        self.show_idle()
        root.after(250, self.poll)

    def _reset(self):
        self.total = self.skipped = self.finished_files = self.failed = self.warnings = 0
        self.pct = 0.0
        self.current = self.kind = self.lang = ""
        self.file_start = self.run_start = None
        self.stopped = self.crashed = False

    def set(self, status, detail, color=""):
        self.status.config(text=status, foreground=color)
        self.detail.config(text=detail)

    # ----- idle -----
    def waiting_sources(self):
        if not SOURCE_DIR.exists():
            return 0
        return sum(1 for p in SOURCE_DIR.rglob("*") if p.is_file() and not (OUT_SOURCE / f"{p.stem}.vtt").exists())

    def show_idle(self):
        n = self.waiting_sources()
        if n:
            self.set("Ready", f"{n} recording{'s' if n != 1 else ''} in Source Files not transcribed yet")
        else:
            self.set("Ready", "no new recordings in Source Files; links go to the YouTube list")
        self.bar["value"] = 0

    # ----- buttons -----
    def toggle(self):
        if self.proc is None:
            self.start()
        elif messagebox.askyesno("Whisperer", "Stop the transcription?\nThe file being transcribed will start over next time."):
            self.stopped = True
            self.proc.terminate()

    def open_source(self):
        for name in LANG_FOLDERS:
            (SOURCE_DIR / name).mkdir(parents=True, exist_ok=True)
        os.startfile(SOURCE_DIR)

    def open_links(self):
        if not LINKS_FILE.exists():
            LINKS_FILE.write_text(LINKS_HEADER, encoding="utf-8")
        os.startfile(LINKS_FILE)

    def open_results(self):
        OUT_DIR.mkdir(exist_ok=True)
        os.startfile(OUT_DIR)

    def on_close(self):
        if self.proc is not None:
            if not messagebox.askyesno("Whisperer", "Transcription is running. Stop it and close?"):
                return
            self.proc.terminate()
        self.root.destroy()

    # ----- running the script -----
    def start(self):
        if not SCRIPT.exists():
            messagebox.showerror("Whisperer", f"{SCRIPT.name} not found next to {Path(__file__).name}")
            return
        LOG_FILE.parent.mkdir(exist_ok=True)
        self._reset()
        self.run_start = time.time()
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        python = Path(sys.executable).with_name("python.exe")  # not pythonw: the child needs stdout
        if not python.exists():
            python = Path(sys.executable)
        log = open(LOG_FILE, "w", encoding="utf-8")
        try:
            self.proc = subprocess.Popen(
                [str(python), str(SCRIPT)], cwd=HERE, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError as e:
            log.close()
            self.state = "result"
            self.set("Could not start", str(e), "red")
            return
        self.state = "running"
        threading.Thread(target=self.reader, args=(self.proc, log), daemon=True).start()
        self.start_btn.config(text="Stop")
        self.result_btn.config(state="disabled")
        self.bar["value"] = 0
        self.set("Starting…", "reading Source Files and the YouTube list")

    def reader(self, proc, log):
        with log:
            for line in proc.stdout:
                log.write(line)
                log.flush()
                self.lines.put(line.rstrip("\r\n"))
        self.lines.put(proc.wait())

    def poll(self):
        try:
            while True:
                item = self.lines.get_nowait()
                if isinstance(item, int):
                    self.finished(item)
                else:
                    self.handle(item)
        except queue.Empty:
            pass
        if self.state == "running" and self.file_start:
            self.show_progress()  # keeps the elapsed time ticking between output lines
        self.root.after(250, self.poll)

    def handle(self, line):
        if m := RE_COUNT.match(line):
            self.skipped += int(m[1])
            self.total += int(m[2])
            self.set("Starting…", f"{self.total} to transcribe, {self.skipped} already done")
        elif line.startswith("Loading model"):
            self.set("Loading model…", f"{self.total} to transcribe, {self.skipped} already done")
        elif m := RE_FILE.match(line):
            self.current, self.lang = m[2], m[3]
            self.kind = "recording" if m[1] == "Source" else "YouTube"
            self.pct, self.file_start = 0.0, time.time()
            self.show_progress()
        elif m := RE_PCT.match(line):
            self.pct = float(m[1])
            self.show_progress()
        elif m := RE_DONE.match(line):
            self.file_done()
            self.set(f"{self.finished_files}/{self.total} · {short(self.current)}", f"✓ done in {m[1]} min")
        elif line.startswith(FILE_FAILED):
            self.file_done()
            self.failed += 1
            self.set(f"{self.finished_files}/{self.total} · {short(self.current)}", short(line.strip(), 80))
        elif line.startswith("! "):
            self.warnings += 1
        elif line.startswith("Traceback"):
            self.crashed = True

    def file_done(self):
        """The current file now counts in finished_files, so its own % starts again from zero."""
        self.finished_files += 1
        self.pct, self.file_start = 0.0, None
        self.update_bar()

    def update_bar(self):
        self.bar["value"] = (self.finished_files + self.pct / 100) / self.total * 1000 if self.total else 0

    def show_progress(self):
        self.update_bar()
        elapsed = time.time() - self.file_start
        parts = [self.kind, f"language: {self.lang}", f"{self.pct:.0f}%", f"{minutes(elapsed)} elapsed"]
        if self.pct >= 3:
            parts.append(f"≈ {minutes(elapsed * (100 - self.pct) / self.pct)} left")
        self.set(f"{self.finished_files + 1}/{self.total} · {short(self.current)}", " · ".join(parts))

    def finished(self, code):
        self.proc = None
        self.state = "result"
        self.start_btn.config(text="Start")
        if self.finished_files - self.failed > 0:  # something new was transcribed in this run
            self.result_btn.config(state="normal")
        total = minutes(time.time() - self.run_start)
        if self.stopped:
            self.set("Stopped", f"{self.finished_files} of {self.total} finished; the unfinished file was not saved")
        elif self.crashed or code != 0:
            self.set("Error", f"the script stopped after {total} · see logs\\run.log", "red")
        elif self.total == 0:
            self.set("Nothing to do", "no new recordings in Source Files and no new links in the YouTube list")
        else:
            self.bar["value"] = 1000
            ok = self.finished_files - self.failed
            status = f"All done · {ok} transcribed" + (f", {self.failed} failed" if self.failed else "")
            detail = f"{total} · transcripts are in the transcripts folder"
            if self.warnings:
                detail += f" · {self.warnings} warning{'s' if self.warnings != 1 else ''} in logs\\run.log"
            self.set(status, detail, "red" if self.failed else "")


def main():
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)  # crisp text on scaled displays; must run before Tk()
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
