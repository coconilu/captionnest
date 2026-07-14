# CaptionNest media runtime

CaptionNest only reads media and decodes audio for speech recognition. The Windows release
therefore builds PyAV against a pinned LGPL FFmpeg configuration instead of using the generic
PyPI wheel, which currently carries the GPL-licensed x264 and x265 encoder libraries.

## Pinned inputs

| Input | Version / reference |
|---|---|
| PyAV source | 18.0.0 |
| PyAV source SHA-256 | `4ef7e72c3d3a872584a1215173b16e0226811037f40dcdbf75992631098df1ba` |
| FFmpeg port | 8.1.2 via Microsoft vcpkg |
| vcpkg baseline | `db4723bd0a99eab031f1a3dee4336dca43049c87` |
| Windows triplet | `x64-windows` (shared libraries) |

The manifest deliberately excludes the vcpkg `gpl`, `all-gpl`, `nonfree`, `x264`, and `x265`
features. `scripts/build-media-wheel.ps1` records the final wheel hash and the inputs used for
the build. `scripts/check-media-license.ps1` remains the final fail-closed check against the
installed wheel and its actual DLL inventory.

## Local build

On Windows with Visual Studio Build Tools, Python 3.12, uv, and a bootstrapped checkout of the
pinned vcpkg baseline:

```powershell
uv sync --extra asr --extra desktop --extra dev --locked
.\scripts\build-media-wheel.ps1 `
  -VcpkgRoot C:\path\to\vcpkg `
  -PythonExecutable .\.venv\Scripts\python.exe
```

The repaired wheel and provenance record are written to `packaging/dist/media-wheel/`. Build
and download directories under `packaging/build/` and `packaging/dist/` are intentionally not
committed.
