# capsule_preferences.py
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

import contextlib
import os
import re
from gettext import gettext as _

from gi.repository import Adw, Gdk, Gtk, Xdp

from runexe.backend.logger import Logger
from runexe.backend.managers.eagletracing import tracing_supported
from runexe.backend.managers.library import LibraryManager
from runexe.backend.managers.runtime import RuntimeManager
from runexe.backend.models.config import CapsuleConfig
from runexe.backend.models.enum import Arch
from runexe.backend.models.result import Result
from runexe.backend.runner import Runner
from runexe.backend.utils.display import DisplayUtils
from runexe.backend.utils.gpu import GPUUtils, GPUVendors
from runexe.backend.utils.manager import ManagerUtils
from runexe.backend.utils.threading import RunAsync
from runexe.backend.utils.vulkan import VulkanUtils
from runexe.backend.wine.adaptive import is_supported_runner
from runexe.backend.wine.regkeys import RegKeys
from runexe.frontend.utils.common import format_runner_name
from runexe.frontend.utils.gtk import GtkUtils
from runexe.frontend.windows.display import DisplayDialog
from runexe.frontend.windows.dlloverrides import DLLOverridesDialog
from runexe.frontend.windows.drives import DrivesDialog
from runexe.frontend.windows.envvars import EnvironmentVariablesDialog
from runexe.frontend.windows.sandbox import SandboxDialog

logging = Logger()

FLATPAK_INSTALL_COMMANDS = {}


# noinspection PyUnusedLocal
@Gtk.Template(resource_path="/io/github/runexe/RunExe/details-preferences.ui")
class PreferencesView(Adw.PreferencesPage):
    __gtype_name__ = "DetailsPreferences"

    # region Widgets
    btn_manage_sandbox = Gtk.Template.Child()
    btn_eagle_logs = Gtk.Template.Child()
    btn_cwd_reset = Gtk.Template.Child()
    btn_cwd = Gtk.Template.Child()
    row_discrete = Gtk.Template.Child()
    row_adaptive_launch = Gtk.Template.Child()
    row_eagle_tracing = Gtk.Template.Child()
    row_wayland = Gtk.Template.Child()
    row_winebridge = Gtk.Template.Child()
    row_manage_display = Gtk.Template.Child()
    row_runtime = Gtk.Template.Child()
    row_cwd = Gtk.Template.Child()
    label_cwd = Gtk.Template.Child()
    row_env_variables = Gtk.Template.Child()
    row_overrides = Gtk.Template.Child()
    row_drives = Gtk.Template.Child()
    row_sandbox = Gtk.Template.Child()
    entry_name = Gtk.Template.Child()
    text_notes = Gtk.Template.Child()
    btn_save_notes = Gtk.Template.Child()
    switch_wayland = Gtk.Template.Child()
    switch_winebridge = Gtk.Template.Child()
    switch_discrete = Gtk.Template.Child()
    switch_runtime = Gtk.Template.Child()
    switch_sandbox = Gtk.Template.Child()
    switch_adaptive_launch = Gtk.Template.Child()
    switch_eagle_tracing = Gtk.Template.Child()
    combo_runner = Gtk.Template.Child()
    combo_dxvk = Gtk.Template.Child()
    combo_vkd3d = Gtk.Template.Child()
    combo_windows = Gtk.Template.Child()
    combo_language = Gtk.Template.Child()
    combo_sync = Gtk.Template.Child()
    spinner_dxvk = Gtk.Template.Child()
    spinner_vkd3d = Gtk.Template.Child()
    spinner_runner = Gtk.Template.Child()
    spinner_windows = Gtk.Template.Child()
    spinner_display = Gtk.Template.Child()
    group_details = Gtk.Template.Child()
    str_list_languages = Gtk.Template.Child()
    str_list_runner = Gtk.Template.Child()
    str_list_dxvk = Gtk.Template.Child()
    str_list_vkd3d = Gtk.Template.Child()
    str_list_windows = Gtk.Template.Child()

    # endregion

    def __init__(self, details, config, **kwargs):
        super().__init__(**kwargs)

        # common variables and references
        self.window = details.window
        self.manager = details.window.manager
        self.config = config
        self.btn_save_notes.connect("clicked", self.__save_notes)
        self.text_notes.get_buffer().connect(
            "changed", lambda *_args: self.btn_save_notes.set_sensitive(True)
        )
        self.queue = details.queue
        self.details = details
        self.combo_runner.set_list_factory(
            GtkUtils.create_full_width_string_list_factory()
        )

        if not is_cpak() and not Xdp.Portal.running_under_sandbox():
            return

        self._install_commands = FLATPAK_INSTALL_COMMANDS

        # region signals
        self.row_manage_display.connect("activated", self.__show_display_settings)
        self.row_overrides.connect(
            "activated", self.__show_feature_dialog, DLLOverridesDialog
        )
        self.row_env_variables.connect(
            "activated", self.__show_feature_dialog, EnvironmentVariablesDialog
        )
        self.row_drives.connect("activated", self.__show_feature_dialog, DrivesDialog)
        self.btn_manage_sandbox.connect(
            "clicked", self.__show_feature_dialog, SandboxDialog
        )
        self.btn_cwd.connect("clicked", self.choose_cwd)
        self.btn_cwd_reset.connect("clicked", self.reset_cwd, True)
        self.switch_wayland.connect("state-set", self.__toggle_wayland)
        self.switch_winebridge.connect(
            "state-set", self.__toggle_feature_cb, "winebridge"
        )
        self.switch_sandbox.connect("state-set", self.__toggle_feature_cb, "sandbox")
        self.switch_discrete.connect(
            "state-set", self.__toggle_feature_cb, "discrete_gpu"
        )
        self.switch_adaptive_launch.connect(
            "state-set", self.__toggle_feature_cb, "adaptive_launch"
        )
        self.switch_eagle_tracing.connect(
            "state-set", self.__toggle_feature_cb, "eagle_tracing"
        )
        self.btn_eagle_logs.connect("clicked", self.__open_eagle_logs)
        self.combo_runner.connect("notify::selected", self.__set_runner)
        self.combo_dxvk.connect("notify::selected", self.__set_dxvk)
        self.combo_vkd3d.connect("notify::selected", self.__set_vkd3d)
        self.combo_windows.connect("notify::selected", self.__set_windows)
        self.combo_language.connect("notify::selected-item", self.__set_language)
        self.combo_sync.connect("notify::selected", self.__set_sync_type)
        self.entry_name.connect("changed", self.__check_entry_name)
        self.entry_name.connect("apply", self.__save_name)
        # endregion


        """Toggle some utilities according to its availability"""
        self.__update_adaptive_launch_support()

        vulkan_supported = VulkanUtils.check_support()
        if not vulkan_supported:
            self.combo_dxvk.set_sensitive(False)
            self.combo_vkd3d.set_sensitive(False)

        is_wayland_session = DisplayUtils.display_server_type() == "wayland"
        self.switch_wayland.set_sensitive(is_wayland_session)

    def __create_unavailable_popover(
        self, command: str | None, message: str | None = None
    ) -> Gtk.Popover:
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)

        unavailable_label = Gtk.Label(
            label=message or _("This feature is unavailable on your system."),
            xalign=0,
            wrap=True,
        )
        box.append(unavailable_label)

        if command:
            command_label = Gtk.Label(label=_("To add it, please run:"))
            command_label.set_xalign(0)
            box.append(command_label)

            command_box = Gtk.Box(spacing=6)
            command_box.set_hexpand(True)

            command_entry = Gtk.Entry()
            command_entry.set_editable(False)
            command_entry.set_hexpand(True)
            command_entry.set_text(command)
            command_entry.add_css_class("monospace")
            command_entry.set_width_chars(len(command))
            command_entry.set_focusable(False)

            btn_copy_command = Gtk.Button()
            btn_copy_command.set_icon_name("edit-copy-symbolic")
            btn_copy_command.set_tooltip_text(_("Copy command"))
            btn_copy_command.connect(
                "clicked", self.__copy_command_to_clipboard, command
            )

            command_box.append(command_entry)
            command_box.append(btn_copy_command)
            box.append(command_box)

        popover.set_child(box)
        return popover

    def __add_unavailable_indicator(
        self,
        row: Adw.ActionRow,
        command: str | None,
        message: str | None = None,
    ):
        if not row:
            return

        popover = self.__create_unavailable_popover(command, message)
        menu_button = Gtk.MenuButton()
        menu_button.set_valign(Gtk.Align.CENTER)
        menu_button.set_icon_name("dialog-warning-symbolic")
        menu_button.set_has_frame(False)
        menu_button.set_popover(popover)
        menu_button.set_tooltip_text(
            message or _("This feature is unavailable on your system.")
        )

        row.add_suffix(menu_button)
        return menu_button

    def __copy_command_to_clipboard(self, _widget, command: str):
        display = Gdk.Display.get_default()
        if not display:
            return

        clipboard = Gdk.Display.get_clipboard(display)
        clipboard.set_content(Gdk.ContentProvider.new_for_value(command))
        self.window.show_toast(_("Copied to clipboard"))

    def __check_entry_name(self, *_args):
        if self.entry_name.get_text() != self.config.Name:
            is_duplicate = self.entry_name.get_text() in self.manager.local_capsules
            if is_duplicate:
                self.window.show_toast(_("This capsule name is already in use."))
                self.__valid_name = False
                self.entry_name.add_css_class("error")
                return
        self.__valid_name = True
        self.entry_name.remove_css_class("error")

    def __save_name(self, *_args):
        if not self.__valid_name:
            self.entry_name.set_text(self.config.Name)
            self.__valid_name = True
            return

        new_name = self.entry_name.get_text()
        old_name = self.config.Name

        library_manager = LibraryManager()
        entries = library_manager.get_library()

        for uuid, entry in entries.items():
            capsule = entry.get("capsule") or {}
            if capsule.get("name") == old_name:
                logging.info(f"Updating library entry for {entry.get('name')}")
                entries[uuid]["capsule"]["name"] = new_name
                break

        library_manager.__library = entries
        library_manager.save_library()

        self.manager.update_config(config=self.config, key="Name", value=new_name)

        self.manager.update_capsules(silent=True)  # Updates backend capsules list and UI
        self.window.page_library.update()
        self.details.view_capsule.label_name.set_text(self.config.Name)

    def choose_cwd(self, widget):
        def set_path(_dialog, response):
            if response != Gtk.ResponseType.ACCEPT:
                return

            path = dialog.get_file().get_path()

            self.manager.update_config(
                config=self.config, key="WorkingDir", value=dialog.get_file().get_path()
            )
            self.__update_working_directory_row(path)

        dialog = Gtk.FileChooserNative.new(
            title=_("Select Working Directory"),
            action=Gtk.FileChooserAction.SELECT_FOLDER,
            parent=self.window,
        )

        dialog.set_modal(True)
        dialog.connect("response", set_path)
        dialog.show()

    def reset_cwd(self, *_args):
        self.manager.update_config(config=self.config, key="WorkingDir", value="")
        self.__update_working_directory_row()

    def __update_working_directory_row(self, working_dir=None):
        """Update the working directory."""

        working_dir = working_dir if working_dir is not None else self.config.WorkingDir
        has_custom_dir = bool(working_dir)

        if has_custom_dir:
            basename = os.path.basename(os.path.normpath(working_dir)) or working_dir
            self.label_cwd.set_label(basename)
            self.label_cwd.set_tooltip_text(working_dir)
        else:
            self.label_cwd.set_label(_("(Default)"))
            self.label_cwd.set_tooltip_text(None)

        self.btn_cwd_reset.set_visible(has_custom_dir)

    def update_combo_components(self):
        """
        This function update the components' combo boxes with the
        items in the manager catalogs. It also temporarily disable
        the functions connected to the combo boxes to avoid the
        capsule configuration to be updated during the process.
        """
        self.combo_runner.handler_block_by_func(self.__set_runner)
        self.combo_dxvk.handler_block_by_func(self.__set_dxvk)
        self.combo_vkd3d.handler_block_by_func(self.__set_vkd3d)
        self.combo_language.handler_block_by_func(self.__set_language)
        self.combo_windows.handler_block_by_func(self.__set_windows)


        self.str_list_dxvk.append(_("Disabled"))
        self.str_list_vkd3d.append(_("Disabled"))

        for index, dxvk in enumerate(self.manager.dxvk_available):
            self.str_list_dxvk.append(dxvk)

        for index, vkd3d in enumerate(self.manager.vkd3d_available):
            self.str_list_vkd3d.append(vkd3d)

        for index, runner in enumerate(self.manager.runners_available):
            self.str_list_runner.append(format_runner_name(runner))


        for lang in ManagerUtils.get_languages():
            self.str_list_languages.append(lang)

        self.combo_runner.handler_unblock_by_func(self.__set_runner)
        self.combo_dxvk.handler_unblock_by_func(self.__set_dxvk)
        self.combo_vkd3d.handler_unblock_by_func(self.__set_vkd3d)
        self.combo_language.handler_unblock_by_func(self.__set_language)
        self.combo_windows.handler_unblock_by_func(self.__set_windows)

    def __save_notes(self, _button):
        buffer = self.text_notes.get_buffer()
        notes = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        result = self.manager.update_config(self.config, key="Notes", value=notes)
        if not result.ok:
            self.window.show_toast(_("The notes could not be saved."))
            return
        self.config = result.data["config"]
        self.btn_save_notes.set_sensitive(False)

    def set_config(self, config: CapsuleConfig):
        if self.config.Name != config.Name or not self.btn_save_notes.get_sensitive():
            self.text_notes.get_buffer().set_text(config.Notes)
            self.btn_save_notes.set_sensitive(False)
        self.config = config
        parameters = self.config.Parameters

        # temporary lock functions connected to the widgets
        self.switch_wayland.handler_block_by_func(self.__toggle_wayland)
        self.switch_winebridge.handler_block_by_func(self.__toggle_feature_cb)
        self.switch_adaptive_launch.handler_block_by_func(self.__toggle_feature_cb)
        self.switch_eagle_tracing.handler_block_by_func(self.__toggle_feature_cb)
        self.switch_sandbox.handler_block_by_func(self.__toggle_feature_cb)
        self.switch_discrete.handler_block_by_func(self.__toggle_feature_cb)
        self.combo_runner.handler_block_by_func(self.__set_runner)
        self.combo_dxvk.handler_block_by_func(self.__set_dxvk)
        self.combo_vkd3d.handler_block_by_func(self.__set_vkd3d)
        self.combo_windows.handler_block_by_func(self.__set_windows)
        self.combo_language.handler_block_by_func(self.__set_language)
        self.switch_wayland.set_active(parameters.wayland)
        self.switch_winebridge.set_active(parameters.winebridge)
        self.switch_sandbox.set_active(parameters.sandbox)
        self.switch_adaptive_launch.set_active(parameters.adaptive_launch)
        self.switch_eagle_tracing.set_active(parameters.eagle_tracing)

        # self.toggle_sync.set_active(parameters["sync"] == "wine")
        # self.toggle_esync.set_active(parameters["sync"] == "esync")
        # self.toggle_fsync.set_active(parameters["sync"] == "fsync")

        self.switch_discrete.set_active(parameters.discrete_gpu)

        self.__update_working_directory_row()

        self.entry_name.set_text(config.Name)

        self.row_cwd.set_subtitle(
            _('Directory that contains the data of "{}".'.format(config.Name))
        )

        self.combo_language.set_selected(
            ManagerUtils.get_languages(from_locale=self.config.Language, get_index=True)
        )

        # region Windows Versions
        # NOTE: this should not be here but it's the only way to handle windows
        # versions in the current structure, we will fix this in the future
        # with the new RunExe Backend.
        self.windows_versions = {
            "win11": "Windows 11",
            "win10": "Windows 10",
            "win81": "Windows 8.1",
            "win8": "Windows 8",
            "win7": "Windows 7",
            "win2008r2": "Windows 2008 R2",
            "win2008": "Windows 2008",
            "vista": "Windows Vista",
            "winxp": "Windows XP",
        }

        if self.config.Arch == Arch.WIN32:
            self.windows_versions["win98"] = "Windows 98"
            self.windows_versions["win95"] = "Windows 95"

        for index, windows_version in enumerate(self.windows_versions):
            self.str_list_windows.append(self.windows_versions[windows_version])
            if windows_version == self.config.Windows:
                self.combo_windows.set_selected(index)
        # endregion

        parameters = self.config.Parameters


        _dxvk = self.config.DXVK
        if parameters.dxvk:
            if _dxvk in self.manager.dxvk_available:
                if _i_dxvk := self.manager.dxvk_available.index(_dxvk) + 1:
                    self.combo_dxvk.set_selected(_i_dxvk)
        else:
            self.combo_dxvk.set_selected(0)

        _vkd3d = self.config.VKD3D
        if parameters.vkd3d:
            if _vkd3d in self.manager.vkd3d_available:
                if _i_vkd3d := self.manager.vkd3d_available.index(_vkd3d) + 1:
                    self.combo_vkd3d.set_selected(_i_vkd3d)
        else:
            self.combo_vkd3d.set_selected(0)


        _runner = self.config.Runner
        if _runner in self.manager.runners_available:
            _i_runner = self.manager.runners_available.index(_runner)
            self.combo_runner.set_selected(_i_runner)
        elif self.manager.runners_available:
            _fallback_runner = self.manager.runners_available[0]
            self.combo_runner.set_selected(0)
            self.config = self.manager.update_config(
                config=self.config, key="Runner", value=_fallback_runner
            ).data["config"]

        sync_types = [
            "wine",
            "esync",
            "fsync",
            "ntsync",
        ]
        for sync in sync_types:
            if sync == parameters.sync:
                self.combo_sync.set_selected(sync_types.index(sync))

        # unlock functions connected to the widgets
        self.switch_wayland.handler_unblock_by_func(self.__toggle_wayland)
        self.switch_winebridge.handler_unblock_by_func(self.__toggle_feature_cb)
        self.switch_adaptive_launch.handler_unblock_by_func(self.__toggle_feature_cb)
        self.switch_eagle_tracing.handler_unblock_by_func(self.__toggle_feature_cb)
        self.switch_sandbox.handler_unblock_by_func(self.__toggle_feature_cb)
        self.switch_discrete.handler_unblock_by_func(self.__toggle_feature_cb)
        self.combo_runner.handler_unblock_by_func(self.__set_runner)
        self.combo_dxvk.handler_unblock_by_func(self.__set_dxvk)
        self.combo_vkd3d.handler_unblock_by_func(self.__set_vkd3d)
        self.combo_windows.handler_unblock_by_func(self.__set_windows)
        self.combo_language.handler_unblock_by_func(self.__set_language)

        self.__update_adaptive_launch_support()

    def __update_adaptive_launch_support(self) -> None:
        tracing = tracing_supported(self.config)
        if not tracing and self.config.Parameters.eagle_tracing:
            self.__toggle_feature(False, "eagle_tracing")
        self.switch_eagle_tracing.handler_block_by_func(self.__toggle_feature_cb)
        self.switch_eagle_tracing.set_active(self.config.Parameters.eagle_tracing)
        self.switch_eagle_tracing.handler_unblock_by_func(self.__toggle_feature_cb)
        self.switch_eagle_tracing.set_sensitive(tracing)
        self.row_eagle_tracing.set_tooltip_text(
            "" if tracing else _("Soda 11.0-26 or newer with Eagle tracing is required.")
        )
        supported = is_supported_runner(self.config.Runner)
        message = (
            ""
            if supported
            else _("Soda 11.0-5 or newer is required to enable Adaptive Launch.")
        )
        if not hasattr(self, "_adaptive_launch_warning"):
            self._adaptive_launch_warning = self.__add_unavailable_indicator(
                self.row_adaptive_launch,
                None,
                _("Soda 11.0-5 or newer is required to enable Adaptive Launch."),
            )
        self._adaptive_launch_warning.set_visible(not supported)
        self.switch_adaptive_launch.set_sensitive(supported)
        self.switch_adaptive_launch.set_tooltip_text(message)
        self.row_adaptive_launch.set_tooltip_text(message)

    def __open_eagle_logs(self, _widget):
        path = os.path.join(ManagerUtils.get_capsule_path(self.config), "logs", "runs")
        try:
            os.makedirs(path, mode=0o700, exist_ok=True)
        except OSError as error:
            logging.error(f"Could not create Eagle log folder: {error}")
            self.window.show_toast(_("Could not open folder"))
            return
        ManagerUtils.open_filemanager(path_type="custom", custom_path=path)

    def __show_display_settings(self, widget):
        new_window = DisplayDialog(
            parent_window=self.window,
            config=self.config,
            details=self.details,
            queue=self.queue,
            widget=widget,
            spinner_display=self.spinner_display,
        )
        new_window.present()

    def __show_feature_dialog(self, _widget: Gtk.Widget, dialog: Adw.Window) -> None:
        """Present dialog of a specific feature."""
        window = dialog(window=self.window, config=self.config)
        window.present()

    def __toggle_feature(self, state: bool, key: str) -> None:
        """Toggle a specific feature."""
        self.config = self.manager.update_config(
            config=self.config, key=key, value=state, scope="Parameters"
        ).data["config"]

    def __toggle_feature_cb(self, _widget: Gtk.Widget, state: bool, key: str) -> None:
        self.__toggle_feature(state=state, key=key)
        if key == "eagle_tracing":
            self.__update_adaptive_launch_support()

    def __toggle_wayland(self, _widget: Gtk.Widget, state: bool) -> None:
        self.__toggle_feature(state=state, key="wayland")
        rk = RegKeys(self.config)
        RunAsync(rk.toggle_wayland_driver, state=state)


    def __set_sync_type(self, *_args):
        """
        Set the sync type (wine, esync, fsync, ntsync)
        """
        sync_types = [
            "wine",
            "esync",
            "fsync",
            "ntsync",
        ]
        sync = sync_types[self.combo_sync.get_selected()]
        if sync == self.config.Parameters.sync:
            return

        self.queue.add_task()
        self.combo_sync.set_sensitive(False)
        RunAsync(
            self.manager.update_config,
            config=self.config,
            key="sync",
            value=sync,
            scope="Parameters",
        )
        self.combo_sync.set_sensitive(True)
        self.queue.end_task()


    def __set_runner(self, *_args):
        """Set the runner to use for the capsule"""

        def set_widgets_status(status=True):
            if status:
                self.spinner_runner.stop()
                self.spinner_runner.set_visible(False)
            else:
                self.spinner_runner.start()
                self.spinner_runner.set_visible(True)

        @GtkUtils.run_in_main_loop
        def update(result: Result[dict], error=False):
            if isinstance(result, Result) and isinstance(
                result.data, dict
            ):  # expecting Result[dict].data["config"]
                self.details.update_runner_label(runner)

                if "config" in result.data:
                    self.config = result.data["config"]
                self.__update_adaptive_launch_support()

            set_widgets_status(True)
            self.queue.end_task()

        runner = self.manager.runners_available[self.combo_runner.get_selected()]

        def run_task(status=True):
            if not status:
                update(Result(True))
                self.combo_runner.handler_block_by_func(self.__set_runner)
                self.combo_runner.handler_unblock_by_func(self.__set_runner)
                return

            self.queue.add_task()
            RunAsync(
                Runner.runner_update,
                callback=update,
                config=self.config,
                manager=self.manager,
                runner=runner,
            )

        def apply_runner():
            set_widgets_status(False)
            run_task()

        if self.config.Parameters.hidraw_devices and "soda" not in runner.lower():
            dialog = Adw.MessageDialog.new(
                self.window,
                _("HIDRAW support is not guaranteed"),
                _(
                    "Selective HIDRAW access is tested with Soda. The new "
                    "runner may ignore it or behave differently. Continue anyway?"
                ),
            )
            dialog.add_response("cancel", _("_Cancel"))
            dialog.add_response("continue", _("_Continue"))
            dialog.set_response_appearance(
                "continue", Adw.ResponseAppearance.SUGGESTED
            )
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")

            def on_response(_dialog, response):
                if response == "continue":
                    apply_runner()
                    return

                self.combo_runner.handler_block_by_func(self.__set_runner)
                if self.config.Runner in self.manager.runners_available:
                    self.combo_runner.set_selected(
                        self.manager.runners_available.index(self.config.Runner)
                    )
                self.combo_runner.handler_unblock_by_func(self.__set_runner)

            dialog.connect("response", on_response)
            dialog.present()
            return

        apply_runner()

    def __dll_component_task_func(self, *args, **kwargs):
        # Remove old version
        self.manager.install_dll_component(
            config=kwargs["config"], component=kwargs["component"], remove=True
        )
        # Install new version
        self.manager.install_dll_component(
            config=kwargs["config"], component=kwargs["component"]
        )


    def __set_dxvk(self, *_args):
        """Set the DXVK version to use for the capsule"""
        selected = self.combo_dxvk.get_selected()
        version = self.manager.dxvk_available[selected - 1] if selected else None
        self.set_dxvk_status(pending=True)
        self.queue.add_task()
        RunAsync(
            task_func=self.manager.set_graphics_component,
            callback=self.set_dxvk_status,
            config=self.config,
            component="dxvk",
            enabled=selected != 0,
            version=version,
        )

    def __set_vkd3d(self, *_args):
        """Set the VKD3D version to use for the capsule"""
        selected = self.combo_vkd3d.get_selected()
        version = self.manager.vkd3d_available[selected - 1] if selected else None
        self.set_vkd3d_status(pending=True)
        self.queue.add_task()
        RunAsync(
            task_func=self.manager.set_graphics_component,
            callback=self.set_vkd3d_status,
            config=self.config,
            component="vkd3d",
            enabled=selected != 0,
            version=version,
        )


    def __set_windows(self, *_args):
        """Set the Windows version to use for the capsule"""

        # self.manager.dxvk_available[self.combo_dxvk.get_selected()]
        @GtkUtils.run_in_main_loop
        def update(result, error=False):
            if error:
                versions = list(self.windows_versions)
                selected = 0
                if self.config.Windows in versions:
                    selected = versions.index(self.config.Windows)
                self.combo_windows.handler_block_by_func(self.__set_windows)
                self.combo_windows.set_selected(selected)
                self.combo_windows.handler_unblock_by_func(self.__set_windows)
            else:
                self.config = self.manager.update_config(
                    config=self.config, key="Windows", value=windows_version
                ).data["config"]
            self.spinner_windows.stop()
            self.spinner_windows.set_visible(False)
            self.combo_windows.set_sensitive(True)
            self.queue.end_task()

        self.queue.add_task()
        self.spinner_windows.start()
        self.spinner_windows.set_visible(True)
        self.combo_windows.set_sensitive(False)
        rk = RegKeys(self.config)

        for index, windows_version in enumerate(self.windows_versions):
            if self.combo_windows.get_selected() == index:
                RunAsync(rk.lg_set_windows, callback=update, version=windows_version)
                break

    def __set_language(self, *_args):
        """Set the language to use for the capsule"""
        index = self.combo_language.get_selected()
        language = ManagerUtils.get_languages(from_index=index)
        self.config = self.manager.update_config(
            config=self.config,
            key="Language",
            value=language[0],
        ).data["config"]

    def __set_graphics_status(self, status, error, pending, spinner):
        self.combo_dxvk.set_sensitive(not pending)
        self.combo_vkd3d.set_sensitive(not pending)
        if pending:
            spinner.start()
            spinner.set_visible(True)
            return

        if not error and isinstance(status, Result) and status.ok and status.data:
            self.config = status.data["config"]
        elif error or not isinstance(status, Result) or not status.ok:
            logging.error("Failed to update graphics components")
        for name, handler in (
            ("dxvk", self.__set_dxvk),
            ("vkd3d", self.__set_vkd3d),
        ):
            combo = getattr(self, f"combo_{name}")
            available = getattr(self.manager, f"{name}_available")
            version = getattr(self.config, name.upper())
            selected = 0
            if getattr(self.config.Parameters, name) and version in available:
                selected = available.index(version) + 1
            combo.handler_block_by_func(handler)
            combo.set_selected(selected)
            combo.handler_unblock_by_func(handler)
        spinner.stop()
        spinner.set_visible(False)
        self.queue.end_task()

    @GtkUtils.run_in_main_loop
    def set_dxvk_status(self, status=None, error=None, pending=False):
        """Set the dxvk status"""
        self.__set_graphics_status(status, error, pending, self.spinner_dxvk)


    @GtkUtils.run_in_main_loop
    def set_vkd3d_status(self, status=None, error=None, pending=False):
        """Set the vkd3d status"""
        self.__set_graphics_status(status, error, pending, self.spinner_vkd3d)


