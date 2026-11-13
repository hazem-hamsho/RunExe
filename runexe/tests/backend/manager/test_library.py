import base64
from pathlib import Path

import pytest

from runexe.backend.globals import Paths
from runexe.backend.managers import library as library_module
from runexe.backend.managers.library import LibraryManager
from runexe.backend.managers.thumbnail import ThumbnailManager
from runexe.backend.models.config import CapsuleConfig
from runexe.backend.utils.manager import ManagerUtils

PNG_DATA = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zwe0AAAAASUVORK5CYII="
)


def _write_cover(path: Path) -> None:
    path.write_bytes(PNG_DATA)


@pytest.fixture
def library_manager(monkeypatch, tmp_path):
    library_path = tmp_path / "library.yml"
    capsule_path = tmp_path / "capsule"
    capsule_path.mkdir()

    monkeypatch.setattr(LibraryManager, "library_path", str(library_path))
    monkeypatch.setattr(
        ManagerUtils,
        "get_capsule_path",
        lambda _config: str(capsule_path),
    )
    return LibraryManager(), capsule_path


def _config(program_folder: Path) -> CapsuleConfig:
    return CapsuleConfig(
        Name="Games",
        Path="Games",
        External_Programs={
            "program-id": {
                "id": "program-id",
                "name": "Example",
                "executable": "example.exe",
                "path": str(program_folder / "example.exe"),
                "folder": str(program_folder / "working-directory"),
            }
        },
    )


def _entry() -> dict:
    return {
        "capsule": {"name": "Games", "path": "Games"},
        "name": "Example",
        "id": "program-id",
        "icon": "io.github.runexe.RunExe-program",
    }


def test_add_to_library_prefers_cover_next_to_program(
    monkeypatch, tmp_path, library_manager
):
    manager, capsule_path = library_manager
    program_folder = tmp_path / "program"
    program_folder.mkdir()
    program_cover = program_folder / "example.exe.png"
    _write_cover(program_cover)
    _write_cover(capsule_path / "library.png")

    manager.add_to_library(_entry(), _config(program_folder))

    entry = next(iter(manager.get_library().values()))
    assert entry["thumbnail"].startswith("grid:")
    cover = capsule_path / "grids" / entry["thumbnail"].removeprefix("grid:")
    assert cover.read_bytes() == PNG_DATA


@pytest.mark.parametrize("extension", ["jpg", "jpeg", "webp", "PNG"])
def test_add_to_library_accepts_supported_program_cover_extensions(
    extension, tmp_path, library_manager
):
    manager, capsule_path = library_manager
    program_folder = tmp_path / "program"
    program_folder.mkdir()
    _write_cover(program_folder / f"example.exe.{extension}")

    manager.add_to_library(_entry(), _config(program_folder))

    entry = next(iter(manager.get_library().values()))
    cover = capsule_path / "grids" / entry["thumbnail"].removeprefix("grid:")
    assert cover.read_bytes() == PNG_DATA


def test_add_to_library_prefers_png_when_multiple_local_covers_exist(
    tmp_path, library_manager
):
    manager, capsule_path = library_manager
    program_folder = tmp_path / "program"
    program_folder.mkdir()
    jpg_cover = program_folder / "example.exe.jpg"
    png_cover = program_folder / "example.exe.png"
    jpg_cover.write_bytes(PNG_DATA + b"jpg")
    png_cover.write_bytes(PNG_DATA + b"png")

    manager.add_to_library(_entry(), _config(program_folder))

    entry = next(iter(manager.get_library().values()))
    cover = capsule_path / "grids" / entry["thumbnail"].removeprefix("grid:")
    assert cover.read_bytes() == PNG_DATA + b"png"


def test_add_to_library_uses_capsule_cover_before_remote(
    monkeypatch, tmp_path, library_manager
):
    manager, capsule_path = library_manager
    program_folder = tmp_path / "program"
    program_folder.mkdir()
    _write_cover(capsule_path / "library.png")

    manager.add_to_library(_entry(), _config(program_folder))

    entry = next(iter(manager.get_library().values()))
    cover = capsule_path / "grids" / entry["thumbnail"].removeprefix("grid:")
    assert cover.read_bytes() == PNG_DATA


def test_add_to_library_ignores_invalid_local_cover(
    monkeypatch, tmp_path, library_manager
):
    manager, _capsule_path = library_manager
    program_folder = tmp_path / "program"
    program_folder.mkdir()
    (program_folder / "example.exe.png").write_bytes(b"not an image")
    manager.add_to_library(_entry(), _config(program_folder))

    entry = next(iter(manager.get_library().values()))
    assert entry.get("thumbnail") is None


def test_set_thumbnail_replaces_managed_cover(tmp_path, library_manager):
    manager, capsule_path = library_manager
    program_folder = tmp_path / "program"
    program_folder.mkdir()
    first_cover = tmp_path / "first.png"
    _write_cover(first_cover)
    second_cover = tmp_path / "second.jpg"
    second_cover.write_bytes(PNG_DATA + b"\n")

    manager.add_to_library(
        {
            **_entry(),
            "thumbnail": manager.import_thumbnail(first_cover, _config(program_folder)),
        },
        _config(program_folder),
    )
    entry_uuid, entry = next(iter(manager.get_library().items()))
    old_cover = capsule_path / "grids" / entry["thumbnail"].removeprefix("grid:")

    assert manager.set_thumbnail(entry_uuid, second_cover, _config(program_folder))

    entry = manager.get_library()[entry_uuid]
    new_cover = capsule_path / "grids" / entry["thumbnail"].removeprefix("grid:")
    assert new_cover.read_bytes() == PNG_DATA + b"\n"
    assert not old_cover.exists()


def test_set_thumbnail_reuses_identical_managed_cover(tmp_path, library_manager):
    manager, capsule_path = library_manager
    program_folder = tmp_path / "program"
    program_folder.mkdir()
    first_cover = tmp_path / "first.png"
    second_cover = tmp_path / "second.png"
    _write_cover(first_cover)
    _write_cover(second_cover)
    config = _config(program_folder)
    thumbnail = manager.import_thumbnail(first_cover, config)
    entry_uuid = manager.add_to_library({**_entry(), "thumbnail": thumbnail}, config)

    assert manager.set_thumbnail(entry_uuid, second_cover, config)

    assert manager.get_library()[entry_uuid]["thumbnail"] == thumbnail
    assert (capsule_path / "grids" / thumbnail.removeprefix("grid:")).exists()


def test_set_thumbnail_keeps_cover_used_by_another_entry(tmp_path, library_manager):
    manager, capsule_path = library_manager
    program_folder = tmp_path / "program"
    program_folder.mkdir()
    first_cover = tmp_path / "first.png"
    _write_cover(first_cover)
    second_cover = tmp_path / "second.png"
    _write_cover(second_cover)
    config = _config(program_folder)
    thumbnail = manager.import_thumbnail(first_cover, config)

    manager.add_to_library({**_entry(), "thumbnail": thumbnail}, config)
    manager.add_to_library(
        {**_entry(), "id": "other-program-id", "thumbnail": thumbnail}, config
    )
    entry_uuid = next(iter(manager.get_library()))
    old_cover = capsule_path / "grids" / thumbnail.removeprefix("grid:")

    assert manager.set_thumbnail(entry_uuid, second_cover, config)
    assert old_cover.exists()


def test_remove_from_library_removes_managed_cover(tmp_path, library_manager):
    manager, capsule_path = library_manager
    program_folder = tmp_path / "program"
    program_folder.mkdir()
    cover = tmp_path / "cover.png"
    _write_cover(cover)
    config = _config(program_folder)
    thumbnail = manager.import_thumbnail(cover, config)
    entry_uuid = manager.add_to_library({**_entry(), "thumbnail": thumbnail}, config)
    stored_cover = capsule_path / "grids" / thumbnail.removeprefix("grid:")

    manager.remove_from_library(entry_uuid, config)

    assert entry_uuid not in manager.get_library()
    assert not stored_cover.exists()


def test_import_thumbnail_reuses_identical_cover(tmp_path, library_manager):
    manager, capsule_path = library_manager
    program_folder = tmp_path / "program"
    program_folder.mkdir()
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    _write_cover(first)
    _write_cover(second)
    config = _config(program_folder)

    first_thumbnail = manager.import_thumbnail(first, config)
    second_thumbnail = manager.import_thumbnail(second, config)

    assert second_thumbnail == first_thumbnail
    assert len(list((capsule_path / "grids").iterdir())) == 1
