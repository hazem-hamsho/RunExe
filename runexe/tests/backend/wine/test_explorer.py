from runexe.backend.models.config import CapsuleConfig
from runexe.backend.models.result import Result
from runexe.backend.wine.explorer import Explorer


def test_virtual_desktop_wraps_hidden_program_in_background_start(monkeypatch):
    captured = {}

    def fake_launch(self, **kwargs):
        captured.update(kwargs)
        return Result(True)

    monkeypatch.setattr(Explorer, "launch", fake_launch)

    config = CapsuleConfig(Name="TestCapsule", Path="TestCapsulePath")
    result = Explorer(config).launch_desktop(
        desktop="probe",
        width=1280,
        height=720,
        program=r"'C:\Program Files\Example\example.exe'",
        args="--safe-mode",
        background=True,
        sandbox_override="off",
    )

    assert result.status is True
    assert captured["args"] == (
        r"/desktop=probe,1280x720 start /b /wait "
        r"'C:\Program Files\Example\example.exe' --safe-mode"
    )
    assert captured["sandbox_override"] == "off"
