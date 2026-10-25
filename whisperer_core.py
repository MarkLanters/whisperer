"""Shared by the window, the worker and the build: version, folders, settings, models, platform helpers.

Imports only the standard library at module level, so the window starts fast.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

VERSION = "0.1.0"
REPO = "MarkLanters/whisperer"  # GitHub owner/name: releases and update checks

FROZEN = getattr(sys, "frozen", False)  # True inside the packaged app
IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
ENGINE = "mlx" if IS_MAC else "faster-whisper"
SOURCE_DIR = Path(__file__).resolve().parent  # project folder when running from source

MEDIA_EXT = {
    ".mp4", ".m4a", ".mp3", ".wav", ".webm", ".mkv", ".mov", ".aac", ".ogg", ".flac",
    ".opus", ".oga", ".weba", ".wma", ".m4b", ".aif", ".aiff", ".caf", ".amr", ".mka", ".ac3",
    ".avi", ".wmv", ".flv", ".m4v", ".mpg", ".mpeg", ".ts", ".mts", ".m2ts", ".3gp",
}
LANG_FOLDERS = ("uk", "auto")  # created under Source Files when the folder is opened

# Model choice -> Hugging Face repo per engine. Anything else in settings is used as a repo id as is.
MODELS = {
    "large-v3": {"faster-whisper": "Systran/faster-whisper-large-v3",
                 "mlx": "mlx-community/whisper-large-v3-mlx", "size": "3.1 GB"},
    "large-v3-turbo": {"faster-whisper": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
                       "mlx": "mlx-community/whisper-large-v3-turbo", "size": "1.6 GB"},
}

SETTINGS_FILE = "settings.json"  # in the data folder
DEFAULTS = {
    "model": "large-v3",
    "youtube_language": "auto",   # for YouTube List lines without a code
    "source_language": "auto",    # for files directly in Source Files (no language subfolder)
    "keep_audio": False,          # keep downloaded YouTube audio
    "check_updates": True,        # check GitHub for a new version when the app starts
    "skip_version": "",           # an app version the user chose to skip
}


def data_dir():
    """Where Source Files, transcripts, YouTube List.txt, logs and settings live.

    From source: the project folder (unchanged behaviour). Packaged app: ~/Documents/Whisperer.
    """
    if os.environ.get("WHISPERER_DATA"):
        return Path(os.environ["WHISPERER_DATA"])
    if FROZEN:
        return Path.home() / "Documents" / "Whisperer"
    return SOURCE_DIR


def support_dir():
    """App-private folder: downloaded updates and the yt-dlp override."""
    if os.environ.get("WHISPERER_SUPPORT"):
        return Path(os.environ["WHISPERER_SUPPORT"])
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / "Whisperer"
    if IS_WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Whisperer"
    return Path.home() / ".whisperer"


def packages_dir():
    """A newer yt-dlp downloaded by the updater; put first on sys.path so it wins over the bundled one."""
    return support_dir() / "packages"


def activate_overlay():
    """Call before importing yt_dlp. Only the packaged app uses it; from source, pip updates yt-dlp."""
    if FROZEN and packages_dir().is_dir():
        sys.path.insert(0, str(packages_dir()))


def load_settings(folder=None):
    settings = dict(DEFAULTS)
    path = Path(folder or data_dir()) / SETTINGS_FILE
    try:
        settings.update(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return settings


def settings_exist(folder=None):
    return (Path(folder or data_dir()) / SETTINGS_FILE).exists()


def save_settings(settings, folder=None):
    path = Path(folder or data_dir()) / SETTINGS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")


def model_repo(name):
    return MODELS[name][ENGINE] if name in MODELS else name


def model_size(name):
    return MODELS.get(name, {}).get("size", "a few GB")


def model_cached(name):
    """True if the model is already downloaded (no network). Unknown -> True, so nothing blocks a run."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        return True
    # faster-whisper downloads only these files; huggingface_hub would call the snapshot incomplete without the list.
    # mlx-whisper downloads the whole repo, so no list there.
    patterns = (["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
                if ENGINE == "faster-whisper" else None)
    try:
        snapshot_download(model_repo(name), local_files_only=True, allow_patterns=patterns)
        return True
    except Exception:
        return False


def open_path(path):
    """Open a folder in Explorer/Finder or a file in its default app."""
    if IS_WINDOWS:
        os.startfile(path)
    elif IS_MAC:
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def worker_command():
    """Command that runs the transcription as a child process."""
    if FROZEN:
        return [sys.executable, "--worker"]
    python = Path(sys.executable)
    if IS_WINDOWS:
        python = python.with_name("python.exe")  # not pythonw: the child needs stdout
        if not python.exists():
            python = Path(sys.executable)
    return [str(python), str(SOURCE_DIR / "batch_transcribe.py")]


_instance_lock = None  # kept open for the life of the window


def single_instance(folder):
    """Lock <folder>/logs/whisperer.lock; False if another window already holds it. Two windows on one data folder
    would both write logs/run.log (garbling it) and transcribe the same files. The OS drops the lock when the
    process ends, also after a crash. The worker child doesn't inherit it (close_fds)."""
    global _instance_lock
    path = Path(folder) / "logs" / "whisperer.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+")
    try:
        if IS_WINDOWS:
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return False
    _instance_lock = f
    return True


def hard_exit(code=0):
    """Flush output and end the process without interpreter shutdown, which can hang after MLX has used the GPU
    (seen in CI: "self-test OK" printed, then the process never exited). Only call when all files are written."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(code)


def parse_version(v):
    """'v0.1.0' / '2026.10.21' -> (0, 1, 0) / (2026, 10, 21) for comparisons."""
    parts = []
    for p in v.lstrip("vV").split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)
