# library.py
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

from gettext import gettext as _

from gi.repository import Gdk, Gio, GLib, Gtk

from runexe.backend.logger import Logger
from runexe.backend.managers.library import LibraryManager
from runexe.backend.managers.thumbnail import ThumbnailManager
from runexe.backend.models.result import Result
from runexe.backend.utils.manager import ManagerUtils
from runexe.backend.utils.threading import RunAsync
from runexe.backend.wine.wineboot import WineBoot
from runexe.backend.wine.executor import WineExecutor
from runexe.backend.wine.winedbg import WineDbg
from runexe.frontend.utils.gtk import GtkUtils
from runexe.frontend.utils.program import guard_program_launch
from runexe.frontend.utils.sandbox_guard import guard_sandbox_launch

logging = Logger()


class LibraryEntryInitializationError(Exception):
    """Raised when a library entry cannot be initialized."""

    pass


@Gtk.Template(resource_path="/io/github/runexe/RunExe/library-add-entry.ui")
class LibraryAddEntry(Gtk.Box):
    __gtype_name__ = "LibraryAddEntry"

    btn_capsule = Gtk.Template.Child()

    def __init__(self, library, **kwargs):
        super().__init__(**kwargs)
        self.btn_capsule.connect("clicked", library.show_capsule_programs)


@Gtk.Template(resource_path="/io/github/runexe/RunExe/library-entry.ui")
class LibraryEntry(Gtk.Box):
    __gtype_name__ = "LibraryEntry"

    # region Widgets
    btn_run = Gtk.Template.Child()
    btn_stop = Gtk.Template.Child()
    btn_cover = Gtk.Template.Child()
    btn_remove = Gtk.Template.Child()
    label_name = Gtk.Template.Child()
    label_capsule = Gtk.Template.Child()
    label_no_cover = Gtk.Template.Child()
    img_cover = Gtk.Template.Child()
    revealer_details = Gtk.Template.Child()
    overlay = Gtk.Template.Child()

    # endregion

    def __init__(self, library, uuid, entry, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.library = library
        self.window = library.window
        self.manager = library.window.manager
        self.name = entry["name"]
        self.uuid = uuid
        self.entry = entry
        self.__pointer_inside = False
        try:
            self.config = self.__get_config()

            if self.config is None:
                raise LibraryEntryInitializationError(
                    _(
                        'The capsule for "{0}" is no longer available. Removing it from the library.'
                    ).format(self.name)
                )

            self.program = self.__get_program()

            if self.program is None:
                raise LibraryEntryInitializationError(
                    _(
                        'The program "{0}" is no longer available. Removing it from the library.'
                    ).format(self.name)
                )

        except LibraryEntryInitializationError as error:
            self.__handle_initialization_failure(str(error))
            raise

        if len(entry["name"]) >= 15:
            name = entry["name"][:13] + "…"
        else:
            name = entry["name"]

        self.label_name.set_text(name)
        self.label_capsule.set_text(entry["capsule"]["name"])
        self.label_no_cover.set_label(self.name)

        if entry.get("thumbnail"):
            path = ThumbnailManager.get_path(self.config, entry["thumbnail"])

            if path is not None:
                # Gtk.Picture.set_pixbuf deprecated in GTK 4.12
                texture = Gdk.Texture.new_from_filename(path)
                self.img_cover.set_paintable(texture)
                self.img_cover.set_visible(True)
                self.label_no_cover.set_visible(False)
        motion_ctrl = Gtk.EventControllerMotion.new()
        motion_ctrl.connect("enter", self.__on_motion_enter)
        motion_ctrl.connect("leave", self.__on_motion_leave)
        self.overlay.add_controller(motion_ctrl)
        self.btn_run.connect("clicked", self.run_executable)
        self.btn_cover.connect("clicked", self.__choose_cover)
        self.btn_stop.connect("clicked", self.stop_process)
        self.btn_remove.connect("clicked", self.__remove_entry)

    def __get_config(self):
        capsules = self.manager.local_capsules
        capsule_name = (self.entry.get("capsule") or {}).get("name")

        if capsule_name in capsules:
            return capsules[capsule_name]

        self.__remove_from_library()
        return None

    def __get_program(self):
        programs = self.manager.get_programs(self.config)
        programs = [
            p
            for p in programs
            if p["id"] == self.entry["id"] or p["name"] == self.entry["name"]
        ]
        if len(programs) == 0:
            self.__remove_from_library()
            return None
        return programs[0]

    def __remove_from_library(self):
        library_manager = LibraryManager()
        library_manager.remove_from_library(self.uuid, getattr(self, "config", None))

    def __handle_initialization_failure(self, message: str):
        logging.warning(message, jn=False)

        if hasattr(self.window, "show_toast"):
            self.window.show_toast(message)

    @GtkUtils.run_in_main_loop
    def __reset_buttons(self, result: Result | bool = None, error=False):
        match result:
            case Result():
                status = result.status
            case bool():
                status = result
            case _:
                logging.error(
                    f"result should be Result or bool, but it was {type(result)}"
                )
                status = False

        self.btn_remove.set_visible(status)
        self.btn_stop.set_visible(not status)
        self.btn_run.set_visible(status)

    def __is_alive(self):
        winedbg = WineDbg(self.config)

        @GtkUtils.run_in_main_loop
        def set_watcher(result=False, error=False):
            nonlocal winedbg
            self.__reset_buttons()

            RunAsync(
                winedbg.wait_for_process,
                callback=self.__reset_buttons,
                name=self.program["executable"],
                timeout=5,
            )

        RunAsync(
            winedbg.is_process_alive,
            callback=set_watcher,
            name=self.program["executable"],
        )

    def __remove_entry(self, *args):
        self.library.remove_entry(self)

    def __choose_cover(self, *_args):
        def set_cover(dialog, result):
            try:
                file = dialog.open_finish(result)
            except GLib.Error:
                return

            path = file.get_path()
            if path is None:
                self.window.show_toast(_("The cover image could not be opened."))
                return

            try:
                Gdk.Texture.new_from_filename(path)
            except GLib.Error:
                self.window.show_toast(_("The selected file is not a valid image."))
                return

            library_manager = LibraryManager()
            if not library_manager.set_thumbnail(self.uuid, path, self.config):
                self.window.show_toast(_("The cover image could not be saved."))
                return

            self.library.update()
            self.window.show_toast(_("Cover image updated."))

        filters = Gio.ListStore.new(Gtk.FileFilter)
        image_filter = Gtk.FileFilter()
        image_filter.set_name(_("Images"))
        for extension in ("png", "jpg", "jpeg", "webp"):
            image_filter.add_pattern(f"*.{extension}")
            image_filter.add_pattern(f"*.{extension.upper()}")
        filters.append(image_filter)

        dialog = Gtk.FileDialog.new()
        dialog.set_title(_("Select Cover Image"))
        dialog.set_filters(filters)
        dialog.set_default_filter(image_filter)
        pictures = GLib.get_user_special_dir(
            GLib.UserDirectory.DIRECTORY_PICTURES
        )
        if pictures:
            dialog.set_initial_folder(Gio.File.new_for_path(pictures))
        dialog.open(self.window, callback=set_cover)

    def run_executable(self, widget, with_terminal=False):
        def proceed(sandbox_override, exec_path):
            program = self.program
            if exec_path and exec_path != self.program.get("path"):
                program = {**self.program, "path": exec_path}
            self.window.show_toast(
                _('Launching "{0}"…').format(self.program["name"])
            )
            RunAsync(
                WineExecutor.run_program,
                callback=self.__reset_buttons,
                config=self.config,
                program=program,
                sandbox_override=sandbox_override,
            )
            self.__reset_buttons()

        guard_program_launch(
            self.window, self.config, self.program,
            lambda: guard_sandbox_launch(
                self.window, self.config, self.program.get("path"), proceed
            ),
        )

    def stop_process(self, widget):
        self.window.show_toast(_('Stopping "{0}"...').format(self.program["name"]))
        self.btn_stop.set_sensitive(False)

        def complete(_result=None, error=False):
            self.__reset_buttons(True)
            if error:
                self.window.show_toast(_("The program could not be stopped."))

        RunAsync(
            WineBoot(self.config).kill,
            callback=complete,
            force_if_stalled=True,
        )

    def __on_motion_enter(self, *args):
        self.__pointer_inside = True
        self.__sync_details_revealer()

    def __on_motion_leave(self, *args):
        self.__pointer_inside = False
        self.__sync_details_revealer()

    def __sync_details_revealer(self):
        self.revealer_details.set_reveal_child(self.__pointer_inside)

    # hide() and show() are essentialy workarounds to avoid keeping
    # the empty space of the hidden entry in the GtkFlowBox
    def hide(self):
        self.get_parent().set_visible(False)

    def show(self):
        self.get_parent().set_visible(True)
