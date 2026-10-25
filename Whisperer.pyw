"""Whisperer window: Start / Put Here / YouTube list / Result buttons and a progress bar around batch_transcribe.py.

From source: `pyw Whisperer.pyw` on Windows (or the Whisperer shortcut), `python3 Whisperer.pyw` on a Mac.
The script runs as a child process in the data folder (whisperer_core.data_dir); its output goes to logs/run.log
exactly like the PowerShell command in CLAUDE.md, so the usual status commands work.

The packaged app runs this file for everything:
    Whisperer                 the window
    Whisperer --worker        the transcription (started by the window)
    Whisperer --self-test [audio]   versions, paths, optional test transcription (used by the build)
"""
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, ttk

import whisperer_core as core

DATA = core.data_dir()
SOURCE_DIR = DATA / "Source Files"
LINKS_FILE = DATA / "YouTube List.txt"
OUT_DIR = DATA / "transcripts"
OUT_SOURCE = OUT_DIR / "Source Files"
LOG_FILE = DATA / "logs" / "run.log"
LOG_HINT = "logs\\run.log" if core.IS_WINDOWS else "logs/run.log"
GRAY = "#666666" if core.IS_WINDOWS else "gray50"

LINKS_HEADER = (
    "# One link per line; lines starting with # are ignored. Playlists work too.\n"
    "# Optional language code before the link: uk, en, ... or auto (detect).\n"
    "# Example: uk https://www.youtube.com/watch?v=VIDEO_ID\n"
)
COMMON_LANGUAGES = ["auto", "uk", "en", "pl", "de", "fr", "es", "it", "pt", "ro", "cs", "tr", "ar", "he", "zh", "ja", "ko"]

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


def bold_font(size=None):
    if core.IS_WINDOWS:
        return ("Segoe UI", size or 10, "bold")
    f = tkfont.nametofont("TkDefaultFont").copy()
    f.configure(weight="bold", **({"size": size} if size else {}))
    return f


def keep_awake(on):
    """Windows: stop the PC from sleeping during a run (sleep kills it). The Mac uses caffeinate, see App.start."""
    if core.IS_WINDOWS:
        try:
            from ctypes import windll
            ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
            windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
        except Exception:
            pass


class App:
    def __init__(self, root):
        self.root = root
        root.title("Whisperer")
        root.resizable(False, False)
        self.proc = None
        self.awake = None  # caffeinate on the Mac
        self.lines = queue.Queue()  # lines from the reader thread; an int marks the exit code
        self.ui_calls = queue.Queue()  # callables from background threads, run on the Tk thread
        self.state = "idle"  # idle | running | result | updating
        self._reset()

        frm = ttk.Frame(root, padding=(14, 12, 14, 14))
        frm.pack(fill="both", expand=True)
        self.status = ttk.Label(frm, text="", font=bold_font(), width=54, anchor="w")
        self.status.pack(fill="x")
        self.detail = ttk.Label(frm, text="", foreground=GRAY, anchor="w")
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
        self.build_menu()

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind("<FocusIn>", lambda e: self.show_idle() if self.state == "idle" else None)
        self.show_idle()
        root.after(250, self.poll)
        if core.FROZEN and core.load_settings()["check_updates"]:
            root.after(1500, lambda: self.check_updates(auto=True))

    def _reset(self):
        self.total = self.skipped = self.finished_files = self.failed = self.warnings = 0
        self.pct = 0.0
        self.current = self.kind = self.lang = ""
        self.file_start = self.run_start = None
        self.stopped = self.crashed = self.model_download = False

    def set(self, status, detail, color=""):
        self.status.config(text=status, foreground=color)
        self.detail.config(text=detail)

    def ui(self, fn):
        """Run fn on the Tk thread (Tk isn't thread-safe)."""
        self.ui_calls.put(fn)

    def build_menu(self):
        menubar = tk.Menu(self.root)
        if core.IS_MAC:
            appmenu = tk.Menu(menubar, name="apple")  # the menu named after the app
            appmenu.add_command(label="About Whisperer", command=self.about)
            appmenu.add_command(label="Check for Updates…", command=lambda: self.check_updates(auto=False))
            appmenu.add_separator()
            menubar.add_cascade(menu=appmenu)
            self.root.createcommand("tk::mac::ShowPreferences", self.open_settings)  # Whisperer > Settings…, Cmd+,
            self.root.createcommand("tk::mac::Quit", self.on_close)  # Cmd+Q asks first while a run is going
        else:
            menubar.add_command(label="Settings", command=self.open_settings)
            menubar.add_command(label="Check for updates", command=lambda: self.check_updates(auto=False))
            menubar.add_command(label="About", command=self.about)
        self.root.config(menu=menubar)

    # ----- idle -----
    def waiting_sources(self):
        if not SOURCE_DIR.exists():
            return 0
        return sum(1 for p in SOURCE_DIR.rglob("*")
                   if p.is_file() and p.suffix.lower() in core.MEDIA_EXT and not (OUT_SOURCE / f"{p.stem}.vtt").exists())

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
        for name in core.LANG_FOLDERS:
            (SOURCE_DIR / name).mkdir(parents=True, exist_ok=True)
        core.open_path(SOURCE_DIR)

    def open_links(self):
        if not LINKS_FILE.exists():
            DATA.mkdir(parents=True, exist_ok=True)
            LINKS_FILE.write_text(LINKS_HEADER, encoding="utf-8")
        core.open_path(LINKS_FILE)

    def open_results(self):
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        core.open_path(OUT_DIR)

    def about(self):
        messagebox.showinfo("Whisperer", f"Whisperer {core.VERSION}\n\nEngine: {core.ENGINE}\n"
                                         f"Your files: {DATA}\n\ngithub.com/{core.REPO}")

    def on_close(self):
        if self.proc is not None:
            if not messagebox.askyesno("Whisperer", "Transcription is running. Stop it and close?"):
                return
            self.proc.terminate()
        if self.state == "updating" and not messagebox.askyesno("Whisperer", "An update is downloading. Cancel it and close?"):
            return
        keep_awake(False)
        self.root.destroy()

    # ----- running the script -----
    def start(self):
        if not core.FROZEN and not (core.SOURCE_DIR / "batch_transcribe.py").exists():
            messagebox.showerror("Whisperer", f"batch_transcribe.py not found next to {Path(__file__).name}")
            return
        model = core.load_settings()["model"]
        need_model = not core.model_cached(model)
        if need_model and not messagebox.askyesno("Whisperer", f"The {model} speech model needs to be downloaded once "
                                                               f"({core.model_size(model)}).\n\nDownload it now and start?"):
            return
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._reset()
        self.model_download = need_model
        self.run_start = time.time()
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        log = open(LOG_FILE, "w", encoding="utf-8")
        try:
            self.proc = subprocess.Popen(
                core.worker_command(), cwd=DATA, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if core.IS_WINDOWS else 0,
            )
        except OSError as e:
            log.close()
            self.state = "result"
            self.set("Could not start", str(e), "red")
            return
        keep_awake(True)
        if core.IS_MAC:
            try:  # no idle sleep while the worker runs; caffeinate exits with it
                self.awake = subprocess.Popen(["caffeinate", "-i", "-w", str(self.proc.pid)])
            except OSError:
                self.awake = None
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
                self.ui_calls.get_nowait()()
        except queue.Empty:
            pass
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
            if self.model_download:
                self.bar.config(mode="indeterminate")
                self.bar.start(15)
                self.set("Downloading the speech model…", "only the first time; this can take a while")
            else:
                self.set("Loading model…", f"{self.total} to transcribe, {self.skipped} already done")
        elif m := RE_FILE.match(line):
            self.stop_indeterminate()
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

    def stop_indeterminate(self):
        if str(self.bar.cget("mode")) == "indeterminate":
            self.bar.stop()
            self.bar.config(mode="determinate")

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
        self.stop_indeterminate()
        keep_awake(False)
        if self.awake is not None:
            self.awake.terminate()
            self.awake = None
        self.start_btn.config(text="Start")
        if self.finished_files - self.failed > 0:  # something new was transcribed in this run
            self.result_btn.config(state="normal")
        total = minutes(time.time() - self.run_start)
        if self.stopped:
            self.set("Stopped", f"{self.finished_files} of {self.total} finished; the unfinished file was not saved")
        elif self.crashed or code != 0:
            self.set("Error", f"the script stopped after {total} · see {LOG_HINT}", "red")
        elif self.total == 0:
            self.set("Nothing to do", "no new recordings in Source Files and no new links in the YouTube list")
        else:
            self.bar["value"] = 1000
            ok = self.finished_files - self.failed
            status = f"All done · {ok} transcribed" + (f", {self.failed} failed" if self.failed else "")
            detail = f"{total} · transcripts are in the transcripts folder"
            if self.warnings:
                detail += f" · {self.warnings} warning{'s' if self.warnings != 1 else ''} in {LOG_HINT}"
            self.set(status, detail, "red" if self.failed else "")

    # ----- settings -----
    def open_settings(self):
        SettingsDialog(self.root)

    # ----- updates -----
    def check_updates(self, auto):
        if self.state == "updating":
            return

        def work():
            import whisperer_update as upd
            try:
                release = upd.latest_release()
                ytdlp = None
                if core.FROZEN:
                    ytdlp = (upd.ytdlp_version(), upd.ytdlp_latest())
            except Exception as e:
                if not auto:
                    self.ui(lambda: messagebox.showerror("Whisperer", f"Couldn't check for updates:\n{e}"))
                return
            self.ui(lambda: self.offer_updates(release, ytdlp, auto))

        threading.Thread(target=work, daemon=True).start()

    def offer_updates(self, release, ytdlp, auto):
        import whisperer_update as upd
        settings = core.load_settings()
        app_new = release is not None and upd.is_newer(release["version"])
        if app_new and not (auto and settings["skip_version"] == release["version"]):
            if self.proc is not None:
                if not auto:
                    messagebox.showinfo("Whisperer", f"Whisperer {release['version']} is available. "
                                                     "Install it when the transcription has finished.")
                return
            choice = ask_update(self.root, f"Whisperer {release['version']} is available",
                                f"You have {core.VERSION}.", release["notes"] or "No release notes.",
                                ["Update", "Later", "Skip this version"])
            if choice == "Skip this version":
                settings["skip_version"] = release["version"]
                core.save_settings(settings)
            elif choice == "Update":
                ok, why = upd.can_install_app_update()
                if ok:
                    self.run_update(f"Whisperer {release['version']}",
                                    lambda progress: upd.prepare_app_update(release, progress), self.finish_app_update)
                elif messagebox.askyesno("Whisperer", f"{why}\n\nOpen the download page instead?"):
                    webbrowser.open(release["url"])
            return  # one dialog at a time; yt-dlp is offered on the next check
        if ytdlp and core.parse_version(ytdlp[1]["version"]) > core.parse_version(ytdlp[0]):
            if self.proc is not None:
                return
            if ask_update(self.root, f"yt-dlp {ytdlp[1]['version']} is available",
                          f"You have {ytdlp[0]}. yt-dlp downloads YouTube audio; new versions fix it when YouTube changes.",
                          None, ["Update", "Later"]) == "Update":
                self.run_update(f"yt-dlp {ytdlp[1]['version']}",
                                lambda progress: upd.install_ytdlp(ytdlp[1], progress), self.finish_ytdlp_update)
            return
        if not auto:
            if release is None:
                messagebox.showinfo("Whisperer", f"You have Whisperer {core.VERSION}. No releases are published yet.")
            else:
                extra = "" if core.FROZEN else "\n\n(Running from source code: update with git pull.)"
                messagebox.showinfo("Whisperer", f"Whisperer {core.VERSION} is up to date.{extra}")

    def run_update(self, what, job, done):
        """Runs job(progress) in a thread with the progress bar; then done(result) on the Tk thread."""
        self.state = "updating"
        self.start_btn.config(state="disabled")
        self.bar["value"] = 0
        self.set(f"Downloading {what}…", "")

        def progress(got, total):
            def show():
                self.bar["value"] = got / total * 1000 if total else 0
                self.detail.config(text=f"{got / 1e6:.0f} of {total / 1e6:.0f} MB" if total else f"{got / 1e6:.0f} MB")
            self.ui(show)

        def work():
            try:
                result = job(progress)
            except Exception as e:
                self.ui(lambda: self.update_failed(e))
                return
            self.ui(lambda: done(result))

        threading.Thread(target=work, daemon=True).start()

    def update_failed(self, error):
        self.state = "result"  # not idle: keeps the message when the window gets focus
        self.start_btn.config(state="normal")
        self.bar["value"] = 0
        self.set("Update failed", short(str(error), 80), "red")

    def finish_app_update(self, new_app):
        import whisperer_update as upd
        upd.install_app_update(new_app)
        self.set("Installing the update…", "Whisperer will reopen in a moment")
        self.root.after(300, self.root.destroy)

    def finish_ytdlp_update(self, _):
        import whisperer_update as upd
        self.state = "result"
        self.start_btn.config(state="normal")
        self.bar["value"] = 1000
        self.set("yt-dlp updated", f"version {upd.ytdlp_version()} is used from the next Start")


def ask_update(root, title, text, notes, buttons):
    """Modal dialog with optional release notes; returns the clicked button's label (None if closed)."""
    win = tk.Toplevel(root)
    win.title("Whisperer")
    win.transient(root)
    win.resizable(False, False)
    choice = {"value": None}
    frm = ttk.Frame(win, padding=16)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text=title, font=bold_font(12)).pack(anchor="w")
    ttk.Label(frm, text=text, wraplength=420, justify="left").pack(anchor="w", pady=(4, 8))
    if notes:
        box = tk.Text(frm, width=60, height=10, wrap="word", relief="solid", borderwidth=1)
        box.insert("1.0", notes)
        box.config(state="disabled")
        box.pack(fill="both", pady=(0, 10))
    row = ttk.Frame(frm)
    row.pack(fill="x")
    for label in reversed(buttons):
        def pick(label=label):
            choice["value"] = label
            win.destroy()
        ttk.Button(row, text=label, command=pick).pack(side="right", padx=(6, 0))
    win.grab_set()
    root.wait_window(win)
    return choice["value"]


class SettingsDialog:
    def __init__(self, root):
        self.settings = core.load_settings()
        self.win = win = tk.Toplevel(root)
        win.title("Settings")
        win.transient(root)
        win.resizable(False, False)
        frm = ttk.Frame(win, padding=16)
        frm.pack(fill="both", expand=True)

        self.models = {f"{name} — {'best quality' if name == 'large-v3' else 'faster'}, {info['size']}": name
                       for name, info in core.MODELS.items()}
        if self.settings["model"] not in core.MODELS:
            self.models[self.settings["model"]] = self.settings["model"]
        current = next(label for label, name in self.models.items() if name == self.settings["model"])
        self.model = tk.StringVar(value=current)
        self.yt_lang = tk.StringVar(value=self.settings["youtube_language"])
        self.src_lang = tk.StringVar(value=self.settings["source_language"])
        self.keep_audio = tk.BooleanVar(value=self.settings["keep_audio"])
        self.check_updates = tk.BooleanVar(value=self.settings["check_updates"])

        rows = [
            ("Speech model", ttk.Combobox(frm, textvariable=self.model, values=list(self.models), state="readonly", width=38)),
            ("Language of YouTube links\nwithout a code", ttk.Combobox(frm, textvariable=self.yt_lang, values=COMMON_LANGUAGES, width=10)),
            ("Language of files directly\nin Source Files", ttk.Combobox(frm, textvariable=self.src_lang, values=COMMON_LANGUAGES, width=10)),
        ]
        for i, (label, widget) in enumerate(rows):
            ttk.Label(frm, text=label, justify="left").grid(row=i, column=0, sticky="w", pady=4, padx=(0, 12))
            widget.grid(row=i, column=1, sticky="w", pady=4)
        ttk.Label(frm, text="Files in Source Files/uk and Source Files/auto always use that folder's language.",
                  foreground=GRAY, wraplength=380, justify="left").grid(row=3, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Checkbutton(frm, text="Keep downloaded YouTube audio", variable=self.keep_audio).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Checkbutton(frm, text="Check for updates when Whisperer starts", variable=self.check_updates).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Label(frm, text=f"Your files: {DATA}", foreground=GRAY, wraplength=380, justify="left").grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(10, 0))
        row = ttk.Frame(frm)
        row.grid(row=7, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(row, text="Save", command=self.save).pack(side="right", padx=(6, 0))
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="right")
        win.grab_set()

    def save(self):
        langs = {}
        for key, var in (("youtube_language", self.yt_lang), ("source_language", self.src_lang)):
            code = var.get().strip().lower() or "auto"
            if code not in COMMON_LANGUAGES:
                import whisperer_engine
                if code not in whisperer_engine.language_codes():
                    messagebox.showerror("Settings", f"Unknown language code '{code}'.\nUse a code like uk, en, de - or auto.",
                                         parent=self.win)
                    return
            langs[key] = code
        self.settings.update(langs, model=self.models[self.model.get()], keep_audio=self.keep_audio.get(),
                             check_updates=self.check_updates.get())
        core.save_settings(self.settings)
        self.win.destroy()


# ---------- packaged-app modes ----------
def run_worker():
    for fd, name in ((1, "stdout"), (2, "stderr")):  # a windowed app may start without Python-level streams
        if getattr(sys, name) is None:
            setattr(sys, name, open(fd, "w", encoding="utf-8", errors="replace", closefd=False))
    core.activate_overlay()  # a newer yt-dlp from the updater wins over the bundled one
    DATA.mkdir(parents=True, exist_ok=True)
    os.chdir(DATA)
    import batch_transcribe
    try:
        batch_transcribe.main()
    except BaseException:
        import traceback
        traceback.print_exc()  # the window looks for "Traceback"
        core.hard_exit(1)
    core.hard_exit(0)


def self_test(args):
    import faulthandler
    for stream in (sys.stdout, sys.stderr):  # every line out at once, also when the watchdog below exits
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(line_buffering=True)
    # if something hangs (e.g. a GPU without a working Metal driver), print where every thread is and exit
    faulthandler.dump_traceback_later(int(os.environ.get("WHISPERER_SELFTEST_TIMEOUT", "240")), exit=True)
    core.activate_overlay()
    import av
    import yt_dlp
    import whisperer_engine as engine
    print(f"Whisperer {core.VERSION} | engine {core.ENGINE} | Python {sys.version.split()[0]} | packaged {core.FROZEN}")
    print(f"yt_dlp {yt_dlp.version.__version__} from {Path(yt_dlp.__file__).parent}")
    print(f"av {av.__version__} | languages {len(engine.language_codes())}")
    print(f"data folder {core.data_dir()} | support folder {core.support_dir()}")
    if args:
        repo = os.environ.get("WHISPERER_MODEL") or core.model_repo(core.load_settings()["model"])
        print(f"loading {repo}")
        started = time.time()
        model = engine.load(repo)
        print(f"loaded in {time.time() - started:.1f} s; transcribing {args[0]}")
        found = []

        def show(s, duration):
            print(f"  [{s.start:6.2f} - {s.end:6.2f}] {s.text.strip()}")
            found.append(s)

        started = time.time()
        duration = engine.transcribe(model, args[0], None, show)
        print(f"duration {duration:.1f} s, {len(found)} segments, transcribed in {time.time() - started:.1f} s")
        if not found:
            print("self-test FAILED: no speech recognized")
            return 1
    print("self-test OK")
    return 0


def main():
    args = sys.argv[1:]
    if args[:1] == ["--worker"]:
        run_worker()
        return
    if args[:1] == ["--self-test"]:
        try:
            code = self_test(args[1:])
        except BaseException:
            import traceback
            traceback.print_exc()
            code = 1
        core.hard_exit(code)
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)  # crisp text on scaled displays; must run before Tk()
    except Exception:
        pass
    root = tk.Tk()
    if not core.single_instance(DATA):
        root.withdraw()
        messagebox.showinfo("Whisperer", "Whisperer is already open. Use the window that is already running.\n\n"
                                         "(If you installed a new copy, quit the old one first.)")
        root.destroy()
        return
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
