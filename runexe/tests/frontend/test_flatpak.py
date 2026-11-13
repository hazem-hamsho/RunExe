# ruff: noqa: E402

import shlex
from types import SimpleNamespace

import gi
import pytest

gi.require_version("Adw", "1")

from gi.repository import Gio

capsules_resource = Gio.Resource.load("/app/share/runexe/runexe.gresource")
capsules_resource._register()

from runexe.frontend.utils import flatpak
from runexe.frontend.views import new_capsule_dialog, preferences


class FolderStub:
    def get_path(self):
        return "/run/user/1000/doc/id/RunExe"


class FileDialogStub:
    def set_title(self, _title):
        pass

    def set_modal(self, _modal):
        pass

    def select_folder(self, parent, callback):
        callback(self, object())

    def select_folder_finish(self, _result):
        return FolderStub()


class FileChooserNativeStub:
    def __init__(self, response):
        self.response = response

    def set_modal(self, _modal):
        pass

    def connect(self, signal, callback):
        assert signal == "response"
        self.callback = callback

    def show(self):
        self.callback(self, self.response)

    def get_file(self):
        return FolderStub()


class ExpanderStub:
    def __init__(self):
        self.expanded = False
        self.rows = []

    def get_expanded(self):
        return self.expanded

    def add_row(self, row):
        self.rows.append(row)


def test_runner_entries_are_created_on_first_expansion(monkeypatch):
    entries = []
    expander = ExpanderStub()
    runner = {
        "prefix": "soda",
        "count": 0,
        "expander": expander,
        "offline_runners": [],
        "expander_queue": [],
        "expanded": False,
    }
    view = SimpleNamespace(
        window=object(),
        manager=SimpleNamespace(
            get_offline_components=lambda _component_type: ["soda-9.0-1"]
        ),
        _PreferencesWindow__registry=[],
        _PreferencesWindow__display_unstable_candidate=lambda *_args: True,
    )

    monkeypatch.setattr(
        preferences,
        "ComponentEntry",
        lambda _window, component, component_type: entries.append(
            (component, component_type)
        ),
    )

    preferences.PreferencesWindow._PreferencesWindow__populate_runners_helper(
        view, "runner", {}, [runner]
    )
    preferences.PreferencesWindow._PreferencesWindow__on_runner_expander_expanded(
        view, expander, None, runner
    )
    assert entries == []

    expander.expanded = True
    preferences.PreferencesWindow._PreferencesWindow__on_runner_expander_expanded(
        view, expander, None, runner
    )
    preferences.PreferencesWindow._PreferencesWindow__on_runner_expander_expanded(
        view, expander, None, runner
    )

    assert entries == [
        (
            [
                "soda-9.0-1",
                {
                    "Installed": True,
                    "Channel": "unstable",
                    "Category": "runners",
                    "Sub-category": "wine",
                },
            ],
            "runner",
        )
    ]
    assert len(expander.rows) == 1


def test_filesystem_override_command_uses_current_app_id(monkeypatch):
    monkeypatch.setenv("FLATPAK_ID", "io.github.runexe.RunExe.Devel")
    monkeypatch.setattr(
        flatpak.ManagerUtils,
        "get_portal_host_path",
        lambda _path: "/media/Game Drive/$(touch marker); RunExe' Test",
    )

    command = flatpak.get_filesystem_override_command("/run/user/1000/doc/id/path")

    assert shlex.split(command) == [
        "flatpak",
        "override",
        "--user",
        "--filesystem=/media/Game Drive/$(touch marker); RunExe' Test",
        "io.github.runexe.RunExe.Devel",
    ]


def test_filesystem_override_command_requires_flatpak(monkeypatch):
    monkeypatch.delenv("FLATPAK_ID", raising=False)

    assert flatpak.get_filesystem_override_command("/media/Games") is None


def test_filesystem_override_command_requires_host_mapping(monkeypatch):
    monkeypatch.setenv("FLATPAK_ID", "io.github.runexe.RunExe")
    monkeypatch.setattr(
        flatpak.ManagerUtils,
        "get_portal_host_path",
        lambda _path: None,
    )

    assert flatpak.get_filesystem_override_command("/run/user/1000/doc/id/path") is None


@pytest.mark.parametrize("suffix", [":ro", ":rw", ":create"])
def test_filesystem_override_command_rejects_flatpak_permission_suffix(
    monkeypatch, suffix
):
    monkeypatch.setenv("FLATPAK_ID", "io.github.runexe.RunExe")
    monkeypatch.setattr(
        flatpak.ManagerUtils,
        "get_portal_host_path",
        lambda _path: f"/media/Games{suffix}",
    )

    assert flatpak.get_filesystem_override_command("/run/user/1000/doc/id/path") is None


def test_resolve_capsules_directory_accepts_direct_access(monkeypatch):
    monkeypatch.setattr(
        flatpak.ManagerUtils,
        "resolve_portal_path",
        lambda _path: "/media/Games/RunExe",
    )

    assert (
        flatpak.resolve_capsules_directory(object(), "/run/user/1000/doc/id/RunExe")
        == "/media/Games/RunExe"
    )


def test_resolve_capsules_directory_shows_guidance_for_portal_path(monkeypatch):
    portal_path = "/run/user/1000/doc/id/RunExe"
    calls = []
    monkeypatch.setattr(
        flatpak.ManagerUtils,
        "resolve_portal_path",
        lambda _path: portal_path,
    )
    monkeypatch.setattr(
        flatpak,
        "show_external_folder_access_dialog",
        lambda parent, path: calls.append((parent, path)),
    )
    parent = object()

    assert flatpak.resolve_capsules_directory(parent, portal_path) is None
    assert calls == [(parent, portal_path)]


def test_new_capsule_picker_keeps_state_when_direct_access_is_missing(monkeypatch):
    dialog = FileDialogStub()
    monkeypatch.setattr(
        new_capsule_dialog,
        "Gtk",
        SimpleNamespace(FileDialog=SimpleNamespace(new=lambda: dialog)),
    )
    monkeypatch.setattr(
        new_capsule_dialog,
        "resolve_capsules_directory",
        lambda _parent, _path: None,
    )
    view = SimpleNamespace(window=object(), custom_path="unchanged")

    new_capsule_dialog.RunExeNewCapsuleDialog._RunExeNewCapsuleDialog__choose_path(view)

    assert view.custom_path == "unchanged"


def test_preferences_picker_keeps_state_when_direct_access_is_missing(monkeypatch):
    response = 1
    dialog = FileChooserNativeStub(response)
    monkeypatch.setattr(
        preferences,
        "Gtk",
        SimpleNamespace(
            FileChooserNative=SimpleNamespace(new=lambda **_kwargs: dialog),
            FileChooserAction=SimpleNamespace(SELECT_FOLDER=0),
            ResponseType=SimpleNamespace(ACCEPT=response),
        ),
    )
    monkeypatch.setattr(
        preferences,
        "resolve_capsules_directory",
        lambda _parent, _path: None,
    )
    data_calls = []
    view = SimpleNamespace(
        window=object(),
        data=SimpleNamespace(set=lambda *args: data_calls.append(args)),
    )

    preferences.PreferencesWindow._PreferencesWindow__choose_capsules_path(view, None)

    assert data_calls == []
