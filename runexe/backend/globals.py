# globals.py
#
# Copyright 2025 mirkobrombin <brombin94@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, in version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

import os
import shutil
from pathlib import Path
from typing import Dict

from runexe.backend.utils import json, yaml


def is_cpak() -> bool:
    return "CPAK_CONTAINER_ID" in os.environ


def is_official_package() -> bool:
    return "FLATPAK_ID" in os.environ or is_cpak()


def _has_windows_unsafe_component(path: str) -> bool:
    return any(
        part.endswith((".", " "))
        for part in Path(os.path.abspath(path)).parts
        if part != os.path.sep
    )


def _get_wine_compatible_base(path: str) -> str:
    path = os.path.abspath(path)
    if not _has_windows_unsafe_component(path):
        return path

    flatpak_data = "/var/data"
    try:
        data_home = os.path.dirname(path)
        if (
            "FLATPAK_ID" in os.environ
            and os.path.isdir(flatpak_data)
            and os.path.samefile(data_home, flatpak_data)
        ):
            return os.path.join(flatpak_data, os.path.relpath(path, data_home))
    except OSError:
        pass
    return path


class Paths:
    xdg_data_home = os.environ.get(
        "XDG_DATA_HOME", os.path.join(Path.home(), ".local/share")
    )

    # Icon paths
    icons_user = f"{xdg_data_home}/icons"

    # Local paths
    base = _get_wine_compatible_base(f"{xdg_data_home}/runexe")

    # User applications path
    applications = f"{xdg_data_home}/applications/"

    temp = f"{base}/temp"
    runtimes = f"{base}/runtimes"
    winebridge = f"{base}/winebridge"
    runners = f"{base}/runners"
    capsules = f"{base}/capsules"
    dxvk = f"{base}/dxvk"
    vkd3d = f"{base}/vkd3d"
    templates = f"{base}/templates"
    library = f"{base}/library.yml"
    process_metrics = f"{base}/process_metrics.sqlite"


class TrdyPaths:
    # External managers paths
    wine = f"{Path.home()}/.wine"
    lutris = f"{Path.home()}/Games"
    playonlinux = f"{Path.home()}/.PlayOnLinux/wineprefix"
    capsulesv1 = f"{Path.home()}/.RunExe"


# check if capsules exists in xdg data path
os.makedirs(Paths.base, exist_ok=True)

try:
    os.getcwd()
except OSError:
    try:
        os.chdir(Paths.base)
    except OSError:
        pass

base_version = ""
if os.path.isfile("/app/manifest.json"):
    with open("/app/manifest.json", mode="r", encoding="utf-8") as file:
        base_version = (
            json.load(file)  # type: ignore
            .get("base-version", "")
            .removeprefix("stable-")
        )

# encoding detection correction, following windows defaults
locale_encodings: Dict[str, str] = {"ja_JP": "cp932", "zh_CN": "gbk"}
