from io import StringIO

import pytest

from runexe.backend.models.config import CapsuleConfig
from runexe.backend.models.samples import Samples
from runexe.backend.wine.winecommand import WineEnv


def _reload(config):
    stream = StringIO()
    assert config.dump(stream).status is True
    stream.seek(0)
    result = CapsuleConfig.load(stream)
    assert result.status is True
    return result.data


def test_capsule_notes_survive_config_serialization():
    config = CapsuleConfig(Name="Test", Notes="Installation notes\nSecond line: <text> & symbols")

    assert _reload(config).Notes == config.Notes


@pytest.mark.parametrize(
    "excluded_defaults",
    [
        ["XDG_CONFIG_HOME"],
        ["XCURSOR_SIZE", "XDG_CONFIG_HOME"],
        ["XCURSOR_SIZE", "XDG_CONFIG_HOME", "XMODIFIERS"],
        [
            "MANGOHUD_CONFIG",
            "XCURSOR_SIZE",
            "XDG_CONFIG_HOME",
            "XMODIFIERS",
        ],
    ],
)
def test_load_migrates_default_inherited_environment(monkeypatch, excluded_defaults):
    legacy_defaults = [
        name
        for name in Samples.default_inherited_environment
        if name not in excluded_defaults
    ]
    config = CapsuleConfig(
        Limit_System_Environment=True,
        Inherited_Environment_Variables=legacy_defaults,
    )
    monkeypatch.setenv("XMODIFIERS", "@im=fcitx")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/var/config")

    loaded = _reload(config)
    env = WineEnv(allowed_keys=loaded.Inherited_Environment_Variables)

    assert env.get()["envs"]["XMODIFIERS"] == "@im=fcitx"
    assert env.get()["envs"]["XDG_CONFIG_HOME"] == "/var/config"


@pytest.mark.parametrize(
    "limit_environment,inherited",
    [
        (False, []),
        (True, ["DISPLAY"]),
        (True, ["DISPLAY", "CUSTOM_VARIABLE"]),
    ],
)
def test_load_preserves_custom_inherited_environment(limit_environment, inherited):
    config = CapsuleConfig(
        Limit_System_Environment=limit_environment,
        Inherited_Environment_Variables=inherited,
    )

    loaded = _reload(config)

    assert loaded.Inherited_Environment_Variables == inherited


def test_load_discards_legacy_component_update_preference():
    result = CapsuleConfig._fill_with(
        {"Parameters": {"show_component_updates": False}}
    )

    assert result.ok
    assert "show_component_updates" not in result.data.Parameters
