# runtime.py
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
from functools import lru_cache

from runexe.backend.globals import Paths


class RuntimeManager:
    @staticmethod
    @lru_cache
    def get_runtimes(_filter: str = "capsules"):
        runtimes = {
            "capsules": RuntimeManager.__get_capsules_runtime(),
        }

        return runtimes.get(_filter, False)

    @staticmethod
    def get_runtime_env(_filter: str = "capsules"):
        runtime = RuntimeManager.get_runtimes(_filter)
        env = ""

        if runtime:
            for p in runtime:
                env += f":{p}"

        else:
            return False

        ld = os.environ.get("LD_LIBRARY_PATH")
        if ld:
            env += f":{ld}"

        return env

    @staticmethod
    def __get_runtime(paths: list, structure: list):
        def check_structure(found, expected):
            for e in expected:
                if e not in found:
                    return False
            return True

        for runtime_path in paths:
            if not os.path.exists(runtime_path):
                continue

            structure_found = []
            for root, dirs, files in os.walk(runtime_path):
                for d in dirs:
                    structure_found.append(d)

            if not check_structure(structure_found, structure):
                return []

            return [f"{runtime_path}/{s}" for s in structure]

        return False

    @staticmethod
    def __get_capsules_runtime():
        paths = ["/app/etc/runtime", Paths.runtimes]
        structure = ["lib", "lib32"]

        return RuntimeManager.__get_runtime(paths, structure)
