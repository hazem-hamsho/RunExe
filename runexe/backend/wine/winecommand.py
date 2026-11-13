import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
from typing import Iterable, Optional

from runexe.backend.globals import (
    Paths,
    is_cpak,
)
from runexe.backend.identity import start_identity_bridge
from runexe.backend.logger import Logger
from runexe.backend.managers.runtime import RuntimeManager
from runexe.backend.managers.sandbox import SandboxManager
from runexe.backend.models.config import CapsuleConfig
from runexe.backend.models.result import Result
from runexe.backend.utils.display import DisplayUtils
from runexe.backend.utils.generic import (
    detect_encoding,
    get_host_architecture,
    is_ntsync_available,
)
from runexe.backend.utils.gpu import GPUUtils
from runexe.backend.utils.hidraw import normalize_hidraw_id
from runexe.backend.utils.manager import ManagerUtils
from runexe.backend.utils.launch_options import handle_launch_options
from runexe.backend.utils.terminal import TerminalUtils

logging = Logger()


def _is_enabled_value(value) -> bool:
    return value not in (None, "", "0", False)


class WineEnv:
    """
    This class is used to store and return a command environment.
    """

    __env: dict = {}
    __result: dict = {"envs": {}, "overrides": []}

    def __init__(self, clean: bool = False, allowed_keys: Optional[Iterable[str]] = None):
        self.__env = {}
        if clean:
            return

        if allowed_keys is None:
            self.__env = os.environ.copy()
            return

        for key in allowed_keys:
            if not isinstance(key, str):
                continue
            if key in os.environ:
                self.__env[key] = os.environ[key]

    def add(self, key, value, override=False):
        if key in self.__env:
            if override:
                self.__result["overrides"].append(f"{key}={value}")
            else:
                return
        self.__env[key] = value

    def add_bundle(self, bundle, override=False):
        for key, value in bundle.items():
            self.add(key, value, override)

    def get(self):
        result = self.__result
        result["count_envs"] = len(result["envs"])
        result["count_overrides"] = len(result["overrides"])
        result["envs"] = self.__env
        return result

    def remove(self, key):
        if key in self.__env:
            del self.__env[key]

    def is_empty(self, key):
        return len(self.__env.get(key, "").strip()) == 0

    def concat(self, key, values, sep=":"):
        if isinstance(values, str):
            values = [values]
        values = sep.join(values)

        if self.has(key):
            values = self.__env[key] + sep + values
        self.add(key, values, True)

    def has(self, key):
        return key in self.__env

    def get_value(self, key):
        return self.__env.get(key)

    def is_enabled(self, key):
        return _is_enabled_value(self.__env.get(key))


def _proton_option_enabled(get_value, option: str) -> bool:
    for key in (f"PROTON_USE_{option}", f"PROTON_ENABLE_{option}"):
        value = get_value(key)
        if value is not None:
            return _is_enabled_value(value)
    return False


def _wayland_requested(env: "WineEnv", params) -> bool:
    return getattr(params, "wayland", False) or _proton_option_enabled(
        env.get_value, "WAYLAND"
    )


def _wayland_available(env: "WineEnv") -> bool:
    return DisplayUtils.display_server_type() == "wayland" and bool(
        env.has("WAYLAND_DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )


def apply_wayland_preferences(
    env: "WineEnv", params, xlocale_dir: Optional[str] = None
) -> None:
    proton_wayland_enabled = _proton_option_enabled(env.get_value, "WAYLAND")
    if not _wayland_requested(env, params):
        return
    if DisplayUtils.display_server_type() != "wayland":
        return
    wayland_display = os.environ.get("WAYLAND_DISPLAY")
    if not env.has("WAYLAND_DISPLAY") and wayland_display:
        env.add("WAYLAND_DISPLAY", wayland_display, override=True)
    if env.has("WAYLAND_DISPLAY") or wayland_display:
        env.add("WINE_USE_EGL", "1", override=True)
        if proton_wayland_enabled:
            if env.has("PROTON_WAYLAND_MONITOR"):
                env.add(
                    "WAYLANDDRV_PRIMARY_MONITOR",
                    env.get_value("PROTON_WAYLAND_MONITOR"),
                    override=True,
                )
            env.concat(
                "WINEDLLOVERRIDES",
                ["winex11.drv=d", "winewayland.drv=b"],
                sep=";",
            )
            env.add("WINE_DISABLE_FULLSCREEN_HACK", "1", override=True)
            env.add("WINE_MOVE_HACK", "1")
            env.add("PROTON_USE_XALIA", "0", override=True)
            env.remove("SDL_GAMECONTROLLER_IGNORE_DEVICES")
            if xlocale_dir:
                env.add("XLOCALEDIR", xlocale_dir)
        env.remove("DISPLAY")


def apply_hidraw_preferences(env: "WineEnv", params) -> None:
    selected = []
    for value in getattr(params, "hidraw_devices", []):
        identifier = normalize_hidraw_id(value)
        if identifier and identifier not in selected:
            selected.append(identifier)

    if selected:
        env.add("PROTON_ENABLE_HIDRAW", ",".join(selected), override=True)


def apply_openxr_preferences(
    env: "WineEnv", runner_name: str, runner_path: str, capsule_path: str
) -> None:
    if not runner_name.lower().startswith("soda-"):
        return

    source = os.path.join(runner_path, "share/openxr/wineopenxr64.json")
    if not os.path.isfile(source):
        return

    target_dir = os.path.join(capsule_path, "drive_c/openxr")
    try:
        capsule_root = os.path.realpath(capsule_path)
        target_parent = os.path.realpath(os.path.dirname(target_dir))
        if os.path.commonpath((capsule_root, target_parent)) != capsule_root:
            raise OSError("OpenXR manifest path escapes the capsule")

        os.makedirs(target_dir, exist_ok=True)
        if os.path.commonpath(
            (capsule_root, os.path.realpath(target_dir))
        ) != capsule_root:
            raise OSError("OpenXR manifest path escapes the capsule")

        with open(source, "rb") as manifest:
            data = manifest.read(4097)
        if len(data) > 4096 or b"wineopenxr.dll" not in data:
            raise OSError("Invalid OpenXR manifest")

        target = os.path.join(target_dir, "wineopenxr64.json")
        if os.path.isfile(target):
            with open(target, "rb") as current:
                if current.read(4097) == data:
                    env.add("SODA_OPENXR_RUNTIME", "host")
                    return

        fd, temporary = tempfile.mkstemp(prefix=".wineopenxr-", dir=target_dir)
        try:
            with os.fdopen(fd, "wb") as manifest:
                manifest.write(data)
                manifest.flush()
                os.fsync(manifest.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
        env.add("SODA_OPENXR_RUNTIME", "host")
    except OSError as error:
        logging.warning(f"Could not prepare Soda OpenXR: {error}")


def apply_fex_preferences(env: "WineEnv", runner_name: str, runner_path: str) -> None:
    if (
        get_host_architecture() != "aarch64"
        or not runner_name.lower().startswith("soda-")
    ):
        return

    config = os.path.join(runner_path, "share/fex-emu/Config.json")
    unixlib = os.path.join(runner_path, "lib/wine/aarch64-unix/libwow64fex.so")
    if not os.path.isfile(config) or not os.path.isfile(unixlib):
        return

    env.add("FEX_APP_CONFIG", config)
    env.add("FEX_APP_CONFIG_LOCATION", os.path.dirname(config))


def apply_identity_bridge(
    env: "WineEnv", runner_name: str, runner_path: str, capsule_path: str
) -> None:
    unixlib = "windows.security.authentication.onlineid.so"
    locations = (
        os.path.join(runner_path, "lib/wine/x86_64-unix", unixlib),
        os.path.join(runner_path, "lib64/wine/x86_64-unix", unixlib),
        os.path.join(runner_path, "lib/wine/i386-unix", unixlib),
        os.path.join(runner_path, "lib/wine/aarch64-unix", unixlib),
        os.path.join(runner_path, "lib64/wine/aarch64-unix", unixlib),
    )
    if not runner_name.lower().startswith("soda-") or not any(
        os.path.isfile(location) for location in locations
    ):
        return
    socket_path = start_identity_bridge(capsule_path)
    if socket_path:
        env.add("SODA_IDENTITY_BRIDGE_SOCKET", socket_path, override=True)


class WineCommand:
    """
    This class is used to run a wine command with a custom environment.
    It also handles the launch in a terminal or not.
    """

    def __init__(
        self,
        config: CapsuleConfig,
        command: str,
        terminal: bool = False,
        arguments: str = "",
        environment: dict = {},
        communicate: bool = False,
        colors: str = "default",
        minimal: bool = False,
        pre_script: Optional[str] = None,
        post_script: Optional[str] = None,
        pre_script_args: Optional[str] = None,
        post_script_args: Optional[str] = None,
        cwd: Optional[str] = None,
        sandbox_override: Optional[str] = None,
        forced_dll_overrides: Optional[str] = None,
        capture_exit_code: bool = False,
        capture_stderr: bool = False,
    ):
        _environment = environment.copy()
        self.config = self._get_config(config)
        self.forced_dll_overrides = forced_dll_overrides
        self.minimal = minimal
        # Per-launch override of the dedicated sandbox decided in the config:
        #   None  -> follow the capsule setting
        #   "off" -> run this launch without the dedicated sandbox
        self.sandbox_override = sandbox_override
        self._exit_code_batch = None
        self._exit_code_marker = None
        if capture_exit_code and communicate:
            command, arguments = self._prepare_exit_code_capture(
                command, arguments or ""
            )
        self.arguments = arguments
        self.cwd = self._get_cwd(cwd)
        self.runner, self.runner_runtime = self._get_runner_info()
        self.command = self.get_cmd(
            command,
            pre_script,
            post_script,
            pre_script_args,
            post_script_args,
            environment=_environment,
        )
        self.terminal = terminal
        self.env = self.get_env(_environment)
        self.communicate = communicate
        self.capture_stderr = capture_stderr and communicate
        self.colors = colors
        self.returncode = None

    def _prepare_exit_code_capture(
        self, command: str, arguments: str
    ) -> tuple[str, str]:
        temp_dir = os.path.join(
            ManagerUtils.get_capsule_path(self.config), "drive_c", "windows", "temp"
        )
        os.makedirs(temp_dir, exist_ok=True)
        batch = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".bat",
            dir=temp_dir,
            delete=False,
            newline="",
        )
        marker = f"RUNEXE_EXIT_CODE_{os.path.basename(batch.name)[:-4]}"
        command_line = f"{command} {arguments}".rstrip()
        with batch:
            batch.write(
                "@echo off\r\n"
                f"{command_line}\r\n"
                'set "RUNEXE_EXIT_CODE=%ERRORLEVEL%"\r\n'
                f"echo {marker}=%RUNEXE_EXIT_CODE%\r\n"
                "exit /b %RUNEXE_EXIT_CODE%\r\n"
            )
        self._exit_code_batch = batch.name
        self._exit_code_marker = marker
        windows_path = f"C:\\windows\\temp\\{os.path.basename(batch.name)}"
        return "cmd.exe", f"/d /c {shlex.quote(windows_path)}"

    def _extract_exit_code(self, output: str, fallback: int) -> tuple[str, int]:
        marker = getattr(self, "_exit_code_marker", None)
        if not marker:
            return output, fallback

        pattern = re.compile(rf"^{re.escape(marker)}=(\d+)\r?$", re.MULTILINE)
        matches = list(pattern.finditer(output))
        if not matches:
            return output, fallback

        status = int(matches[-1].group(1))
        output = pattern.sub("", output).rstrip("\r\n")
        return output, status

    def _clear_exit_code_capture(self) -> None:
        batch = getattr(self, "_exit_code_batch", None)
        if batch:
            try:
                os.unlink(batch)
            except FileNotFoundError:
                pass
            self._exit_code_batch = None

    def _get_config(self, config: CapsuleConfig) -> CapsuleConfig:
        if cnf := config.data.get("config"):
            return cnf

        if not isinstance(config, CapsuleConfig):
            logging.error("Invalid config type: %s" % type(config))
            return CapsuleConfig()

        return config

    def _get_cwd(self, cwd) -> str:
        config = self.config

        capsule = ManagerUtils.get_capsule_path(config)

        if not cwd:
            """
            If no cwd is given, use the WorkingDir from the
            capsule configuration.
            """
            cwd = config.WorkingDir
        if cwd == "" or not os.path.exists(cwd):
            """
            If the WorkingDir is empty, use the capsule path as
            working directory.
            """
            cwd = capsule

        return cwd

    def get_env(
        self,
        environment: Optional[dict] = None,
        return_clean_env: bool = False,
    ) -> dict:
        config = self.config
        clean_env = return_clean_env
        allowed_env_keys: Optional[Iterable[str]] = None
        if not clean_env and getattr(config, "Limit_System_Environment", False):
            allowed_env_keys = config.Inherited_Environment_Variables

        env = WineEnv(clean=clean_env, allowed_keys=allowed_env_keys)
        arch = config.Arch
        params = config.Parameters

        # Capsule Path as environment variable
        env.add("CAPSULE", config.Path)

        if None in [arch, params]:
            return env.get()["envs"]

        if environment is None:
            environment = {}

        capsule = ManagerUtils.get_capsule_path(config)
        runner_path = ManagerUtils.get_runner_path(config.Runner)

        # Clean some env variables which can cause trouble
        # ref: <https://github.com/hazem-hamsho/RunExe/issues/2127>
        # env.remove("XDG_DATA_HOME")

        dll_overrides = []
        gpu = GPUUtils().get_gpu()
        DisplayUtils.check_nvidia_device()
        ld = []

        if not clean_env and self.runner:
            env.add("WINE", shlex.split(self.runner)[0], override=True)

        # Capsule environment variables
        if config.Environment_Variables:
            for key, value in config.Environment_Variables.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    logging.warning(f"Ignoring malformed environment variable {key!r}")
                    continue
                env.add(key, value, override=True)

        # Environment variables from argument
        if environment:
            if environment.get("WINEDLLOVERRIDES"):
                dll_overrides.append(environment["WINEDLLOVERRIDES"])
                del environment["WINEDLLOVERRIDES"]

            if environment.get("DXVK_CONFIG_FILE", "") == "capsule_root":
                environment["DXVK_CONFIG_FILE"] = os.path.join(capsule, "dxvk.conf")

            for e in environment:
                env.add(e, environment[e], override=True)

        apply_openxr_preferences(env, config.Runner, runner_path, capsule)
        apply_identity_bridge(env, config.Runner, runner_path, capsule)
        apply_fex_preferences(env, config.Runner, runner_path)

        # Language
        if config.Language != "sys":
            # ensure an encoding is set (e.g. zh_CN -> zh_CN.UTF-8), otherwise
            # wine renders non-Latin text as garbage
            language = config.Language
            if "." not in language:
                language = f"{language}.UTF-8"
            env.add("LC_ALL", language)

        # Capsule DLL_Overrides
        if config.DLL_Overrides:
            for k, v in config.DLL_Overrides.items():
                dll_overrides.append(f"{k}={v}")

        # Default DLL overrides
        dll_overrides.append("winemenubuilder=''")

        # Get Runtime libraries
        if params.use_runtime and not self.terminal:
            _rb = RuntimeManager.get_runtime_env("capsules")
            if _rb:
                logging.info("Using RunExe runtime")
                ld += _rb
            else:
                logging.warning("RunExe runtime was requested but not found")

        # Get Runner libraries
        if arch == "win64":
            runner_libs = [
                "lib",
                "lib64",
                "lib/wine/x86_64-unix",
                "lib32/wine/x86_64-unix",
                "lib64/wine/x86_64-unix",
                "lib/wine/i386-unix",
                "lib32/wine/i386-unix",
                "lib64/wine/i386-unix",
            ]
            gst_libs = [
                "lib64/gstreamer-1.0",
                "lib/gstreamer-1.0",
                "lib32/gstreamer-1.0",
            ]
        else:
            runner_libs = [
                "lib",
                "lib/wine/i386-unix",
                "lib32/wine/i386-unix",
                "lib64/wine/i386-unix",
            ]
            gst_libs = ["lib/gstreamer-1.0", "lib32/gstreamer-1.0"]

        if get_host_architecture() == "aarch64":
            runner_libs.insert(2, "lib/wine/aarch64-unix")

        if not config.Runner.startswith("sys-"):
            for lib in runner_libs:
                _path = os.path.join(runner_path, lib)
                if os.path.exists(_path):
                    ld.append(_path)

            # Embedded GStreamer environment variables
            if not env.has("RUNEXE_USE_SYSTEM_GSTREAMER"):
                gst_env_path = []
                for lib in gst_libs:
                    if os.path.exists(os.path.join(runner_path, lib)):
                        gst_env_path.append(os.path.join(runner_path, lib))
                if len(gst_env_path) > 0:
                    env.add("GST_PLUGIN_SYSTEM_PATH", ":".join(gst_env_path), override=True)

        # DXVK environment variables
        if params.dxvk:
            env.add("WINE_LARGE_ADDRESS_AWARE", "1")
            env.add(
                "DXVK_SHADER_CACHE_PATH", os.path.join(capsule, "cache", "dxvk_shader")
            )
            env.add("STAGING_SHARED_MEMORY", "1")
            env.add("__GL_SHADER_DISK_CACHE", "1")
            env.add(
                "__GL_SHADER_DISK_CACHE_PATH",
                os.path.join(capsule, "cache", "gl_shader"),
            )
            env.add(
                "MESA_SHADER_CACHE_DIR", os.path.join(capsule, "cache", "mesa_shader")
            )

        # VKD3D environment variables
        if params.vkd3d:
            env.add(
                "VKD3D_SHADER_CACHE_PATH", os.path.join(capsule, "cache", "vkd3d_shader")
            )

        apply_hidraw_preferences(env, params)

        self._apply_sync_environment(env, params.sync, self.runner)

        # Wine debug level
        debug_level = "fixme-all"
        if params.fixme_logs:
            debug_level = "+fixme-all"
        env.add("WINEDEBUG", debug_level)

        # Aco compiler
        # if params["aco_compiler"]:
        #     env.add("ACO_COMPILER", "aco")

        # PulseAudio latency
        if params.pulseaudio_latency:
            env.add("PULSE_LATENCY_MSEC", "60")

        # Discrete GPU
        if params.discrete_gpu:
            discrete = gpu["prime"]["discrete"]
            if discrete is not None:
                gpu_envs = discrete["envs"]
                for p in gpu_envs:
                    env.add(p, gpu_envs[p])
                env.concat("VK_ICD_FILENAMES", discrete["icd"])

        # VK_ICD
        if not env.has("VK_ICD_FILENAMES"):
            if gpu["prime"]["integrated"] is not None:
                """
                System support PRIME but user disabled the discrete GPU
                setting (previus check skipped), so using the integrated one.
                """
                env.concat("VK_ICD_FILENAMES", gpu["prime"]["integrated"]["icd"])
            else:
                """
                System doesn't support PRIME, so using the first result
                from the gpu vendors list.
                """
                if "vendors" in gpu and len(gpu["vendors"]) > 0:
                    _first = list(gpu["vendors"].keys())[0]
                    env.concat("VK_ICD_FILENAMES", gpu["vendors"][_first]["icd"])
                else:
                    logging.warning(
                        "No GPU vendor found, keep going without setting VK_ICD_FILENAMES…"
                    )

        # Add ld to LD_LIBRARY_PATH
        if ld:
            env.concat("LD_LIBRARY_PATH", ld)

        # Vblank
        # env.add("__GL_SYNC_TO_VBLANK", "0")
        # env.add("vblank_mode", "0")

        # DLL Overrides
        if getattr(self, "forced_dll_overrides", None):
            dll_overrides.append(self.forced_dll_overrides)
        env.concat("WINEDLLOVERRIDES", dll_overrides, sep=";")
        if env.is_empty("WINEDLLOVERRIDES"):
            env.remove("WINEDLLOVERRIDES")

        # Wine prefix
        env.add("WINEPREFIX", capsule, override=True)
        # Wine arch
        env.add("WINEARCH", arch)

        xlocale_dir = None
        if runner_path:
            candidate = os.path.join(runner_path, "share/X11/locale")
            if os.path.isdir(candidate):
                xlocale_dir = candidate
        apply_wayland_preferences(env, params, xlocale_dir)

        resolved_env = env.get()["envs"]
        return resolved_env

    @staticmethod
    def _apply_sync_environment(env: WineEnv, sync: str, runner: str) -> None:
        if sync == "esync":
            env.add("WINEESYNC", "1")
        elif sync == "fsync":
            env.add("WINEFSYNC", "1")
            # Wine 11 runners prefer ntsync over fsync unless told not to
            env.add("PROTON_NO_NTSYNC", "1")
        elif sync in ("wine", "ntsync"):
            if is_ntsync_available(runner):
                env.add("WINENTSYNC", "1")
            elif sync == "ntsync":
                logging.warning(
                    "ntsync requested but unavailable, falling back to fsync"
                )
                env.add("WINEFSYNC", "1")

    def _get_runner_info(self) -> tuple[str, str]:
        config = self.config
        runner = ManagerUtils.get_runner_path(config.Runner)
        arch = config.Arch
        runner_runtime = ""

        if runner in [None, ""]:
            return "", ""

        if runner.startswith("sys-"):
            """
            If the runner type is system, set the runner binary
            path to the system command. Else set it to the full path.
            """
            runner = shutil.which("wine")

        else:
            runner = f"{runner}/bin/wine"

        if arch == "win64" and os.path.exists(f"{runner}64"):
            runner = f"{runner}64"

        runner = shlex.quote(runner)  # type: ignore

        return runner, runner_runtime

    def get_cmd(
        self,
        command,
        pre_script: Optional[str] = None,
        post_script: Optional[str] = None,
        pre_script_args: Optional[str] = None,
        post_script_args: Optional[str] = None,
        return_clean_cmd: bool = False,
        environment: Optional[dict] = None,
    ) -> str:
        config = self.config
        params = config.Parameters
        runner = self.runner

        if environment is None:
            environment = {}

        launch_prefix, launch_suffix = "", ""
        if self.arguments:
            launch_prefix, launch_suffix, launch_environment = (
                handle_launch_options(self.arguments)
            )
            if launch_environment.get("WINEDLLOVERRIDES") and environment.get(
                "WINEDLLOVERRIDES"
            ):
                environment["WINEDLLOVERRIDES"] += ";" + launch_environment.pop(
                    "WINEDLLOVERRIDES"
                )
            environment.update(launch_environment)

        if not return_clean_cmd:
            if params.eagle_tracing and not self.minimal:
                from runexe.backend.managers.eagletracing import (
                    trace_command,
                    tracing_supported,
                )
                if tracing_supported(config, runner):
                    runner = trace_command(config, runner, self.cwd)
            command = f"{runner} {command}"

        if self.arguments:
            if launch_prefix:
                command = f"{launch_prefix} {command}"
            if launch_suffix:
                command = f"{command} {launch_suffix}"

        if post_script not in (None, ""):
            post_cmd_parts = [post_script]
            if post_script_args not in (None, ""):
                post_cmd_parts.extend(shlex.split(post_script_args))
            post_cmd = " ".join(shlex.quote(part) for part in post_cmd_parts)
            command = f"{command} ; sh {post_cmd}"

        if pre_script not in (None, ""):
            pre_cmd_parts = [pre_script]
            if pre_script_args not in (None, ""):
                pre_cmd_parts.extend(shlex.split(pre_script_args))
            pre_cmd = " ".join(shlex.quote(part) for part in pre_cmd_parts)
            command = f"sh {pre_cmd} ; {command}"

        return command

    def _get_sandbox_manager(self) -> SandboxManager:
        # Expose the runner root inside the dedicated sandbox. Symlinks are
        # resolved so the real target gets shared, not just the link.
        share_paths_ro = [Paths.runners, Paths.temp]
        cpak_grants = "/run/cpak/grants"
        if is_cpak() and os.path.isdir(cpak_grants):
            share_paths_ro.append(cpak_grants)

        runner_root = ManagerUtils.get_runner_path(self.config.Runner)
        for extra in (runner_root,):
            if extra and not str(extra).startswith("sys-"):
                share_paths_ro.append(os.path.realpath(extra))

        # The working directory may be a transient document portal path
        # (/run/user/<uid>/doc/<id>/...) which is not reliably reachable inside
        # the nested sandbox: it can disappear or simply not be accessible to
        # bwrap's chdir, which would make nothing launch at all. Resolve it to a
        # real host path and fall back to the capsule path (always exposed and
        # present) whenever it is not a usable directory.
        capsule_path = ManagerUtils.get_capsule_path(self.config)
        chdir = ManagerUtils.resolve_portal_path(self.cwd) if self.cwd else capsule_path
        if (
            not chdir
            or ("/run/user/" in chdir and "/doc/" in chdir)
            or not os.path.isdir(chdir)
        ):
            logging.warning(
                f"Working directory '{self.cwd}' is not usable inside the "
                "dedicated sandbox, falling back to the capsule path.",
                jn=True,
            )
            chdir = capsule_path

        share_paths_rw = [capsule_path]
        for value in (
            getattr(self, "arguments", ""),
            getattr(self, "command", ""),
        ):
            try:
                arguments = shlex.split(value)
            except ValueError:
                continue
            for argument in arguments:
                if (
                    ManagerUtils.is_portal_document_path(argument)
                    and argument not in share_paths_rw
                ):
                    share_paths_rw.append(argument)

        hidraw_selected = any(
            normalize_hidraw_id(value)
            for value in self.config.Parameters.hidraw_devices
        )
        return SandboxManager(
            envs=self.env,
            chdir=chdir,
            clear_env=True,
            share_paths_rw=share_paths_rw,
            share_paths_ro=[p for p in share_paths_ro if p],
            share_net=self.config.Sandbox.share_net,
            share_sound=self.config.Sandbox.share_sound,
            share_input=self.config.Sandbox.share_input or hidraw_selected,
            share_usb=self.config.Sandbox.share_usb or hidraw_selected,
            share_hidraw=hidraw_selected,
        )

    def run(self) -> Result[Optional[str]]:
        """
        Run command with pre-configured parameters

        :return: `status` is True if command executed successfully,
                 `data` may be available even if `status` is False.
        """
        if None in [self.runner, self.env]:
            return Result(
                False, message="runner or env is not ready, Wine command terminated."
            )

        # Log the final command that will be executed
        logging.info(f"Executing command: {self.command}")

        use_sandbox = self.config.Parameters.sandbox
        if self.sandbox_override == "off":
            use_sandbox = False
            logging.warning(
                "Launching without the dedicated sandbox on user request: the "
                "target is outside the capsule and cannot be reached otherwise.",
                jn=True,
            )
        sandbox = self._get_sandbox_manager() if use_sandbox else None

        # run command in external terminal if terminal is True
        if self.terminal:
            if sandbox:
                return Result(
                    status=TerminalUtils().execute(
                        sandbox.get_cmd(self.command), self.env, self.colors, self.cwd
                    )
                )
            else:
                return Result(
                    status=TerminalUtils().execute(
                        self.command, self.env, self.colors, self.cwd
                    )
                )

        # prepare proc if we are going to execute command internally
        # proc should always be `Popen[bytes]` to make sure
        # stdout_data's type is `bytes`
        proc: subprocess.Popen[bytes]
        if sandbox:
            proc = sandbox.run(self.command)
        else:
            try:
                proc = subprocess.Popen(
                    self.command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE if self.capture_stderr else None,
                    shell=True,
                    env=self.env,
                    cwd=self.cwd,
                    start_new_session=True,
                )
            except FileNotFoundError:
                self._clear_exit_code_capture()
                return Result(False, message="File not found")

        if not self.communicate:
            return Result(True)

        try:
            stdout_data, stderr_data = proc.communicate()
            if self.capture_stderr:
                stdout_data = b"\n".join(data for data in (stdout_data, stderr_data) if data)
        finally:
            self._clear_exit_code_capture()

        # Consider changing the locale to C.UTF-8 when
        # executing commands, to ensure consistent output and
        # enable callers to make use of the returned value,
        # also without requiring the encoding detection dance
        codec = detect_encoding(stdout_data)
        rv: str
        try:
            rv = stdout_data.decode(codec)
        except (UnicodeDecodeError, LookupError, TypeError):
            # UnicodeDecodeError: codec mismatch
            # LookupError: unknown codec name
            # TypeError: codec is None
            logging.warning("stdout decoding failed")
            rv = str(stdout_data)[2:-1]  # trim b''

        rv, self.returncode = self._extract_exit_code(rv, proc.returncode)

        if self.returncode:
            return Result(
                False,
                data=rv,
                message=f"Command exited with status {self.returncode}.",
            )

        # "ShellExecuteEx" exception may occur while executing command,
        # previously we rerun the command without `cwd` and `stdout=PIPE`
        # to fix it, which is removed since it may lead to unexpected behavior
        if "ShellExecuteEx" in rv:
            logging.warning("ShellExecuteEx exception seems occurred.")
            return Result(
                False, data=rv, message="ShellExecuteEx exception seems occurred."
            )

        return Result(True, data=rv)
