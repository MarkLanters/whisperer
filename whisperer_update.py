"""Update checks: the app (GitHub releases) and yt-dlp (PyPI). Nothing is downloaded or installed without the
user's approval in the window; every download is checked against its published SHA-256 before use.
"""
import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import whisperer_core as core

MAC_ASSET = "Whisperer-Dev-mac-arm64.zip" if core.DEV else "Whisperer-mac-arm64.zip"
SUMS_ASSET = "SHA256SUMS.txt"
HEADERS = {"User-Agent": f"Whisperer/{core.APP_VERSION}", "Accept": "application/json"}

# Runs after the app quits: swaps the old bundle for the new one (or puts the old one back) and reopens it.
INSTALL_SCRIPT = r"""
pid="$1"; old="$2"; new="$3"
while kill -0 "$pid" 2>/dev/null; do sleep 0.5; done
rm -rf "$old.previous"
if mv "$old" "$old.previous" && mv "$new" "$old"; then
  rm -rf "$old.previous"
elif [ -d "$old.previous" ] && [ ! -d "$old" ]; then
  mv "$old.previous" "$old"
fi
xattr -dr com.apple.quarantine "$old" 2>/dev/null
open "$old"
"""


def _context():
    # python.org's macOS Python has no system CA certificates; the packaged app relies on certifi
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _open(url, timeout=20):
    return urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout, context=_context())


def _json(url):
    with _open(url) as r:
        return json.load(r)


def download(url, dest, progress=None):
    """Saves url to dest, calling progress(done_bytes, total_bytes); returns the SHA-256 hex digest."""
    h = hashlib.sha256()
    with _open(url, timeout=60) as r, open(dest, "wb") as f:
        total, done = int(r.headers.get("Content-Length") or 0), 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    return h.hexdigest()


# ---------- the app ----------
def latest_release():
    """Newest release of this app's channel as {'version', 'notes', 'url', 'assets': {name: url}}, or None.

    Prod: GitHub's /releases/latest, which never returns pre-releases, so dev builds can't reach friends.
    Dev: the newest pre-release tagged vX.Y.Z-dev.N.
    """
    if core.DEV:
        dev = [r for r in _json(f"https://api.github.com/repos/{core.REPO}/releases?per_page=30")
               if r.get("prerelease") and not r.get("draft") and "-dev." in r["tag_name"]]
        if not dev:
            return None
        d = max(dev, key=lambda r: core.parse_version(r["tag_name"]))
    else:
        try:
            d = _json(f"https://api.github.com/repos/{core.REPO}/releases/latest")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
    return {
        "version": d["tag_name"].lstrip("vV"),
        "notes": (d.get("body") or "").strip(),
        "url": d["html_url"],
        "assets": {a["name"]: a["browser_download_url"] for a in d.get("assets", [])},
    }


def is_newer(version):
    return core.parse_version(version) > core.parse_version(core.APP_VERSION)


def app_bundle():
    return Path(sys.executable).resolve().parents[2]  # .../Whisperer.app/Contents/MacOS/Whisperer


def can_install_app_update():
    """(True, '') when the app can replace itself, otherwise (False, reason for the user)."""
    name = core.APP_NAME
    if not core.FROZEN:
        return False, f"{name} is running from source code. Update it with git pull."
    if not core.IS_MAC:
        return False, "Automatic install isn't available on this system yet."
    bundle = app_bundle()
    if "/AppTranslocation/" in str(bundle):
        return False, (f"macOS is running {name} from a temporary location. "
                       f"Move {name}.app to the Applications folder, open it from there and try again.")
    if not os.access(bundle.parent, os.W_OK):
        return False, f"No permission to replace {name} in {bundle.parent}."
    return True, ""


def prepare_app_update(release, progress=None):
    """Downloads the new app, checks its SHA-256 and unpacks it; returns the new Whisperer.app (Whisperer Dev.app)."""
    assets = release["assets"]
    if MAC_ASSET not in assets or SUMS_ASSET not in assets:
        raise RuntimeError("this release has no Mac download")
    with _open(assets[SUMS_ASSET]) as r:
        sums = r.read().decode("utf-8", "replace")
    m = re.search(r"^([0-9a-fA-F]{64})\s+\*?" + re.escape(MAC_ASSET) + r"\s*$", sums, re.M)
    if not m:
        raise RuntimeError(f"{SUMS_ASSET} has no checksum for {MAC_ASSET}")
    work = core.support_dir() / "update"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    archive = work / MAC_ASSET
    if download(assets[MAC_ASSET], archive, progress) != m[1].lower():
        raise RuntimeError("checksum mismatch: the download is damaged or not genuine, nothing was installed")
    # ditto keeps the bundle's symlinks, permissions and signature; zipfile would not
    subprocess.run(["ditto", "-x", "-k", str(archive), str(work / "new")], check=True)
    app = work / "new" / f"{core.APP_NAME}.app"
    if not (app / "Contents" / "MacOS" / "Whisperer").exists():  # the executable keeps its name in both channels
        raise RuntimeError(f"the download doesn't contain {core.APP_NAME}.app")
    return app


def install_app_update(new_app):
    """Starts the helper that swaps the bundle once this process exits. The caller must quit right after."""
    script = new_app.parent.parent / "install.sh"
    script.write_text(INSTALL_SCRIPT, encoding="utf-8")
    subprocess.Popen(["/bin/sh", str(script), str(os.getpid()), str(app_bundle()), str(new_app)],
                     start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------- yt-dlp ----------
def ytdlp_version():
    """Version of the yt-dlp the worker will use (the downloaded override if present, else the bundled one)."""
    override = core.packages_dir() / "yt_dlp" / "version.py"
    if core.FROZEN and override.exists():
        m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)", override.read_text(encoding="utf-8"))
        if m:
            return m[1]
    from yt_dlp.version import __version__
    return __version__


def ytdlp_latest():
    """Newest yt-dlp on PyPI as {'version', 'url', 'sha256'}."""
    d = _json("https://pypi.org/pypi/yt-dlp/json")
    wheel = next(f for f in d["urls"] if f["filename"].endswith("-py3-none-any.whl"))
    return {"version": d["info"]["version"], "url": wheel["url"], "sha256": wheel["digests"]["sha256"]}


def install_ytdlp(latest, progress=None):
    """Downloads the yt-dlp wheel, checks its SHA-256 and unpacks yt_dlp/ into packages_dir() (used from the next run)."""
    work = core.support_dir() / "update-yt-dlp"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    wheel = work / "yt_dlp.whl"
    if download(latest["url"], wheel, progress) != latest["sha256"].lower():
        raise RuntimeError("checksum mismatch: the download is damaged or not genuine, nothing was installed")
    staged = work / "packages"
    with zipfile.ZipFile(wheel) as z:
        z.extractall(staged, [n for n in z.namelist() if n.startswith("yt_dlp/")])
    target = core.packages_dir()
    shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    staged.rename(target)
    shutil.rmtree(work, ignore_errors=True)
