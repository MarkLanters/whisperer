# PyInstaller spec for the Mac app. Build on an Apple Silicon Mac (GitHub Actions does it, see .github/workflows):
#   pyinstaller --noconfirm --clean whisperer.spec   ->  dist/Whisperer.app, or dist/Whisperer Dev.app when
#   whisperer_build.py (written by the workflow) says CHANNEL = "dev"
import importlib.util
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

VERSION = re.search(r'^VERSION = "([^"]+)"', Path("whisperer_core.py").read_text(encoding="utf-8"), re.M)[1]
build = {}
if Path("whisperer_build.py").exists():
    exec(Path("whisperer_build.py").read_text(encoding="utf-8"), build)
DEV = build.get("CHANNEL") == "dev"
APP = "Whisperer Dev" if DEV else "Whisperer"  # same as whisperer_core.APP_NAME

datas, binaries, hiddenimports = [], [], []
for pkg in ("mlx", "mlx_whisper"):  # compiled MLX, its Metal shaders, mlx-whisper's tokenizer and mel files
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# mlx is a namespace package; make sure its dylibs and Metal library are in even if collect_all misses them
mlx_dir = Path(next(iter(importlib.util.find_spec("mlx").submodule_search_locations)))
for f in mlx_dir.rglob("*"):
    dest = str(f.parent.relative_to(mlx_dir.parent))
    if f.suffix in (".so", ".dylib"):
        binaries.append((str(f), dest))
    elif f.suffix == ".metallib":
        datas.append((str(f), dest))

hiddenimports += collect_submodules("yt_dlp")  # extractors are imported lazily
hiddenimports += ["batch_transcribe", "whisperer_engine", "whisperer_update", "whisperer_build", "certifi"]

a = Analysis(
    ["Whisperer.pyw"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # torch: listed by mlx-whisper, only used for model conversion; the rest is the Windows engine
    excludes=["torch", "faster_whisper", "ctranslate2", "onnxruntime", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Whisperer",
    console=False,
    argv_emulation=False,
    target_arch="arm64",
)
coll = COLLECT(exe, a.binaries, a.datas, name="Whisperer")
app = BUNDLE(
    coll,
    name=f"{APP}.app",
    icon=None,
    # a separate bundle id lets the dev app sit next to the prod app with its own macOS permissions
    bundle_identifier="io.github.marklanters.whisperer" + (".dev" if DEV else ""),
    version=VERSION,
    info_plist={
        "CFBundleName": APP,
        "CFBundleDisplayName": APP,
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "14.0",  # MLX wheels need macOS 14+
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,  # follow dark mode
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSDocumentsFolderUsageDescription": f"{APP} keeps your recordings and transcripts in Documents/{APP}.",
    },
)
