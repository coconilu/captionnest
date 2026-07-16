from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

SEMVER_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class VersionUpdateError(ValueError):
    """Raised when a release version or project version declaration is invalid."""


@dataclass(frozen=True)
class VersionLocation:
    label: str
    relative_path: str
    pattern: re.Pattern[str]


def _pattern(expression: str) -> re.Pattern[str]:
    return re.compile(expression, re.MULTILINE | re.DOTALL)


VERSION_LOCATIONS = (
    VersionLocation(
        "Python",
        "apps/sidecar/pyproject.toml",
        _pattern(
            r'(?P<prefix>^\[project\].*?^version\s*=\s*")'
            r'(?P<version>[^"]+)(?P<suffix>")'
        ),
    ),
    VersionLocation(
        "PythonLock",
        "apps/sidecar/uv.lock",
        _pattern(
            r'(?P<prefix>^\[\[package\]\]\s*\r?\nname\s*=\s*"captionnest"\s*\r?\n'
            r'version\s*=\s*")(?P<version>[^"]+)(?P<suffix>")'
        ),
    ),
    VersionLocation(
        "Web",
        "apps/web/package.json",
        _pattern(
            r'(?P<prefix>\A\{\s*"name"\s*:\s*"captionnest-web"\s*,.*?'
            r'"version"\s*:\s*")'
            r'(?P<version>[^"]+)(?P<suffix>")'
        ),
    ),
    VersionLocation(
        "WebLock",
        "apps/web/package-lock.json",
        _pattern(
            r'(?P<prefix>\A\{\s*"name"\s*:\s*"captionnest-web"\s*,\s*"version"\s*:\s*")'
            r'(?P<version>[^"]+)(?P<suffix>")'
        ),
    ),
    VersionLocation(
        "WebLockRoot",
        "apps/web/package-lock.json",
        _pattern(
            r'(?P<prefix>"packages"\s*:\s*\{\s*""\s*:\s*\{\s*'
            r'"name"\s*:\s*"captionnest-web"\s*,\s*"version"\s*:\s*")'
            r'(?P<version>[^"]+)(?P<suffix>")'
        ),
    ),
    VersionLocation(
        "Tauri",
        "apps/desktop/tauri.conf.json",
        _pattern(
            r'(?P<prefix>\A\{.*?"productName"\s*:\s*"CaptionNest"\s*,\s*'
            r'"version"\s*:\s*")(?P<version>[^"]+)(?P<suffix>")'
        ),
    ),
    VersionLocation(
        "Cargo",
        "apps/desktop/Cargo.toml",
        _pattern(
            r'(?P<prefix>^\[package\].*?^version\s*=\s*")'
            r'(?P<version>[^"]+)(?P<suffix>")'
        ),
    ),
    VersionLocation(
        "CargoLock",
        "apps/desktop/Cargo.lock",
        _pattern(
            r'(?P<prefix>^\[\[package\]\]\s*\r?\n'
            r'name\s*=\s*"captionnest-desktop"\s*\r?\nversion\s*=\s*")'
            r'(?P<version>[^"]+)(?P<suffix>")'
        ),
    ),
)


def normalize_version(value: str) -> str:
    version = value.strip()
    if not SEMVER_PATTERN.fullmatch(version):
        raise VersionUpdateError(
            f"Invalid release version {value!r}; expected MAJOR.MINOR.PATCH without a v prefix."
        )
    return version


def _read_utf8(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except FileNotFoundError as exc:
        raise VersionUpdateError(f"Missing version file: {path}") from exc


def get_project_versions(root: Path) -> dict[str, str]:
    root = root.resolve()
    contents: dict[Path, str] = {}
    versions: dict[str, str] = {}
    for location in VERSION_LOCATIONS:
        path = root / location.relative_path
        if path not in contents:
            contents[path] = _read_utf8(path)
        text = contents[path]
        matches = list(location.pattern.finditer(text))
        if len(matches) != 1:
            raise VersionUpdateError(
                f"Expected one {location.label} version in {path}; found {len(matches)}."
            )
        versions[location.label] = matches[0].group("version")
    return versions


def set_project_version(root: Path, value: str) -> tuple[Path, ...]:
    root = root.resolve()
    version = normalize_version(value)
    original: dict[Path, str] = {}
    updated: dict[Path, str] = {}

    for location in VERSION_LOCATIONS:
        path = root / location.relative_path
        if path not in updated:
            original[path] = _read_utf8(path)
            updated[path] = original[path]
        text, count = location.pattern.subn(
            lambda match: f'{match.group("prefix")}{version}{match.group("suffix")}',
            updated[path],
            count=1,
        )
        if count != 1:
            raise VersionUpdateError(
                f"Expected one {location.label} version in {path}; found {count}."
            )
        updated[path] = text

    changed = tuple(path for path, text in updated.items() if text != original[path])
    for path in changed:
        path.write_bytes(updated[path].encode("utf-8"))

    versions = get_project_versions(root)
    mismatches = {label: current for label, current in versions.items() if current != version}
    if mismatches:
        raise VersionUpdateError(f"Version update did not converge: {mismatches}")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Update every CaptionNest release version.")
    parser.add_argument("version", help="Release version in MAJOR.MINOR.PATCH format")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Project root (defaults to the repository containing this module)",
    )
    args = parser.parse_args()

    try:
        changed = set_project_version(args.root, args.version)
    except VersionUpdateError as exc:
        parser.error(str(exc))

    if changed:
        for path in changed:
            print(path.relative_to(args.root.resolve()).as_posix())
    else:
        print(f"All project versions already match {normalize_version(args.version)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
