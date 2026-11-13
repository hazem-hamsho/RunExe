import os

from runexe.backend.models.config import CapsuleConfig
from runexe.backend.wine.drives import Drives


def test_device_backed_drive_is_ejectable(tmp_path):
    dosdevices = tmp_path / "dosdevices"
    dosdevices.mkdir()
    (dosdevices / "d:").symlink_to(tmp_path)
    (dosdevices / "d::").symlink_to("/dev/sr0")
    config = CapsuleConfig(Path=str(tmp_path), Custom_Path=str(tmp_path))
    drives = Drives(config)

    assert drives.is_ejectable("D") is True
    assert drives.is_ejectable("E:") is False


def test_set_drive_path_replaces_broken_symlink(tmp_path):
    capsule_path = tmp_path / "capsule"
    dosdevices_path = capsule_path / "dosdevices"
    dosdevices_path.mkdir(parents=True)
    drive_path = dosdevices_path / "h:"
    drive_path.symlink_to("/missing/portal/path", target_is_directory=True)

    config = CapsuleConfig(
        Name="Test",
        Path=str(capsule_path),
        Custom_Path=True,
    )

    Drives(config).set_drive_path("H", "/home/test/Games")

    assert drive_path.is_symlink()
    assert os.readlink(drive_path) == "/home/test/Games"
