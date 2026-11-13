# ruff: noqa: E402

from types import SimpleNamespace

import pytest
from gi.repository import Gio

capsules_resource = Gio.Resource.load("/app/share/runexe/runexe.gresource")
capsules_resource._register()

from runexe.frontend.views import capsule_details


class FakeDialog:
    def __init__(self, selected_file):
        self.selected_file = selected_file
        self.callback = None

    def set_modal(self, _modal):
        pass

    def connect(self, _signal, callback):
        self.callback = callback

    def set_current_name(self, _name):
        pass

    def get_file(self):
        return self.selected_file

    def show(self):
        self.callback(self, capsule_details.Gtk.ResponseType.ACCEPT)


@pytest.mark.parametrize(
    ("selected_file", "expected_path"),
    (
        (None, None),
        (SimpleNamespace(get_path=lambda: None), None),
        (
            SimpleNamespace(get_path=lambda: "/tmp/backup.tar.gz"),
            "/tmp/backup.tar.gz",
        ),
    ),
)
def test_backup_dialog_requires_local_path(monkeypatch, selected_file, expected_path):
    dialog = FakeDialog(selected_file)
    calls = []

    class FakeFileChooserNative:
        @staticmethod
        def new(**_kwargs):
            return dialog

    fake_gtk = SimpleNamespace(
        FileChooserAction=capsule_details.Gtk.FileChooserAction,
        FileChooserNative=FakeFileChooserNative,
        ResponseType=capsule_details.Gtk.ResponseType,
    )
    monkeypatch.setattr(capsule_details, "Gtk", fake_gtk)
    monkeypatch.setattr(
        capsule_details, "RunAsync", lambda **kwargs: calls.append(kwargs)
    )
    view = SimpleNamespace(
        config=SimpleNamespace(Name="Test", Path="Test"),
        window=SimpleNamespace(),
    )

    capsule_details.CapsuleView._CapsuleView__backup(view, None, "full")

    if expected_path is None:
        assert calls == []
    else:
        assert len(calls) == 1
        assert calls[0]["path"] == expected_path
        assert calls[0]["scope"] == "full"
