# ruff: noqa: E402

from types import SimpleNamespace

import pytest
from gi.repository import Gio

capsules_resource = Gio.Resource.load("/app/share/runexe/runexe.gresource")
capsules_resource._register()

from runexe.backend.models.config import CapsuleConfig
from runexe.frontend.views import capsule_details
from runexe.frontend.views.capsule_details import CapsuleView


class WidgetStub:
    def __init__(self):
        self.text = None

    def set_text(self, text):
        self.text = text

    def set_tooltip_text(self, _text):
        pass

    def set_visible(self, _visible):
        pass


def test_flatpak_system_runner_uses_builtin_label(monkeypatch):
    runner_label = WidgetStub()
    widget = WidgetStub()
    config = CapsuleConfig(
        Name="Test",
        Runner="sys-wine-11.0",
        Environment="Application",
        Update_Date="2026-01-01 00:00:00.000000",
    )
    view = SimpleNamespace(
        manager=SimpleNamespace(
            runners_available=["sys-wine-11.0"],
            versioning_manager=SimpleNamespace(needs_migration=lambda _config: False),
        ),
        label_name=widget,
        label_arch=widget,
        label_runner=runner_label,
        img_runner=widget,
        set_runner_identity=lambda runner: CapsuleView.set_runner_identity(view, runner),
        label_environment=widget,
        dot_versioning=widget,
        btn_versioning_badge=widget,
        label_state=widget,
        _CapsuleView__update_by_env=lambda: None,
        update_programs=lambda: None,
        populate_updates=lambda: None,
    )
    monkeypatch.setenv("FLATPAK_ID", "io.github.runexe.RunExe")
    monkeypatch.setattr(
        capsule_details.ManagerUtils,
        "get_capsule_path",
        lambda _config: "/tmp/test-capsule",
    )
    monkeypatch.setattr(capsule_details.os.path, "exists", lambda _path: False)

    CapsuleView.set_config(view, config)

    assert runner_label.text == "Built-in Wine 11.0"


def test_missing_runner_dialog_waits_for_versioning_upgrade(monkeypatch):
    events = []
    callbacks = []
    widget = WidgetStub()
    config = CapsuleConfig(
        Name="Test",
        Runner="missing-runner",
        Environment="Application",
        Update_Date="2026-01-01 00:00:00.000000",
        Versioning=True,
    )

    def show_upgrade(on_close=None):
        events.append("upgrade")
        callbacks.append(on_close)

    view = SimpleNamespace(
        manager=SimpleNamespace(
            runners_available=[],
            versioning_manager=SimpleNamespace(needs_migration=lambda _config: True),
        ),
        label_name=widget,
        label_arch=widget,
        label_runner=widget,
        img_runner=widget,
        set_runner_identity=lambda runner: CapsuleView.set_runner_identity(view, runner),
        label_environment=widget,
        dot_versioning=widget,
        btn_versioning_badge=widget,
        label_state=widget,
        _CapsuleView__update_by_env=lambda: None,
        _CapsuleView__upgrade_versioning=show_upgrade,
        _CapsuleView__alert_missing_runner=lambda: events.append("missing-runner"),
        update_programs=lambda: None,
        populate_updates=lambda: None,
    )

    monkeypatch.setattr(
        capsule_details.ManagerUtils,
        "get_capsule_path",
        lambda _config: "/tmp/test-capsule",
    )
    monkeypatch.setattr(capsule_details.os.path, "exists", lambda _path: False)

    CapsuleView.set_config(view, config)

    assert events == ["upgrade"]
    assert callbacks[0] is not None

    callbacks[0]()

    assert events == ["upgrade", "missing-runner"]


def test_versioning_upgrade_schedules_follow_up_after_close(monkeypatch):
    events = []
    connections = []

    class DialogStub:
        def connect(self, signal, callback):
            events.append("connect")
            connections.append((signal, callback))

        def present(self):
            events.append("present")

    dialog = DialogStub()
    monkeypatch.setattr(
        capsule_details,
        "UpgradeVersioningDialog",
        lambda _parent: dialog,
    )
    monkeypatch.setattr(
        capsule_details.GLib,
        "idle_add",
        lambda callback: events.append("scheduled") or callback(),
    )

    CapsuleView._CapsuleView__upgrade_versioning(
        SimpleNamespace(),
        lambda: events.append("missing-runner"),
    )

    assert events == ["connect", "present"]
    assert connections[0][0] == "close-request"

    assert connections[0][1](dialog) is False
    assert events == ["connect", "present", "scheduled", "missing-runner"]


@pytest.mark.parametrize("force_update", [False, True])
def test_update_programs_controls_cache_refresh(monkeypatch, force_update):
    calls = []
    config = CapsuleConfig(Name="Test")
    manager = SimpleNamespace(
        get_programs=lambda _config, **kwargs: calls.append(kwargs) or []
    )
    view = SimpleNamespace(
        config=config,
        manager=manager,
        empty_list=lambda: None,
        row_no_programs=SimpleNamespace(set_visible=lambda _visible: None),
        show_hidden=False,
    )

    monkeypatch.setattr(capsule_details.GLib, "idle_add", lambda *_args: None)
    monkeypatch.setattr(
        capsule_details,
        "WineServer",
        lambda _config: SimpleNamespace(is_alive=lambda: False),
    )

    CapsuleView.update_programs(view, force_update=force_update)

    assert calls == [{"force_update": force_update}]


def test_populate_updates_hides_empty_group():
    visibility = []
    view = SimpleNamespace(
        config=CapsuleConfig(Name="Test"),
        manager=SimpleNamespace(
            settings=SimpleNamespace(get_boolean=lambda _key: True),
            get_component_updates=lambda _config: [],
        ),
        group_updates=SimpleNamespace(
            remove=lambda _row: None,
            set_visible=visibility.append,
        ),
        _CapsuleView__update_rows=[],
    )

    CapsuleView.populate_updates(view)

    assert visibility == [False]
