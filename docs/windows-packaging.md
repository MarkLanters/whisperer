# Windows packaging — deferred plan

Status (2026-10-25): **Windows package not started.** The Mac app is built first. When the user asks for the
Windows app, read this file, `CLAUDE.md` and the current code, then pick up from "Steps" below. Everything decided
for the Mac version (shared code, updater, GitHub releases) should be reused, not redone.

Already in the shared code (written for the Mac, platform-aware):
- `whisperer_core.data_dir()` → `Documents\Whisperer` when frozen; `support_dir()` → `%LOCALAPPDATA%\Whisperer`.
- `whisperer_core.worker_command()` → `[sys.executable, "--worker"]` when frozen; `Whisperer.pyw` handles `--worker`
  and `--self-test` and fixes missing stdout/stderr in a windowed app.
- Keep-awake on Windows (`keep_awake()` in `Whisperer.pyw`), model-download approval, Settings dialog with model choice.
- Updater: release check, SHA-256 checks and the yt-dlp override (`activate_overlay`) are cross-platform;
  `can_install_app_update()` / `prepare_app_update()` are Mac-only (zip + `ditto` + shell helper) — Windows needs
  the installer variant described below and a Windows asset name.

## Decisions already made
- Audience: a few friends. Not a public release, no company IT to satisfy.
- Hosting: GitHub — releases page for downloads, update checks read the latest release.
- Signing: **unsigned for now.** Friends click *More info → Run anyway* on the SmartScreen dialog once.
  Signing can be added later without changing the app (Azure Trusted Signing or an OV certificate).
- Updates: always with user approval, never during a transcription, separately for the app, yt-dlp and the model.

## Target
- Per-user install, no admin rights: Inno Setup installer → `%LOCALAPPDATA%\Programs\Whisperer\`,
  Start-menu shortcut, uninstaller. Download ≈ 120–150 MB, installed ≈ 250–350 MB (no PyTorch on Windows).
- User data in `Documents\Whisperer\`: `Source Files\{uk,auto}`, `transcripts\`, `YouTube List.txt`, `logs\`.
  The program folder is never written to.
- Model (`large-v3`, ≈ 3 GB) is not in the installer: downloaded on first launch after the user agrees,
  with a progress bar, into `%LOCALAPPDATA%\Whisperer\models`.

## Engine
- faster-whisper on CPU, `int8`, `vad_filter=True` — same as the current script.
- `CPU_THREADS` from the core count, not hard-coded 14.
- Offer `large-v3-turbo` (much faster, nearly the same quality) for weaker PCs; model is a setting.
- Optional later: NVIDIA CUDA build (adds ≈ 1 GB of cuBLAS/cuDNN). AMD GPUs stay CPU-only (see CLAUDE.md).

## PyInstaller notes
- **One-folder** build, not one-file: one-file unpacks to a temp dir on every launch (slow start)
  and triggers more antivirus false positives.
- There is no `python.exe` in a frozen app. The window starts the worker as a second copy of itself:
  `Whisperer.exe --worker`, with `CREATE_NO_WINDOW`, stdout piped as today. Stop/close kills that process.
- Collect as data/hidden imports and check each one in the build:
  - `faster_whisper/assets/*.onnx` (Silero VAD) — VAD fails at runtime without it.
  - `onnxruntime`, `ctranslate2` (its DLLs), `tokenizers`, `av` (bundled FFmpeg DLLs), `yt_dlp` (+ its lazy extractors).
- Keep `av<15` (see Known issues in CLAUDE.md).
- Keep the PC awake during a run with `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`;
  sleep kills a run (CLAUDE.md, Known issues).

## Updater (shared with Mac)
- Check on launch (can be turned off) and via a "Check for updates" button; show version + changelog;
  buttons *Update / Later / Skip this version*.
- App update: download the installer from the GitHub release, verify SHA-256 from the release's checksum
  file, run it with `/SILENT` after the app closes. HTTPS only.
- yt-dlp update: yt-dlp breaks when YouTube changes, so it updates independently of app releases —
  download the official release, verify its `SHA2-256SUMS`, put it in a user folder that is placed
  first on `sys.path` at start-up so it overrides the bundled copy.
- Model update: rare; ask before downloading 3 GB.

## Build and release
- GitHub Actions `windows-latest` job next to the macOS job, triggered by a version tag; uploads
  `Whisperer-Setup-x.y.z.exe` and its SHA-256 to the release.
- Include third-party licenses (Whisper/faster-whisper/CTranslate2 MIT, yt-dlp Unlicense, PyAV BSD + FFmpeg LGPL).

## Lessons from the current Windows script (keep them)
- Child output needs `PYTHONIOENCODING=utf-8` and `PYTHONUNBUFFERED=1`, otherwise Cyrillic crashes and the log lags.
- `os.startfile` for folders is Windows-only (Mac uses `open`).
- `.pyw` files have no association on the user's PC — the installer's shortcut replaces `Whisperer.lnk`.
- Output naming and skip logic must stay as in CLAUDE.md ("Do not use `Path.with_suffix`" etc.).

## Steps
1. ~~Windows paths in the shared settings module~~ — done (`whisperer_core`).
2. ~~Worker mode via the `.exe`; keep-awake on Windows~~ — done; verify in the frozen build.
3. PyInstaller spec for Windows (separate from the Mac `whisperer.spec`: faster-whisper instead of mlx, exclude mlx);
   build locally on this PC and test with one short file.
4. Inno Setup script; test install/uninstall without admin.
5. Hook the shared updater to the installer; test an update from x.y.z to x.y.z+1.
6. Add the Windows job to GitHub Actions.
7. Test on a friend's PC: SmartScreen *Run anyway*, Defender, a user name with Cyrillic/spaces.
