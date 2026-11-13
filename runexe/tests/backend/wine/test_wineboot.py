from runexe.backend.models.config import CapsuleConfig
from runexe.backend.wine.wineboot import WineBoot
from runexe.backend.wine.winecommand import WineCommand


def test_wineboot_disables_unmanaged_mono(monkeypatch):
    launches = []

    class FakeWineCommand:
        def __init__(self, _config, **kwargs):
            launches.append(kwargs)

        def run(self):
            return None

    monkeypatch.setattr(
        "runexe.backend.wine.wineprogram.WineCommand",
        FakeWineCommand,
    )
    monkeypatch.setattr(
        "runexe.backend.wine.wineboot.WineServer.is_alive", lambda _self: True
    )
    wineboot = WineBoot(CapsuleConfig())

    wineboot.init()
    wineboot.update()
    wineboot.restart()
    wineboot.shutdown()
    wineboot.send_status(11)
    wineboot.send_status(12)
    wineboot.force()
    wineboot.kill()

    assert [launch["command"] for launch in launches] == [
        "wineboot -i /nogui",
        "wineboot -u /nogui",
        "wineboot -r /nogui",
        "wineboot -s /nogui",
        "wineboot -e -f -k -r /nogui",
        "wineboot -e -f -k -s /nogui",
        "wineboot force /nogui",
        "wineboot -k /nogui",
    ]
    assert [launch.get("forced_dll_overrides") for launch in launches] == [
        "mscoree=d",
        "mscoree=d",
        None,
        None,
        None,
        None,
        None,
        None,
    ]
    assert all(
        launch["environment"]["WINEDLLOVERRIDES"] == "winemenubuilder=d"
        for launch in launches
    )


def test_forced_dll_overrides_take_precedence(monkeypatch, tmp_path):
    config = CapsuleConfig(
        Path=str(tmp_path),
        Runner="sys-wine",
        DLL_Overrides={"mscoree": "n,b"},
    )
    monkeypatch.setattr(
        "runexe.backend.wine.winecommand.ManagerUtils.get_capsule_path",
        lambda _config: str(tmp_path),
    )
    monkeypatch.setattr(
        "runexe.backend.wine.winecommand.ManagerUtils.get_runner_path",
        lambda _runner: "sys-wine",
    )
    monkeypatch.setattr(
        "runexe.backend.wine.winecommand.DisplayUtils.check_nvidia_device",
        lambda: None,
    )
    monkeypatch.setattr(
        "runexe.backend.wine.winecommand.GPUUtils.get_gpu",
        lambda _self: {"prime": {"discrete": None, "integrated": None}, "vendors": {}},
    )
    monkeypatch.setattr(
        "runexe.backend.wine.winecommand.RuntimeManager.get_runtime_env",
        lambda *_: [],
    )

    winecmd = WineCommand.__new__(WineCommand)
    winecmd.config = config
    winecmd.minimal = True
    winecmd.arguments = ""
    winecmd.runner = "/usr/bin/wine"
    winecmd.runner_runtime = ""
    winecmd.terminal = False
    winecmd.forced_dll_overrides = "mscoree=d"

    env = winecmd.get_env()

    assert env["WINEDLLOVERRIDES"].split(";")[-1] == "mscoree=d"
    assert config.DLL_Overrides == {"mscoree": "n,b"}
