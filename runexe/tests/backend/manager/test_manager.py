"""Core Manager tests"""

import contextlib
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from runexe.backend.managers import manager as manager_module
from runexe.backend.managers.data import DataManager, UserDataKeys
from runexe.backend.managers.manager import Manager
from runexe.backend.models.config import CapsuleConfig
from runexe.backend.utils.connection import ConnectionUtils
from runexe.backend.utils.gsettings_stub import GSettingsStub
from runexe.backend.utils.manager import ManagerUtils


@pytest.fixture(autouse=True)
def reset_manager_singleton():
    existing = Manager._instances.pop(Manager, None)
    if existing and getattr(existing, "playtime_tracker", None):
        with contextlib.suppress(Exception):
            existing.playtime_tracker.shutdown()

    yield

    existing = Manager._instances.pop(Manager, None)
    if existing and getattr(existing, "playtime_tracker", None):
        with contextlib.suppress(Exception):
            existing.playtime_tracker.shutdown()


def test_manager_is_singleton():
    assert Manager(is_cli=True) is Manager(
        is_cli=True
    ), "Manager should be singleton object"
    assert Manager(is_cli=True) is Manager(
        g_settings=GSettingsStub(), is_cli=True
    ), "Manager should be singleton even with different argument"


def test_manager_default_gsettings_stub():
    assert Manager().settings.get_boolean("anything") is False


def test_delete_sandboxed_capsule_does_not_restart_wine(
    mocker, monkeypatch, tmp_path
):
    capsules_path = tmp_path / "capsules"
    capsule_path = capsules_path / "Sandboxed"
    capsule_path.mkdir(parents=True)
    config = CapsuleConfig(
        Name="Sandboxed",
        Path="Sandboxed",
        Runner="soda-11.0-5",
        Environment="Application",
    )
    config.Parameters.sandbox = True

    monkeypatch.setattr(manager_module.Paths, "capsules", str(capsules_path))
    monkeypatch.setattr(
        manager_module.Paths, "applications", str(tmp_path / "applications")
    )
    wineboot = mocker.patch.object(manager_module, "WineBoot")
    wineserver = mocker.patch.object(manager_module, "WineServer").return_value
    library = mocker.patch.object(manager_module, "LibraryManager").return_value
    signal_send = mocker.patch.object(manager_module.SignalManager, "send")
    manager = object.__new__(Manager)
    manager.check_capsules = mocker.Mock()

    assert manager.delete_capsule(config) is True
    assert not capsule_path.exists()
    wineboot.assert_not_called()
    wineserver.force_kill.assert_called_once_with()
    wineserver.wait.assert_not_called()
    library.remove_capsule_entries.assert_called_once_with("Sandboxed")
    manager.check_capsules.assert_called_once_with(silent=True)
    signal_send.assert_not_called()


def test_manager_cli_checks_connection_when_online(mocker):
    check_connection = mocker.patch.object(
        ConnectionUtils,
        "check_connection",
        autospec=True,
        return_value=True,
    )

    Manager(is_cli=True)
    check_connection.assert_called_once()


def test_manager_forced_offline_setting_skips_connection_check(mocker):
    class ForcedOfflineSettings(GSettingsStub):
        @staticmethod
        def get_boolean(key: str) -> bool:
            if key == "force-offline":
                return True
            return GSettingsStub.get_boolean(key)

    class EmptyChecksResult:
        data = {}

    check_connection = mocker.patch.object(
        ConnectionUtils,
        "check_connection",
        autospec=True,
        return_value=True,
    )
    checks = mocker.patch.object(
        Manager,
        "checks",
        autospec=True,
        return_value=EmptyChecksResult(),
    )

    manager = Manager(g_settings=ForcedOfflineSettings(), is_cli=False)

    assert manager.utils_conn.force_offline is True
    check_connection.assert_not_called()
    checks.assert_called_once()


def test_check_runners_ignores_failed_system_wine(mocker):
    run = mocker.patch.object(manager_module.subprocess, "run")
    run.return_value.returncode = 126
    run.return_value.stdout = b""
    mocker.patch.object(manager_module.shutil, "which", return_value="/app/bin/wine")
    mocker.patch.object(manager_module, "glob", return_value=[])
    manager = object.__new__(Manager)

    assert manager.check_runners(install_latest=False) is True
    assert manager.runners_available == []


def test_check_runners_ignores_unexecutable_system_wine(mocker):
    mocker.patch.object(
        manager_module.subprocess,
        "run",
        side_effect=OSError(8, "Exec format error"),
    )
    mocker.patch.object(manager_module.shutil, "which", return_value="/app/bin/wine")
    mocker.patch.object(manager_module, "glob", return_value=[])
    manager = object.__new__(Manager)

    assert manager.check_runners(install_latest=False) is True
    assert manager.runners_available == []


def test_check_runners_adds_usable_system_wine(mocker):
    run = mocker.patch.object(manager_module.subprocess, "run")
    run.return_value.returncode = 0
    run.return_value.stdout = b"wine-11.0\n"
    mocker.patch.object(manager_module.shutil, "which", return_value="/app/bin/wine")
    mocker.patch.object(manager_module, "glob", return_value=[])
    manager = object.__new__(Manager)

    assert manager.check_runners(install_latest=False) is True
    assert manager.runners_available == ["sys-wine-11.0"]
    run.assert_called_once_with(
        ["/app/bin/wine", "--version"],
        stdout=manager_module.subprocess.PIPE,
        stderr=manager_module.subprocess.PIPE,
    )


def test_manager_rejects_file_as_custom_capsules_path(monkeypatch, tmp_path):
    custom_path = tmp_path / "custom-path"
    custom_path.write_text("")
    default_path = tmp_path / "capsules"

    for name in (
        "runners",
        "runtimes",
        "winebridge",
        "dxvk",
        "vkd3d",
        "templates",
        "temp",
    ):
        monkeypatch.setattr(manager_module.Paths, name, str(tmp_path / name))
    monkeypatch.setattr(manager_module.Paths, "capsules", str(default_path))
    monkeypatch.setattr(
        DataManager,
        "get",
        lambda _self, key, default=None: (
            str(custom_path) if key == UserDataKeys.CustomCapsulesPath else default
        ),
    )

    manager = Manager(check_connection=False, is_cli=True)
    manager.check_app_dirs()

    assert manager_module.Paths.capsules == str(default_path)
    assert default_path.is_dir()


@pytest.mark.parametrize(
    ("environment", "runners", "expected"),
    [
        ("Application", ["soda-11.0-1", "sys-wine-11.0"], "sys-wine-11.0"),
        ("Application", ["soda-11.0-1"], "sys-wine-10.20"),
        (
            "Application",
            ["soda-11.0-1", "sys-wine-11.0", "sys-wine-current"],
            "sys-wine-10.20",
        ),
    ],
)
def test_check_capsules_updates_versioned_system_runner(
    mocker, monkeypatch, tmp_path, environment, runners, expected
):
    capsules_path = tmp_path / "capsules"
    capsule_path = capsules_path / "Test"
    capsule_path.mkdir(parents=True)
    config_path = capsule_path / "capsule.yml"
    CapsuleConfig(
        Name="Test",
        Path="Test",
        Runner="sys-wine-10.20",
        Environment=environment,
        session_arguments="--from-desktop",
        run_in_terminal=True,
    ).dump(str(config_path))

    manager = object.__new__(Manager)
    manager.runners_available = runners
    manager.settings = GSettingsStub()
    manager.is_cli = True
    wineboot = mocker.patch.object(manager_module, "WineBoot")
    wineserver = mocker.patch.object(manager_module, "WineServer")
    apply_rules = mocker.patch.object(manager_module.RegistryRuleManager, "apply_rules")
    monkeypatch.setattr(manager_module.Paths, "capsules", str(capsules_path))

    manager.check_capsules(silent=True)

    assert manager.local_capsules["Test"].Runner == expected
    assert manager.local_capsules["Test"].session_arguments == ""
    assert manager.local_capsules["Test"].run_in_terminal is False
    persisted = CapsuleConfig.load(str(config_path)).data
    assert persisted.Runner == expected
    assert persisted.session_arguments == "--from-desktop"
    assert persisted.run_in_terminal is True
    wineboot.assert_not_called()
    wineserver.assert_not_called()
    apply_rules.assert_not_called()


@pytest.mark.parametrize("external_drive", (True, False))
def test_loaded_prefix_keeps_user_files_after_account_change(monkeypatch, tmp_path, external_drive):
    capsules = tmp_path / "capsules"
    prefix = capsules / "Portable"
    prefix.mkdir(parents=True)
    drive = tmp_path / "external-drive" if external_drive else prefix / "drive_c"
    profile = drive / "users/source-user"
    profile.mkdir(parents=True)
    if external_drive:
        (prefix / "drive_c").symlink_to(drive)
    (profile / "document.txt").write_text("existing document")
    CapsuleConfig(Name="Portable", Path="Portable", Runner="runner").dump(
        str(prefix / "capsule.yml")
    )
    manager = object.__new__(Manager)
    manager.runners_available = ["runner"]
    manager.settings = GSettingsStub()
    manager.is_cli = True
    monkeypatch.setattr(manager_module.Paths, "capsules", str(capsules))
    monkeypatch.setenv("USER", "destination-user")

    manager.check_capsules(silent=True)

    users = prefix / "drive_c/users"
    if external_drive:
        assert not (users / "destination-user").exists()
    else:
        assert (users / "destination-user").is_symlink()
        assert (users / "destination-user/document.txt").read_text() == "existing document"
    assert profile.is_dir() and not profile.is_symlink()


def test_full_backup_import_preserves_files_for_another_account(monkeypatch, tmp_path):
    from runexe.backend.managers import backup
    from runexe.backend.managers.backup import BackupManager

    source = tmp_path / "source"
    prefix = source / "Portable"
    profile = prefix / "drive_c/users/source-user"
    profile.mkdir(parents=True)
    (profile / "document.txt").write_text("existing document")
    config = CapsuleConfig(Name="Portable", Path="Portable", Runner="runner", Notes="Keep these installation notes.")
    config.dump(str(prefix / "capsule.yml"))
    archive = tmp_path / "portable.tar.gz"
    monkeypatch.setattr(manager_module.Paths, "capsules", str(source))
    assert BackupManager.export_backup(config, "full", str(archive)).ok

    destination = tmp_path / "destination"
    destination.mkdir()
    manager = object.__new__(Manager)
    manager.runners_available = ["runner"]
    manager.settings = GSettingsStub()
    manager.is_cli = True
    monkeypatch.setattr(backup, "Manager", lambda: manager)
    monkeypatch.setattr(manager_module.Paths, "capsules", str(destination))
    monkeypatch.setenv("USER", "destination-user")

    assert BackupManager.import_backup("full", str(archive)).ok

    users = destination / "Portable/drive_c/users"
    assert (users / "destination-user/document.txt").read_text() == "existing document"
    assert (users / "source-user/document.txt").read_text() == "existing document"
    assert not (profile.parent / "destination-user").exists()
    restored = CapsuleConfig.load(str(destination / "Portable/capsule.yml"))
    assert restored.ok
    assert restored.data.Notes == config.Notes


def test_get_programs_preserves_per_program_runtime_options(monkeypatch):
    class Settings:
        @staticmethod
        def get_boolean(_key):
            return False

    class WindowsPath:
        @staticmethod
        def is_windows(_path):
            return False

    manager = object.__new__(Manager)
    manager._programs_cache = {}
    manager.settings = Settings()
    config = CapsuleConfig(
        Name="Test",
        External_Programs={
            "program-id": {
                "id": "program-id",
                "name": "Example",
                "executable": "example.exe",
                "path": "/games/example.exe",
                "environment": {"DXVK_HUD": "fps"},
                "arguments": "--safe-mode",
                "arguments_enabled": False,
                "discrete_gpu": True,
                "sync": "esync",
                "hide_console": True,
                "file_extensions": [".txt", ".json"],
                "automatic_backup": {
                    "enabled": True,
                    "destination": "/backups",
                    "paths": ["%CAPSULE_PATH%/save.dat"],
                    "keep": 3,
                },
            }
        },
    )

    monkeypatch.setattr(manager_module, "glob", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(manager_module, "WinePath", lambda _config: WindowsPath())
    monkeypatch.setattr(
        manager_module.ManagerUtils, "get_capsule_path", lambda _config: "/capsule"
    )

    program = Manager.get_programs(manager, config)[0]

    assert program["environment"] == {"DXVK_HUD": "fps"}
    assert program["arguments"] == "--safe-mode"
    assert program["arguments_enabled"] is False
    assert program["discrete_gpu"] is True
    assert program["sync"] == "esync"
    assert program["hide_console"] is True
    assert program["file_extensions"] == [".txt", ".json"]
    assert program["automatic_backup"] == {
        "enabled": True,
        "destination": "/backups",
        "paths": ["%CAPSULE_PATH%/save.dat"],
        "keep": 3,
    }


def test_create_capsule_checks_every_essential_component_before_retry():
    manager = object.__new__(Manager)
    manager.runners_available = []
    manager.dxvk_available = []
    manager.vkd3d_available = []
    calls = []

    def make_available(name, values):
        def check():
            calls.append(name)
            values.append(name)
            return True

        return check

    manager.check_runners = make_available("runner", manager.runners_available)
    manager.check_dxvk = make_available("dxvk", manager.dxvk_available)
    manager.check_vkd3d = make_available("vkd3d", manager.vkd3d_available)
    manager.organize_components = lambda: None
    cancelled = Event()
    cancelled.set()

    result = manager.create_capsule(
        name="Test",
        environment="application",
        cancel_event=cancelled,
    )

    assert not result.ok
    assert calls == ["runner", "dxvk", "vkd3d"]


def test_create_capsule_rejects_win32_for_wow64_only_runner(tmp_path, monkeypatch):
    runner = tmp_path / "runner"
    ntdll = runner / "lib/wine/x86_64-unix/ntdll.so"
    ntdll.parent.mkdir(parents=True)
    ntdll.touch()
    monkeypatch.setattr(ManagerUtils, "get_runner_path", lambda *_args: str(runner))
    manager = object.__new__(Manager)
    manager.runners_available = ["runner"]
    manager.dxvk_available = ["dxvk"]
    manager.vkd3d_available = ["vkd3d"]
    messages = []

    result = manager.create_capsule(
        name="Test", environment="application", runner="runner",
        arch="win32", path=str(tmp_path), fn_logger=messages.append,
    )

    assert not result.ok
    assert "32-bit prefix" in result.message
    assert result.message in messages
    assert not (tmp_path / "Test").exists()


class ComponentUpdateSettings:
    def __init__(self, show_updates=True, release_candidate=False):
        self.show_updates = show_updates
        self.release_candidate = release_candidate

    def get_boolean(self, key: str) -> bool:
        return {
            "show-component-updates": self.show_updates,
            "release-candidate": self.release_candidate,
        }[key]


def test_component_updates_can_be_disabled_globally(mocker):
    manager = object.__new__(Manager)
    manager.settings = ComponentUpdateSettings(show_updates=False)
    manager._Manager__collect_runner_update = mocker.Mock()
    manager._Manager__collect_winebridge_update = mocker.Mock()
    config = CapsuleConfig()

    assert Manager.get_component_updates(manager, config) == []
    manager._Manager__collect_runner_update.assert_not_called()
    manager._Manager__collect_winebridge_update.assert_not_called()


def test_component_update_checks_are_enabled_by_default():
    manager = object.__new__(Manager)
    manager.settings = ComponentUpdateSettings()
    manager.supported_wine_runners = {"soda-11.0-1": {}}
    manager.supported_proton_runners = {}
    config = CapsuleConfig(Runner="soda-10.0-1")

    assert Manager.get_component_updates(manager, config) == [
        {
            "id": "runner",
            "title": "Runner",
            "current": "soda-10.0-1",
            "latest": "soda-11.0-1",
            "component_type": "runner",
        }
    ]


def test_custom_runner_is_not_compared_with_an_unrelated_family():
    manager = object.__new__(Manager)
    manager.settings = GSettingsStub()
    manager.supported_wine_runners = {"wine-ge-proton8-27-lol": {}}
    manager.supported_proton_runners = {}

    assert manager._Manager__latest_runner_for("wine-10.19-staging-tkg-amd64") == (
        None,
        "",
    )


def _make_update_manager(release_candidate: bool) -> Manager:
    manager = object.__new__(Manager)
    manager.settings = ComponentUpdateSettings(release_candidate=release_candidate)
    manager.supported_wine_runners = {}
    manager.supported_proton_runners = {}
    manager.supported_dxvk = {}
    manager.supported_vkd3d = {}
    manager.supported_winebridge = {}
    manager.winebridge_available = []
    return manager


@pytest.mark.parametrize("prerelease_channel", ["rc", "unstable"])
@pytest.mark.parametrize(
    ("release_candidate", "expected"),
    [(False, "dxvk-2.7.1"), (True, "dxvk-3.0-1")],
)
def test_component_updates_respect_release_channel(
    prerelease_channel, release_candidate, expected
):
    manager = _make_update_manager(release_candidate)
    manager.supported_dxvk = {
        "dxvk-3.0-1": {"Channel": prerelease_channel},
        "dxvk-2.7.1": {"Channel": "stable"},
    }
    config = CapsuleConfig(DXVK="dxvk-2.6.2")
    config.Parameters.dxvk = True

    updates = Manager.get_component_updates(manager, config)

    assert {update["id"]: update["latest"] for update in updates} == {"dxvk": expected}


@pytest.mark.parametrize(
    ("release_candidate", "expected"),
    [(False, "soda-9.0-2"), (True, "soda-10.0-1")],
)
def test_runner_updates_respect_release_channel(release_candidate, expected):
    manager = _make_update_manager(release_candidate)
    manager.supported_wine_runners = {
        "soda-10.0-1": {"Channel": "unstable"},
        "soda-9.0-2": {"Channel": "stable"},
    }
    config = CapsuleConfig(Runner="soda-9.0-1")

    updates = Manager.get_component_updates(manager, config)

    assert {update["id"]: update["latest"] for update in updates} == {
        "runner": expected
    }


def test_get_programs_can_refresh_cached_results(monkeypatch):
    class Settings:
        @staticmethod
        def get_boolean(_key):
            return False

    class WindowsPath:
        @staticmethod
        def is_windows(_path):
            return False

    cached = [{"name": "Cached program"}]
    manager = object.__new__(Manager)
    manager._programs_cache = {"Test": cached}
    manager.settings = Settings()
    config = CapsuleConfig(Name="Test")

    monkeypatch.setattr(manager_module, "glob", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(manager_module, "WinePath", lambda _config: WindowsPath())
    monkeypatch.setattr(
        manager_module.ManagerUtils, "get_capsule_path", lambda _config: "/capsule"
    )

    assert Manager.get_programs(manager, config) is cached
    assert Manager.get_programs(manager, config, force_update=True) == []


@pytest.mark.parametrize("target_available", [True, False])
def test_custom_prefix_without_placeholder_uses_its_current_config(monkeypatch, tmp_path, target_available):
    capsules = tmp_path / "capsules"
    local = capsules / "Imported"
    local.mkdir(parents=True)
    target = tmp_path / "external" / "Imported"
    stale = CapsuleConfig(Name="Imported", Path=str(target), Custom_Path=True, Runner="old-runner")
    stale.dump(str(local / "capsule.yml"))
    local_before = (local / "capsule.yml").read_bytes()
    if target_available:
        target.mkdir(parents=True)
        current = stale.copy()
        current.Runner = "new-runner"
        current.Environment_Variables = {"KEEP_SETTING": "yes"}
        current.dump(str(target / "capsule.yml"))
        target_before = (target / "capsule.yml").read_bytes()
    manager = object.__new__(Manager)
    manager.runners_available = ["new-runner"]
    manager.settings = GSettingsStub()
    manager.is_cli = True
    monkeypatch.setattr(manager_module.Paths, "capsules", str(capsules))

    manager.check_capsules(silent=True)

    assert (local / "capsule.yml").read_bytes() == local_before
    if target_available:
        config = manager.local_capsules["Imported"]
        assert config.Runner == "new-runner"
        assert config.Environment_Variables == {"KEEP_SETTING": "yes"}
        assert (target / "capsule.yml").read_bytes() == target_before
        manager.check_capsules(silent=True)
        assert manager.local_capsules["Imported"].Runner == "new-runner"
    else:
        assert manager.local_capsules == {}
        assert not target.exists()


def test_prefix_creation_reports_wineboot_failure_before_waiting(monkeypatch, tmp_path):
    from runexe.backend.models.result import Result

    capsules = tmp_path / "capsules"
    capsules.mkdir()
    manager = object.__new__(Manager)
    for attribute in ("runners_available", "dxvk_available", "vkd3d_available"):
        setattr(manager, attribute, ["fixture"])
    monkeypatch.setattr(manager_module.Paths, "capsules", str(capsules))
    monkeypatch.setattr(manager_module.FileUtils, "chattr_f", lambda path: None)
    monkeypatch.setattr(manager_module.WineUtils, "ensure_user_profile_alias", lambda path: True)
    monkeypatch.setattr(manager_module, "WineBoot", lambda config: SimpleNamespace(
        init=lambda: Result(False, message="Command exited with status 23."),
    ))
    def unexpected_wait(*args, **kwargs):
        raise AssertionError("Waited for prefix files after wineboot failed")
    monkeypatch.setattr(manager_module.FileUtils, "wait_for_files", unexpected_wait)
    monkeypatch.setattr(manager_module.logging, "error", lambda *args, **kwargs: None)
    messages = []

    result = manager.create_capsule(
        name="Test", environment="custom", runner="fixture", arch="win64",
        configuration=CapsuleConfig(), fn_logger=messages.append,
    )

    assert not result.ok
    assert "status 23" in result.message
    assert result.message in messages
