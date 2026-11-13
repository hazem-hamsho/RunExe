# bulkupdate.py
#
# Copyright 2025 mirkobrombin <brombin94@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, in version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

from gettext import gettext as _, ngettext
from typing import ClassVar

from gi.repository import Adw, GLib, Gtk

from runexe.backend.utils.threading import RunAsync


@Gtk.Template(resource_path="/io/github/runexe/RunExe/bulk-update-dialog.ui")
class RunExeBulkUpdateDialog(Adw.Dialog):
    __gtype_name__ = "RunExeBulkUpdateDialog"

    # region Widgets
    stack = Gtk.Template.Child()
    btn_cancel = Gtk.Template.Child()
    btn_apply = Gtk.Template.Child()
    group_components = Gtk.Template.Child()
    group_capsules = Gtk.Template.Child()
    status_progress = Gtk.Template.Child()
    progress_bar = Gtk.Template.Child()
    label_progress = Gtk.Template.Child()
    switch_remove_old = Gtk.Template.Child()

    # endregion

    # Preferred display order for the component checklist.
    __component_order: ClassVar[list[str]] = [
        "runner",
        "dxvk",
        "vkd3d",
        "winebridge",
    ]

    def __init__(self, window, **kwargs):
        super().__init__(**kwargs)

        self.window = window
        self.manager = window.manager

        # path -> {"config": CapsuleConfig, "updates": [update dict, ...]}
        self.__capsule_updates = {}
        # component id -> Adw.SwitchRow
        self.__component_rows = {}
        # path -> Adw.SwitchRow
        self.__capsule_rows = {}
        self.__total = 0

        # collect the capsules that have at least one available update, reusing
        # the same detection used by the per-capsule details page
        component_titles = {}
        for config in self.manager.local_capsules.values():
            updates = self.manager.get_component_updates(config)
            if not updates:
                continue
            self.__capsule_updates[config.Path] = {
                "config": config,
                "updates": updates,
            }
            for update in updates:
                component_titles.setdefault(update["id"], update["title"])

        # populate the component checklist, keeping a stable order
        for component in self.__component_order:
            if component not in component_titles:
                continue
            row = Adw.SwitchRow(title=component_titles[component], active=True)
            row.connect("notify::active", self.__on_selection_changed)
            self.group_components.add(row)
            self.__component_rows[component] = row

        # populate the capsule checklist
        for path, data in self.__capsule_updates.items():
            config = data["config"]
            available = ", ".join(update["title"] for update in data["updates"])
            row = Adw.SwitchRow(title=config.Name, subtitle=available, active=True)
            row.connect("notify::active", self.__on_selection_changed)
            self.group_capsules.add(row)
            self.__capsule_rows[path] = row

        # connect signals
        self.btn_cancel.connect("clicked", self.__on_cancel)
        self.btn_apply.connect("clicked", self.__on_apply)

        self.__on_selection_changed()

    def __selected_components(self) -> list:
        return [c for c, row in self.__component_rows.items() if row.get_active()]

    def __on_selection_changed(self, *_args) -> None:
        has_component = any(
            row.get_active() for row in self.__component_rows.values()
        )
        has_capsule = any(row.get_active() for row in self.__capsule_rows.values())
        self.btn_apply.set_sensitive(has_component and has_capsule)

    def __build_jobs(self) -> list:
        """Return [(path, config, [update dict, ...])] for the current selection."""
        selected = self.__selected_components()
        jobs = []
        for path, row in self.__capsule_rows.items():
            if not row.get_active():
                continue
            data = self.__capsule_updates[path]
            updates = [u for u in data["updates"] if u["id"] in selected]
            if updates:
                jobs.append((path, data["config"], updates))
        return jobs

    def __on_cancel(self, *_args) -> None:
        self.close()

    def __on_apply(self, *_args) -> None:
        jobs = self.__build_jobs()
        if not jobs:
            self.close()
            return

        self.__total = sum(len(updates) for _p, _c, updates in jobs)
        self.progress_bar.set_fraction(0)
        self.label_progress.set_label("")
        self.set_can_close(False)
        self.stack.set_visible_child_name("page_progress")

        RunAsync(
            self.__run_updates,
            callback=self.__on_finished,
            jobs=jobs,
            remove_old=self.switch_remove_old.get_active(),
        )

    @staticmethod
    def __cleanup_target(update: dict):
        component = update["id"]
        if component == "runner":
            component_type = update["component_type"]
        elif component in {"dxvk", "vkd3d"}:
            component_type = component
        else:
            return None

        current = update.get("current")
        if not current or current == update.get("latest"):
            return None
        return component_type, current

    def __run_updates(self, jobs, remove_old=False) -> dict:
        """Worker thread: apply the updates and report progress on the main loop."""
        ok = 0
        failed = 0
        step = 0
        cleanup_targets = set()

        for _path, config, updates in jobs:
            # apply the runner first, so the DLL overrides get re-initialized
            # on top of the new runner prefix
            ordered = sorted(updates, key=lambda u: 0 if u["id"] == "runner" else 1)
            for update in ordered:
                step += 1
                GLib.idle_add(
                    self.__set_progress, step, config.Name, update["title"]
                )

                result = self.manager.apply_component_update(config, update)
                if result and getattr(result, "ok", False):
                    ok += 1
                    if remove_old and (target := self.__cleanup_target(update)):
                        cleanup_targets.add(target)
                    data = getattr(result, "data", None)
                    if isinstance(data, dict) and data.get("config"):
                        # carry the updated config forward so the next update
                        # on this capsule does not overwrite the previous one
                        config = data["config"]
                else:
                    failed += 1

        removed = 0
        for component_type, component_name in sorted(cleanup_targets):
            result = self.manager.component_manager.uninstall(
                component_type, component_name
            )
            if result and getattr(result, "ok", False):
                removed += 1

        return {"ok": ok, "failed": failed, "removed": removed}

    def __set_progress(self, step: int, capsule_name: str, component_title: str) -> bool:
        if self.__total:
            self.progress_bar.set_fraction(step / self.__total)
        self.label_progress.set_label(f"{capsule_name}: {component_title}")
        return False

    def __on_finished(self, result, error=False) -> None:
        ok = result.get("ok", 0) if isinstance(result, dict) else 0
        failed = result.get("failed", 0) if isinstance(result, dict) else 0
        removed = result.get("removed", 0) if isinstance(result, dict) else 0

        # reload capsules from disk and refresh the list (and the home banner)
        self.manager.check_capsules()
        self.window.page_list.update_capsules_list()

        if failed:
            message = _("Updated {0} components, {1} failed.").format(ok, failed)
        elif removed:
            message = ngettext(
                "Updated {0} components and removed {1} old version.",
                "Updated {0} components and removed {1} old versions.",
                removed,
            ).format(ok, removed)
        else:
            message = _("Updated {0} components.").format(ok)
        self.window.show_toast(message)

        self.set_can_close(True)
        self.close()
