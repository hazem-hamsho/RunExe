import subprocess

import pytest

from runexe.backend.models.config import CapsuleConfig
from runexe.backend.wine.wineserver import WineServer


def test_is_alive_skips_missing_capsule_directory(monkeypatch, mocker, tmp_path):
    capsule_path = tmp_path / "missing"
    config = CapsuleConfig(Name="Missing", Runner="sys-wine-11.0")
    popen = mocker.patch("runexe.backend.wine.wineserver.subprocess.Popen")
    monkeypatch.setattr(
        "runexe.backend.wine.wineserver.ManagerUtils.get_capsule_path",
        lambda _config: str(capsule_path),
    )
    monkeypatch.setattr(
        "runexe.backend.wine.wineserver.ManagerUtils.get_runner_path",
        lambda _runner: "/usr/bin",
    )

    assert WineServer(config).is_alive() is False
    popen.assert_not_called()


def test_is_alive_handles_capsule_removed_during_check(monkeypatch, mocker, tmp_path):
    capsule_path = tmp_path / "removed"
    capsule_path.mkdir()
    config = CapsuleConfig(Name="Removed", Runner="sys-wine-11.0")
    mocker.patch(
        "runexe.backend.wine.wineserver.subprocess.Popen",
        side_effect=FileNotFoundError,
    )
    monkeypatch.setattr(
        "runexe.backend.wine.wineserver.ManagerUtils.get_capsule_path",
        lambda _config: str(capsule_path),
    )
    monkeypatch.setattr(
        "runexe.backend.wine.wineserver.ManagerUtils.get_runner_path",
        lambda _runner: "/usr/bin",
    )

    assert WineServer(config).is_alive() is False


def test_is_alive_reaps_timed_out_waiter(monkeypatch, mocker, tmp_path):
    config = CapsuleConfig(Name="Test", Runner="soda-test")
    monkeypatch.setattr(
        "runexe.backend.wine.wineserver.ManagerUtils.get_capsule_path",
        lambda _config: str(tmp_path),
    )
    process = mocker.Mock()
    process.wait.side_effect = [subprocess.TimeoutExpired("wineserver", 0.5), 0]
    run = mocker.patch.object(WineServer, "_run", return_value=process)
    assert WineServer(config).is_alive() is True

    run.assert_called_once_with(["-w"])
    process.kill.assert_called_once_with()
    assert process.wait.call_args_list == [mocker.call(timeout=0.5), mocker.call()]


def test_is_alive_reaps_finished_waiter(monkeypatch, mocker, tmp_path):
    config = CapsuleConfig(Name="Test", Runner="soda-test")
    monkeypatch.setattr(
        "runexe.backend.wine.wineserver.ManagerUtils.get_capsule_path",
        lambda _config: str(tmp_path),
    )
    process = mocker.Mock()
    process.wait.return_value = 0
    run = mocker.patch.object(WineServer, "_run", return_value=process)

    assert WineServer(config).is_alive() is False

    run.assert_called_once_with(["-w"])
    process.wait.assert_called_once_with(timeout=0.5)
    process.kill.assert_not_called()


@pytest.mark.parametrize("signal,argument", [(-1, "-k"), (9, "-k9")])
@pytest.mark.parametrize("kind", ["bundled", "system"])
def test_kill_uses_native_server(monkeypatch, mocker, tmp_path, kind, signal, argument):
    capsule = tmp_path / "Capsule with spaces"
    runner = tmp_path / "Runner with spaces"
    config = CapsuleConfig(Name="Test", Path=str(capsule), Runner="soda-test")
    if kind == "system":
        config.Runner = "sys-wine-11.0"
    monkeypatch.setattr(
        "runexe.backend.wine.wineserver.ManagerUtils.get_capsule_path",
        lambda _config: str(capsule),
    )
    monkeypatch.setattr(
        "runexe.backend.wine.wineserver.ManagerUtils.get_runner_path",
        lambda _runner: str(runner),
    )
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    process = mocker.Mock()
    process.wait.return_value = 0
    popen = mocker.patch(
        "runexe.backend.wine.wineserver.subprocess.Popen", return_value=process
    )
    launch = mocker.patch.object(WineServer, "launch")

    server = WineServer(config)
    server.kill(signal)

    launch.assert_not_called()
    args, kwargs = popen.call_args
    expected = "wineserver"
    if kind != "system":
        expected = str(runner / "bin/wineserver")
    assert args == ([expected, argument],)
    assert kwargs["cwd"] == str(capsule)
    assert kwargs["env"]["WINEPREFIX"] == str(capsule)
    assert kwargs.get("shell", False) is False
    process.wait.assert_called_once_with()
    assert server.returncode == 0


def test_wait_uses_native_server(monkeypatch, mocker, tmp_path):
    config = CapsuleConfig(Name="Test", Runner="soda-test")
    monkeypatch.setattr(
        "runexe.backend.wine.wineserver.ManagerUtils.get_capsule_path",
        lambda _config: str(tmp_path),
    )
    monkeypatch.setattr(
        "runexe.backend.wine.wineserver.ManagerUtils.get_runner_path",
        lambda _runner: str(tmp_path / "Runner with spaces"),
    )
    process = mocker.Mock()
    popen = mocker.patch(
        "runexe.backend.wine.wineserver.subprocess.Popen", return_value=process
    )

    WineServer(config).wait()

    assert popen.call_args.args == (
        [str(tmp_path / "Runner with spaces/bin/wineserver"), "-w"],
    )
    assert popen.call_args.kwargs.get("shell", False) is False
    process.wait.assert_called_once_with()


def test_force_kill_keeps_sandbox_launcher_cleanup(monkeypatch, mocker, tmp_path):
    config = CapsuleConfig(Name="Test", Runner="soda-test")
    config.Parameters.sandbox = True
    monkeypatch.setattr(
        "runexe.backend.wine.wineserver.ManagerUtils.get_capsule_path",
        lambda _config: str(tmp_path),
    )
    terminate = mocker.patch(
        "runexe.backend.managers.sandbox.SandboxManager.terminate_prefix"
    )
    procs = mocker.patch("runexe.backend.wine.wineserver.ProcUtils.get_by_env")
    kill = mocker.patch.object(WineServer, "kill")

    WineServer(config).force_kill()

    terminate.assert_called_once_with(str(tmp_path))
    procs.assert_not_called()
    kill.assert_not_called()


@pytest.mark.parametrize("argument", ["-k", "-w"])
def test_stop_reaps_timed_out_native_server(mocker, argument):
    config = CapsuleConfig(Name="Test", Runner="soda-test")
    process = mocker.Mock()
    process.wait.side_effect = [subprocess.TimeoutExpired("wineserver", 30), 0]
    run = mocker.patch.object(WineServer, "_run", return_value=process)
    server = WineServer(config)
    if argument == "-w":
        mocker.patch.object(server, "kill", side_effect=lambda **_kw: setattr(server, "returncode", 0))

    with pytest.raises(subprocess.TimeoutExpired):
        server.stop()

    run.assert_called_once_with([argument])
    process.kill.assert_called_once_with()
    assert process.wait.call_args_list == [mocker.call(timeout=30), mocker.call()]


@pytest.mark.parametrize("codes,expected", [([0, 0], True), ([1], False), ([0, 1], False)])
def test_stop_checks_native_server_status(mocker, codes, expected):
    config = CapsuleConfig(Name="Test", Runner="soda-test")
    process = mocker.Mock()
    process.wait.side_effect = codes
    run = mocker.patch.object(WineServer, "_run", return_value=process)

    assert WineServer(config).stop() is expected

    assert run.call_args_list == [mocker.call([arg]) for arg in ["-k", "-w"][:len(codes)]]


def test_stop_uses_tracked_sandbox_only(monkeypatch, mocker, tmp_path):
    config = CapsuleConfig(Name="Test", Runner="soda-test")
    config.Parameters.sandbox = True
    monkeypatch.setattr("runexe.backend.wine.wineserver.ManagerUtils.get_capsule_path", lambda _config: str(tmp_path))
    terminate = mocker.patch("runexe.backend.managers.sandbox.SandboxManager.terminate_prefix")
    run = mocker.patch.object(WineServer, "_run")

    assert WineServer(config).stop() is True

    terminate.assert_called_once_with(str(tmp_path), timeout=30)
    run.assert_not_called()
