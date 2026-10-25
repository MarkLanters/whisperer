# Whisperer — batch transcription of YouTube videos and meeting recordings

The main file is `batch_transcribe.py`. Read it in full before making changes.

Files: `batch_transcribe.py` (the transcription run), `Whisperer.pyw` (window + entry point of the packaged app), `whisperer_core.py` (version, folders, settings, models, platform helpers; standard library only), `whisperer_engine.py` (faster-whisper on Windows, mlx-whisper on Mac), `whisperer_update.py` (update checks/installs), `whisperer.spec` + `.github/workflows/mac.yml` (Mac build), `README.md` (for friends), `CHANGELOG.md` (release notes), `docs/windows-packaging.md` (deferred Windows package).

## What it does
- Reads links from `YouTube List.txt` (one per line, playlists allowed, lines starting with `#` are ignored). A language code may precede a link: `uk https://...`, `en https://...`, `auto https://...`. Without a code, `LANGUAGE` applies. A playlist's code applies to all of its videos. Codes are validated against faster-whisper's language list; a line with an unknown code is skipped with a warning. If a video appears on several lines, the first line's language wins.
- Reads local video/audio files from `Source Files` (typically Teams recordings downloaded from OneDrive/SharePoint; extensions in `MEDIA_EXT`). The first subfolder sets the language: `Source Files/uk`, `Source Files/auto`; files directly in `Source Files` or in a subfolder with any other name use `SOURCE_LANGUAGE`. The built-in Teams transcript is not good enough for the user, which is why recordings are transcribed here.
- Downloads YouTube audio with yt-dlp (format `bestaudio[ext=m4a]`, no ffmpeg needed) into `audio/`; after a successful transcription the audio is deleted (`KEEP_AUDIO`). Files in `Source Files` are never deleted.
- Transcribes with faster-whisper on Windows: model `large-v3`, `device="cpu"`, `compute_type="int8"`, `vad_filter=True`, 14 threads. On a Mac it uses mlx-whisper on the Apple GPU (no VAD there). Both behind `whisperer_engine.load()` / `transcribe()`, which yields segments while transcription runs.
- Settings: constants at the top of `batch_transcribe.py`; a `settings.json` in the data folder (written by the window's Settings dialog) overrides model, both default languages and keep-audio. No `settings.json` → the constants apply. Models: `large-v3` or `large-v3-turbo` (mapped to a Hugging Face repo per engine in `whisperer_core.MODELS`).
- Data folder (`whisperer_core.data_dir()`): the project folder when run from source (as always), `~/Documents/Whisperer` in the packaged app, or `WHISPERER_DATA` if set. The window starts the script there, so all paths in it stay relative.
- Output is `.vtt` only (WebVTT with timestamps; the user does not want `.txt` or `.srt`): YouTube goes to `transcripts/` as `Video title [VIDEO_ID].vtt`, source files go to `transcripts/Source Files/` under the source file's name.
- The model is loaded once per run. Source files are processed first while YouTube audio downloads in a background thread (queue size `PREFETCH`).
- Window app: `Whisperer.pyw` (tkinter, no extra dependencies), opened with the `Whisperer.lnk` shortcut (`pyw -3.12 Whisperer.pyw`; `.pyw` has no file association on this PC, so a double-click on the `.pyw` itself does nothing). Buttons: Start/Stop (runs `batch_transcribe.py` as a child `python.exe` with no console window), Put Here (opens `Source Files`, creating `uk` and `auto` subfolders), YouTube list (opens `YouTube List.txt`), Result (opens `transcripts`; greyed out until a run finishes with at least one new transcript, greyed out again on the next Start). The progress bar shows overall progress (finished files + the current file's %); the text above it shows the file, language, %, elapsed time and an estimate. It parses the script's printed lines (`to process:`, `[Source n/N]`, `NN.N%`, `✓ Done in`, `! ... failed`, `Traceback`), so keep those formats when changing the script's output. It writes the same `logs\run.log`, so the status check below works for runs started from the window. Closing the window or pressing Stop kills the run. Menu: Settings, Check for updates, About (on a Mac in the app menu; Settings is ⌘,). Before Start it asks to download the model if it isn't cached, and keeps the computer awake during a run (Windows `SetThreadExecutionState`, Mac `caffeinate`). Also the entry point of the packaged app: `--worker` runs the script, `--self-test [audio]` prints versions/paths and optionally transcribes a file (`WHISPERER_MODEL` overrides the model).
- Finished work is skipped: YouTube by the ID in square brackets in a `.vtt` name in `transcripts/`; source files by the existence of `transcripts/Source Files/<name>.vtt`. A `.vtt` is written as `.vtt.part` and renamed when complete, so its existence means "done".

## Distribution (decided 2026-10-25)
- The app is being turned into a packaged app for **a few friends**, hosted in **one public GitHub repo** (code + releases + update checks), **unsigned** for now. Since the repo is public, this file and all code are visible to anyone: never commit recordings, transcripts, logs, tokens or personal data, and commit with the GitHub `noreply` email.
- The user has an Apple Silicon Mac (M1/M2 MacBook Pro) and tests the Mac app on it; this Windows PC can't run it. Updates (app, yt-dlp, model) always need the user's approval.
- **Mac first.** The Windows package is deferred; its full plan is in `docs/windows-packaging.md`. When the user asks for the Windows app, read that file first.
- The current Windows script and window must keep working as they are while the Mac app is built.

## Mac app
- Status (2026-10-25): code, spec and workflow written; Windows path tested; **the Mac build hasn't run yet** — first GitHub Actions run and a test on the user's Mac are pending. This PC can't run MLX or build the `.app`.
- Requirements: Apple Silicon, macOS 14+ (MLX wheels exist only for macOS 14+; the workflow uses `macos-14` so pip picks those wheels — building on a newer runner raises the minimum macOS). Install: `requirements-mac.txt`, then `pip install --no-deps mlx-whisper==0.4.3` (it lists PyTorch, which isn't needed; the spec excludes it).
- Build/release: `.github/workflows/mac.yml`. Manual run (Actions → Mac app → Run workflow) builds and tests, artifact only. Release: bump `VERSION` in `whisperer_core.py`, add a `## X.Y.Z` section to `CHANGELOG.md` (it becomes the release notes the app shows), commit, push tag `vX.Y.Z`; the workflow refuses a tag that doesn't match `VERSION`. Assets: `Whisperer-mac-arm64.zip` + `SHA256SUMS.txt` (names are used by the updater).
- CI self-tests: transcribes a `say` sentence with `mlx-community/whisper-tiny-mlx` (GPU, falls back to CPU via `WHISPERER_MLX_DEVICE=cpu` since CI Macs may have no Metal), and checks that a downloaded yt-dlp overrides the bundled one.
- Updates (`whisperer_update.py`), always after the user clicks: app — newest GitHub release, zip checked against `SHA256SUMS.txt`, unpacked with `ditto`, a shell helper swaps the bundle after the app quits and reopens it (refuses when macOS runs the app translocated from Downloads, or the folder isn't writable); yt-dlp — newest wheel from PyPI, checked against PyPI's SHA-256, unpacked into `~/Library/Application Support/Whisperer/packages`, which `activate_overlay()` puts first on `sys.path` in the packaged app. From source, updates are git pull / pip. Update checks at start-up run only in the packaged app.
- mlx-whisper returns segments only at the end; `whisperer_engine` replaces the `print` of the `mlx_whisper.transcribe` module and parses its verbose lines `[mm:ss.mmm --> mm:ss.mmm] text` for live progress. This depends on mlx-whisper 0.4.3 internals (pinned) — recheck when upgrading. Get that module with `importlib.import_module("mlx_whisper.transcribe")`: `from mlx_whisper import transcribe` returns the function, not the module.
- Audio is decoded with PyAV (`whisperer_engine.decode_audio`, verified identical to faster-whisper's decoder), so no ffmpeg is needed on the Mac.
- Packaged app on macOS uses certifi for HTTPS (python.org's macOS Python has no system CA certificates).
- Friends install: unzip, drag to Applications, first launch blocked (unsigned) → System Settings → Privacy & Security → Open Anyway. README has the steps.

## Environment
- Windows, PowerShell, VS Code. Run Python only via `py` (Python 3.12, where all libraries are installed). VS Code may show a 3.14 interpreter that has no libraries; use `py` and `py -m pip`.
- CPU: Intel i5-13600KF (14 cores), 32 GB RAM.
- GPU: AMD Radeon RX 6750 XT — no CUDA, so transcription is CPU-only. A possible GPU route is whisper.cpp with Vulkan (not implemented).
- Dependencies: `yt-dlp`, `faster-whisper`, `av<15` (`requirements-windows.txt`). Install with `py -m pip install --only-binary=:all: -r requirements-windows.txt`.
- The Bash tool here has no network (DNS fails); run network checks from PowerShell.

## Known issues
- PyAV 15+ is incompatible with faster-whisper (`open() got an unexpected keyword argument 'metadata_errors'`), hence `av<15`.
- Microsoft C++ Build Tools are not installed and not needed: install packages with `--only-binary=:all:` so pip never tries to build from source.
- huggingface_hub warnings about symlinks and HF_TOKEN are harmless; ignore them.
- Recordings are mostly Ukrainian. Whisper's auto-detection only looks at the first 30 s and handles mixed-language speech poorly; with the wrong language Whisper may translate instead of transcribe. Setting the language explicitly is the priority.
- YouTube may start demanding "confirm you're not a bot" — the fix is browser cookies (`cookiesfrombrowser` in yt-dlp).
- The script has no access to SharePoint: recordings are downloaded into `Source Files` by hand.
- Do not use `Path.with_suffix` for output names: if a title contains a dot it drops everything after it, including `[ID]`, and skipping breaks. Build paths as `OUT_DIR / f"{base}.vtt"`.
- Files with the same name in different `Source Files` subfolders map to the same `.vtt`, so the second one is treated as done. Rename such files before processing.
- `! Download failed ...: ERROR:` with an empty message is a transient YouTube failure. Check the video with `py -m yt_dlp --simulate <url>` and rerun the script: finished items are skipped, only the missed ones are processed.
- Pylance in VS Code reports `Import "faster_whisper..." could not be resolved` because of the 3.14 interpreter; ignore it.
- Every extension in `MEDIA_EXT` was checked to decode through PyAV 14.2 (Opus, WMA/WMV, AMR/3GP, AIFF, CAF, AC3, AVI, FLV, MPEG, TS/MTS/M2TS and others). DRM-protected files (`.m4p`, protected `.wma`) and video without an audio track will fail. A new extension only needs to be added to `MEDIA_EXT`; PyAV decodes almost any format.
- huggingface_hub 1.x calls a cached model "incomplete" if any repo file is missing; faster-whisper downloads only 5 files, so `whisperer_core.model_cached` passes the same `allow_patterns` (otherwise the window would offer to re-download a model that is there).
- Transcription cannot resume mid-file: if the process dies (PC shut down, sleep), that file starts over. A leftover `*.vtt.part` can be deleted.

## Using it (guide for a new session)

### Typical requests
- The user can also run everything from the window (`Whisperer.lnk`); a run started there shows up in `logs\run.log` and as a `python` process, same as below.
- "run the transcription" → run (below). First say how many files are queued; a long queue takes hours.
- "status", "what's up", "progress" → status check (below). Answer briefly: what is done, the current file's %, roughly how much is left.
- "a command to watch progress" → give `Get-Content "logs\run.log" -Encoding UTF8 -Wait -Tail 20` (Ctrl+C exits without stopping the run).
- Teams recordings: the user downloads `.mp4` from OneDrive/SharePoint (Stream → Download) into `Source Files\uk` or `Source Files\auto`. Don't suggest the built-in Teams transcript.
- Redo a file in another language: change the code in `YouTube List.txt` (or move the recording to another `Source Files` subfolder) and delete its `.vtt`, otherwise it is skipped.

### Running
Run in the background (run_in_background) with the log inside the project so any later session can find it:
```powershell
New-Item -ItemType Directory -Force logs | Out-Null; $env:PYTHONIOENCODING = "utf-8"; $env:PYTHONUNBUFFERED = "1"; py batch_transcribe.py *> logs\run.log
```
- `PYTHONIOENCODING=utf-8` is required: when output goes to a file, Python otherwise uses the Windows code page and can crash on Cyrillic or `✓`. `PYTHONUNBUFFERED=1` keeps the log live.
- Each run overwrites `logs\run.log`.
- After starting, confirm transcription has begun: in a background Bash, wait for a `%` line or an error (`until grep -qE '^\s+[0-9]+\.[0-9]%|failed|Traceback|Error|All done' logs/run.log; do sleep 2; done`). A long `Start-Sleep` is blocked.

### Checking status
```powershell
$log = "logs\run.log"
"log modified: $((Get-Item $log).LastWriteTime), now: $(Get-Date)"
Get-Content $log -Encoding UTF8 | Where-Object { $_ -match '^\[Source|^\[YouTube|Done in|failed|Traceback|All done' }
Get-Content $log -Encoding UTF8 -Tail 2
Get-Process py, python -ErrorAction SilentlyContinue | Select-Object Id, StartTime
Get-ChildItem transcripts, "transcripts\Source Files" -Filter *.vtt | Sort-Object LastWriteTime | Select-Object -Last 5 Name, LastWriteTime
```
- Process present and log fresh → running. No process and no `All done.` at the end of the log → it died; rerun with the same command.
- The background-task completion notice is lost if the Claude session restarts, while Python may keep running — always check `Get-Process`.

### Speed (for estimates)
- CPU transcription takes roughly 0.9–1.8× the recording length (Teams recordings ≈ 1.7×). Estimate the remainder from the % in the log and the time since the file started.
- During a run, remind the user not to shut down the PC or let it sleep.

### PowerShell details
- Open files with `[ID]` in the name via `-LiteralPath` (otherwise `[...]` is treated as a wildcard).
- `Get-Content` in PowerShell 5.1 without `-Encoding UTF8` shows Cyrillic as mojibake; the file itself is fine.

## Working rules
- Don't break the skip logic, the output naming in `transcripts/` or the `YouTube List.txt` format — the user relies on them.
- Briefly describe the plan before large changes.
- After changes, check syntax: `py -m py_compile batch_transcribe.py Whisperer.pyw whisperer_core.py whisperer_engine.py whisperer_update.py`.
- For window tests, set `WHISPERER_DATA` (and `WHISPERER_SUPPORT` for updater tests) to a scratch folder so the user's files and `settings.json` aren't touched.
- Don't process the whole queue without permission — it takes hours. Test with one short file.
- Don't edit `YouTube List.txt` unless asked.
- When you add a feature or find a new issue, update this file.
- Reply to the user in the language they write in (usually Ukrainian), briefly; give commands ready to paste into PowerShell.
