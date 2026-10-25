import re
import time
import queue
import threading
from pathlib import Path

import yt_dlp
from faster_whisper import WhisperModel
from faster_whisper.tokenizer import _LANGUAGE_CODES

# ---------- Settings ----------
MODEL = "large-v3"
LANGUAGE = None           # default language for YouTube List lines without a code; None = auto-detect
SOURCE_LANGUAGE = None    # language for files placed directly in Source Files (no language subfolder)
CPU_THREADS = 14
LINKS_FILE = Path("YouTube List.txt")
AUDIO_DIR = Path("audio")
SOURCE_DIR = Path("Source Files")
OUT_DIR = Path("transcripts")
OUT_SOURCE = OUT_DIR / "Source Files"
KEEP_AUDIO = False        # True = keep downloaded YouTube audio (Source Files are never deleted)
PREFETCH = 2              # how many videos to download ahead while transcribing
MEDIA_EXT = {
    ".mp4", ".m4a", ".mp3", ".wav", ".webm", ".mkv", ".mov", ".aac", ".ogg", ".flac",
    ".opus", ".oga", ".weba", ".wma", ".m4b", ".aif", ".aiff", ".caf", ".amr", ".mka", ".ac3",
    ".avi", ".wmv", ".flv", ".m4v", ".mpg", ".mpeg", ".ts", ".mts", ".m2ts", ".3gp",
}
# ------------------------------


def safe_name(s):
    return re.sub(r'[\\/:*?"<>|]', "_", s).strip()[:150]


def vtt_time(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02}.{ms:03}"


def parse_lang(code):
    """'auto' -> None, a known code -> the code, otherwise ValueError."""
    code = code.lower()
    if code == "auto":
        return None
    if code in _LANGUAGE_CODES:
        return code
    raise ValueError(code)


def read_links():
    """Line format: `[language] link`, e.g. `uk https://...` or `auto https://...`."""
    if not LINKS_FILE.exists():
        return []
    links = []
    for line in LINKS_FILE.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and not parts[0].lower().startswith("http"):
            try:
                lang = parse_lang(parts[0])
            except ValueError:
                print(f"! Unknown language code '{parts[0]}' in line: {line} - skipped")
                continue
            link = parts[1].strip()
        else:
            link, lang = line, LANGUAGE
        links.append((link, lang))
    return links


def read_sources():
    """Returns [(file, language, output .vtt), ...]; the first subfolder Source Files/<language>/ sets the language."""
    if not SOURCE_DIR.exists():
        return []
    items = []
    for p in sorted(SOURCE_DIR.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in MEDIA_EXT:
            continue
        rel = p.relative_to(SOURCE_DIR)
        lang = SOURCE_LANGUAGE
        if len(rel.parts) > 1:
            try:
                lang = parse_lang(rel.parts[0])
            except ValueError:
                pass
        items.append((p, lang, OUT_SOURCE / f"{p.stem}.vtt"))
    return items


def expand(links):
    """Expands playlists into single videos; returns [(url, id, lang), ...] without duplicates."""
    items, seen = [], set()
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": "in_playlist"}) as ydl:
        for link, lang in links:
            try:
                info = ydl.extract_info(link, download=False)
            except Exception as e:
                print(f"! Could not read {link}: {e}")
                continue
            entries = info.get("entries") if "entries" in info else [info]
            for e in entries:
                if not e or e["id"] in seen:
                    continue
                seen.add(e["id"])
                items.append((f"https://www.youtube.com/watch?v={e['id']}", e["id"], lang))
    return items


def download(url):
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio",
        "outtmpl": str(AUDIO_DIR / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return info["id"], info.get("title", info["id"]), Path(ydl.prepare_filename(info))


def downloader(items, q):
    for url, _, lang in items:
        try:
            q.put((*download(url), lang))
        except Exception as e:
            print(f"\n! Download failed {url}: {e}")
    q.put(None)


def transcribe(model, media, lang, out_vtt):
    """Writes the .vtt and returns True on success. The file only appears once fully written."""
    start = time.time()
    try:
        segments, info = model.transcribe(str(media), language=lang, vad_filter=True)
        cues = ["WEBVTT\n"]
        for s in segments:
            text = s.text.strip()
            pct = s.end / info.duration * 100 if info.duration else 0
            print(f"  {pct:5.1f}%  {text}")
            cues.append(f"{vtt_time(s.start)} --> {vtt_time(s.end)}\n{text}\n")
        tmp = out_vtt.parent / f"{out_vtt.name}.part"
        tmp.write_text("\n".join(cues), encoding="utf-8")
        tmp.replace(out_vtt)
    except Exception as e:
        print(f"  ! Transcription failed: {e}")
        return False
    mins = (time.time() - start) / 60
    print(f"  ✓ Done in {mins:.1f} min (recording {info.duration / 60:.1f} min)")
    return True


def main():
    AUDIO_DIR.mkdir(exist_ok=True)
    OUT_SOURCE.mkdir(parents=True, exist_ok=True)

    all_sources = read_sources()
    sources = [it for it in all_sources if not it[2].exists()]
    print(f"Source files: {len(all_sources)}, already done: {len(all_sources) - len(sources)}, to process: {len(sources)}")

    done = {p.stem.rsplit("[", 1)[-1].rstrip("]") for p in OUT_DIR.glob("*.vtt")}
    all_items = expand(read_links())
    items = [it for it in all_items if it[1] not in done]
    print(f"YouTube videos: {len(all_items)}, already done: {len(all_items) - len(items)}, to process: {len(items)}")
    if not sources and not items:
        return

    q = queue.Queue(maxsize=PREFETCH)
    if items:
        threading.Thread(target=downloader, args=(items, q), daemon=True).start()

    print(f"Loading model {MODEL}...")
    model = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=CPU_THREADS)

    for n, (media, lang, out_vtt) in enumerate(sources, 1):
        print(f"\n[Source {n}/{len(sources)}] {media.relative_to(SOURCE_DIR)}  (language: {lang or 'auto'})")
        transcribe(model, media, lang, out_vtt)

    if items:
        n = 0
        while (job := q.get()) is not None:
            vid, title, audio, lang = job
            n += 1
            print(f"\n[YouTube {n}/{len(items)}] {title}  (language: {lang or 'auto'})")
            out_vtt = OUT_DIR / f"{safe_name(title)} [{vid}].vtt"  # not with_suffix: it drops everything after a dot in the title
            if transcribe(model, audio, lang, out_vtt) and not KEEP_AUDIO:
                audio.unlink(missing_ok=True)

    print("\nAll done.")


if __name__ == "__main__":
    main()
