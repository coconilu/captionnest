from pathlib import Path

import pytest

from sublingo_local.release_version import (
    VERSION_LOCATIONS,
    VersionUpdateError,
    get_project_versions,
    normalize_version,
    set_project_version,
)

ROOT = Path(__file__).resolve().parents[1]


def _copy_version_files(destination: Path) -> None:
    copied: set[str] = set()
    for location in VERSION_LOCATIONS:
        if location.relative_path in copied:
            continue
        copied.add(location.relative_path)
        source = ROOT / location.relative_path
        target = destination / location.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())


@pytest.mark.parametrize(
    "value",
    ["", "v1.2.3", "1.2", "1.2.3.4", "01.2.3", "1.02.3", "1.2.03", "1.2.3-beta"],
)
def test_normalize_version_rejects_non_release_versions(value: str) -> None:
    with pytest.raises(VersionUpdateError):
        normalize_version(value)


def test_project_versions_are_consistent() -> None:
    versions = get_project_versions(ROOT)

    assert len(set(versions.values())) == 1, versions


def test_set_project_version_updates_every_declaration(tmp_path: Path) -> None:
    _copy_version_files(tmp_path)

    changed = set_project_version(tmp_path, "1.2.3")

    assert len(changed) == len({item.relative_path for item in VERSION_LOCATIONS})
    assert set(get_project_versions(tmp_path).values()) == {"1.2.3"}


def test_set_project_version_is_idempotent(tmp_path: Path) -> None:
    _copy_version_files(tmp_path)
    set_project_version(tmp_path, "1.2.3")

    assert set_project_version(tmp_path, "1.2.3") == ()


def test_set_project_version_does_not_partially_write(tmp_path: Path) -> None:
    _copy_version_files(tmp_path)
    cargo_lock = tmp_path / "src-tauri" / "Cargo.lock"
    cargo_lock.write_text("missing package metadata\n", encoding="utf-8")
    before = (tmp_path / "pyproject.toml").read_bytes()

    with pytest.raises(VersionUpdateError):
        set_project_version(tmp_path, "1.2.3")

    assert (tmp_path / "pyproject.toml").read_bytes() == before
