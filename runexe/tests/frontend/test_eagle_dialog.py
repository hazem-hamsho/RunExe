# ruff: noqa: E402

import gc
import weakref
from types import SimpleNamespace
from unittest.mock import Mock

from gi.repository import Gio

Gio.resources_register(Gio.Resource.load("/app/share/runexe/runexe.gresource"))

from runexe.frontend.views import capsule_details


def test_eagle_chooser_stays_alive_until_response(monkeypatch, tmp_path):
    dialogs = []
    gtk = capsule_details.Gtk

    def create_dialog(**kwargs):
        dialog = gtk.FileChooserNative.new(**kwargs)
        dialogs.append(weakref.ref(dialog))
        return dialog

    monkeypatch.setattr(
        capsule_details,
        "Gtk",
        SimpleNamespace(
            FileChooserNative=SimpleNamespace(new=create_dialog),
            FileChooserAction=gtk.FileChooserAction,
            ResponseType=gtk.ResponseType,
        ),
    )
    monkeypatch.setattr(
        capsule_details.ManagerUtils, "get_capsule_path", lambda _config: str(tmp_path)
    )
    view = SimpleNamespace(
        config=object(), window=gtk.Window(), analyze_with_eagle=Mock()
    )
    try:
        capsule_details.CapsuleView.run_eagle(view, None)
        gc.collect()
        dialog = dialogs[0]()
        assert dialog is not None
        dialog.emit("response", gtk.ResponseType.CANCEL)
        view.analyze_with_eagle.assert_not_called()
        assert view._eagle_dialog is None
        assert not dialog.get_visible()
    finally:
        view.window.destroy()
