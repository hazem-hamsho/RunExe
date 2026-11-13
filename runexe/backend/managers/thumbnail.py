# thumbnail.py
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
from pathlib import Path
from typing import Optional

from runexe.backend.globals import Paths
from runexe.backend.logger import Logger
from runexe.backend.models.config import CapsuleConfig
from runexe.backend.utils.manager import ManagerUtils

logging = Logger()


class ThumbnailManager:
    @staticmethod
    def get_path(config: Optional[CapsuleConfig], uri: str):
        if uri.startswith("grid:"):
            if config is None:
                return None
            return ThumbnailManager.__load_grid(config, uri)
        logging.error("Unknown URI: " + uri)
        return None

    @staticmethod
    def __load_grid(config: CapsuleConfig, uri: str):
        capsule_path = ManagerUtils.get_capsule_path(config)
        file_name = uri[5:]
        path = os.path.join(capsule_path, "grids", file_name)

        if not os.path.exists(path):
            logging.error("Grid not found: " + path)
            return None

        return path
