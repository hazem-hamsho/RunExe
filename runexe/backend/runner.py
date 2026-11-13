# runner.py
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
from typing import TYPE_CHECKING

from runexe.backend.logger import Logger
from runexe.backend.managers.runtime import RuntimeManager
from runexe.backend.models.config import CapsuleConfig
from runexe.backend.models.result import Result
from runexe.backend.utils.manager import ManagerUtils
from runexe.backend.utils.wine import WineUtils
from runexe.backend.wine.wineboot import WineBoot

if TYPE_CHECKING:
    from runexe.backend.managers.manager import Manager

logging = Logger()


class Runner:
    """
    This class handle everything related to the runner (e.g. Wine).
    It should not contain any manager logic (e.g. catalogs, checks, etc.) or
    any capsule related stuff (e.g. config handling, etc.), also DXVK and VKD3D
    handling should not performed from here.
    """

    @staticmethod
    def runner_update(
        config: CapsuleConfig, manager: "Manager", runner: str
    ) -> Result[dict]:
        """
        This method should be executed after changing the runner
        for a capsule. It does a prefix update and re-initialize the
        active DLLComponents (dxvk, vkd3d…) to re-create
        the overrides and fix broken registry keys.
        """
        logging.info(f"Doing runner update for capsule: {config.Name}")
        wineboot = WineBoot(config)

        if not runner.startswith("sys-"):
            runner_path = ManagerUtils.get_runner_path(runner)

            if not os.path.exists(runner_path):
                logging.error(f"Runner {runner} not found in {runner_path}")
                return Result(status=False, data={"config": config})

        # kill wineserver after update
        wineboot.kill(force_if_stalled=True)

        capsule_path = ManagerUtils.get_capsule_path(config)
        existing_profiles = WineUtils.get_user_profile_ids(capsule_path)
        if existing_profiles is None:
            logging.error("Could not inspect the capsule user profiles.")
            return Result(status=False, data={"config": config})

        # update capsule config
        up_config = manager.update_config(
            config=config, key="Runner", value=runner
        ).data["config"]

        # perform a prefix update
        wineboot.update()
        if not WineUtils.unlink_user_profile_links(capsule_path, existing_profiles):
            logging.error("Could not unlink the new capsule user profile.")
            return Result(status=False, data={"config": up_config})

        # re-initialize DLLComponents
        if config.Parameters.dxvk:
            manager.install_dll_component(config, "dxvk", overrides_only=True)
        if config.Parameters.vkd3d:
            manager.install_dll_component(config, "vkd3d", overrides_only=True)

        return Result(status=True, data={"config": up_config})
