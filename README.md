# Whisperer

Turns recordings (Teams meetings, voice notes, any audio or video) and YouTube videos into subtitle files with
timestamps (`.vtt`), using OpenAI's Whisper speech model. Everything runs on your own computer: nothing is uploaded.

## Mac: install
Needs a Mac with Apple Silicon (M1 or newer) and macOS 14 Sonoma or newer.

1. Download `Whisperer-mac-arm64.zip` from the [latest release](https://github.com/MarkLanters/whisperer/releases/latest)
   and double-click it to unzip.
2. Drag **Whisperer** into **Applications**.
3. Open it. The first time, macOS blocks it because the app isn't signed by Apple:
   click **Done**, open **System Settings → Privacy & Security**, scroll down and click **Open Anyway**,
   then confirm. After that it opens normally.
4. Allow access to the Documents folder when asked: your files are kept in `Documents/Whisperer`.

The first transcription downloads the speech model once (about 3 GB; 1.6 GB for the faster model in Settings).

## Using it
- **Put Here** opens `Documents/Whisperer/Source Files`. Put recordings into the `uk` folder for Ukrainian,
  or `auto` to let Whisper detect the language (any other Whisper language code works as a folder name too).
  Setting the language is better than auto: Whisper only listens to the first 30 seconds to guess it.
- **YouTube list** opens `YouTube List.txt`: one link per line, playlists work, an optional language code
  before the link (`uk https://...`).
- **Start** transcribes everything new; finished files are skipped next time. Keep the Mac awake and plugged in
  during long runs (Whisperer stops idle sleep, but closing the lid on battery still sleeps).
- **Result** opens the `transcripts` folder.
- **Whisperer → Settings…** (⌘,): speech model, default languages, keeping YouTube audio, update checks.

Whisperer checks for updates when it starts and asks before installing anything: the app itself, and yt-dlp,
which downloads YouTube audio and needs updates whenever YouTube changes.

## Running from source
Windows: `py -m pip install --only-binary=:all: -r requirements-windows.txt`, then `pyw Whisperer.pyw`.

Mac: `python3 -m pip install -r requirements-mac.txt` and `python3 -m pip install --no-deps mlx-whisper==0.4.3`,
then `python3 Whisperer.pyw` (use Python from python.org, which includes Tk for the window).
From source, files stay in the project folder.
