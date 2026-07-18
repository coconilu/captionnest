# -*- mode: python ; coding: utf-8 -*-

import re
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)


ROOT = Path(SPECPATH).resolve().parents[1]
SIDECAR_ROOT = ROOT / "apps" / "sidecar"
PACKAGING_ROOT = ROOT / "tooling" / "packaging"
DESKTOP_ROOT = ROOT / "apps" / "desktop"
with (SIDECAR_ROOT / "pyproject.toml").open("rb") as version_stream:
    VERSION = tomllib.load(version_stream)["project"]["version"]
VERSION_PARTS = tuple(int(part) for part in re.findall(r"\d+", VERSION)[:4])
VERSION_TUPLE = (VERSION_PARTS + (0, 0, 0, 0))[:4]
VERSION_TEXT = ".".join(str(part) for part in VERSION_TUPLE)
VERSION_INFO = VSVersionInfo(
    ffi=FixedFileInfo(filevers=VERSION_TUPLE, prodvers=VERSION_TUPLE),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",
                    [
                        StringStruct("CompanyName", "CaptionNest contributors"),
                        StringStruct("FileDescription", "CaptionNest Python sidecar"),
                        StringStruct("FileVersion", VERSION_TEXT),
                        StringStruct("InternalName", "captionnest-sidecar"),
                        StringStruct(
                            "LegalCopyright", "Copyright 2026 CaptionNest contributors"
                        ),
                        StringStruct("OriginalFilename", "captionnest-sidecar.exe"),
                        StringStruct("ProductName", "CaptionNest"),
                        StringStruct("ProductVersion", VERSION_TEXT),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)
DATAS = [
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
    (str(PACKAGING_ROOT / "dist" / "FFMPEG_BUILD_INFO.txt"), "."),
]
DATAS += copy_metadata("captionnest")
media_provenance = (
    PACKAGING_ROOT / "dist" / "media-wheel" / "MEDIA_WHEEL_PROVENANCE.json"
)
if media_provenance.is_file():
    DATAS.append((str(media_provenance), "."))
BINARIES = []
HIDDEN_IMPORTS = [
    "sublingo_local.app",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

for package_name in ("av", "ctranslate2", "faster_whisper", "huggingface_hub", "tokenizers"):
    package_datas, package_binaries, package_hidden_imports = collect_all(package_name)
    DATAS += package_datas
    BINARIES += package_binaries
    HIDDEN_IMPORTS += package_hidden_imports

for distribution_name in ("av", "ctranslate2", "faster-whisper", "huggingface-hub"):
    try:
        DATAS += copy_metadata(distribution_name, recursive=True)
    except Exception:
        # Not every PyInstaller version exposes metadata for every transitive package.
        pass

analysis = Analysis(
    [str(PACKAGING_ROOT / "sidecar_entry.py")],
    pathex=[str(SIDECAR_ROOT / "src")],
    binaries=BINARIES,
    datas=DATAS,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "ruff"],
    noarchive=False,
    optimize=1,
)
python_archive = PYZ(analysis.pure)
icon_path = DESKTOP_ROOT / "icons" / "icon.ico"
executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="captionnest-sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path) if icon_path.is_file() else None,
    version=VERSION_INFO,
    contents_directory="_internal",
)
collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="captionnest-sidecar",
)
