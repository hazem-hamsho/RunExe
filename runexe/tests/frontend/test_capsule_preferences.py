import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from gi.repository import Gio

from runexe.backend.models.config import CapsuleConfig

resource_path = Path(os.environ.get("RUNEXE_TEST_RESOURCE", "/app/share/runexe/runexe.gresource"))
if not resource_path.exists():
    pytest.skip(
        "Capsule preferences tests require the RunExe Flatpak runtime",
        allow_module_level=True,
    )
Gio.resources_register(Gio.Resource.load(str(resource_path)))


def _make_view(config):
    return SimpleNamespace(config=config, switch_hdr=Mock())


@pytest.mark.parametrize("success", [False, True])
def test_capsule_notes_are_kept_when_saving_fails(success):
    from gi.repository import Gtk
    from runexe.backend.models.result import Result
    from runexe.frontend.views.capsule_preferences import PreferencesView

    config = CapsuleConfig(Name="Test")
    text = Gtk.TextView()
    notes = "Installation notes\nDo not discard this text."
    text.get_buffer().set_text(notes)
    button = Gtk.Button()
    manager = SimpleNamespace(update_config=Mock(return_value=Result(success, {"config": config})))
    view = SimpleNamespace(config=config, text_notes=text, btn_save_notes=button, manager=manager, window=SimpleNamespace(show_toast=Mock()))

    PreferencesView._PreferencesView__save_notes(view, button)

    manager.update_config.assert_called_once_with(config, key="Notes", value=notes)
    assert button.get_sensitive() is not success
    buffer = text.get_buffer()
    assert buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True) == notes
    assert view.window.show_toast.called is not success


def test_unchanged_sync_does_not_update_capsule(monkeypatch):
    from runexe.frontend.views import capsule_preferences
    from runexe.frontend.views.capsule_preferences import PreferencesView

    config = CapsuleConfig()
    config.Parameters.sync = "ntsync"
    view = SimpleNamespace(
        config=config,
        combo_sync=Mock(),
        manager=Mock(),
        queue=Mock(),
    )
    view.combo_sync.get_selected.return_value = 3
    run_async = Mock()
    monkeypatch.setattr(capsule_preferences, "RunAsync", run_async)

    PreferencesView._PreferencesView__set_sync_type(view)

    run_async.assert_not_called()
    view.manager.update_config.assert_not_called()
    view.combo_sync.set_sensitive.assert_not_called()
    view.queue.add_task.assert_not_called()


@pytest.mark.parametrize("error", (None, ValueError("winecfg failed")))
def test_windows_version_is_saved_only_after_winecfg_succeeds(monkeypatch, error):
    from gi.repository import Adw, GLib, Gtk

    from runexe.frontend.views import capsule_preferences
    from runexe.frontend.views.capsule_preferences import PreferencesView

    Adw.init()
    config = CapsuleConfig(Windows="win10")
    combo = Adw.ComboRow(model=Gtk.StringList.new(["Windows 10", "Windows 11"]))
    combo.set_selected(0)
    view = SimpleNamespace(
        config=config,
        combo_windows=combo,
        windows_versions={"win10": "Windows 10", "win11": "Windows 11"},
        manager=Mock(),
        queue=Mock(),
        spinner_windows=Mock(),
    )
    view._PreferencesView__set_windows = lambda *_args: (
        PreferencesView._PreferencesView__set_windows(view)
    )
    combo.connect("notify::selected", view._PreferencesView__set_windows)
    run_async = Mock()
    monkeypatch.setattr(capsule_preferences, "RunAsync", run_async)
    monkeypatch.setattr(capsule_preferences, "RegKeys", Mock())

    def persist(**kwargs):
        config.Windows = kwargs["value"]
        return SimpleNamespace(data={"config": config})

    view.manager.update_config.side_effect = persist
    combo.set_selected(1)

    assert config.Windows == "win10"
    view.manager.update_config.assert_not_called()
    assert not combo.get_sensitive()
    callback = run_async.call_args.kwargs["callback"]
    callback(None, error)
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)

    if error:
        assert config.Windows == "win10"
        assert combo.get_selected() == 0
        view.manager.update_config.assert_not_called()
    else:
        assert config.Windows == "win11"
        assert combo.get_selected() == 1
        view.manager.update_config.assert_called_once_with(
            config=config, key="Windows", value="win11"
        )
    assert run_async.call_count == 1
    assert combo.get_sensitive()
    view.queue.end_task.assert_called_once_with()
    view.spinner_windows.stop.assert_called_once_with()


@pytest.mark.parametrize(
    ("runner", "supported"),
    [
        ("soda-11.0-4", False),
        ("soda-11.0-5", True),
        ("soda-12.0-1", True),
        ("protosoda-11.0-1", False),
        ("wine-ge-8-26", False),
    ],
)
def test_adaptive_launch_requires_soda(runner, supported):
    from runexe.frontend.views.capsule_preferences import PreferencesView

    config = CapsuleConfig(Runner=runner)
    view = SimpleNamespace(
        config=config,
        switch_adaptive_launch=Mock(),
        row_adaptive_launch=Mock(),
        _adaptive_launch_warning=Mock(),
        switch_eagle_tracing=Mock(),
        row_eagle_tracing=Mock(),
        _PreferencesView__toggle_feature_cb=Mock(),
    )

    PreferencesView._PreferencesView__update_adaptive_launch_support(view)

    view.switch_adaptive_launch.set_sensitive.assert_called_once_with(supported)
    message = (
        ""
        if supported
        else "Soda 11.0-5 or newer is required to enable Adaptive Launch."
    )
    view.switch_adaptive_launch.set_tooltip_text.assert_called_once_with(message)
    view.row_adaptive_launch.set_tooltip_text.assert_called_once_with(message)
    view._adaptive_launch_warning.set_visible.assert_called_once_with(not supported)


def test_open_eagle_logs_keeps_window_alive_when_directory_is_unwritable(monkeypatch):
    from runexe.frontend.views import capsule_preferences
    from runexe.frontend.views.capsule_preferences import PreferencesView

    def denied(*args, **kwargs):
        raise PermissionError("log directory is not writable")

    monkeypatch.setattr(
        capsule_preferences.ManagerUtils, "get_capsule_path", lambda _config: "/home/test/capsule"
    )
    monkeypatch.setattr(capsule_preferences.os, "makedirs", denied)
    opened = Mock()
    monkeypatch.setattr(capsule_preferences.ManagerUtils, "open_filemanager", opened)
    view = SimpleNamespace(config=Mock(), window=SimpleNamespace(show_toast=Mock()))
    PreferencesView._PreferencesView__open_eagle_logs(view, None)
    view.window.show_toast.assert_called_once()
    opened.assert_not_called()


def _make_graphics_view(config):
    from runexe.frontend.views.capsule_preferences import PreferencesView

    view = SimpleNamespace(
        config=config,
        manager=Mock(
            dxvk_available=["dxvk-old", "dxvk-new"],
            vkd3d_available=["vkd3d-old", "vkd3d-new"],
        ),
        combo_dxvk=Mock(),
        combo_vkd3d=Mock(),
        spinner_dxvk=Mock(),
        spinner_vkd3d=Mock(),
        queue=Mock(),
    )
    view._PreferencesView__set_dxvk = Mock()
    view._PreferencesView__set_vkd3d = Mock()
    view._PreferencesView__set_graphics_status = lambda *args: (
        PreferencesView._PreferencesView__set_graphics_status(view, *args)
    )
    view.set_dxvk_status = lambda **kwargs: (
        PreferencesView.set_dxvk_status.__wrapped__(view, **kwargs)
    )
    view.set_vkd3d_status = lambda **kwargs: (
        PreferencesView.set_vkd3d_status.__wrapped__(view, **kwargs)
    )
    return view


@pytest.mark.parametrize("component", ["dxvk", "vkd3d"])
def test_graphics_removal_does_not_save_while_pending(monkeypatch, component):
    from runexe.frontend.views import capsule_preferences
    from runexe.frontend.views.capsule_preferences import PreferencesView

    config = CapsuleConfig(DXVK="dxvk-old", VKD3D="vkd3d-old")
    setattr(config.Parameters, component, True)
    view = _make_graphics_view(config)
    getattr(view, f"combo_{component}").get_selected.return_value = 0
    jobs = []
    monkeypatch.setattr(
        capsule_preferences, "RunAsync", lambda **kwargs: jobs.append(kwargs)
    )

    getattr(PreferencesView, f"_PreferencesView__set_{component}")(view)

    assert getattr(config.Parameters, component)
    view.manager.update_config.assert_not_called()
    view.combo_dxvk.set_sensitive.assert_called_once_with(False)
    view.combo_vkd3d.set_sensitive.assert_called_once_with(False)
    assert len(jobs) == 1 and jobs[0]["enabled"] is False


@pytest.mark.parametrize("component", ["dxvk", "vkd3d"])
def test_graphics_failure_restores_selector(component):
    from runexe.backend.models.result import Result

    config = CapsuleConfig(DXVK="dxvk-old", VKD3D="vkd3d-old")
    setattr(config.Parameters, component, True)
    view = _make_graphics_view(config)

    getattr(view, f"set_{component}_status")(status=Result(False, message="failed"))

    getattr(view, f"combo_{component}").set_selected.assert_called_once_with(1)
    getattr(view, f"combo_{component}").handler_block_by_func.assert_called_once()
    getattr(view, f"combo_{component}").handler_unblock_by_func.assert_called_once()
    assert getattr(config.Parameters, component)
    view.queue.end_task.assert_called_once()


def test_graphics_success_updates_both_selectors():
    from runexe.backend.models.result import Result

    view = _make_graphics_view(CapsuleConfig())
    updated = CapsuleConfig(DXVK="dxvk-new", VKD3D="vkd3d-new")
    updated.Parameters.dxvk = True
    updated.Parameters.vkd3d = True

    view.set_vkd3d_status(status=Result(True, data={"config": updated}))

    assert view.config is updated
    view.combo_dxvk.set_selected.assert_called_once_with(2)
    view.combo_vkd3d.set_selected.assert_called_once_with(2)
    view.queue.end_task.assert_called_once()
