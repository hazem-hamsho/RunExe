# list.py
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

from datetime import datetime
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, GLib, Gtk, Xdp

from runexe.backend.globals import is_cpak
from runexe.backend.models.config import CapsuleConfig
from runexe.backend.models.result import Result
from runexe.backend.state import EventManager, Events, SignalManager, Signals
from runexe.backend.utils.threading import RunAsync
from runexe.backend.wine.executor import WineExecutor
from runexe.frontend.params import APP_ID
from runexe.frontend.utils.filters import add_all_filters, add_executable_filters
from runexe.frontend.utils.sandbox_guard import guard_sandbox_launch


def _capsule_order_id(config: CapsuleConfig) -> str:
    return f"capsule:{config.Path}"


def _ordered_capsules(configs, configured_order):
    positions = {capsule_id: index for index, capsule_id in enumerate(configured_order)}
    fallback = len(positions)
    return sorted(
        configs,
        key=lambda config: (
            positions.get(_capsule_order_id(config), fallback),
            config.Name.casefold(),
            config.Path,
        ),
    )


def _replace_group_order(configured_order, previous_order, new_order):
    replacements = iter(new_order)
    previous = set(previous_order)
    result = [
        next(replacements) if item in previous else item for item in configured_order
    ]
    result.extend(replacements)
    return result


@Gtk.Template(resource_path="/io/github/runexe/RunExe/capsule-row.ui")
class RunExeCapsuleRow(Adw.ActionRow):
    __gtype_name__ = "RunExeCapsuleRow"

    Adw.init()

    # region Widgets
    button_run = Gtk.Template.Child()
    button_order = Gtk.Template.Child()
    button_move_top = Gtk.Template.Child()
    button_move_up = Gtk.Template.Child()
    button_move_down = Gtk.Template.Child()
    button_move_bottom = Gtk.Template.Child()
    wrap_box = Gtk.Template.Child()

    # endregion

    def __init__(self, window, config: CapsuleConfig, reorder_callback=None, **kwargs):
        super().__init__(**kwargs)

        # common variables and references
        self.window = window
        self.manager = window.manager
        self.config = config
        self.reorder_callback = reorder_callback

        # Format update date
        update_date = _("N/A")
        if self.config.Update_Date:
            try:
                temp_date = datetime.strptime(
                    self.config.Update_Date, "%Y-%m-%d %H:%M:%S.%f"
                )
                update_date = temp_date.strftime("%d %B, %Y %H:%M:%S")
            except ValueError:
                update_date = _("N/A")

        # Check runner type by name
        if self.config.Runner.startswith("lutris"):
            self.runner_type = "wine"
        else:
            self.runner_type = "proton"

        # connect signals
        self.connect("activated", self.show_details)
        self.button_run.connect("clicked", self.run_executable)
        self.button_move_top.connect("clicked", self.__reorder, "top")
        self.button_move_up.connect("clicked", self.__reorder, "up")
        self.button_move_down.connect("clicked", self.__reorder, "down")
        self.button_move_bottom.connect("clicked", self.__reorder, "bottom")

        # populate widgets
        self.set_title(self.config.Name)
        if self.window.settings.get_boolean("update-date"):
            self.set_subtitle(update_date)

        self.wrap_box.append(Gtk.Label.new(self.config.Environment))

        # Set tooltip text
        self.button_run.set_tooltip_text(_(f"Run executable in “{self.config.Name}”"))

    def __reorder(self, _button, position):
        self.reorder_callback(self, position)

    def set_reorder_state(self, can_move_up, can_move_down, visible):
        self.button_order.set_visible(visible)
        self.button_move_top.set_sensitive(can_move_up)
        self.button_move_up.set_sensitive(can_move_up)
        self.button_move_down.set_sensitive(can_move_down)
        self.button_move_bottom.set_sensitive(can_move_down)

    def run_executable(self, *_args):
        """Display file dialog for executable"""
        if not is_cpak() and not Xdp.Portal.running_under_sandbox():
            return

        def set_path(_dialog, response):
            if response != Gtk.ResponseType.ACCEPT:
                return

            path = dialog.get_file().get_path()

            def proceed(sandbox_override, run_path):
                self.window.show_toast(
                    _("Launching “{0}” in “{1}”…").format(
                        dialog.get_file().get_basename(), self.config.Name
                    )
                )
                _executor = WineExecutor(
                    self.config, exec_path=run_path, sandbox_override=sandbox_override
                )
                RunAsync(_executor.run, self._on_executable_finished)

            guard_sandbox_launch(self.window, self.config, path, proceed)

        dialog = Gtk.FileChooserNative.new(
            title=_("Select Executable"),
            action=Gtk.FileChooserAction.OPEN,
            parent=self.window,
            accept_label=_("Run"),
        )

        add_executable_filters(dialog)
        add_all_filters(dialog)
        dialog.set_modal(True)
        dialog.connect("response", set_path)
        dialog.show()

    def _on_executable_finished(self, _result=None, _error=None):
        """Refresh discovered programs after an executable exits."""
        self.manager.get_programs(self.config, force_update=True)

    def show_details(self, widget=None, config=None):
        if config is None:
            config = self.config
        self.window.page_details.view_preferences.update_combo_components()
        self.window.show_details_view(config=config)

    def disable(self):
        self.window.go_back()
        self.set_visible(False)


@Gtk.Template(resource_path="/io/github/runexe/RunExe/list.ui")
class CapsuleView(Adw.Bin):
    __gtype_name__ = "CapsuleView"
    __capsules = {}

    # region Widgets
    list_capsules = Gtk.Template.Child()
    group_capsules = Gtk.Template.Child()
    pref_page = Gtk.Template.Child()
    capsule_status = Gtk.Template.Child()
    btn_create = Gtk.Template.Child()
    entry_search = Gtk.Template.Child()
    search_bar = Gtk.Template.Child()
    no_capsules_found = Gtk.Template.Child()
    update_banner = Gtk.Template.Child()

    # endregion

    def __init__(self, window, arg_capsule=None, **kwargs):
        super().__init__(**kwargs)

        # common variables and references
        self.window = window
        self.arg_capsule = arg_capsule

        # connect signals
        self.btn_create.connect("clicked", self.window.show_add_view)
        self.entry_search.connect("changed", self.__search_capsules)
        self.update_banner.connect("button-clicked", self.__show_bulk_update)

        # backend signals
        SignalManager.connect(
            Signals.ManagerLocalCapsulesLoaded, self.update_capsules_list
        )

        self.capsule_status.set_icon_name(APP_ID)

        self.update_capsules_list()

    def __search_capsules(self, widget, event=None, data=None):
        """
        This function search in the list of capsules the
        text written in the search entry.
        """
        terms = widget.get_text()
        self.list_capsules.set_filter_func(self.__filter_capsules, terms)
        self.__update_empty_state(terms)

    @staticmethod
    def __filter_capsules(row, terms=None):
        text = row.get_title().lower()
        return terms.lower() in text

    def __update_empty_state(self, terms):
        has_capsules = bool(self.__capsules)
        has_matches = any(
            self.__filter_capsules(row, terms) for row in self.__capsules.values()
        )
        self.pref_page.set_visible(has_matches)
        self.capsule_status.set_visible(not has_capsules)
        self.no_capsules_found.set_visible(bool(terms) and not has_matches)

    def update_capsules_list(self, *args, refresh_updates=True) -> None:
        self.__capsules = {}
        while self.list_capsules.get_first_child():
            self.list_capsules.remove(self.list_capsules.get_first_child())

        local_capsules = self.window.manager.local_capsules

        configured_order = self.window.settings.get_strv("capsule-order")
        configs = _ordered_capsules(list(local_capsules.values()), configured_order)

        for config in configs:
            _entry = RunExeCapsuleRow(self.window, config, self.__reorder_capsule)
            self.__capsules[config.Path] = _entry

            self.list_capsules.append(_entry)

        has_local_capsules = self.list_capsules.get_first_child() is not None
        self.group_capsules.set_visible(has_local_capsules)

        self.__update_empty_state(self.entry_search.get_text())
        self.__update_reorder_states(configs)

        if refresh_updates:
            self.update_component_updates_banner()

    def __reorder_capsule(self, row, position):
        configured_order = self.window.settings.get_strv("capsule-order")
        configs = _ordered_capsules(
            list(self.window.manager.local_capsules.values()), configured_order
        )
        group = list(configs)
        index = group.index(row.config)
        destinations = {
            "top": 0,
            "up": max(0, index - 1),
            "down": min(len(group) - 1, index + 1),
            "bottom": len(group) - 1,
        }
        destination = destinations[position]
        if destination == index:
            return

        previous_order = [_capsule_order_id(config) for config in group]
        group.insert(destination, group.pop(index))
        new_order = [_capsule_order_id(config) for config in group]
        self.window.settings.set_strv(
            "capsule-order",
            _replace_group_order(configured_order, previous_order, new_order),
        )
        self.update_capsules_list(refresh_updates=False)

    def __update_reorder_states(self, configs):
        for index, config in enumerate(configs):
            self.__capsules[config.Path].set_reorder_state(
                can_move_up=index > 0,
                can_move_down=index < len(configs) - 1,
                visible=len(configs) > 1,
            )

    def update_component_updates_banner(self) -> None:
        self.__update_banner_state(self.window.manager.local_capsules)

    def __update_banner_state(self, local_capsules) -> None:
        """Reveal the home banner when some capsules can update their components.

        The component catalog is fetched asynchronously, so wait for it to be
        organized in a worker thread before counting, then touch the banner
        back on the main loop.
        """
        configs = list(local_capsules.values())

        def count_updates():
            EventManager.wait(Events.ComponentsOrganizing)
            return sum(
                1
                for config in configs
                if self.window.manager.get_component_updates(config)
            )

        RunAsync(count_updates, callback=self.__apply_banner_state)

    def __apply_banner_state(self, count, error=False) -> None:
        if not count:
            self.update_banner.set_revealed(False)
            return

        self.update_banner.set_title(
            ngettext(
                "Component updates are available for {0} capsule.",
                "Component updates are available for {0} capsules.",
                count,
            ).format(count)
        )
        self.update_banner.set_revealed(True)

    def __show_bulk_update(self, *_args) -> None:
        from runexe.frontend.windows.bulkupdate import RunExeBulkUpdateDialog

        dialog = RunExeBulkUpdateDialog(self.window)
        dialog.present(self.window)

    def show_page(self, page: str) -> None:
        if config := self.window.manager.local_capsules.get(page):
            self.window.page_details.view_preferences.update_combo_components()
            self.window.show_details_view(config=config)

    def disable_capsule(self, config):
        self.__capsules[config.Path].disable()
