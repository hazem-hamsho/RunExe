# manager.py
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

import fnmatch
import os
import random
import re
import shutil
import subprocess
import time
import uuid
from copy import deepcopy
from datetime import datetime
from gettext import gettext as _
from glob import glob
from threading import Event
from typing import Any, Callable, ClassVar, Dict, List, Optional, Tuple

import pathvalidate

from runexe.backend.dlls.dxvk import DXVKComponent
from runexe.backend.dlls.vkd3d import VKD3DComponent
from runexe.backend.globals import Paths
from runexe.backend.logger import Logger
from runexe.backend.managers.component import ComponentManager
from runexe.backend.managers.data import DataManager, UserDataKeys
from runexe.backend.managers.dependency import DependencyManager
from runexe.backend.managers.importer import ImportManager
from runexe.backend.managers.installer import InstallerManager
from runexe.backend.managers.library import LibraryManager
from runexe.backend.managers.playtime import ProcessSessionTracker
from runexe.backend.managers.registry_rule import RegistryRuleManager
from runexe.backend.managers.repository import RepositoryManager
from runexe.backend.managers.template import TemplateManager
from runexe.backend.managers.versioning import VersioningManager
from runexe.backend.models.config import CapsuleConfig
from runexe.backend.models.process import (
    ProcessFinishedPayload,
    ProcessStartedPayload,
)
from runexe.backend.models.result import Result
from runexe.backend.models.samples import Samples
from runexe.backend.state import (
    EventManager,
    Events,
    Notification,
    SignalManager,
    Signals,
)
from runexe.backend.utils import yaml
from runexe.backend.utils.connection import ConnectionUtils
from runexe.backend.utils.file import FileUtils
from runexe.backend.utils.generic import sort_by_version
from runexe.backend.utils.gpu import GPUUtils, GPUVendors
from runexe.backend.utils.gsettings_stub import GSettingsStub
from runexe.backend.utils.lnk import LnkUtils
from runexe.backend.utils.manager import ManagerUtils
from runexe.backend.utils.singleton import Singleton
from runexe.backend.utils.threading import RunAsync
from runexe.backend.utils.wine import WineUtils
from runexe.backend.wine.drives import Drives
from runexe.backend.wine.reg import Reg
from runexe.backend.wine.regkeys import RegKeys
from runexe.backend.wine.uninstaller import Uninstaller
from runexe.backend.wine.wineboot import WineBoot
from runexe.backend.wine.winepath import WinePath
from runexe.backend.wine.wineserver import WineServer

logging = Logger()


class Manager(metaclass=Singleton):
    """
    This is the core of RunExe, everything starts from here. There should
    be only one instance of this class, as it checks for the existence of
    the capsules' directories and creates them if they don't exist. Also
    check for components, dependencies, and installers so this check should
    not be performed every time the manager is initialized.
    """

    # component lists
    runtimes_available = []
    winebridge_available = []
    runners_available = []
    external_runners: set[str] = set()
    dxvk_available = []
    vkd3d_available = []
    local_capsules: Dict[str, CapsuleConfig] = {}
    _programs_cache: Dict[str, List[dict]] = {}
    supported_runtimes = {}
    supported_winebridge = {}
    supported_wine_runners = {}
    supported_proton_runners = {}
    supported_dxvk = {}
    supported_vkd3d = {}
    supported_dependencies = {}
    supported_installers = {}
    _playtime_signals_connected: bool = False

    def __init__(
        self,
        g_settings: Any = None,
        check_connection: bool = True,
        is_cli: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)

        times = {"start": time.time()}

        # common variables
        self.is_cli = is_cli
        self.settings = g_settings or GSettingsStub
        # The CLI used to be wired offline unconditionally, which left the
        # component catalog empty and made capsule creation fail when nothing was
        # cached locally. Honor the actual force-offline setting instead, so the
        # CLI can fetch and install components just like the GUI.
        self.utils_conn = ConnectionUtils(
            force_offline=self.settings.get_boolean("force-offline")
        )
        self.data_mgr = DataManager()
        _offline = True

        if check_connection and not self.utils_conn.force_offline:
            _offline = not self.utils_conn.check_connection()

        # validating user-defined Paths.capsules
        if user_capsules_path := self.data_mgr.get(UserDataKeys.CustomCapsulesPath):
            is_portal_path = (
                "/run/user/" in user_capsules_path and "/doc/" in user_capsules_path
            )
            if is_portal_path:
                # Portal-backed paths are not reliable enough for capsule storage.
                logging.error(
                    f"Custom capsules path {user_capsules_path} uses the document "
                    f"portal! Falling back to default path."
                )
            elif os.path.isdir(user_capsules_path) and os.access(
                user_capsules_path, os.W_OK
            ):
                Paths.capsules = user_capsules_path
            else:
                logging.error(
                    f"Custom capsules path {user_capsules_path} is not a writable "
                    f"directory! Falling back to default path."
                )

        # sub-managers
        self.repository_manager = RepositoryManager(get_index=not _offline)
        if self.repository_manager.aborted_connections > 0:
            self.utils_conn.status = False
            _offline = True

        times["RepositoryManager"] = time.time()
        self.versioning_manager = VersioningManager(self)
        times["VersioningManager"] = time.time()
        self.component_manager = ComponentManager(self, _offline)
        self.installer_manager = InstallerManager(self, _offline)
        self.dependency_manager = DependencyManager(self, _offline)
        self.import_manager = ImportManager(self)
        times["ImportManager"] = time.time()

        # Initialize playtime tracker
        self._initialize_playtime_tracker()
        times["PlaytimeTracker"] = time.time()

        # React to runtime changes in playtime preference when available
        if hasattr(self.settings, "connect"):
            try:
                self.settings.connect(
                    "changed::playtime-enabled", self._on_playtime_enabled_changed
                )
            except Exception:
                pass

        # Subscribe to playtime signals (connect once per process)
        if not Manager._playtime_signals_connected:
            SignalManager.connect(Signals.ProgramStarted, self._on_program_started)
            SignalManager.connect(Signals.ProgramFinished, self._on_program_finished)
            Manager._playtime_signals_connected = True

        if not self.is_cli:
            times.update(self.checks(install_latest=False, first_run=True).data)
        else:
            logging.set_silent()

        if "BOOT_TIME" in os.environ:
            _temp_times = times.copy()
            last = 0
            times_str = "Boot times:"
            for f, t in _temp_times.items():
                if last == 0:
                    last = int(round(t))
                    continue
                t = int(round(t))
                times_str += f"\n\t - {f} took: {t - last}s"
                last = t
            logging.info(times_str)

    def checks(
        self,
        install_latest=False,
        first_run=False,
        progress_callback: Optional[Callable[..., None]] = None,
    ) -> Result:
        logging.info("Performing RunExe checks…")

        rv = Result(status=True, data={})

        steps: List[Tuple[Optional[str], str, Callable[[], bool | None]]] = [
            ("check_app_dirs", _("Preparing folders…"), self.check_app_dirs),
            (
                "check_dxvk",
                _("Setting up DXVK…"),
                lambda: self.check_dxvk(install_latest),
            ),
            (
                "check_vkd3d",
                _("Setting up VKD3D…"),
                lambda: self.check_vkd3d(install_latest),
            ),
            (
                "check_runtimes",
                _("Preparing runtimes…"),
                lambda: self.check_runtimes(install_latest),
            ),
            (
                "check_winebridge",
                _("Preparing WineBridge…"),
                lambda: self.check_winebridge(install_latest),
            ),
            (
                "check_runners",
                _("Preparing runners…"),
                lambda: self.check_runners(install_latest),
            ),
        ]

        if first_run:
            steps.extend(
                [
                    (
                        None,
                        _("Organizing components…"),
                        self.organize_components,
                    ),
                    (
                        None,
                        _("Cleaning temporary files…"),
                        self.__clear_temp,
                    ),
                ]
            )

        steps.extend(
            [
                (
                    None,
                    _("Organizing dependencies…"),
                    self.organize_dependencies,
                ),
                (
                    None,
                    _("Organizing installers…"),
                    self.organize_installers,
                ),
                ("check_capsules", _("Loading capsules…"), self.check_capsules),
            ]
        )

        total_steps = len(steps)

        for index, (data_key, description, func) in enumerate(steps, start=1):
            if progress_callback:
                try:
                    progress_callback(
                        description=description,
                        current_step=index,
                        total_steps=total_steps,
                        completed=False,
                    )
                except Exception as error:  # pragma: no cover - defensive
                    logging.debug(f"Progress callback start failed: {error}")

            result = func()

            if result is False:
                rv.set_status(False)

            if progress_callback:
                try:
                    progress_callback(
                        description=description,
                        current_step=index,
                        total_steps=total_steps,
                        completed=True,
                    )
                except Exception as error:  # pragma: no cover - defensive
                    logging.debug(f"Progress callback end failed: {error}")

            if data_key:
                rv.data[data_key] = time.time()

        return rv

    def __del__(self):
        # best-effort shutdown of playtime tracker
        try:
            if hasattr(self, "playtime_tracker") and self.playtime_tracker:
                self.playtime_tracker.shutdown()
        except Exception:
            pass

    def _initialize_playtime_tracker(self) -> None:
        playtime_enabled = self.settings.get_boolean("playtime-enabled")
        playtime_interval = self.settings.get_int("playtime-heartbeat-interval")
        tracker = ProcessSessionTracker(
            enabled=playtime_enabled,
            heartbeat_interval=playtime_interval if playtime_interval > 0 else 60,
        )
        if not self.is_cli:
            tracker.recover_open_sessions()
        self.playtime_tracker = tracker

    def _on_playtime_enabled_changed(self, _settings, _key) -> None:
        enabled = self.settings.get_boolean("playtime-enabled")
        if not enabled:
            if (
                getattr(self, "playtime_tracker", None)
                and self.playtime_tracker.enabled
            ):
                self._launch_to_session.clear()
                self.playtime_tracker.disable_tracking()
            return

        if getattr(self, "playtime_tracker", None) and self.playtime_tracker.enabled:
            return

        self._initialize_playtime_tracker()

    # Playtime signal handlers
    _launch_to_session: Dict[str, int] = {}

    # Public Playtime API (wrap tracker with Result)
    def playtime_start(
        self,
        *,
        capsule_id: str,
        capsule_name: str,
        capsule_path: str,
        program_name: str,
        program_path: str,
    ) -> Result[int]:
        try:
            sid = self.playtime_tracker.start_session(
                capsule_id=capsule_id,
                capsule_name=capsule_name,
                capsule_path=capsule_path,
                program_name=program_name,
                program_path=program_path,
            )
            return Result(True, data=sid)
        except Exception as e:
            logging.exception(e)
            return Result(False, message=str(e))

    def playtime_finish(
        self,
        session_id: int,
        *,
        status: str = "success",
        ended_at: Optional[int] = None,
    ) -> Result[None]:
        try:
            if status == "success":
                self.playtime_tracker.mark_exit(
                    session_id, status="success", ended_at=ended_at
                )
            else:
                self.playtime_tracker.mark_failure(session_id, status=status)
            return Result(True)
        except Exception as e:
            logging.exception(e)
            return Result(False, message=str(e))

    def _on_program_started(self, data: Optional[Result] = None) -> None:
        try:
            if not data or not data.data:
                return
            payload: ProcessStartedPayload = data.data  # type: ignore
            logging.debug(
                f"Playtime signal: started launch_id={payload.launch_id} capsule={payload.capsule_name} program={payload.program_name}"
            )
            res = self.playtime_start(
                capsule_id=payload.capsule_id,
                capsule_name=payload.capsule_name,
                capsule_path=payload.capsule_path,
                program_name=payload.program_name,
                program_path=payload.program_path,
            )
            if not res.ok:
                return
            sid = int(res.data or -1)
            self._launch_to_session[payload.launch_id] = sid

            config = self._get_payload_config(payload)
            if config:
                RegistryRuleManager.apply_rules(config, trigger="start_program")
        except Exception:
            pass

    def _on_program_finished(self, data: Optional[Result] = None) -> None:
        try:
            if not data or not data.data:
                return
            payload: ProcessFinishedPayload = data.data  # type: ignore
            sid = self._launch_to_session.pop(payload.launch_id, -1)
            if sid and sid > 0:
                status = payload.status
                ended_at = int(payload.ended_at or time.time())
                logging.debug(
                    f"Playtime signal: finished launch_id={payload.launch_id} status={status} sid={sid}"
                )
                self.playtime_finish(sid, status=status, ended_at=ended_at)

            config = self._get_payload_config(payload)
            if config:
                RegistryRuleManager.apply_rules(config, trigger="stop_program")
        except Exception:
            pass

    def _get_payload_config(self, payload) -> Optional[CapsuleConfig]:
        config = self.local_capsules.get(payload.capsule_name)
        if isinstance(config, CapsuleConfig):
            return config

        try:
            config_path = os.path.join(payload.capsule_path, "capsule.yml")
        except Exception:
            return None

        loaded = CapsuleConfig.load(config_path)
        if loaded.status:
            return loaded.data
        return None

    def __clear_temp(self, force: bool = False):
        """Clears the temp directory if user setting allows it. Use the force
        parameter to force clearing the directory.
        """
        if self.settings.get_boolean("temp") or force:
            try:
                shutil.rmtree(Paths.temp)
                os.makedirs(Paths.temp, exist_ok=True)
                logging.info("Temp directory cleaned successfully!")
            except FileNotFoundError:
                self.check_app_dirs()

    def get_cache_details(self) -> dict:
        self.check_app_dirs()
        file_utils = FileUtils()

        temp_size_bytes = file_utils.get_path_size(Paths.temp, human=False)
        templates = []
        templates_size_bytes = 0

        for template in TemplateManager.get_templates():
            template_uuid = template.get("uuid", "")
            template_path = os.path.join(Paths.templates, template_uuid)

            size_bytes = file_utils.get_path_size(template_path, human=False)
            templates_size_bytes += size_bytes

            templates.append(
                {
                    "uuid": template_uuid,
                    "env": template.get("env", ""),
                    "created": template.get("created", ""),
                    "size": file_utils.get_human_size(size_bytes),
                    "size_bytes": size_bytes,
                }
            )

        total_size_bytes = temp_size_bytes + templates_size_bytes

        return {
            "temp": {
                "path": Paths.temp,
                "size": file_utils.get_human_size(temp_size_bytes),
                "size_bytes": temp_size_bytes,
            },
            "templates": templates,
            "templates_size": file_utils.get_human_size(templates_size_bytes),
            "templates_size_bytes": templates_size_bytes,
            "total_size": file_utils.get_human_size(total_size_bytes),
            "total_size_bytes": total_size_bytes,
        }

    def clear_temp_cache(self) -> Result[None]:
        try:
            self.__clear_temp(force=True)
        except Exception as ex:
            logging.error(f"Failed to clear temp cache: {ex}")
            return Result(False, message=str(ex))

        return Result(True)

    def clear_template_cache(self, template_uuid: str) -> Result[None]:
        self.check_app_dirs()
        try:
            TemplateManager.delete_template(template_uuid)
        except Exception as ex:
            logging.error(f"Failed to clear template cache: {ex}")
            return Result(False, message=str(ex))

        return Result(True)

    def clear_templates_cache(self) -> Result[None]:
        self.check_app_dirs()
        try:
            for template in TemplateManager.get_templates():
                TemplateManager.delete_template(template.get("uuid", ""))
        except Exception as ex:
            logging.error(f"Failed to clear templates cache: {ex}")
            return Result(False, message=str(ex))

        return Result(True)

    def clear_all_caches(self) -> Result[None]:
        temp_result = self.clear_temp_cache()
        if not temp_result.ok:
            return temp_result

        templates_result = self.clear_templates_cache()
        if not templates_result.ok:
            return templates_result

        return Result(True)

    def update_capsules(self, silent: bool = False):
        """Checks for new capsules and update the list view."""
        self.check_capsules(silent)
        SignalManager.send(Signals.ManagerLocalCapsulesLoaded)

    def check_app_dirs(self):
        """
        Checks for the existence of the capsules' directories, and creates them
        if they don't exist.
        """
        if not os.path.isdir(Paths.runners):
            logging.info("Runners path doesn't exist, creating now.")
            os.makedirs(Paths.runners, exist_ok=True)

        if not os.path.isdir(Paths.runtimes):
            logging.info("Runtimes path doesn't exist, creating now.")
            os.makedirs(Paths.runtimes, exist_ok=True)

        if not os.path.isdir(Paths.winebridge):
            logging.info("WineBridge path doesn't exist, creating now.")
            os.makedirs(Paths.winebridge, exist_ok=True)

        if not os.path.isdir(Paths.capsules):
            logging.info("Capsules path doesn't exist, creating now.")
            os.makedirs(Paths.capsules, exist_ok=True)

        if not os.path.isdir(Paths.dxvk):
            logging.info("Dxvk path doesn't exist, creating now.")
            os.makedirs(Paths.dxvk, exist_ok=True)

        if not os.path.isdir(Paths.vkd3d):
            logging.info("Vkd3d path doesn't exist, creating now.")
            os.makedirs(Paths.vkd3d, exist_ok=True)

        if not os.path.isdir(Paths.templates):
            logging.info("Templates path doesn't exist, creating now.")
            os.makedirs(Paths.templates, exist_ok=True)

        if not os.path.isdir(Paths.temp):
            logging.info("Temp path doesn't exist, creating now.")
            os.makedirs(Paths.temp, exist_ok=True)

    @RunAsync.run_async
    def organize_components(self):
        """Get components catalog and organizes into supported_ lists."""
        EventManager.wait(Events.ComponentsFetching)
        catalog = self.component_manager.fetch_catalog()
        if len(catalog) == 0:
            EventManager.done(Events.ComponentsOrganizing)
            logging.info("No components found.")
            return

        self.supported_wine_runners = catalog["wine"]
        self.supported_proton_runners = catalog["proton"]
        self.supported_runtimes = catalog["runtimes"]
        self.supported_winebridge = catalog["winebridge"]
        self.supported_dxvk = catalog["dxvk"]
        self.supported_vkd3d = catalog["vkd3d"]
        EventManager.done(Events.ComponentsOrganizing)

    @RunAsync.run_async
    def organize_dependencies(self):
        """Organizes dependencies into supported_dependencies."""
        EventManager.wait(Events.DependenciesFetching)
        catalog = self.dependency_manager.fetch_catalog()
        if len(catalog) == 0:
            EventManager.done(Events.DependenciesOrganizing)
            logging.info("No dependencies found!")
            return

        self.supported_dependencies = catalog
        EventManager.done(Events.DependenciesOrganizing)

    @RunAsync.run_async
    def organize_installers(self):
        """Organizes installers into supported_installers."""
        EventManager.wait(Events.InstallersFetching)
        catalog = self.installer_manager.fetch_catalog()
        if len(catalog) == 0:
            EventManager.done(Events.InstallersOrganizing)
            logging.info("No installers found!")
            return

        self.supported_installers = catalog
        EventManager.done(Events.InstallersOrganizing)

    def remove_dependency(self, config: CapsuleConfig, dependency: list):
        """Uninstall a dependency and remove it from the capsule config."""
        dependency = dependency[0]
        logging.info(f"Removing {dependency} dependency from {config.Name}")
        uninstallers = config.Uninstallers

        names = Uninstaller.get_names(uninstallers.get(dependency))
        if not names:
            return Result(
                status=False,
                message=f"No uninstaller is available for {dependency}.",
            )

        uninstaller = Uninstaller(config)
        for name in names:
            result = uninstaller.from_name(name)
            if not result.ok:
                return Result(
                    False,
                    message=result.message or f"Failed to uninstall {name}.",
                )

        # remove dependency from capsule configuration
        if dependency in config.Installed_Dependencies:
            config.Installed_Dependencies.remove(dependency)

        self.update_config(
            config, key="Installed_Dependencies", value=config.Installed_Dependencies
        )
        self.update_config(
            config,
            key=dependency,
            value=None,
            scope="Uninstallers",
            remove=True,
        )
        return Result(status=True, data={"removed": True})

    def check_runners(self, install_latest: bool = True) -> bool:
        """
        Check for available runners (both system and RunExe) and install
        the latest version if install_latest is True. It also masks the
        winemenubuilder tool.
        """
        runners = glob(f"{Paths.runners}/*/")
        ManagerUtils.set_external_runner_paths({})
        self.external_runners = set()
        self.runners_available, runners_available = [], []

        # lock winemenubuilder.exe
        for runner in runners:
            winemenubuilder_paths = [
                f"{runner}lib64/wine/x86_64-windows/winemenubuilder.exe",
                f"{runner}lib/wine/x86_64-windows/winemenubuilder.exe",
                f"{runner}lib32/wine/i386-windows/winemenubuilder.exe",
                f"{runner}lib/wine/i386-windows/winemenubuilder.exe",
            ]
            for winemenubuilder in winemenubuilder_paths:
                if os.path.isfile(winemenubuilder):
                    os.rename(winemenubuilder, f"{winemenubuilder}.lock")

        # check system wine
        if system_wine := shutil.which("wine"):
            """
            If the Wine command is available, get the runner version
            and add it to the runners_available list.
            """
            try:
                result = subprocess.run(
                    [system_wine, "--version"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                version = result.stdout.decode("utf-8").split()
                if result.returncode == 0 and version:
                    runners_available.append(f"sys-{version[0]}")
                else:
                    logging.warning("System Wine version check failed.")
            except OSError as error:
                logging.warning(f"System Wine is not executable: {error}")

        # check capsules runners
        for runner in runners:
            _runner = os.path.basename(os.path.normpath(runner))
            runners_available.append(_runner)

        runners_available = self.__sort_runners(runners_available, "")

        runners_order = {
            "soda": [],
            "caffe": [],
            "vaniglia": [],
            "lutris": [],
            "others": [],
            "sys-": [],
        }

        for i in runners_available:
            for r in runners_order:
                if i.startswith(r):
                    runners_order[r].append(i)
                    break
            else:
                runners_order["others"].append(i)

        self.runners_available = [x for l in list(runners_order.values()) for x in l]

        if len(self.runners_available) > 0:
            logging.info(
                "Runners found:\n - {0}".format("\n - ".join(self.runners_available))
            )

        tmp_runners = self.get_managed_wine_runners()

        if len(tmp_runners) == 0 and install_latest:
            logging.warning("No managed runners found.")

            if self.utils_conn.check_connection():
                # if connected, install the latest runner from repository
                try:
                    if not self.settings.get_boolean("release-candidate"):
                        tmp_runners = []
                        for runner in self.supported_wine_runners.items():
                            if runner[1]["Channel"] not in ["rc", "unstable"]:
                                tmp_runners.append(runner)
                                break
                        runner_name = next(iter(tmp_runners))[0]
                    else:
                        tmp_runners = self.supported_wine_runners
                        runner_name = next(iter(tmp_runners))
                    self.component_manager.install("runner", runner_name)
                except StopIteration:
                    return False
            else:
                return False

        return True

    def get_managed_wine_runners(self) -> list[str]:
        """Return managed runners that can create regular Wine capsules."""
        return [
            runner
            for runner in self.runners_available
            if not runner.startswith("sys-")
        ]

    def check_runtimes(self, install_latest: bool = True) -> bool:
        self.runtimes_available = []
        if "FLATPAK_ID" in os.environ:
            self.runtimes_available = ["flatpak-managed"]
            return True

        runtimes = os.listdir(Paths.runtimes)

        if len(runtimes) == 0:
            if install_latest and self.utils_conn.check_connection():
                logging.warning("No runtime found.")
                try:
                    version = next(iter(self.supported_runtimes))
                    return self.component_manager.install("runtime", version)
                except StopIteration:
                    return False
            return False

        runtime = runtimes[0]  # runtimes cannot be more than one
        manifest = os.path.join(Paths.runtimes, runtime, "manifest.yml")

        if os.path.exists(manifest):
            with open(manifest, "r") as f:
                data = yaml.load(f)
                version = data.get("version")
                if version:
                    version = f"runtime-{version}"
                    self.runtimes_available = [version]
                    return True
        return False

    def __winebridge_status(self) -> tuple[Optional[str], Optional[str], bool]:
        def _is_newer(candidate: str, current: str) -> bool:
            versions = [candidate, current]
            try:
                sorted_versions = sort_by_version(list(versions))
            except ValueError:
                sorted_versions = sorted(versions, reverse=True)
            return sorted_versions[0] == candidate and candidate != current

        self.winebridge_available = []
        winebridge = os.listdir(Paths.winebridge)
        latest_supported = None

        if self.supported_winebridge:
            try:
                latest_supported = sort_by_version(
                    list(self.supported_winebridge.keys())
                )[0]
            except ValueError:
                latest_supported = sorted(
                    list(self.supported_winebridge.keys()), reverse=True
                )[0]

        version_file = os.path.join(Paths.winebridge, "VERSION")
        installed_identifier = None
        if os.path.exists(version_file):
            with open(version_file, "r") as f:
                version = f.read().strip()
                if version:
                    installed_identifier = f"winebridge-{version}"
                    self.winebridge_available = [installed_identifier]

        missing_installation = len(winebridge) == 0 or not installed_identifier
        needs_latest = False
        if latest_supported:
            needs_latest = missing_installation or _is_newer(
                latest_supported, installed_identifier
            )

        return latest_supported, installed_identifier, needs_latest

    def winebridge_update_status(self) -> dict:
        latest_supported, installed_identifier, needs_latest = (
            self.__winebridge_status()
        )
        return {
            "latest_supported": latest_supported,
            "installed_identifier": installed_identifier,
            "needs_latest": needs_latest,
            "missing": not installed_identifier,
        }

    def check_winebridge(
        self, install_latest: bool = True, update: bool = False
    ) -> bool:
        latest_supported, installed_identifier, needs_latest = (
            self.__winebridge_status()
        )

        can_install = install_latest or update
        if can_install and needs_latest and latest_supported:
            if not self.utils_conn.check_connection():
                return False
            logging.warning("WineBridge installation/update required.")
            res = self.component_manager.install("winebridge", latest_supported)
            if res.ok:
                self.winebridge_available = [latest_supported]
                return True
            return False

        if needs_latest and not can_install:
            return False

        return bool(self.winebridge_available)

    def check_dxvk(self, install_latest: bool = True) -> bool:
        res = self.__check_component("dxvk", install_latest)
        if res:
            self.dxvk_available = res
        return res is not False

    def check_vkd3d(self, install_latest: bool = True) -> bool:
        res = self.__check_component("vkd3d", install_latest)
        if res:
            self.vkd3d_available = res
        return res is not False

    def get_offline_components(
        self, component_type: str, extra_name_check: str = ""
    ) -> list:
        components = {
            "dxvk": {
                "available": self.dxvk_available,
                "supported": self.supported_dxvk,
            },
            "vkd3d": {
                "available": self.vkd3d_available,
                "supported": self.supported_vkd3d,
            },
            "runner": {
                "available": self.runners_available,
                "supported": self.supported_wine_runners,
            },
            "runner:proton": {
                "available": self.runners_available,
                "supported": self.supported_proton_runners,
            },
        }
        if component_type not in components:
            logging.warning(f"Unknown component type found: {component_type}")
            raise ValueError("Component type not supported.")

        component_list = components[component_type]
        offline_components = list(
            set(component_list["available"]).difference(
                component_list["supported"].keys()
            )
        )

        if component_type == "runner":
            offline_components = [
                runner
                for runner in offline_components
                if not runner.startswith("sys-")
            ]
        elif component_type == "runner:proton":
            offline_components = []

        if (
            extra_name_check
            and extra_name_check not in component_list["available"]
            and extra_name_check not in component_list["supported"]
        ):
            offline_components.append(extra_name_check)

        try:
            return sort_by_version(offline_components)
        except ValueError:
            return sorted(offline_components, reverse=True)

    def __check_component(
        self, component_type: str, install_latest: bool = True
    ) -> bool | list:
        components = {
            "dxvk": {
                "available": self.dxvk_available,
                "supported": self.supported_dxvk,
                "path": Paths.dxvk,
            },
            "vkd3d": {
                "available": self.vkd3d_available,
                "supported": self.supported_vkd3d,
                "path": Paths.vkd3d,
            },
            "runtime": {
                "available": self.runtimes_available,
                "supported": self.supported_runtimes,
                "path": Paths.runtimes,
            },
        }

        if component_type not in components:
            logging.warning(f"Unknown component type found: {component_type}")
            raise ValueError("Component type not supported.")

        component = components[component_type]
        component["available"] = os.listdir(component["path"])

        if len(component["available"]) > 0:
            logging.info(
                "{0}s found:\n - {1}".format(
                    component_type.capitalize(), "\n - ".join(component["available"])
                )
            )

        if len(component["available"]) == 0 and install_latest:
            logging.warning(f"No {component_type} found.")

            if self.utils_conn.check_connection():
                # if connected, install the latest component from repository
                try:
                    if not self.settings.get_boolean("release-candidate"):
                        tmp_components = []
                        for cpnt in component["supported"].items():
                            if cpnt[1]["Channel"] not in ["rc", "unstable"]:
                                tmp_components.append(cpnt)
                                break
                        component_version = next(iter(tmp_components))[0]
                    else:
                        tmp_components = component["supported"]
                        component_version = next(iter(tmp_components))
                    installed = self.component_manager.install(
                        component_type, component_version
                    )
                    if not installed.ok:
                        return False
                    component["available"] = [component_version]
                except StopIteration:
                    return False
            else:
                return False

        try:
            return sort_by_version(component["available"])
        except ValueError:
            return sorted(component["available"], reverse=True)

    def get_programs(
        self, config: CapsuleConfig, force_update: bool = False
    ) -> List[dict]:
        """
        Get the list of programs (both from the drive and the user defined
        in the capsule configuration file).
        """
        if config is None:
            return []

        cache_key = config.Name
        if not force_update and cache_key in self._programs_cache:
            return self._programs_cache[cache_key]

        capsule = ManagerUtils.get_capsule_path(config)
        winepath = WinePath(config)
        results = glob(f"{capsule}/drive_c/users/*/Desktop/*.lnk", recursive=True)
        results += glob(
            f"{capsule}/drive_c/users/*/Start Menu/Programs/**/*.lnk", recursive=True
        )
        results += glob(
            f"{capsule}/drive_c/ProgramData/Microsoft/Windows/Start Menu/Programs/**/*.lnk",
            recursive=True,
        )
        results += glob(
            f"{capsule}/drive_c/users/*/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/**/*.lnk",
            recursive=True,
        )
        installed_programs = []
        ignored_patterns = [
            "*installer*",
            "*unins*",
            "*setup*",
            "*debug*",
            "*report*",
            "*crash*",
            "*err*",
            "_*",
            "start",
            "OriginEr",
            "*website*",
            "*web site*",
            "*user_manual*",
        ]
        found = []
        ext_programs = config.External_Programs

        """
        Process External_Programs
        """
        for _, _program in ext_programs.items():
            found.append(_program["executable"])
            if winepath.is_windows(_program["path"]):
                program_folder = ManagerUtils.get_exe_parent_dir(
                    config, _program["path"]
                )
            else:
                program_folder = os.path.dirname(_program["path"])
            installed_programs.append(
                {
                    "executable": _program.get("executable"),
                    "arguments": _program.get("arguments"),
                    "arguments_enabled": _program.get("arguments_enabled", True),
                    "environment": _program.get("environment"),
                    "name": _program.get("name"),
                    "path": _program.get("path"),
                    "icon": "io.github.runexe.RunExe-program",
                    "pre_script": _program.get("pre_script"),
                    "pre_script_args": _program.get("pre_script_args"),
                    "post_script": _program.get("post_script"),
                    "post_script_args": _program.get("post_script_args"),
                    "folder": _program.get("folder", program_folder),
                    "dxvk": _program.get("dxvk"),
                    "vkd3d": _program.get("vkd3d"),
                    "discrete_gpu": _program.get("discrete_gpu"),
                    "sync": _program.get("sync"),
                    "pulseaudio_latency": _program.get("pulseaudio_latency"),
                    "virtual_desktop": _program.get("virtual_desktop"),
                    "winebridge": _program.get("winebridge"),
                    "hide_console": _program.get("hide_console"),
                    "autostart": _program.get("autostart", False),
                    "file_extensions": _program.get("file_extensions", []),
                    "automatic_backup": _program.get("automatic_backup"),
                    "removed": _program.get("removed"),
                    "id": _program.get("id"),
                }
            )

        for program in results:
            """
            for each .lnk file, try to get the executable path and
            append it to the installed_programs list with its icon,
            skip if the path contains the "Uninstall" word.
            """
            executable_path = LnkUtils.get_data(program)
            if executable_path in [None, ""]:
                continue
            executable_name = executable_path.split("\\")[-1]
            program_folder = ManagerUtils.get_exe_parent_dir(config, executable_path)
            stop = False

            for pattern in ignored_patterns:
                try:
                    if fnmatch.fnmatch(executable_name.lower(), pattern):
                        stop = True
                        break
                except:  # safe to ignore
                    pass
            if stop:
                continue

            path_check = os.path.join(
                capsule, executable_path.replace("C:\\", "drive_c\\").replace("\\", "/")
            )
            if os.path.exists(path_check):
                if executable_name not in found:
                    installed_programs.append(
                        {
                            "executable": executable_name,
                            "arguments": "",
                            "name": executable_name.rsplit(".", 1)[0],
                            "path": executable_path,
                            "folder": program_folder,
                            "icon": "io.github.runexe.RunExe-program",
                            "id": str(uuid.uuid4()),
                            "auto_discovered": True,
                        }
                    )
                    found.append(executable_name)

        self._programs_cache[cache_key] = installed_programs
        return installed_programs

    def check_capsules(self, silent: bool = False):
        """
        Check for local capsules and update the local_capsules list.
        Will also mark the broken ones if the configuration file is missing
        """
        capsules = os.listdir(Paths.capsules)

        # Empty local capsules
        self.local_capsules = {}
        self._programs_cache = {}

        # custom-path capsules whose location is currently unreachable (e.g. an
        # unmounted drive), so the user can be told instead of them silently
        # vanishing from the list
        unavailable = []

        def process_capsule(capsule):
            _name = capsule
            _capsule = str(os.path.join(Paths.capsules, capsule))
            _placeholder = os.path.join(_capsule, "placeholder.yml")
            _config = os.path.join(_capsule, "capsule.yml")
            _placeholder_target = None

            if os.path.exists(_placeholder):
                with open(_placeholder, "r") as f:
                    try:
                        placeholder_yaml = yaml.load(f)
                        if placeholder_yaml.get("Path"):
                            _placeholder_target = placeholder_yaml.get("Path")
                            _config = os.path.join(_placeholder_target, "capsule.yml")
                        else:
                            raise ValueError("Missing Path in placeholder.yml")
                    except (yaml.YAMLError, ValueError):
                        return

            config_load = CapsuleConfig.load(_config)

            if (
                config_load.status
                and not _placeholder_target
                and config_load.data.Custom_Path
                and os.path.isabs(config_load.data.Path)
            ):
                target = config_load.data.Path
                target_config = os.path.join(target, "capsule.yml")
                if os.path.realpath(target_config) != os.path.realpath(_config):
                    _placeholder_target = target
                    _config = target_config
                    config_load = CapsuleConfig.load(_config)
                    if config_load.status:
                        config_load.data.Path = target
                        config_load.data.Custom_Path = True

            if not config_load.status:
                if _placeholder_target:
                    # the capsule lives on a custom path that is not reachable now
                    unavailable.append(_name)
                return

            config = config_load.data
            session_arguments = config.session_arguments
            run_in_terminal = config.run_in_terminal

            # Clear Run Executable parameters on new session start
            if config.session_arguments:
                config.session_arguments = ""

            if config.run_in_terminal:
                config.run_in_terminal = False

            # Check if the path in the capsule config corresponds to the folder name
            # if not, change the config to reflect the folder name
            # if the folder name is "illegal" across all platforms, rename the folder

            # "universal" platform works for all filesystem/OSes
            sane_name = pathvalidate.sanitize_filepath(_name, platform="universal")
            if config.Custom_Path is False:  # There shouldn't be problems with this
                if config.Path != _name or sane_name != _name:
                    logging.warning(
                        'Illegal capsule folder or mismatch between config "Path" and folder name'
                    )
                    if sane_name != _name:
                        # This hopefully doesn't happen, but it's managed
                        logging.warning(f"Broken path in capsule {_name}, fixing...")
                        shutil.move(
                            _capsule, str(os.path.join(Paths.capsules, sane_name))
                        )
                        # Restart the process capsule function. Normally, can't be recursive!
                        process_capsule(sane_name)
                        return

                    config.Path = sane_name
                    self.update_config(config=config, key="Path", value=sane_name)

            sample = CapsuleConfig()
            miss_keys = sample.keys() - config.keys()
            for key in miss_keys:
                logging.warning(f"Key {key} is missing for capsule {_name}, updating…")
                self.update_config(config=config, key=key, value=sample[key])

            miss_params_keys = sample.Parameters.keys() - config.Parameters.keys()

            for key in miss_params_keys:
                """
                For each missing key in the capsule configuration, set
                it to the default value.
                """
                logging.warning(
                    f"Parameters key {key} is missing for capsule {_name}, updating…"
                )
                self.update_config(
                    config=config,
                    key=key,
                    value=sample.Parameters[key],
                    scope="Parameters",
                )

            system_runners = [
                runner for runner in self.runners_available if runner.startswith("sys-")
            ]
            if (
                config.Runner.startswith("sys-")
                and len(system_runners) == 1
                and config.Runner != system_runners[0]
            ):
                logging.info(
                    f"System Wine changed from {config.Runner} to {system_runners[0]}."
                )
                config.Runner = system_runners[0]
                persisted_config = config.copy()
                persisted_config.session_arguments = session_arguments
                persisted_config.run_in_terminal = run_in_terminal
                persisted_config.dump(_config)

            self.local_capsules[config.Name] = config

            try:
                real_path = ManagerUtils.get_capsule_path(config)
                users_path = os.path.join(real_path, "drive_c", "users")
                if os.path.isdir(users_path) and not os.path.islink(
                    os.path.join(real_path, "drive_c")
                ):
                    if not WineUtils.ensure_user_profile_alias(real_path):
                        logging.warning(
                            f"Could not prepare user profile aliases for capsule {_name}."
                        )
                for p in [
                    os.path.join(real_path, "cache", "dxvk_shader"),
                    os.path.join(real_path, "cache", "gl_shader"),
                    os.path.join(real_path, "cache", "mesa_shader"),
                    os.path.join(real_path, "cache", "vkd3d_shader"),
                ]:
                    if not os.path.exists(p):
                        os.makedirs(p, exist_ok=True)

                for c in os.listdir(real_path):
                    c = str(c)
                    if c.endswith(".dxvk-cache"):
                        # NOTE: the following code tries to create the caching directories
                        #       if one or more already exist, it will fail silently as there
                        #       is no need to create them again.
                        try:
                            shutil.move(
                                os.path.join(real_path, c),
                                os.path.join(real_path, "cache", "dxvk_shader"),
                            )
                        except (shutil.Error, OSError):
                            pass
                    elif "vkd3d-proton.cache" in c:
                        try:
                            shutil.move(
                                os.path.join(real_path, c),
                                os.path.join(real_path, "cache", "vkd3d_shader"),
                            )
                        except (shutil.Error, OSError):
                            pass
                    elif c == "GLCache":
                        try:
                            shutil.move(
                                os.path.join(real_path, c),
                                os.path.join(real_path, "cache", "gl_shader"),
                            )
                        except (shutil.Error, OSError):
                            pass
            except (OSError, PermissionError) as e:
                logging.warning(
                    f"Could not perform maintenance for capsule {_name}: {e}"
                )

        for b in sorted(capsules):
            """
            For each capsule add the path name to the `local_capsules` variable
            and append the config.
            """
            process_capsule(b)

        if len(self.local_capsules) > 0 and not silent:
            logging.info(
                "RunExe found:\n - {0}".format("\n - ".join(self.local_capsules))
            )

        if unavailable and not silent and not self.is_cli:
            logging.warning(
                "Unavailable capsules (offline location):\n - {0}".format(
                    "\n - ".join(unavailable)
                )
            )
            SignalManager.send(
                Signals.GNotification,
                Result(
                    True,
                    Notification(
                        title="RunExe",
                        text=_(
                            "Some capsules are unavailable because their location is "
                            "offline: {0}"
                        ).format(", ".join(unavailable)),
                        image="drive-harddisk-symbolic",
                    ),
                ),
            )

    # Update parameters in capsule config
    def update_config(
        self,
        config: CapsuleConfig,
        key: str,
        value: Any,
        scope: str = "",
        remove: bool = False,
        fallback: bool = False,
    ) -> Result[dict]:
        """
        Update parameters in capsule config. Use the scope argument to
        update the parameters in the specified scope (e.g. Parameters).
        A new key will be created if another already exists and fallback
        is set to True.
        TODO: move to capsule.py (Capsule manager)
        """
        _name = config.Name
        logging.info(f"Setting Key {key}={value} for capsule {_name}…")

        _config = config.copy()
        wineboot = WineBoot(_config)
        wineserver = WineServer(_config)
        capsule_path = ManagerUtils.get_capsule_path(config)

        if key == "sync":
            """
            Workaround <https://github.com/hazem-hamsho/RunExe/issues/916>
            Sync type change requires wineserver restart or wine will fail
            to execute any command.
            """
            wineboot.kill()
            wineserver.wait()

        if scope:
            if remove:
                del config[scope][key]
            elif config[scope].get(key) and fallback:
                config[scope][f"{key}-{uuid.uuid4()}"] = value
            else:
                config[scope][key] = value
        else:
            if remove:
                del config[key]
            elif config.get(key) and fallback:
                config[f"{key}-{uuid.uuid4()}"] = value
            else:
                config[key] = value

        result = config.dump(os.path.join(capsule_path, "capsule.yml"))
        if not result.status:
            return Result(False, data={"config": config}, message=result.message)

        config.Update_Date = str(datetime.now())

        if scope == "External_Programs":
            self._programs_cache.pop(config.Name, None)

        component_keys = {
            "Runner",
            "DXVK",
            "VKD3D",
        }

        if key in component_keys or scope in component_keys:
            RegistryRuleManager.apply_rules(config, trigger="components")

        return Result(status=True, data={"config": config})

    def apply_audio_driver(self, driver: str) -> Result[None]:
        """Apply the configured audio driver override to every capsule."""

        logging.info(f"Applying audio driver '{driver}' to local capsules…")
        try:
            for config in self.local_capsules.values():
                if not isinstance(config, CapsuleConfig):
                    continue
                RegKeys(config).set_audio_driver(driver)
        except ValueError as exc:
            logging.error(str(exc))
            return Result(False, message=str(exc))

        return Result(True)

    def create_capsule_from_config(self, config: CapsuleConfig) -> bool:
        """Create a capsule from a config object."""
        logging.info(f"Creating new {config.Name} capsule from config…")

        config = deepcopy(config)
        sample = CapsuleConfig()
        for key in sample.keys():
            if key not in config.keys():
                config[key] = sample[key]

        if config.Runner not in self.runners_available:
            config.Runner = self.get_latest_runner()

        if config.DXVK not in self.dxvk_available:
            config.DXVK = self.dxvk_available[0] if self.dxvk_available else ""

        if config.VKD3D not in self.vkd3d_available:
            config.VKD3D = self.vkd3d_available[0] if self.vkd3d_available else ""

        result = self.create_capsule(
            name=config.Name,
            environment=config.Environment or "Custom",
            runner=config.Runner,
            dxvk=config.DXVK,
            vkd3d=config.VKD3D,
            versioning=config.Versioning,
            sandbox=config.Parameters.sandbox,
            arch=config.Arch,
            configuration=config,
        )
        if not result.ok:
            return False

        restored = result.data["config"]
        logging.info(f"New capsule from config created: {restored.Path}")
        self.update_capsules(silent=True)
        return True

    def create_capsule(
        self,
        name,
        environment: str,
        path: str = "",
        runner: str = False,
        dxvk: bool = False,
        vkd3d: bool = False,
        versioning: bool = False,
        sandbox: bool = False,
        fn_logger: callable = None,
        arch: str = "win64",
        custom_environment: Optional[str] = None,
        cancel_event: Optional[Event] = None,
        configuration: Optional[CapsuleConfig] = None,
    ) -> Result[dict]:
        """
        Create a new capsule from the given arguments.
        TODO: will be replaced by the CapsuleBuilder class.
        """

        def log_update(message):
            if fn_logger:
                fn_logger(message)

        cancellation_announced = False

        cleanup_config = CapsuleConfig()
        cleanup_config.Name = name
        cleanup_config.Environment = environment.capitalize()
        cleanup_config.Custom_Path = bool(path)

        def is_cancelled() -> bool:
            return cancel_event is not None and cancel_event.is_set()

        def abort_build(message: Optional[str] = None) -> Result[dict]:
            nonlocal cancellation_announced

            if not cancellation_announced:
                log_update(
                    message
                    or _("Cancellation requested. Stopping after the current step…")
                )
                cancellation_announced = True

            return Result(False, data={"config": cleanup_config})

        def check_cancel(message: Optional[str] = None) -> Optional[Result[dict]]:
            if not is_cancelled():
                return None
            return abort_build(message)

        # check for essential components
        check_attempts = 0

        def components_check():
            nonlocal check_attempts

            if check_attempts > 2:
                logging.error("Fail to install components, tried 3 times.", jn=True)
                log_update(_("Fail to install components, tried 3 times."))
                return False

            # Only runner, DXVK and VKD3D are essential.
            if 0 in [
                len(self.runners_available),
                len(self.dxvk_available),
                len(self.vkd3d_available),
            ]:
                logging.error("Missing essential components. Installing…")
                log_update(_("Missing essential components. Installing…"))
                if len(self.runners_available) == 0:
                    self.check_runners()
                if len(self.dxvk_available) == 0:
                    self.check_dxvk()
                if len(self.vkd3d_available) == 0:
                    self.check_vkd3d()
                self.organize_components()
                check_attempts += 1
                return components_check()

            return True

        if not components_check():
            return Result(False)

        cancel_result = check_cancel()
        if cancel_result is not None:
            return cancel_result

        # default components versions if not specified
        if not runner:
            # if no runner is specified, use the first one from available
            runner = self.get_latest_runner()
        runner_name = runner

        if arch == "win32" and not WineUtils.supports_win32_prefix(
            ManagerUtils.get_runner_path(runner)
        ):
            message = _(
                "This runner cannot create a 32-bit prefix. Use a 64-bit capsule "
                "to run 32-bit applications, or select a runner with 32-bit Unix support."
            )
            log_update(message)
            return Result(False, message=message)

        if not dxvk:
            # if no dxvk is specified, use the first one from available
            dxvk = self.dxvk_available[0] if self.dxvk_available else ""
        dxvk_name = dxvk

        if not vkd3d:
            # if no vkd3d is specified, use the first one from available
            vkd3d = self.vkd3d_available[0] if self.vkd3d_available else ""
        vkd3d_name = vkd3d

        # define capsule parameters
        capsule_name = name
        capsule_name_path = capsule_name.replace(" ", "-")
        capsule_name_path = pathvalidate.sanitize_filename(
            capsule_name_path, platform="universal"
        )

        # get capsule path
        uses_default_path = not path or os.path.realpath(path) == os.path.realpath(
            Paths.capsules
        )
        if uses_default_path:
            # if no path is specified, use the name as path
            capsule_custom_path = False
            capsule_complete_path = os.path.join(Paths.capsules, capsule_name_path)
            cleanup_config.Path = capsule_name_path
        else:
            capsule_custom_path = True
            capsule_complete_path = os.path.join(path, capsule_name_path)
            cleanup_config.Path = capsule_complete_path

        cleanup_config.Custom_Path = capsule_custom_path

        cancel_result = check_cancel()
        if cancel_result is not None:
            return cancel_result

        # if another capsule with same path exists, append a random number
        if os.path.exists(capsule_complete_path):
            """
            if capsule path already exists, create a new one
            using the name and a random number.
            """
            rnd = random.randint(100, 200)
            capsule_name = f"{capsule_name}__{rnd}"
            capsule_name_path = f"{capsule_name_path}__{rnd}"
            capsule_complete_path = f"{capsule_complete_path}__{rnd}"
            cleanup_config.Name = capsule_name

            if capsule_custom_path:
                cleanup_config.Path = capsule_complete_path
            else:
                cleanup_config.Path = capsule_name_path

        # define registers that should be awaited
        reg_files = [
            os.path.join(capsule_complete_path, "system.reg"),
            os.path.join(capsule_complete_path, "user.reg"),
        ]
        prefix_files = reg_files + [
            os.path.join(
                capsule_complete_path,
                "drive_c",
                "windows",
                "system32",
                "kernel32.dll",
            )
        ]

        # create the capsule directory
        try:
            os.makedirs(capsule_complete_path)
            # Pre-create drive_c directory and set the case-fold flag
            capsule_drive_c = os.path.join(capsule_complete_path, "drive_c")
            os.makedirs(capsule_drive_c)
            FileUtils.chattr_f(capsule_drive_c)
        except:
            logging.error(
                f"Failed to create capsule directory: {capsule_complete_path}", jn=True
            )
            log_update(_("Failed to create capsule directory."))
            return Result(False)

        cancel_result = check_cancel()
        if cancel_result is not None:
            return cancel_result

        if capsule_custom_path:
            placeholder_dir = os.path.join(Paths.capsules, capsule_name_path)
            try:
                os.makedirs(placeholder_dir)
                with open(os.path.join(placeholder_dir, "placeholder.yml"), "w") as f:
                    placeholder = {"Path": capsule_complete_path}
                    f.write(yaml.dump(placeholder))
            except:
                logging.error(
                    f"Failed to create placeholder directory/file at: {placeholder_dir}",
                    jn=True,
                )
                log_update(_("Failed to create placeholder directory/file."))
                return Result(False)

            cancel_result = check_cancel()
            if cancel_result is not None:
                return cancel_result

        # generate capsule configuration
        logging.info("Generating capsule configuration…")
        log_update(_("Generating capsule configuration…"))
        config = (
            deepcopy(configuration) if configuration is not None else CapsuleConfig()
        )
        config.Name = capsule_name
        config.Arch = arch
        config.Runner = runner_name
        config.DXVK = dxvk_name
        config.VKD3D = vkd3d_name
        config.Path = capsule_name_path
        if path:
            config.Path = capsule_complete_path
        config.Custom_Path = capsule_custom_path
        config.Environment = environment.capitalize()
        config.Creation_Date = str(datetime.now())
        config.Update_Date = str(datetime.now())
        config.Parameters.sandbox = sandbox
        if versioning:
            config.Versioning = True
        if configuration is None:
            config.Limit_System_Environment = True
            config.Inherited_Environment_Variables = (
                Samples.default_inherited_environment.copy()
            )

        cleanup_config = config

        cancel_result = check_cancel()
        if cancel_result is not None:
            return cancel_result

        # get template
        template = (
            None
            if configuration is not None
            else TemplateManager.get_env_template(environment)
        )
        template_updated = False
        if template:
            log_update(_("Template found, applying…"))
            if TemplateManager.unpack_template(template, config):
                config.Installed_Dependencies = template["config"][
                    "Installed_Dependencies"
                ]
                config.Uninstallers = template["config"]["Uninstallers"]
            else:
                template = None
                try:
                    shutil.rmtree(capsule_complete_path)
                    os.makedirs(capsule_drive_c)
                    FileUtils.chattr_f(capsule_drive_c)
                except OSError:
                    logging.error(
                        f"Failed to restore capsule directory: {capsule_complete_path}",
                        jn=True,
                    )
                    log_update(_("Failed to create capsule directory."))
                    return Result(False)

        cancel_result = check_cancel()
        if cancel_result is not None:
            return cancel_result

        if not WineUtils.ensure_user_profile_alias(capsule_complete_path):
            logging.warning(
                "Could not create the shared Wine user profile, "
                "continuing with the existing profiles."
            )
            log_update(_("Could not share the user profile, continuing…"))

        # initialize wineprefix
        reg = Reg(config)
        rk = RegKeys(config)
        wineboot = WineBoot(config)
        wineserver = WineServer(config)

        # execute wineboot on the capsule path
        log_update(_("The Wine config is being updated…"))
        initialized = wineboot.init()
        if not initialized.ok:
            message = _("Failed to initialize the Wine prefix.")
            if initialized.message:
                message = f"{message} {initialized.message}"
            logging.error(message, jn=True)
            log_update(message)
            return Result(False, data={"config": config}, message=message)
        if not FileUtils.wait_for_files(prefix_files, timeout=5):
            logging.error("Wine prefix initialization failed.", jn=True)
            message = _("Failed to initialize the Wine prefix.")
            log_update(message)
            return Result(False, data={"config": config}, message=message)
        log_update(_("Wine config updated!"))

        cancel_result = check_cancel()
        if cancel_result is not None:
            return cancel_result

        if not WineUtils.unlink_user_profile_links(capsule_complete_path):
            logging.error("Could not sandbox the capsule user directory.", jn=True)
            message = _("Failed to sandbox the capsule user directory.")
            log_update(message)
            created_paths = [capsule_complete_path]
            if capsule_custom_path:
                created_paths.append(os.path.join(Paths.capsules, capsule_name_path))
            for created_path in created_paths:
                try:
                    shutil.rmtree(created_path)
                except OSError:
                    logging.error(
                        f"Failed to remove unsafe capsule directory: {created_path}",
                        jn=True,
                    )
            return Result(False, data={"config": config}, message=message)

        cancel_result = check_cancel()
        if cancel_result is not None:
            return cancel_result

        # wait for registry files to be created
        FileUtils.wait_for_files(reg_files)

        cancel_result = check_cancel()
        if cancel_result is not None:
            return cancel_result

        # apply Windows version
        if not template and not custom_environment:
            cancel_result = check_cancel()
            if cancel_result is not None:
                return cancel_result

            logging.info("Setting Windows version…")
            log_update(_("Setting Windows version…"))
            if config.Windows != "win10" or (
                "soda" not in runner_name.lower() and "caffe" not in runner_name.lower()
            ):
                try:
                    rk.lg_set_windows(config.Windows)
                except ValueError as e:
                    logging.warning(str(e))
                wineboot.update()

            FileUtils.wait_for_files(reg_files)

            # apply CMD settings
            logging.info("Setting CMD default settings…")
            log_update(_("Apply CMD default settings…"))
            rk.apply_cmd_settings()
            wineboot.update()

            FileUtils.wait_for_files(reg_files)

            logging.info("Enabling font smoothing…")
            log_update(_("Enabling font smoothing…"))
            rk.apply_font_smoothing()

            audio_driver = self.settings.get_string("audio-driver")
            if audio_driver not in ("", "default"):
                logging.info("Configuring audio driver…")
                log_update(_("Configuring audio driver…"))
                try:
                    rk.set_audio_driver(audio_driver)
                except ValueError as exc:
                    logging.warning(str(exc))

            wineboot.update()

            FileUtils.wait_for_files(reg_files)

            # blacklisting processes
            logging.info("Optimizing environment…")
            log_update(_("Optimizing environment…"))
            _blacklist_dll = ["winemenubuilder.exe"]
            for _dll in _blacklist_dll:
                reg.add(
                    key="HKEY_CURRENT_USER\\Software\\Wine\\DllOverrides",
                    value=_dll,
                    data="",
                )

        if configuration is not None:
            parameters = config.Parameters
            rk.toggle_virtual_desktop(
                parameters.virtual_desktop,
                parameters.virtual_desktop_res,
            )
            rk.toggle_wayland_driver(parameters.wayland)
            rk.set_renderer(parameters.renderer)
            rk.set_dpi(parameters.custom_dpi)
            if parameters.dpi_scaling != "default":
                result = rk.set_dpi_scaling(parameters.dpi_scaling)
                if not result.ok:
                    raise ValueError(result.message)
            rk.set_grab_fullscreen(parameters.fullscreen_capture)
            rk.set_take_focus(parameters.take_focus)
            rk.set_decorated(parameters.decorated)
            rk.set_mouse_warp(int(parameters.mouse_warp))
            wineboot.update()

        # apply environment configuration
        logging.info(f"Applying environment: [{environment}]…")
        log_update(_("Applying environment: {0}…").format(environment))
        env = None

        cancel_result = check_cancel()
        if cancel_result is not None:
            return cancel_result

        if configuration is not None:
            env = {
                "Parameters": {},
                "Installed_Dependencies": config.Installed_Dependencies,
            }
        elif environment.lower() not in ["custom"]:
            env = Samples.environments[environment.lower()]
        elif custom_environment:
            try:
                with open(custom_environment, "r", encoding="utf-8") as f:
                    env = yaml.load(f.read())
                    logging.warning("Using a custom environment recipe…")
                    log_update(_("(!) Using a custom environment recipe…"))
            except (
                FileNotFoundError,
                PermissionError,
                UnicodeError,
                yaml.YAMLError,
            ):
                logging.error("Recipe not not found or not valid…")
                log_update(_("(!) Recipe not not found or not valid…"))
                return Result(False)

            wineboot.kill()

        if self.settings.get_boolean("disable-home-drive"):
            Drives(config).remove_drive("Z")

        if env:
            while wineserver.is_alive():
                cancel_result = check_cancel()
                if cancel_result is not None:
                    return cancel_result
                time.sleep(1)

            for prm in config.Parameters:
                if prm in env.get("Parameters", {}):
                    config.Parameters[prm] = env["Parameters"][prm]

            cancel_result = check_cancel()
            if cancel_result is not None:
                return cancel_result

            if (not template and config.Parameters.dxvk) or (
                template and template["config"]["DXVK"] != dxvk
            ):
                cancel_result = check_cancel()
                if cancel_result is not None:
                    return cancel_result

                # perform dxvk installation if configured
                logging.info("Installing DXVK…")
                log_update(_("Installing DXVK…"))
                self.install_dll_component(config, "dxvk", version=dxvk_name)
                template_updated = True

            if (
                not template
                and config.Parameters.vkd3d
                or (template and template["config"]["VKD3D"] != vkd3d)
            ):
                cancel_result = check_cancel()
                if cancel_result is not None:
                    return cancel_result

                # perform vkd3d installation if configured
                logging.info("Installing VKD3D…")
                log_update(_("Installing VKD3D…"))
                self.install_dll_component(config, "vkd3d", version=vkd3d_name)
                template_updated = True

            for dep in env.get("Installed_Dependencies", []):
                if template and dep in template["config"]["Installed_Dependencies"]:
                    continue
                if dep in self.supported_dependencies:
                    cancel_result = check_cancel()
                    if cancel_result is not None:
                        return cancel_result

                    _dep = self.supported_dependencies[dep]
                    log_update(
                        _("Installing dependency: %s …")
                        % _dep.get("Description", "n/a")
                    )
                    res = self.dependency_manager.install(config, [dep, _dep])
                    if not res.ok:
                        logging.error(
                            _("Failed to install dependency: %s")
                            % _dep.get("Description", "n/a"),
                            jn=True,
                        )
                        log_update(
                            _("Failed to install dependency: %s")
                            % _dep.get("Description", "n/a")
                        )
                        if res.message:
                            log_update(res.message)
                        return res
                    template_updated = True

        # save capsule config
        cancel_result = check_cancel()
        if cancel_result is not None:
            return cancel_result

        config.dump(f"{capsule_complete_path}/capsule.yml")

        if versioning:
            # create first state if versioning enabled
            cancel_result = check_cancel()
            if cancel_result is not None:
                return cancel_result

            logging.info("Creating versioning state 0…")
            log_update(_("Creating versioning state 0…"))
            self.versioning_manager.create_state(config=config, message="First boot")

        # set status created and UI usability
        logging.info(f"New capsule created: {capsule_name}", jn=True)
        log_update(_("Finalizing…"))

        # wait for all registry changes to be applied
        FileUtils.wait_for_files(reg_files)

        cancel_result = check_cancel()
        if cancel_result is not None:
            return cancel_result

        # perform wineboot
        wineboot.update()

        cancel_result = check_cancel()
        if cancel_result is not None:
            return cancel_result

        if self.settings.get_boolean("disable-home-drive"):
            Drives(config).remove_drive("Z")

        # caching template
        if configuration is None and (not template or template_updated):
            logging.info("Caching template…")
            log_update(_("Caching template…"))
            TemplateManager.new(environment, config)

        return Result(status=True, data={"config": config})

    @staticmethod
    def __sort_runners(runner_list: list, prefix: str) -> sorted:
        """
        Return a sorted list of runners for a given prefix. Fallback to the
        first available if fallback argument is True.
        """
        runners = [runner for runner in runner_list if runner.startswith(prefix)]

        try:
            runners = sort_by_version(runners, "")
        except ValueError:
            runners = sorted(runners, key=lambda x: x.split("-")[1], reverse=True)

        return runners

    def get_latest_runner(self, runner_prefix: str = "soda") -> list:
        """Return the latest available runner for a given prefix."""
        runners = self.__sort_runners(self.runners_available, runner_prefix)
        if not runners:
            runners = self.__sort_runners(self.runners_available, "")
        return runners[0] if runners else []

    # Config version key for each DLL component that supports upgrades.
    __dll_component_keys = {
        "dxvk": "DXVK",
        "vkd3d": "VKD3D",
    }

    def get_component_updates(self, config: CapsuleConfig) -> list:
        """
        Return the components of a capsule that can be upgraded to a newer
        version available in the online catalog. Each entry is a dict with
        id, title, current, latest and (for runners) component_type.
        """
        updates = []

        if not self.settings.get_boolean("show-component-updates"):
            return updates

        runner_update = self.__collect_runner_update(config)
        if runner_update:
            updates.append(runner_update)

        component_meta = {
            "dxvk": {
                "title": _("DXVK"),
                "enabled": config.Parameters.dxvk,
                "current": config.DXVK,
                "supported": self.supported_dxvk,
            },
            "vkd3d": {
                "title": _("VKD3D"),
                "enabled": config.Parameters.vkd3d,
                "current": config.VKD3D,
                "supported": self.supported_vkd3d,
            },
        }

        for component, meta in component_meta.items():
            if not meta["enabled"] or not meta["current"] or not meta["supported"]:
                continue
            latest = self.__get_latest_supported(meta["supported"])
            if not latest or not self.__is_version_newer(latest, meta["current"]):
                continue
            updates.append(
                {
                    "id": component,
                    "title": meta["title"],
                    "current": meta["current"],
                    "latest": latest,
                }
            )

        winebridge_update = self.__collect_winebridge_update(config)
        if winebridge_update:
            updates.append(winebridge_update)

        return updates

    def apply_component_update(self, config: CapsuleConfig, update: dict) -> Result:
        """Apply a single update entry returned by get_component_updates,
        downloading the target version first if it is not installed yet."""
        component = update["id"]

        if component == "runner":
            return self.__update_runner_component(
                config, update["latest"], update["component_type"]
            )
        if component == "winebridge":
            return self.__update_winebridge_component(config, update["latest"])
        return self.__update_dll_component(config, component, update["latest"])

    def __collect_runner_update(self, config: CapsuleConfig) -> Optional[dict]:
        runner = config.Runner or ""
        if not runner or runner.startswith("sys-"):
            return None

        latest, component_type = self.__latest_runner_for(runner)
        if not latest:
            return None

        return {
            "id": "runner",
            "title": _("Runner"),
            "current": runner,
            "latest": latest,
            "component_type": component_type,
        }

    def __collect_winebridge_update(self, config: CapsuleConfig) -> Optional[dict]:
        if not config.Parameters.winebridge:
            return None

        latest = self.__get_latest_supported(self.supported_winebridge)
        installed = self.winebridge_available[0] if self.winebridge_available else None
        if not latest or not self.__is_version_newer(latest, installed):
            return None

        return {
            "id": "winebridge",
            "title": _("WineBridge"),
            "current": installed or _("Not installed"),
            "latest": latest,
        }

    def __latest_runner_for(self, runner: str):
        """Return (latest_runner_name, component_type) for the newest catalog
        runner that shares the exact family/variant of the given runner, or
        (None, "") if none is newer. Matching is done on a version-stripped
        identity so different variants (e.g. kron4ek wine vs staging vs proton)
        never get mixed up, and versions are compared as integer tuples so
        10.20 ranks above 10.0."""
        family, version = self.__runner_identity(runner)

        for catalog, component_type in (
            (self.supported_wine_runners, "runner"),
            (self.supported_proton_runners, "runner:proton"),
        ):
            catalog = self.__filter_update_catalog(catalog)
            if not catalog:
                continue
            same_family = [
                (name, self.__runner_identity(name)[1])
                for name in catalog.keys()
                if self.__runner_identity(name)[0] == family
            ]
            if not same_family:
                continue
            # the runner belongs to this catalog; pick the newest same-family
            # version, only if it is strictly newer than the current one
            best_name, best_version = None, version
            for name, candidate_version in same_family:
                if candidate_version > best_version:
                    best_name, best_version = name, candidate_version
            return best_name, (component_type if best_name else "")

        return None, ""

    @staticmethod
    def __runner_identity(name: str):
        """Split a runner name into (family, version_tuple). The version is the
        first dotted/dashed number group (e.g. 10.20, 8-26); the family is the
        name with that version removed, so it keeps any variant suffix."""
        normalized = (name or "").lower()
        match = re.search(r"\d+(?:[.-]\d+)+", normalized)
        if not match:
            return normalized, ()
        version = tuple(int(part) for part in re.split(r"[.-]", match.group(0)))
        family = normalized[: match.start()] + normalized[match.end() :]
        return family, version

    def __filter_update_catalog(self, catalog: dict) -> dict:
        if self.settings.get_boolean("release-candidate"):
            return catalog
        return {
            name: component
            for name, component in catalog.items()
            if component.get("Channel") not in ("rc", "unstable")
        }

    def __get_latest_supported(self, supported_dict: dict) -> Optional[str]:
        supported_dict = self.__filter_update_catalog(supported_dict)
        if not supported_dict:
            return None
        keys = list(supported_dict.keys())
        try:
            return sort_by_version(keys)[0]
        except ValueError:
            return sorted(keys, reverse=True)[0]

    @staticmethod
    def __is_version_newer(latest: str, current: Optional[str]) -> bool:
        if not latest:
            return False
        if not current:
            return True
        versions = [latest, current]
        try:
            ordered = sort_by_version(versions.copy())
        except ValueError:
            ordered = sorted(versions, reverse=True)
        return ordered[0] == latest and latest != current

    def __ensure_component_available(self, component: str, version: str) -> Result:
        availability_attrs = {
            "dxvk": "dxvk_available",
            "vkd3d": "vkd3d_available",
        }
        available = getattr(self, availability_attrs[component], [])
        if version in available:
            return Result(True)
        return self.component_manager.install(component, version)

    def __update_dll_component(
        self, config: CapsuleConfig, component: str, version: str
    ) -> Result:
        ensure = self.__ensure_component_available(component, version)
        if not ensure.ok:
            return ensure

        remove_res = self.install_dll_component(
            config=config, component=component, remove=True
        )
        if not remove_res.ok:
            return remove_res

        update_res = self.update_config(
            config=config, key=self.__dll_component_keys[component], value=version
        )
        if not update_res.ok:
            return update_res
        updated_config = update_res.data["config"]

        install_res = self.install_dll_component(
            config=updated_config, component=component, version=version
        )
        if not install_res.ok:
            return install_res

        return Result(True, data={"config": updated_config})

    def set_graphics_component(
        self,
        config: CapsuleConfig,
        component: str,
        enabled: bool,
        version: str | None = None,
    ) -> Result:
        if component not in ("dxvk", "vkd3d"):
            return Result(False, message=_("Invalid graphics component."))

        previous = deepcopy(config)
        candidate = deepcopy(config)
        selected = version or getattr(config, component.upper())
        if enabled and not selected:
            available = getattr(self, f"{component}_available")
            selected = available[0] if available else ""
        if enabled and not selected:
            return Result(False, message=_("No graphics component version available."))
        if enabled:
            setattr(candidate, component.upper(), selected)
        setattr(candidate.Parameters, component, enabled)
        components = [component]
        if component == "dxvk" and not enabled and previous.Parameters.vkd3d:
            candidate.Parameters.vkd3d = False
            components.insert(0, "vkd3d")
        elif component == "vkd3d" and enabled and not previous.Parameters.dxvk:
            candidate.DXVK = previous.DXVK or (
                self.dxvk_available[0] if self.dxvk_available else ""
            )
            if not candidate.DXVK:
                return Result(False, message=_("No DXVK version available."))
            candidate.Parameters.dxvk = True
            components.insert(0, "dxvk")

        changed = []

        def rollback(failure):
            restored = True
            for name in reversed(changed):
                result = self.install_dll_component(
                    previous,
                    name,
                    remove=not getattr(previous.Parameters, name),
                    version=getattr(previous, name.upper()),
                )
                restored = result.ok and restored
            if not restored:
                return Result(
                    False, message=_("Failed to restore graphics components.")
                )
            return failure

        for name in components:
            changed.append(name)
            result = self.install_dll_component(
                candidate,
                name,
                remove=not getattr(candidate.Parameters, name),
                version=getattr(candidate, name.upper()),
            )
            if not result.ok:
                return rollback(result)

        current = deepcopy(config)
        for name in components:
            setattr(current, name.upper(), getattr(candidate, name.upper()))
            setattr(current.Parameters, name, getattr(candidate.Parameters, name))
        candidate = current
        candidate.Update_Date = str(datetime.now().astimezone().replace(tzinfo=None))
        capsule_path = ManagerUtils.get_capsule_path(candidate)
        result = candidate.dump(os.path.join(capsule_path, "capsule.yml"))
        if not result.ok:
            return rollback(result)

        config.DXVK = candidate.DXVK
        config.VKD3D = candidate.VKD3D
        config.Parameters.dxvk = candidate.Parameters.dxvk
        config.Parameters.vkd3d = candidate.Parameters.vkd3d
        config.Update_Date = candidate.Update_Date
        if config.Name in self.local_capsules:
            self.local_capsules[config.Name] = config
        RegistryRuleManager.apply_rules(config, trigger="components")
        return Result(True, data={"config": config})

    def __update_runner_component(
        self, config: CapsuleConfig, runner: str, component_type: str
    ) -> Result:
        from runexe.backend.runner import Runner

        if runner not in self.runners_available:
            res = self.component_manager.install(component_type, runner)
            if not res.ok:
                return res
        return Runner.runner_update(config=config, manager=self, runner=runner)

    def __update_winebridge_component(
        self, config: CapsuleConfig, version: str
    ) -> Result:
        if version in self.winebridge_available:
            return Result(True)
        return self.component_manager.install("winebridge", version)

    def delete_capsule(self, config: CapsuleConfig) -> bool:
        """
        Perform wineserver shutdown and delete the capsule.
        TODO: will be replaced by the RunExeManager class.
        """
        logging.info("Stopping capsule…")
        wineserver = WineServer(config)

        if config.Parameters.sandbox:
            wineserver.force_kill()
        else:
            WineBoot(config).kill(True)
            wineserver.wait()

        if not config.Path:
            logging.error("Empty path found. Disasters unavoidable.")
            return False

        logging.info("Removing applications installed with the capsule…")
        for program in config.External_Programs.values():
            ManagerUtils.remove_desktop_entry(config, program)

        for inst in glob(f"{Paths.applications}/{config.Name}--*"):
            os.remove(inst)

        logging.info("Removing library entries associated with this capsule…")
        library_manager = LibraryManager()
        library_manager.remove_capsule_entries(config.Name)

        if config.Custom_Path:
            logging.info("Removing placeholder…")
            placeholder_path = os.path.join(
                Paths.capsules, os.path.basename(config.Path)
            )
            if not FileUtils.remove_path(placeholder_path):
                logging.debug(f"Failed to remove placeholder at {placeholder_path}")

        logging.info("Removing the capsule…")
        path = ManagerUtils.get_capsule_path(config)
        subprocess.run(["rm", "-rf", path], stdout=subprocess.DEVNULL)

        # The UI invokes delete_capsule in a worker thread. Refresh only the
        # backend state here; its RunAsync callback rebuilds the GTK list on
        # the main loop.
        self.check_capsules(silent=True)

        logging.info(f"Deleted the capsule in: {path}")
        return True

    def repair_capsule(self, config: CapsuleConfig) -> bool:
        """
        This function tries to repair a broken capsule, creating a
        new capsule configuration with the latest runner. Each fixed
        capsule will use the Custom environment.
        TODO: will be replaced by the RunExeManager class.
        """
        logging.info(f"Trying to repair the capsule: [{config.Name}]…")

        wineboot = WineBoot(config)
        capsule_path = f"{Paths.capsules}/{config.Name}"

        # create new config with path as name and Custom environment
        new_config = CapsuleConfig()
        new_config.Name = config.Name
        new_config.Runner = self.get_latest_runner()
        new_config.Path = config.Name
        new_config.Environment = "Custom"
        new_config.Creation_Date = str(datetime.now())
        new_config.Update_Date = str(datetime.now())

        saved = new_config.dump(os.path.join(capsule_path, "capsule.yml"))
        if not saved.status:
            return False

        # Execute wineboot in capsule to generate missing files
        wineboot.init()

        # Update capsules
        self.update_capsules()
        return True

    def install_dll_component(
        self,
        config: CapsuleConfig,
        component: str,
        remove: bool = False,
        version: str = False,
        overrides_only: bool = False,
        exclude: list = None,
    ) -> Result:
        if exclude is None:
            exclude = []

        # DXVK and VKD3D require Vulkan to be present on the host.
        if not remove and component in ("dxvk", "vkd3d"):
            from runexe.backend.utils.vulkan import VulkanUtils

            if not VulkanUtils.check_support():
                logging.warning(
                    f"Skipping {component} installation: Vulkan is not available on this system."
                )
                return Result(
                    status=False,
                    message=_(
                        f"{component.upper()} requires Vulkan, which is not available on this system."
                    ),
                )

        if component == "dxvk":
            _version = (
                version
                or config.DXVK
                or (self.dxvk_available[0] if self.dxvk_available else "")
            )
            if not _version:
                return Result(status=False, message=_("No DXVK version available."))
            manager = DXVKComponent(_version)
        elif component == "vkd3d":
            _version = (
                version
                or config.VKD3D
                or (self.vkd3d_available[0] if self.vkd3d_available else "")
            )
            if not _version:
                return Result(status=False, message=_("No VKD3D version available."))
            manager = VKD3DComponent(_version)
        else:
            return Result(
                status=False, data={"message": f"Invalid component: {component}"}
            )

        if (
            not remove
            and component in ("dxvk", "vkd3d")
            and not manager.checked_dlls
        ):
            return Result(
                status=False,
                message=_("The selected {0} installation is incomplete.").format(
                    component.upper()
                ),
            )

        if remove:
            success = manager.uninstall(config, exclude)
        else:
            success = manager.install(config, overrides_only, exclude)

        if not success:
            message = (
                _("Failed to remove {0}.") if remove else _("Failed to install {0}.")
            )
            return Result(
                status=False,
                message=message.format(component.upper()),
            )
        return Result(status=True)

    def shutdown(self):
        """Cleanup only truly orphaned or stuck capsules."""
        logging.info("Starting shutdown cleanup …")

        active_capsule_ids = []
        if hasattr(self, "playtime_tracker"):
            with self.playtime_tracker._lock:
                active_capsule_ids = [
                    s.capsule_id for s in self.playtime_tracker._tracked.values()
                ]
            self.playtime_tracker.shutdown()

        protected_prefixes = set()
        for b_id in active_capsule_ids:
            if b_id in self.local_capsules:
                protected_prefixes.add(
                    ManagerUtils.get_capsule_path(self.local_capsules[b_id])
                )

        from runexe.backend.utils.proc import ProcUtils

        procs = ProcUtils.get_procs()
        prefix_to_procs = {}

        for proc in procs:
            env = proc.get_env()
            if "WINEPREFIX=" in env:
                for var in env.split("\x00"):
                    if var.startswith("WINEPREFIX="):
                        prefix = var.split("=", 1)[1]
                        if prefix not in prefix_to_procs:
                            prefix_to_procs[prefix] = []
                        prefix_to_procs[prefix].append(proc)

        for prefix, p_list in prefix_to_procs.items():
            if prefix in protected_prefixes:
                continue

            for p in p_list:
                name = p.get_name().lower()
                state = p.get_state()

                if name != "wineserver" and state != "Z":
                    logging.info(
                        f"Prefix {prefix} is active (e.g. {name}), protecting."
                    )
                    protected_prefixes.add(prefix)
                    break

        for prefix, p_list in prefix_to_procs.items():
            if prefix in protected_prefixes:
                continue

            logging.info(f"Cleaning up orphaned/stuck capsule at {prefix} …")
            for proc in p_list:
                proc.kill(9)
