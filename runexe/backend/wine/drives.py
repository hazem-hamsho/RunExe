import os

from runexe.backend.logger import Logger
from runexe.backend.models.config import CapsuleConfig
from runexe.backend.utils.manager import ManagerUtils

logging = Logger()


class Drives:
    def __init__(self, config: CapsuleConfig):
        self.config = config
        capsule = ManagerUtils.get_capsule_path(self.config)
        self.dosdevices_path = os.path.join(capsule, "dosdevices")

    def get_all(self):
        """Get all the drives from the capsule"""
        drives = {}
        if os.path.exists(self.dosdevices_path):
            for drive in os.listdir(self.dosdevices_path):
                if os.path.islink(f"{self.dosdevices_path}/{drive}"):
                    letter = os.path.basename(drive).replace(":", "").upper()
                    if len(letter) == 1 and letter.isalpha():
                        path = os.readlink(f"{self.dosdevices_path}/{drive}")
                        drives[letter] = path
        return drives

    def get_drive(self, letter: str):
        """Get a drive from the capsule"""
        if letter in self.get_all():
            return self.get_all().get(letter)
        return None

    def is_ejectable(self, letter: str) -> bool:
        """Return whether the drive has a Wine device link."""
        device_link = os.path.join(
            self.dosdevices_path, f"{letter.lower().rstrip(':')}::"
        )
        return os.path.islink(device_link)

    def set_drive_path(self, letter: str, path: str):
        """Change a drives path in the capsule"""
        letter = f"{letter}:".lower()
        drive_sym_path = os.path.join(self.dosdevices_path, letter)
        if not os.path.exists(self.dosdevices_path):
            os.makedirs(self.dosdevices_path)
        if not os.path.lexists(drive_sym_path):
            os.symlink(path, drive_sym_path)
            logging.info(f"New drive {letter} added to the capsule")
        else:
            os.remove(drive_sym_path)
            os.symlink(path, drive_sym_path)
            logging.info(f"Drive {letter} path changed to {path}")

    def remove_drive(self, letter: str):
        """Remove a drive from the capsule"""
        if letter.upper() in self.get_all():
            os.remove(f"{self.dosdevices_path}/{letter.lower()}:")
            logging.info(f"Drive {letter} removed from the capsule")
