"""Unit tests for WineExecutor placeholder handling"""

import os
import shlex
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from runexe.backend.dlls.dxvk import DXVKComponent
from runexe.backend.models.config import CapsuleConfig, CapsuleParams
from runexe.backend.models.result import Result
from runexe.backend.models.samples import Samples
from runexe.backend.utils.manager import ManagerUtils
from runexe.backend.wine import winecommand
from runexe.backend.wine.executor import WineExecutor
from runexe.backend.wine.winecommand import (
    WineCommand,
    WineEnv,
    apply_fex_preferences,
    apply_hidraw_preferences,
    apply_identity_bridge,
    apply_openxr_preferences,
    apply_wayland_preferences,
)


def _make_config(
    name: str = "TestCapsule", path: str = "TestCapsulePath"
) -> CapsuleConfig:
    return CapsuleConfig(Name=name, Path=path, Custom_Path="", Environment="Custom")


@pytest.mark.parametrize("refresh_ok", [True, False])
def test_changed_fonts_refresh_before_launch(monkeypatch, tmp_path, refresh_ok):
    fonts = tmp_path / "drive_c/windows/Fonts"
    fonts.mkdir(parents=True)
    stamp = tmp_path / ".fonts_stamp"
    commands = []
    events = []

    class Command:
        def __init__(self, _config, **kwargs):
            commands.append(kwargs)

        def run(self):
            events.append("refresh")
            return Result(refresh_ok)

    monkeypatch.setattr(
        "runexe.backend.wine.wineprogram.WineCommand", Command
    )
    monkeypatch.setattr(
        ManagerUtils, "get_capsule_path", lambda _config: str(tmp_path)
    )
    monkeypatch.setattr(
        "runexe.backend.wine.executor.SignalManager.send", lambda *_args: None
    )
    executor = WineExecutor.__new__(WineExecutor)
    executor.config = _make_config()
    executor.exec_path = executor._raw_exec_path = r"C:\fixture.exe"
    executor.exec_type = "exe"

    def launch():
        events.append("launch")
        return Result(True)

    executor._WineExecutor__launch_with_bridge = launch

    assert executor.run().ok
    assert events == ["refresh", "launch"]
    assert commands[0]["command"] == "wineboot -u /nogui"
    assert commands[0]["communicate"] is True
    assert commands[0]["forced_dll_overrides"] == "mscoree=d"
    assert stamp.exists() == refresh_ok
    if refresh_ok:
        assert float(stamp.read_text()) == fonts.stat().st_mtime

    assert executor.run().ok
    assert len(commands) == (1 if refresh_ok else 2)


def test_build_placeholder_map_uses_program_values():
    config = _make_config()
    program = {
        "name": "My Game",
        "path": "/opt/games/my-game.exe",
    }

    placeholders = WineExecutor._build_placeholder_map(config, program)

    expected_capsule_path = ManagerUtils.get_capsule_path(config)
    assert placeholders["PROGRAM_NAME"] == "My Game"
    assert placeholders["PROGRAM_PATH"] == "/opt/games/my-game.exe"
    assert placeholders["PROGRAM_DIR"] == "/opt/games"
    assert placeholders["CAPSULE_NAME"] == "TestCapsule"
    assert placeholders["CAPSULE_PATH"] == expected_capsule_path


def test_openxr_preferences_install_soda_manifest(tmp_path):
    runner = tmp_path / "runner"
    source = runner / "share/openxr/wineopenxr64.json"
    source.parent.mkdir(parents=True)
    source.write_text(
        '{"runtime":{"library_path":"C:\\\\windows\\\\system32\\\\wineopenxr.dll"}}'
    )
    capsule = tmp_path / "capsule"
    capsule.mkdir()
    env = WineEnv(clean=True)

    apply_openxr_preferences(env, "soda-11.0-7", str(runner), str(capsule))

    target = capsule / "drive_c/openxr/wineopenxr64.json"
    assert target.read_bytes() == source.read_bytes()
    assert env.get()["envs"]["SODA_OPENXR_RUNTIME"] == "host"


def test_openxr_preferences_preserve_runtime_override(tmp_path):
    runner = tmp_path / "runner"
    source = runner / "share/openxr/wineopenxr64.json"
    source.parent.mkdir(parents=True)
    source.write_text('{"runtime":{"library_path":"wineopenxr.dll"}}')
    capsule = tmp_path / "capsule"
    capsule.mkdir()
    env = WineEnv(clean=True)
    env.add("SODA_OPENXR_RUNTIME", "steam")

    apply_openxr_preferences(env, "soda-11.0-7", str(runner), str(capsule))

    assert env.get()["envs"]["SODA_OPENXR_RUNTIME"] == "steam"


def test_identity_bridge_requires_runner_unixlib(tmp_path, monkeypatch):
    called = False

    def start(_context):
        nonlocal called
        called = True
        return "/run/user/1000/bridge.sock"

    monkeypatch.setattr(winecommand, "start_identity_bridge", start)
    env = WineEnv(clean=True)

    apply_identity_bridge(env, "soda-11.0-23", str(tmp_path), str(tmp_path))

    assert not called
    assert "SODA_IDENTITY_BRIDGE_SOCKET" not in env.get()["envs"]


def test_openxr_preferences_reject_drive_c_outside_capsule(tmp_path):
    runner = tmp_path / "runner"
    source = runner / "share/openxr/wineopenxr64.json"
    source.parent.mkdir(parents=True)
    source.write_text('{"runtime":{"library_path":"wineopenxr.dll"}}')
    capsule = tmp_path / "capsule"
    outside = tmp_path / "outside"
    capsule.mkdir()
    outside.mkdir()
    (capsule / "drive_c").symlink_to(outside, target_is_directory=True)
    env = WineEnv(clean=True)

    apply_openxr_preferences(env, "soda-11.0-7", str(runner), str(capsule))

    assert not (outside / "openxr").exists()
    assert "SODA_OPENXR_RUNTIME" not in env.get()["envs"]


def test_fex_preferences_enable_bundled_soda_config(tmp_path, monkeypatch):
    runner = tmp_path / "runner"
    config = runner / "share/fex-emu/Config.json"
    unixlib = runner / "lib/wine/aarch64-unix/libwow64fex.so"
    config.parent.mkdir(parents=True)
    unixlib.parent.mkdir(parents=True)
    config.write_text("{}")
    unixlib.touch()
    env = WineEnv(clean=True)
    monkeypatch.setattr(winecommand, "get_host_architecture", lambda: "aarch64")

    apply_fex_preferences(env, "soda-11.0-7", str(runner))

    resolved = env.get()["envs"]
    assert resolved["FEX_APP_CONFIG"] == str(config)
    assert resolved["FEX_APP_CONFIG_LOCATION"] == str(config.parent)


def test_fex_preferences_preserve_user_overrides(tmp_path, monkeypatch):
    runner = tmp_path / "runner"
    config = runner / "share/fex-emu/Config.json"
    unixlib = runner / "lib/wine/aarch64-unix/libwow64fex.so"
    config.parent.mkdir(parents=True)
    unixlib.parent.mkdir(parents=True)
    config.write_text("{}")
    unixlib.touch()
    env = WineEnv(clean=True)
    env.add("FEX_APP_CONFIG", "/custom/config.json")
    env.add("FEX_APP_CONFIG_LOCATION", "/custom")
    monkeypatch.setattr(winecommand, "get_host_architecture", lambda: "aarch64")

    apply_fex_preferences(env, "soda-11.0-7", str(runner))

    resolved = env.get()["envs"]
    assert resolved["FEX_APP_CONFIG"] == "/custom/config.json"
    assert resolved["FEX_APP_CONFIG_LOCATION"] == "/custom"


def test_replace_placeholders_handles_unknown_tokens():
    placeholders = {"PROGRAM_NAME": "Example", "CAPSULE_NAME": "Capsule"}

    result = WineExecutor._replace_placeholders(
        "run-%PROGRAM_NAME%-on-%CAPSULE_NAME%-%UNKNOWN%",
        placeholders,
    )

    assert result == "run-Example-on-Capsule-%UNKNOWN%"


def test_run_program_substitutes_placeholders(monkeypatch):
    def fake_init(
        self,
        *,
        config,
        exec_path,
        args="",
        terminal=False,
        environment=None,
        move_file=False,
        move_upd_fn=None,
        pre_script=None,
        post_script=None,
        pre_script_args=None,
        post_script_args=None,
        cwd=None,
        monitoring=None,
        program_dxvk=None,
        program_vkd3d=None,
        program_virt_desktop=None,
        program_winebridge=None,
        program_hide_console=False,
        sandbox_override=None,
    ):
        # mimic original __init__ contract enough for run() stub
        self.config = config
        self.use_winebridge = (
            program_winebridge
            if program_winebridge is not None
            else config.Parameters.winebridge
        )
        self.captured = {
            "config": config,
            "exec_path": exec_path,
            "args": args,
            "environment": environment,
            "pre_script": pre_script,
            "post_script": post_script,
            "pre_script_args": pre_script_args,
            "post_script_args": post_script_args,
            "cwd": cwd,
            "program_dxvk": program_dxvk,
            "program_hide_console": program_hide_console,
        }

    def fake_run(self):
        self.captured["use_winebridge"] = self.use_winebridge
        return Result(True, data=self.captured)

    monkeypatch.setattr(WineExecutor, "__init__", fake_init, raising=False)
    monkeypatch.setattr(WineExecutor, "run", fake_run, raising=False)

    config = _make_config(name="Capsule", path="CapsulePath")
    config.Parameters.dxvk = True
    program = {
        "name": "Awesome Game",
        "path": "/games/awesome/game.exe",
        "arguments": "--title=%PROGRAM_NAME%",
        "pre_script": "/scripts/%CAPSULE_NAME%/%PROGRAM_NAME%.sh",
        "pre_script_args": "--prefix=%CAPSULE_PATH%",
        "post_script": None,
        "post_script_args": "--dir=%PROGRAM_DIR%",
        "folder": "%PROGRAM_DIR%",
        "environment": {
            "DXVK_HUD": "fps",
            "WINEDLLOVERRIDES": "version=n,b",
            "WINEARCH": "win32",
            "WINEPREFIX": "/tmp/other",
        },
        "dxvk": False,
        "sync": "esync",
        "winebridge": True,
        "hide_console": True,
    }

    result = WineExecutor.run_program(config=config, program=program, terminal=False)

    assert result.status is True
    data = result.data
    assert data["exec_path"] == "/games/awesome/game.exe"
    assert data["args"] == "--title=Awesome Game"
    assert data["pre_script"] == "/scripts/Capsule/Awesome Game.sh"
    assert data["pre_script_args"] == f"--prefix={ManagerUtils.get_capsule_path(config)}"
    assert data["post_script_args"] == "--dir=/games/awesome"
    assert data["cwd"] == "/games/awesome"
    assert data["environment"] == {
        "DXVK_HUD": "fps",
        "WINEDLLOVERRIDES": "version=n,b",
    }
    assert data["program_dxvk"] is False
    assert data["program_hide_console"] is True
    assert data["config"] is not config
    assert data["config"].Parameters.dxvk is True
    assert data["config"].Parameters.sync == "esync"
    assert data["use_winebridge"] is False
    assert config.Parameters.dxvk is True
    assert config.Parameters.sync == "wine"


def test_run_program_creates_configured_backup_after_exit(monkeypatch):
    events = []

    def fake_init(self, **_kwargs):
        self.use_winebridge = True

    def fake_run(self):
        events.append("run")
        return Result(True, data={"use_winebridge": self.use_winebridge})

    monkeypatch.setattr(WineExecutor, "__init__", fake_init, raising=False)
    monkeypatch.setattr(
        WineExecutor,
        "run",
        fake_run,
        raising=False,
    )
    monkeypatch.setattr(
        "runexe.backend.managers.backup.BackupManager.create_program_backup",
        lambda _config, _program: events.append("backup") or Result(True),
    )
    config = _make_config()
    program = {
        "name": "Game",
        "path": "/games/game.exe",
        "automatic_backup": {
            "enabled": True,
            "destination": "/backups",
            "paths": ["/saves"],
        },
    }

    result = WineExecutor.run_program(config, program)

    assert result.status
    assert result.data["use_winebridge"] is False
    assert events == ["run", "backup"]


def test_program_dxvk_false_adds_builtin_override(tmp_path):
    executable = tmp_path / "program.exe"
    executable.touch()
    config = _make_config()
    config.Parameters.dxvk = True

    executor = WineExecutor(
        config=config,
        exec_path=str(executable),
        program_dxvk=False,
    )

    assert executor.environment["WINEDLLOVERRIDES"] == (
        f"{DXVKComponent.get_override_keys()}=b"
    )


def test_global_dxvk_false_adds_builtin_override(tmp_path):
    executable = tmp_path / "program.exe"
    executable.touch()
    config = _make_config()
    config.Parameters.dxvk = False

    executor = WineExecutor(
        config=config,
        exec_path=str(executable),
    )

    assert executor.environment["WINEDLLOVERRIDES"] == (
        f"{DXVKComponent.get_override_keys()}=b"
    )


def test_program_dxvk_true_overrides_global_disabled_state(tmp_path):
    executable = tmp_path / "program.exe"
    executable.touch()
    config = _make_config()
    config.Parameters.dxvk = False

    executor = WineExecutor(
        config=config,
        exec_path=str(executable),
        program_dxvk=True,
    )

    assert "WINEDLLOVERRIDES" not in executor.environment


def test_soda_adaptive_launch_prepares_a_profile(tmp_path, monkeypatch):
    executable = tmp_path / "program.exe"
    executable.touch()
    capsule = tmp_path / "capsule"
    config = _make_config(path=str(capsule))
    config.Custom_Path = str(capsule)
    config.Runner = "soda-11.0-5"
    config.Parameters.adaptive_launch = True
    prepared = {}

    class FakeProfile:
        def __init__(self, _config, path):
            prepared["executable"] = path
            self.path = tmp_path / "profile"

        def prepare(self):
            prepared["called"] = True
            return 3

    monkeypatch.setattr(
        "runexe.backend.wine.executor.AdaptiveLaunchProfile", FakeProfile
    )

    executor = WineExecutor(config=config, exec_path=str(executable))

    assert prepared == {"executable": str(executable), "called": True}
    assert executor.environment["SODA_ADAPTIVE_PROFILE"] == str(tmp_path / "profile")


def test_soda_adaptive_launch_v2_uses_a_trace_directory(tmp_path, monkeypatch):
    executable = tmp_path / "program.exe"
    executable.touch()
    capsule = tmp_path / "capsule"
    config = _make_config(path=str(capsule))
    config.Custom_Path = str(capsule)
    config.Runner = "soda-11.0-7"
    config.Parameters.adaptive_launch = True

    class FakeProfile:
        def __init__(self, _config, _path):
            self.trace_dir = tmp_path / "trace"

        def prepare(self):
            self.trace_dir.mkdir()
            return 0

    monkeypatch.setattr(
        "runexe.backend.wine.executor.AdaptiveLaunchProfile", FakeProfile
    )

    executor = WineExecutor(config=config, exec_path=str(executable))

    assert executor.environment["SODA_ADAPTIVE_TRACE_DIR"] == str(tmp_path / "trace")
    assert "SODA_ADAPTIVE_PROFILE" not in executor.environment


def test_adaptive_launch_is_ignored_by_other_runners(tmp_path, monkeypatch):
    executable = tmp_path / "program.exe"
    executable.touch()
    config = _make_config()
    config.Runner = "wine-ge-8-26"
    config.Parameters.adaptive_launch = True
    monkeypatch.setattr(
        "runexe.backend.wine.executor.AdaptiveLaunchProfile",
        lambda *_args: pytest.fail("profile should not be created"),
    )

    executor = WineExecutor(config=config, exec_path=str(executable))

    assert "SODA_ADAPTIVE_PROFILE" not in executor.environment
    assert "SODA_ADAPTIVE_TRACE_DIR" not in executor.environment


def test_winecommand_reports_nonzero_exit_status(monkeypatch):
    process = SimpleNamespace(
        returncode=7,
        communicate=lambda: (b"registry failed", None),
    )
    monkeypatch.setattr(
        winecommand.subprocess, "Popen", lambda *_args, **_kwargs: process
    )

    command = WineCommand.__new__(WineCommand)
    command.runner = "/usr/bin/wine"
    command.env = {}
    command.command = "wine reg import test.reg"
    command.config = _make_config()
    command.terminal = False
    command.sandbox_override = None
    command.communicate = True
    command.capture_stderr = False
    command.cwd = None

    result = command.run()

    assert not result.ok
    assert result.data == "registry failed"
    assert result.message == "Command exited with status 7."
    assert command.returncode == 7


@pytest.mark.parametrize("sandboxed", [False, True])
@pytest.mark.parametrize("capture_stderr", [False, True])
def test_winecommand_can_capture_version_output_on_stderr(monkeypatch, sandboxed, capture_stderr):
    process = SimpleNamespace(returncode=0, communicate=lambda: (b"", b"owned Wine diagnostic\nwin10\n"))
    captured = {}
    def launch(*_args, **kwargs):
        captured.update(kwargs)
        return process
    monkeypatch.setattr(winecommand.subprocess, "Popen", launch)
    command = WineCommand.__new__(WineCommand)
    command.runner = "/usr/bin/wine"
    command.env = {}
    command.command = "wine winecfg -v"
    command.config = _make_config()
    command.config.Parameters.sandbox = sandboxed
    command.terminal = False
    command.sandbox_override = None
    command.communicate = True
    command.capture_stderr = capture_stderr
    command.cwd = None
    monkeypatch.setattr(command, "_get_sandbox_manager", lambda: SimpleNamespace(run=launch))

    result = command.run()

    assert result.ok
    assert result.data == ("owned Wine diagnostic\nwin10\n" if capture_stderr else "")
    if not sandboxed:
        assert captured['stderr'] == (subprocess.PIPE if capture_stderr else None)


def test_winecommand_reports_full_windows_exit_status(monkeypatch):
    process = SimpleNamespace(
        returncode=108,
        communicate=lambda: (b"setup failed\nRUNEXE_EXIT_CODE_test=17004\r\n", None),
    )
    monkeypatch.setattr(
        winecommand.subprocess, "Popen", lambda *_args, **_kwargs: process
    )

    command = WineCommand.__new__(WineCommand)
    command.runner = "/usr/bin/wine"
    command.env = {}
    command.command = "wine cmd.exe /d /c test.bat"
    command.config = _make_config()
    command.terminal = False
    command.sandbox_override = None
    command.communicate = True
    command.capture_stderr = False
    command.cwd = None
    command._exit_code_batch = None
    command._exit_code_marker = "RUNEXE_EXIT_CODE_test"

    result = command.run()

    assert not result.ok
    assert result.data == "setup failed"
    assert result.message == "Command exited with status 17004."
    assert command.returncode == 17004


def test_executable_launch_reports_winecommand_failure(monkeypatch):
    command_result = Result(False, data="setup failed", message="status 7")

    class Command:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self):
            return command_result

    monkeypatch.setattr("runexe.backend.wine.executor.WineCommand", Command)
    monkeypatch.setattr(
        WineExecutor,
        "_WineExecutor__set_monitors",
        lambda _self: None,
    )
    executor = WineExecutor.__new__(WineExecutor)
    executor.config = _make_config()
    executor.exec_path = "setup.exe"
    executor.args = "/silent"
    executor.terminal = False
    executor.environment = {}
    executor.pre_script = None
    executor.post_script = None
    executor.pre_script_args = None
    executor.post_script_args = None
    executor.cwd = None
    executor.sandbox_override = None

    result = executor._WineExecutor__launch_exe()

    assert not result.ok
    assert result.data == {"output": command_result}
    assert result.message == "status 7"


def test_component_override_bypasses_winebridge(monkeypatch):
    def fake_init(self, **kwargs):
        self.use_winebridge = kwargs["program_winebridge"]

    def fake_run(self):
        return Result(True, data={"use_winebridge": self.use_winebridge})

    monkeypatch.setattr(WineExecutor, "__init__", fake_init, raising=False)
    monkeypatch.setattr(WineExecutor, "run", fake_run, raising=False)

    result = WineExecutor.run_program(
        config=_make_config(),
        program={
            "path": "/games/example.exe",
            "dxvk": False,
            "winebridge": True,
        },
    )

    assert result.data["use_winebridge"] is False


def test_hide_console_bypasses_winebridge(monkeypatch):
    def fake_init(self, **kwargs):
        self.use_winebridge = kwargs["program_winebridge"]

    def fake_run(self):
        return Result(True, data={"use_winebridge": self.use_winebridge})

    monkeypatch.setattr(WineExecutor, "__init__", fake_init, raising=False)
    monkeypatch.setattr(WineExecutor, "run", fake_run, raising=False)

    result = WineExecutor.run_program(
        config=_make_config(),
        program={
            "path": "/games/example.exe",
            "hide_console": True,
            "winebridge": True,
        },
    )

    assert result.data["use_winebridge"] is False


def test_hide_console_routes_exe_through_start(monkeypatch):
    calls = []

    class FakeWinePath:
        def __init__(self, _config):
            pass

        @staticmethod
        def is_unix(_path):
            return False

        @staticmethod
        def is_windows(_path):
            return False

    def fake_start(self, host_cwd=False):
        assert host_cwd is False
        calls.append("start")
        return Result(True)

    def fake_exe(self):
        calls.append("exe")
        return Result(True)

    monkeypatch.setattr("runexe.backend.wine.executor.WinePath", FakeWinePath)
    monkeypatch.setattr(
        WineExecutor,
        "_WineExecutor__launch_with_starter",
        fake_start,
    )
    monkeypatch.setattr(WineExecutor, "_WineExecutor__launch_exe", fake_exe)

    executor = WineExecutor.__new__(WineExecutor)
    executor.config = _make_config()
    executor.use_winebridge = False
    executor.use_virt_desktop = False
    executor.hide_console = True
    executor.exec_type = "exe"
    executor._raw_exec_path = r"C:\games\example.exe"
    executor.exec_path = "/games/example.exe"

    result = executor._WineExecutor__launch_with_bridge()

    assert result.status is True
    assert calls == ["start"]


def test_winebridge_preserves_terminal_launch(monkeypatch):
    captured = {}

    class FakeWineBridge:
        def __init__(self, _config):
            pass

        @staticmethod
        def is_available():
            return True

        @staticmethod
        def run_exe(exec_path, **kwargs):
            captured["exec_path"] = exec_path
            captured.update(kwargs)
            return Result(True)

    class FakeWinePath:
        def __init__(self, _config):
            pass

        @staticmethod
        def is_unix(_path):
            return False

    monkeypatch.setattr("runexe.backend.wine.executor.WineBridge", FakeWineBridge)
    monkeypatch.setattr("runexe.backend.wine.executor.WinePath", FakeWinePath)

    executor = WineExecutor.__new__(WineExecutor)
    executor.config = _make_config()
    executor.use_winebridge = True
    executor.exec_type = "exe"
    executor._raw_exec_path = r"C:\Games\example.exe"
    executor.terminal = True
    executor.environment = {"GAME_MODE": "1"}
    executor.cwd = r"C:\Games"
    executor.sandbox_override = "off"

    result = executor._WineExecutor__launch_with_bridge()

    assert result.status is True
    assert captured == {
        "exec_path": r"C:\Games\example.exe",
        "terminal": True,
        "environment": {"GAME_MODE": "1"},
        "cwd": r"C:\Games",
        "sandbox_override": "off",
    }


def test_hide_console_virtual_desktop_uses_background_explorer(monkeypatch):
    captured = {}

    class FakeExplorer:
        def __init__(self, _config):
            pass

        def launch_desktop(self, **kwargs):
            captured.update(kwargs)
            return Result(True, data="output")

    monkeypatch.setattr("runexe.backend.wine.executor.Explorer", FakeExplorer)

    executor = WineExecutor.__new__(WineExecutor)
    executor.config = _make_config()
    executor.config.Parameters.virtual_desktop_res = "1280x720"
    executor.exec_path = r"'C:\Program Files\Example\example.exe'"
    executor.args = "--safe-mode"
    executor.environment = {"DXVK_HUD": "fps"}
    executor.cwd = r"C:\Program Files\Example"
    executor.hide_console = True
    executor.sandbox_override = "off"
    executor.monitoring = []

    result = executor._WineExecutor__launch_with_explorer()

    assert result.status is True
    assert captured["background"] is True
    assert captured["sandbox_override"] == "off"


def test_virtual_desktop_converts_raw_unix_path_before_quoting(monkeypatch):
    captured = {}

    class FakeWinePath:
        def __init__(self, _config):
            pass

        @staticmethod
        def is_unix(path):
            captured["checked_path"] = path
            return True

        @staticmethod
        def to_windows(path, native=False, sandbox_override=None):
            captured["converted_path"] = path
            captured["native"] = native
            captured["sandbox_override"] = sandbox_override
            return r"Z:\tmp\Test App.exe"

    def fake_explorer(self):
        return Result(True, data={"exec_path": self.exec_path})

    monkeypatch.setattr("runexe.backend.wine.executor.WinePath", FakeWinePath)
    monkeypatch.setattr(
        WineExecutor,
        "_WineExecutor__launch_with_explorer",
        fake_explorer,
    )

    executor = WineExecutor.__new__(WineExecutor)
    executor.config = _make_config()
    executor.use_winebridge = False
    executor.use_virt_desktop = True
    executor.hide_console = True
    executor.exec_type = "exe"
    executor._raw_exec_path = "/tmp/Test App.exe"
    executor.exec_path = "'/tmp/Test App.exe'"
    executor.sandbox_override = "off"

    result = executor._WineExecutor__launch_with_bridge()

    assert result.status is True
    assert captured["checked_path"] == "/tmp/Test App.exe"
    assert captured["converted_path"] == "/tmp/Test App.exe"
    assert captured["native"] is False
    assert captured["sandbox_override"] == "off"
    assert result.data["exec_path"] == r"'Z:\tmp\Test App.exe'"


def test_windows_executable_cwd_uses_its_parent(monkeypatch):
    captured = {}

    class FakeWinePath:
        def __init__(self, _config):
            pass

        @staticmethod
        def is_windows(path):
            return ":" in path

        @staticmethod
        def to_unix(path, native=False):
            captured["windows_parent"] = path
            captured["native"] = native
            return "/prefix/drive_c/Program Files/Example"

    monkeypatch.setattr("runexe.backend.wine.executor.WinePath", FakeWinePath)

    executor = WineExecutor(
        config=_make_config(),
        exec_path=r"C:\Program Files\Example\example.exe",
    )

    assert captured["windows_parent"] == r"C:\Program Files\Example"
    assert captured["native"] is True
    assert executor.cwd == "/prefix/drive_c/Program Files/Example"


def test_winebridge_launch_preserves_spaces_in_executable_path(monkeypatch):
    executable = "/mnt/storage/DO NOT RENAME OR MOVE/Games/Poly Bridge 2.exe"
    captured = {}

    class FakeWineBridge:
        def __init__(self, _config):
            pass

        @staticmethod
        def is_available():
            return True

        @staticmethod
        def run_exe(exec_path, **kwargs):
            captured["exec_path"] = exec_path
            captured.update(kwargs)
            return ""

    class FakeWinePath:
        def __init__(self, _config):
            pass

        @staticmethod
        def is_unix(exec_path):
            return exec_path.startswith("/")

        @staticmethod
        def to_windows(exec_path, native=False):
            assert native is True
            return exec_path.replace("/", "\\")

    monkeypatch.setattr("runexe.backend.wine.executor.WineBridge", FakeWineBridge)
    monkeypatch.setattr("runexe.backend.wine.executor.WinePath", FakeWinePath)
    monkeypatch.setattr(
        "runexe.backend.wine.executor.ManagerUtils.get_capsule_path",
        lambda _config: "/mnt/storage/DO NOT RENAME OR MOVE",
    )

    executor = WineExecutor.__new__(WineExecutor)
    executor.config = _make_config()
    executor.use_winebridge = True
    executor.exec_type = "exe"
    executor._raw_exec_path = executable
    executor.exec_path = f"'{executable}'"
    executor.terminal = False
    executor.environment = {}
    executor.cwd = None
    executor.sandbox_override = None

    result = executor._WineExecutor__launch_with_bridge()

    assert result.status is True
    assert captured["exec_path"] == executable.replace("/", "\\")
    assert captured["terminal"] is False


def test_external_executable_uses_wine_start(monkeypatch):
    executable = "/games/KeePass Portable/KeePass.exe"
    captured = {}

    class FakeWineBridge:
        def __init__(self, _config):
            pass

        @staticmethod
        def is_available():
            return True

    class FakeWinePath:
        def __init__(self, _config):
            pass

        @staticmethod
        def is_unix(path):
            return path.startswith("/")

        @staticmethod
        def is_windows(_path):
            return False

    monkeypatch.setattr("runexe.backend.wine.executor.WineBridge", FakeWineBridge)
    monkeypatch.setattr("runexe.backend.wine.executor.WinePath", FakeWinePath)
    monkeypatch.setattr(
        "runexe.backend.wine.executor.ManagerUtils.get_capsule_path",
        lambda _config: "/prefix",
    )
    monkeypatch.setattr(
        "runexe.backend.wine.executor.Start.run",
        lambda _self, **kwargs: captured.update(kwargs) or Result(True),
    )
    monkeypatch.setattr(
        WineExecutor,
        "_WineExecutor__set_monitors",
        lambda _self: None,
    )

    executor = WineExecutor.__new__(WineExecutor)
    executor.config = _make_config()
    executor.use_winebridge = True
    executor.use_virt_desktop = False
    executor.hide_console = False
    executor.exec_type = "exe"
    executor._raw_exec_path = executable
    executor.exec_path = shlex.quote(executable)
    executor.terminal = False
    executor.args = "--test"
    executor.environment = {"WINEDEBUG": "+seh"}
    executor.pre_script = None
    executor.post_script = None
    executor.pre_script_args = None
    executor.post_script_args = None
    executor.cwd = "/games/KeePass Portable"
    executor.sandbox_override = None

    result = executor._WineExecutor__launch_with_bridge()

    assert result.status is True
    assert captured["file"] == shlex.quote(executable)
    assert captured["cwd"] == "/games/KeePass Portable"
    assert captured["args"] == "--test"
    assert captured["environment"] == {"WINEDEBUG": "+seh"}
    assert captured["host_cwd"] is True


def test_wine_env_respects_allowed_keys(monkeypatch):
    monkeypatch.setenv("KEEP_ONLY", "1")
    monkeypatch.setenv("DROP_ME", "2")

    env = WineEnv(clean=False, allowed_keys=["KEEP_ONLY"])
    resolved = env.get()["envs"]

    assert resolved["KEEP_ONLY"] == "1"
    assert "DROP_ME" not in resolved


def test_default_wine_environment_inherits_xmodifiers(monkeypatch):
    monkeypatch.setenv("XMODIFIERS", "@im=fcitx")

    env = WineEnv(allowed_keys=Samples.default_inherited_environment)

    assert env.get()["envs"]["XMODIFIERS"] == "@im=fcitx"


@pytest.mark.parametrize(
    ("sync", "ntsync_available", "expected"),
    [
        ("wine", True, {"WINENTSYNC": "1"}),
        ("wine", False, {}),
        ("ntsync", True, {"WINENTSYNC": "1"}),
        ("ntsync", False, {"WINEFSYNC": "1"}),
        ("esync", False, {"WINEESYNC": "1"}),
        ("fsync", False, {"WINEFSYNC": "1", "PROTON_NO_NTSYNC": "1"}),
        ("fsync", True, {"WINEFSYNC": "1", "PROTON_NO_NTSYNC": "1"}),
    ],
)
def test_winecommand_applies_selected_sync_environment(
    monkeypatch, sync, ntsync_available, expected
):
    monkeypatch.setattr(
        winecommand,
        "is_ntsync_available",
        lambda _runner: ntsync_available,
    )
    env = WineEnv(clean=True)

    WineCommand._apply_sync_environment(env, sync, "/runner/bin/wine")

    assert env.get()["envs"] == expected


def test_hidraw_preferences_enable_only_selected_devices():
    env = WineEnv(clean=True)
    params = CapsuleParams(
        hidraw_devices=["0x044f/0xb10a", "1", "0x231D/0x0200"]
    )

    apply_hidraw_preferences(env, params)

    assert env.get()["envs"]["PROTON_ENABLE_HIDRAW"] == (
        "0x044F/0xB10A,0x231D/0x0200"
    )


def test_hidraw_preferences_preserve_manual_environment_when_unset():
    env = WineEnv(clean=True)
    env.add("PROTON_ENABLE_HIDRAW", "0x3344/0x0001")

    apply_hidraw_preferences(env, CapsuleParams())

    assert env.get()["envs"]["PROTON_ENABLE_HIDRAW"] == "0x3344/0x0001"


def test_native_wayland_enables_egl(monkeypatch):
    monkeypatch.setattr(
        "runexe.backend.wine.winecommand.DisplayUtils.display_server_type",
        lambda: "wayland",
    )
    env = WineEnv(clean=True)
    env.add("DISPLAY", ":1")
    env.add("WAYLAND_DISPLAY", "wayland-0")

    apply_wayland_preferences(env, CapsuleParams(wayland=True))

    resolved = env.get()["envs"]
    assert "DISPLAY" not in resolved
    assert resolved["WINE_USE_EGL"] == "1"


@pytest.mark.parametrize("session_type", [None, ""])
def test_native_wayland_without_session_type(monkeypatch, session_type):
    if session_type is None:
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    else:
        monkeypatch.setenv("XDG_SESSION_TYPE", session_type)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    env = WineEnv(clean=True)
    env.add("DISPLAY", ":1")
    env.add("WAYLAND_DISPLAY", "wayland-0")

    apply_wayland_preferences(env, CapsuleParams(wayland=True))

    resolved = env.get()["envs"]
    assert "DISPLAY" not in resolved
    assert resolved["WINE_USE_EGL"] == "1"


@pytest.mark.parametrize(
    "runner_binary,override",
    [
        ("/usr/bin/wine", None),
        ("/runners/Soda with spaces/bin/wine64", None),
        ("/runners/GE-Proton/files/bin/wine", None),
        ("/runners/Soda/bin/wine", "/custom/wine"),
    ],
)
def test_winecommand_filters_host_environment(monkeypatch, tmp_path, runner_binary, override):
    capsule_path = tmp_path / "TestCapsule"
    capsule_path.mkdir()
    runner_path = tmp_path / "runner"
    for sub in [
        "lib",
        "lib64",
        "lib/wine/x86_64-unix",
        "lib32/wine/x86_64-unix",
        "lib32",
        "lib64/wine/x86_64-unix",
        "lib/wine/aarch64-unix",
        "lib/wine/i386-unix",
        "lib32/wine/i386-unix",
        "lib64/wine/i386-unix",
    ]:
        (runner_path / sub).mkdir(parents=True, exist_ok=True)

    config = CapsuleConfig(Name="Test", Path=str(capsule_path), Runner="test")
    params = CapsuleParams()
    params.use_runtime = False
    config.Parameters = params
    config.Limit_System_Environment = True
    config.Inherited_Environment_Variables = ["DISPLAY"]

    monkeypatch.setenv("DISPLAY", ":1")
    monkeypatch.setenv("SHOULD_NOT_PASS", "secret")
    monkeypatch.setenv("WINE", "/wrong/inherited/wine")

    monkeypatch.setattr(
        "runexe.backend.wine.winecommand.ManagerUtils.get_capsule_path",
        lambda _config: str(capsule_path),
    )
    monkeypatch.setattr(
        "runexe.backend.wine.winecommand.ManagerUtils.get_runner_path",
        lambda _runner: str(runner_path),
    )
    monkeypatch.setattr(
        "runexe.backend.wine.winecommand.DisplayUtils.check_nvidia_device",
        lambda: None,
    )
    monkeypatch.setattr(
        "runexe.backend.wine.winecommand.DisplayUtils.display_server_type",
        lambda: "x11",
    )

    def _fake_gpu(self):
        return {
            "prime": {
                "discrete": None,
                "integrated": {"icd": "/tmp/icd", "envs": {}},
            },
            "vendors": {"generic": {"icd": "/tmp/icd", "envs": {}}},
        }

    monkeypatch.setattr(
        "runexe.backend.wine.winecommand.GPUUtils.get_gpu",
        _fake_gpu,
    )
    monkeypatch.setattr(
        "runexe.backend.wine.winecommand.RuntimeManager.get_runtime_env",
        lambda *_: [],
    )
    monkeypatch.setattr(winecommand, "get_host_architecture", lambda: "aarch64")

    winecmd = WineCommand.__new__(WineCommand)
    winecmd.config = config
    winecmd.minimal = True
    winecmd.arguments = ""
    winecmd.runner = shlex.quote(runner_binary)
    winecmd.runner_runtime = ""
    winecmd.terminal = False

    env = winecmd.get_env({"WINE": override} if override else {})
    assert env["WINE"] == (override or runner_binary)
    assert env["DISPLAY"] == ":1"
    assert "DXVK_HDR" not in env
    assert "SHOULD_NOT_PASS" not in env
    assert str(runner_path / "lib/wine/aarch64-unix") in env["LD_LIBRARY_PATH"]


def test_wayland_sandbox_clears_parent_display(monkeypatch, tmp_path):
    capsule_path = tmp_path / "TestCapsule"
    capsule_path.mkdir()
    config = CapsuleConfig(Name="Test", Path=str(capsule_path), Runner="sys-wine")

    monkeypatch.setenv("FLATPAK_ID", "io.github.runexe.RunExe")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(
        "runexe.backend.wine.winecommand.ManagerUtils.get_capsule_path",
        lambda _config: str(capsule_path),
    )
    monkeypatch.setattr(
        "runexe.backend.wine.winecommand.ManagerUtils.get_runner_path",
        lambda _runner: "sys-wine",
    )

    winecmd = WineCommand.__new__(WineCommand)
    winecmd.config = config
    winecmd.env = {"WAYLAND_DISPLAY": "wayland-0"}
    winecmd.cwd = str(capsule_path)
    winecmd.runner_runtime = ""

    command = winecmd._get_sandbox_manager().get_cmd("wine")

    assert "--clear-env" not in command
    assert "env -i" in command
    assert "WAYLAND_DISPLAY=wayland-0" in command
    assert not any(token.startswith("DISPLAY=") for token in shlex.split(command))

    monkeypatch.delenv("FLATPAK_ID")
    command = winecmd._get_sandbox_manager().get_cmd("wine")

    assert command.index("--clearenv") < command.index(
        "--setenv WAYLAND_DISPLAY wayland-0"
    )


def test_dedicated_sandbox_shares_forwarded_document_read_write(monkeypatch, tmp_path):
    capsule_path = tmp_path / "TestCapsule"
    capsule_path.mkdir()
    document = "/run/user/1000/doc/abc123/My Document.txt"
    command_document = "/run/user/1000/doc/def456/Batch Document.txt"
    other_path = "/home/user/Other Document.txt"
    config = CapsuleConfig(Name="Test", Path=str(capsule_path), Runner="sys-wine")

    monkeypatch.setattr(
        "runexe.backend.wine.winecommand.ManagerUtils.get_capsule_path",
        lambda _config: str(capsule_path),
    )
    monkeypatch.setattr(
        "runexe.backend.wine.winecommand.ManagerUtils.get_runner_path",
        lambda _runner: "sys-wine",
    )
    monkeypatch.setattr(
        "runexe.backend.wine.winecommand.ManagerUtils.resolve_portal_path",
        lambda path: path,
    )
    monkeypatch.setattr(
        "runexe.backend.wine.winecommand.ManagerUtils.is_portal_document_path",
        lambda path: path in (document, command_document),
    )

    winecmd = WineCommand.__new__(WineCommand)
    winecmd.config = config
    winecmd.arguments = " ".join(shlex.quote(arg) for arg in (document, other_path))
    winecmd.command = "cmd /c {} {}".format(
        shlex.quote("C:\\probe.bat"),
        shlex.quote(command_document),
    )
    winecmd.cwd = str(capsule_path)
    winecmd.runner_runtime = ""
    winecmd.env = {}

    sandbox = winecmd._get_sandbox_manager()

    assert document in sandbox.share_paths_rw
    assert command_document in sandbox.share_paths_rw
    assert other_path not in sandbox.share_paths_rw


def test_dedicated_sandbox_shares_cpak_file_grants(monkeypatch, tmp_path):
    capsule_path = tmp_path / "TestCapsule"
    capsule_path.mkdir()
    config = CapsuleConfig(Name="Test", Path=str(capsule_path), Runner="sys-wine")

    monkeypatch.setattr(
        "runexe.backend.wine.winecommand.ManagerUtils.get_capsule_path",
        lambda _config: str(capsule_path),
    )
    monkeypatch.setattr(
        "runexe.backend.wine.winecommand.ManagerUtils.get_runner_path",
        lambda _runner: "sys-wine",
    )
    monkeypatch.setattr(winecommand, "is_cpak", lambda: True)
    original_isdir = os.path.isdir
    monkeypatch.setattr(
        winecommand.os.path,
        "isdir",
        lambda path: path == "/run/cpak/grants" or original_isdir(path),
    )

    winecmd = WineCommand.__new__(WineCommand)
    winecmd.config = config
    winecmd.arguments = ""
    winecmd.command = "wine /run/cpak/grants/id/setup.exe"
    winecmd.cwd = str(capsule_path)
    winecmd.runner_runtime = ""
    winecmd.env = {}

    sandbox = winecmd._get_sandbox_manager()

    assert "/run/cpak/grants" in sandbox.share_paths_ro


def _installer_executor(monkeypatch, exec_type, success_codes, returncode=0):
    """A WineExecutor whose installer exits with `returncode` on the Wine side."""
    if returncode:
        result = Result(False, message=f"Command exited with status {returncode}.")
    else:
        result = Result(True)

    class FakeWineCommand:
        def __init__(self, *_args, **_kwargs):
            self.returncode = None

        def run(self):
            self.returncode = returncode
            return result

    monkeypatch.setattr("runexe.backend.wine.wineprogram.WineCommand", FakeWineCommand)
    monkeypatch.setattr(
        WineExecutor,
        "_WineExecutor__set_monitors",
        lambda _self: None,
    )
    executor = WineExecutor.__new__(WineExecutor)
    executor.config = _make_config()
    executor.exec_type = exec_type
    executor.exec_path = "/tmp/capsules/setup." + exec_type
    executor.args = "/quiet"
    executor.terminal = False
    executor.environment = {}
    executor.pre_script = None
    executor.post_script = None
    executor.pre_script_args = None
    executor.post_script_args = None
    executor.cwd = None
    executor.hide_console = False
    executor.sandbox_override = None
    executor.success_codes = success_codes
    return executor


def _launch(executor):
    if executor.exec_type == "msi":
        return executor._WineExecutor__launch_msi()
    return executor._WineExecutor__launch_with_starter()


@pytest.mark.parametrize("exec_type", ["exe", "msi"])
@pytest.mark.parametrize(
    ("returncode", "ok"),
    [
        (0, True),
        # 3010 and 1641 as Wine reports them: one byte.
        (194, True),
        (105, True),
        (67, False),  # 1603, fatal error during installation
        (2, False),
        (1, False),
    ],
)
def test_installer_launch_follows_the_exit_status(
    monkeypatch, exec_type, returncode, ok
):
    executor = _installer_executor(monkeypatch, exec_type, [1641, 3010], returncode)

    result = _launch(executor)

    assert result.ok is ok
    assert result.data["returncode"] == returncode
    assert result.message == ("" if ok else f"Command exited with status {returncode}.")


@pytest.mark.parametrize("exec_type", ["exe", "msi"])
def test_launch_without_success_codes_still_ignores_the_exit_status(
    monkeypatch, exec_type
):
    executor = _installer_executor(monkeypatch, exec_type, None, 67)

    assert _launch(executor).ok


def test_installer_launch_failure_without_exit_status_is_kept(monkeypatch):
    # e.g. the "ShellExecuteEx" detection, which fails with an exit status of 0
    executor = _installer_executor(monkeypatch, "exe", [3010])
    monkeypatch.setattr(
        "runexe.backend.wine.wineprogram.WineCommand",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            run=lambda: Result(False, message="ShellExecuteEx exception"),
        ),
    )

    result = _launch(executor)

    assert not result.ok
    assert result.message == "ShellExecuteEx exception"


def test_executable_launch_accepts_configured_exit_status(monkeypatch):
    class Command:
        returncode = 194

        def __init__(self, *_args, **_kwargs):
            pass

        def run(self):
            return Result(False, message="Command exited with status 194.")

    monkeypatch.setattr("runexe.backend.wine.executor.WineCommand", Command)
    executor = _installer_executor(monkeypatch, "exe", [3010])
    executor.exec_path = "setup.exe"

    assert executor._WineExecutor__launch_exe().ok
    executor.success_codes = [0]
    assert not executor._WineExecutor__launch_exe().ok


@pytest.mark.parametrize(
    ("returncode", "codes", "expected"),
    [
        (None, [3010], False),
        (0, [0], False),
        (194, [3010], True),
        (3010, [3010], True),
        (102, [1638], True),
        (1, [3010], False),
        (194, [], False),
        (-62, [3010], False),
        (-151, [1641], False),
        (True, [1], False),
        (450, [3010], False),
        (194, [-62], False),
    ],
)
def test_is_success_exit_code(returncode, codes, expected):
    assert WineExecutor.is_success_exit_code(returncode, codes) is expected


@pytest.mark.parametrize("arch,version,expected", [
    ("win32", "winxp", "winxp"),
    ("win64", "winxp", "winxp64"),
    ("win64", "win7", "win7"),
])
def test_legacy_windows_version_uses_prefix_architecture(monkeypatch, arch, version, expected):
    from runexe.backend.wine import regkeys

    versions = []
    monkeypatch.setattr(regkeys, "WineCfg", lambda config: SimpleNamespace(
        set_windows_version=lambda value: versions.append(value) or Result(True),
    ))
    keys = regkeys.RegKeys.__new__(regkeys.RegKeys)
    keys.config = SimpleNamespace(Arch=arch)

    keys.lg_set_windows(version)

    assert versions == [expected]


@pytest.mark.parametrize("arch,expected", [("win32", "winxp"), ("win64", "winxp64")])
def test_windows_registry_values_match_prefix_architecture(monkeypatch, arch, expected):
    from runexe.backend.wine import regkeys

    bundles = []
    monkeypatch.setattr(regkeys, "WineBoot", lambda config: SimpleNamespace(
        restart=lambda: None, update=lambda: None,
    ))
    keys = regkeys.RegKeys.__new__(regkeys.RegKeys)
    keys.config = SimpleNamespace(Arch=arch)
    keys.reg = SimpleNamespace(remove=lambda *args: None, import_bundle=bundles.append)

    keys.set_windows("winxp")

    values = {item["value"]: item["data"] for item in bundles[0][
        "HKEY_LOCAL_MACHINE\\Software\\Microsoft\\Windows NT\\CurrentVersion"
    ]}
    assert values["CurrentVersion"] == regkeys.win_versions[expected]["CurrentVersion"]
    assert values["CSDVersion"] == regkeys.win_versions[expected]["CSDVersion"]


@pytest.mark.parametrize("status", [3, 4])
def test_wineboot_preserves_the_session_display(monkeypatch, status):
    from runexe.backend.wine.wineboot import WineBoot

    captured = {}
    monkeypatch.setenv("DISPLAY", ":17")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-fixture")
    def launch(self, **kwargs):
        env = winecommand.WineEnv()
        env.add_bundle(kwargs["environment"], override=True)
        captured.update(env.get()["envs"])
        return Result(True)

    monkeypatch.setattr(WineBoot, "launch", launch)

    assert WineBoot(_make_config()).send_status(status).ok
    assert captured["DISPLAY"] == ":17"
    assert captured["WAYLAND_DISPLAY"] == "wayland-fixture"


@pytest.mark.parametrize("output,expected", [
    ("win11\n", "win11"), ("winxp64\n", "winxp64"),
    ("00e4:err:module:use_lsteamclient lsteamclient disabled.\nwin10\n", "win10"),
    ("win11\r\n00ec:fixme:owned diagnostic\r\n", "win11"),
    ("win10\nwin10\n", "win10"), ("win7\nwin10\n", None),
    ("diagnostic mentions win10", None),
    ("not a Windows version", None), (None, None),
])
def test_winecfg_reads_the_effective_windows_version(monkeypatch, output, expected):
    from runexe.backend.wine.winecfg import WineCfg

    calls = []
    def launch(_self, **kwargs):
        calls.append(kwargs)
        return Result(True, output)
    monkeypatch.setattr(WineCfg, "launch", launch)

    result = WineCfg(_make_config()).get_windows_version()

    assert result.ok is (expected is not None)
    if expected:
        assert result.data == expected
    assert calls == [{"args": "-v", "communicate": True, "capture_stderr": True, "action_name": "get_windows_version"}]


@pytest.mark.parametrize(
    "location",
    [
        "lib/wine/x86_64-unix",
        "lib64/wine/x86_64-unix",
        "lib/wine/i386-unix",
        "lib/wine/aarch64-unix",
        "lib64/wine/aarch64-unix",
    ],
)
def test_identity_bridge_is_exposed_for_compatible_soda_runner(
    tmp_path, monkeypatch, location
):
    runner = tmp_path / "runner"
    unixlib = (
        runner
        / location
        / "windows.security.authentication.onlineid.so"
    )
    unixlib.parent.mkdir(parents=True)
    unixlib.touch()
    capsule = tmp_path / "capsule"
    capsule.mkdir()
    env = WineEnv(clean=True)
    monkeypatch.setattr(
        winecommand, "start_identity_bridge", lambda context: "/run/user/1000/bridge.sock"
    )

    apply_identity_bridge(env, "soda-11.0-23", str(runner), str(capsule))

    assert env.get()["envs"]["SODA_IDENTITY_BRIDGE_SOCKET"] == (
        "/run/user/1000/bridge.sock"
    )
