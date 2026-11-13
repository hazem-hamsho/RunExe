# ruff: noqa: E402

from types import SimpleNamespace

import gi

gi.require_version("Adw", "1")
from gi.repository import Adw, Gio

Gio.resources_register(Gio.Resource.load("/app/share/runexe/runexe.gresource"))

from runexe.frontend.widgets.component import ComponentEntry  # noqa: E402
from runexe.frontend.widgets import component as component_module


def test_usage_refresh_does_not_offer_removal_during_uninstall(monkeypatch):
    Adw.init()
    component_manager = SimpleNamespace(
        is_in_use=lambda *_args: False, uninstall=lambda **_kwargs: None
    )
    window = SimpleNamespace(manager=SimpleNamespace(
        component_manager=component_manager,
        external_runners=set(),
        utils_conn=SimpleNamespace(status=True),
    ))
    entry = ComponentEntry(window, ["wine", {"Installed": True}], "runner")
    pending = []
    monkeypatch.setattr(component_module, "RunAsync", lambda **kwargs: pending.append(kwargs))
    assert entry.btn_remove.get_visible()

    entry.uninstall(None)
    entry.refresh_usage()

    assert pending
    assert not entry.btn_remove.get_visible()
