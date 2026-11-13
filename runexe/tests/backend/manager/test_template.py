import shutil
from threading import Event

from runexe.backend.managers import manager as manager_module
from runexe.backend.managers import template as template_module
from runexe.backend.managers.manager import Manager
from runexe.backend.managers.template import TemplateManager
from runexe.backend.models.config import CapsuleConfig
from runexe.backend.utils import yaml


def test_unpack_missing_template_returns_false(monkeypatch, tmp_path):
    templates = tmp_path / "templates"
    capsules = tmp_path / "capsules"
    templates.mkdir()
    (capsules / "Test" / "drive_c").mkdir(parents=True)
    monkeypatch.setattr(template_module.Paths, "templates", str(templates))
    monkeypatch.setattr(template_module.Paths, "capsules", str(capsules))
    config = CapsuleConfig(Name="Test", Path="Test", Custom_Path=False)

    result = TemplateManager.unpack_template({"uuid": "missing"}, config)

    assert result is False


def test_unpack_template_returns_true(monkeypatch, tmp_path):
    templates = tmp_path / "templates"
    capsules = tmp_path / "capsules"
    source = templates / "template-id" / "drive_c" / "windows"
    source.mkdir(parents=True)
    (source / "system.ini").write_text("template")
    (capsules / "Test" / "drive_c").mkdir(parents=True)
    monkeypatch.setattr(template_module.Paths, "templates", str(templates))
    monkeypatch.setattr(template_module.Paths, "capsules", str(capsules))
    config = CapsuleConfig(Name="Test", Path="Test", Custom_Path=False)

    result = TemplateManager.unpack_template({"uuid": "template-id"}, config)

    assert result is True
    assert (capsules / "Test" / "drive_c" / "windows" / "system.ini").read_text() == (
        "template"
    )


def test_unpack_partial_template_returns_false(monkeypatch, tmp_path):
    def fail_copy(*_args, **_kwargs):
        raise shutil.Error([])

    templates = tmp_path / "templates"
    capsules = tmp_path / "capsules"
    templates.mkdir()
    (capsules / "Test" / "drive_c").mkdir(parents=True)
    monkeypatch.setattr(template_module.Paths, "templates", str(templates))
    monkeypatch.setattr(template_module.Paths, "capsules", str(capsules))
    monkeypatch.setattr(
        template_module.shutil,
        "copytree",
        fail_copy,
    )
    config = CapsuleConfig(Name="Test", Path="Test", Custom_Path=False)

    result = TemplateManager.unpack_template({"uuid": "partial"}, config)

    assert result is False


def test_create_capsule_recovers_from_failed_template(monkeypatch, tmp_path):
    cancel_event = Event()
    captured = {}

    class WineBoot:
        def __init__(self, config):
            captured["config"] = config

        def init(self):
            cancel_event.set()

    manager = object.__new__(Manager)
    manager.runners_available = ["soda-11.0"]
    manager.dxvk_available = ["dxvk-2.7"]
    manager.vkd3d_available = ["vkd3d-3.0"]

    template = {
        "uuid": "missing",
        "config": {
            "Installed_Dependencies": ["template-dependency"],
            "Uninstallers": {"template": {}},
        },
    }

    def unpack_template(_template, _config):
        (tmp_path / "Fresh" / "partial-template-file").write_text("partial")
        return False

    monkeypatch.setattr(manager_module.Paths, "capsules", str(tmp_path))
    monkeypatch.setattr(manager_module.FileUtils, "chattr_f", lambda _path: None)
    monkeypatch.setattr(
        manager_module.TemplateManager,
        "get_env_template",
        lambda _environment: template,
    )
    monkeypatch.setattr(
        manager_module.TemplateManager,
        "unpack_template",
        unpack_template,
    )
    monkeypatch.setattr(manager_module, "Reg", lambda _config: object())
    monkeypatch.setattr(manager_module, "RegKeys", lambda _config: object())
    monkeypatch.setattr(manager_module, "WineBoot", WineBoot)
    monkeypatch.setattr(manager_module, "WineServer", lambda _config: object())

    result = Manager.create_capsule(
        manager,
        name="Fresh",
        environment="custom",
        runner="soda-11.0",
        dxvk="dxvk-2.7",
        vkd3d="vkd3d-3.0",
        cancel_event=cancel_event,
    )

    assert result.ok is False
    assert captured["config"].Installed_Dependencies == []
    assert captured["config"].Uninstallers == {}
    assert not (tmp_path / "Fresh" / "partial-template-file").exists()
    assert (tmp_path / "Fresh" / "drive_c").is_dir()


def test_validate_template_does_not_follow_symlinks(monkeypatch, tmp_path):
    template_uuid = "test-template"
    template_path = tmp_path / template_uuid
    (template_path / "drive_c/ProgramData").mkdir(parents=True)
    windows_path = template_path / "drive_c/windows"
    windows_path.mkdir()

    large_file = windows_path / "system.dat"
    large_file.touch()

    mono_path = windows_path / "mono/mono-2.0/lib/mono/4.0"
    mono_path.mkdir(parents=True)
    (mono_path / "Mono.Posix.dll").symlink_to("missing-target.dll")

    with (template_path / "template.yml").open("w") as file:
        yaml.dump({"uuid": template_uuid}, file)

    monkeypatch.setattr(template_module.Paths, "templates", str(tmp_path))

    assert TemplateManager._TemplateManager__validate_template(template_uuid) is False

    with large_file.open("wb") as file:
        file.truncate(300_000_000)

    assert TemplateManager._TemplateManager__validate_template(template_uuid) is True


def test_new_template_does_not_modify_capsule_config(monkeypatch, tmp_path):
    capsule = tmp_path / "Capsule"
    (capsule / "drive_c/windows").mkdir(parents=True)
    templates = tmp_path / "templates"
    templates.mkdir()
    config = CapsuleConfig(Name="Capsule", Path=str(capsule))

    monkeypatch.setattr(template_module.Paths, "templates", str(templates))
    monkeypatch.setattr(TemplateManager, "get_templates", lambda: [])
    monkeypatch.setattr(
        TemplateManager,
        "_TemplateManager__validate_template",
        lambda _uuid: True,
    )
    monkeypatch.setattr(
        template_module.ManagerUtils,
        "get_capsule_path",
        lambda _config: str(capsule),
    )

    TemplateManager.new("Custom", config)

    assert config.Name == "Capsule"
    assert config.Path == str(capsule)
    assert hasattr(config, "Creation_Date")
    assert hasattr(config, "Update_Date")
