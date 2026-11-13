# main.py
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

import gettext
import locale
import sys
import webbrowser
from os import environ, path

import gi

from runexe.backend.health import HealthChecker
from runexe.backend.globals import is_official_package
from runexe.backend.logger import Logger
from runexe.backend.utils.threading import RunAsync
from runexe.frontend.params import (
    APP_ID,
    APP_MAJOR_VERSION,
    APP_MINOR_VERSION,
    APP_VERSION,
)

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")
gi.require_version("Xdp", "1.0")
gi.require_version("XdpGtk4", "1.0")

# ruff: noqa: E402
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # type: ignore

from runexe.backend.identity import CALLBACK_SCHEME, forward_identity_callback
from runexe.frontend.utils.gtk import FontScaleManager
from runexe.frontend.windows.window import RunExeWindow

logging = Logger()

# region Translations
"""
This code snippet searches for and uploads translations to different
directories, depending on your production or development environment.
The function _() can be used to create and retrieve translations.
"""
share_dir = path.join(sys.prefix, "share")
base_dir = "."

if getattr(sys, "frozen", False):
    base_dir = path.dirname(sys.executable)
    share_dir = path.join(base_dir, "share")
elif sys.argv[0]:
    exec_dir = path.dirname(path.realpath(sys.argv[0]))
    base_dir = path.dirname(exec_dir)
    share_dir = path.join(base_dir, "share")

    if not path.exists(share_dir):
        share_dir = base_dir

locale_dir = path.join(share_dir, "locale")

if not path.exists(locale_dir):  # development
    locale_dir = path.join(base_dir, "build", "mo")

locale.bindtextdomain("io.github.runexe.RunExe", locale_dir)
locale.textdomain("io.github.runexe.RunExe")
gettext.bindtextdomain("io.github.runexe.RunExe", locale_dir)
gettext.textdomain("io.github.runexe.RunExe")
_ = gettext.gettext


# endregion


class RunExe(Adw.Application):
    arg_exe = None
    arg_capsule = None
    arg_args = None
    dark_provider = None
    journal_window = None

    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            resource_base_path="/io/github/runexe/RunExe",
            flags=(
                Gio.ApplicationFlags.HANDLES_COMMAND_LINE
                | (
                    Gio.ApplicationFlags.NON_UNIQUE
                    if environ.get("CPAK_CONTAINER_ID")
                    else 0
                )
            ),
            register_session=True,
        )
        self.__create_action("quit", self.__quit, ["<primary>q", "<primary>w"])
        self.__create_action("about", self.__show_about_dialog)
        self.__create_action("import", self.__show_importer_view, ["<primary>i"])
        self.__create_action("journal", self.__show_journal, ["<primary>j"])
        self.__create_action("preferences", self.__show_preferences, ["<primary>comma"])
        self.__create_action("help", self.__help, ["F1"])
        self.__create_action("new", self.__new_capsule, ["<primary>n"])

        self.__register_arguments()

    def __register_arguments(self):
        """
        This function registers the command line arguments.
            --version, -v: Prints the version of the application.
            --executable, -e: The path of the executable to be launched.
            --lnk, -l: The path of the shortcut to be launched.
            --capsule, -b: The name of the capsule to be used.
            --arguments, -a: The arguments to be passed to the executable.
            --help, -h: Prints the help.
        """
        self.add_main_option(
            "version",
            ord("v"),
            GLib.OptionFlags.NONE,
            GLib.OptionArg.NONE,
            _("Show version"),
            None,
        )
        self.add_main_option(
            "executable",
            ord("e"),
            GLib.OptionFlags.NONE,
            GLib.OptionArg.STRING,
            _("Executable path"),
            None,
        )
        self.add_main_option(
            "lnk",
            ord("l"),
            GLib.OptionFlags.NONE,
            GLib.OptionArg.STRING,
            _("lnk path"),
            None,
        )
        self.add_main_option(
            "capsule",
            ord("b"),
            GLib.OptionFlags.NONE,
            GLib.OptionArg.STRING,
            _("Capsule name"),
            None,
        )
        self.add_main_option(
            "arguments",
            ord("a"),
            GLib.OptionFlags.NONE,
            GLib.OptionArg.STRING,
            _("Pass arguments"),
            None,
        )
        self.add_main_option(
            GLib.OPTION_REMAINING,
            0,
            GLib.OptionFlags.NONE,
            GLib.OptionArg.STRING_ARRAY,
            "URI",
            None,
        )

    def do_command_line(self, command):
        """
        This function is called when the application is launched from the
        command line. It parses the command line arguments and calls the
        corresponding functions.
        See: __register_arguments()
        """
        commands = command.get_options_dict()

        if commands.contains("executable"):
            self.arg_exe = commands.lookup_value("executable").get_string()

        if commands.contains("version"):
            print(APP_VERSION)
            quit()

        if commands.contains("capsule"):
            self.arg_capsule = commands.lookup_value("capsule").get_string()

        if commands.contains("arguments"):
            self.arg_args = commands.lookup_value("arguments").get_string()

        if not self.arg_exe:
            """
            If no executable is specified, look if it was passed without
            the --executable argument.
            """
            for a in sys.argv:
                if a.endswith((".exe", ".msi", ".bat", ".lnk")):
                    self.arg_exe = a
                    logging.info(
                        _("Launching with executable: {0}").format(a),
                    )

        uri = commands.lookup_value(GLib.OPTION_REMAINING)
        if uri:
            return self.__process_uri(uri)

        if self.arg_exe:
            if self.arg_capsule:
                import subprocess

                cmd = [
                    "runexe-cli",
                    "run",
                    "-b",
                    self.arg_capsule,
                    "-e",
                    self.arg_exe,
                ]
                if self.arg_args:
                    cmd += ["-a", self.arg_args]
                subprocess.Popen(cmd)
                return 0

            try:
                from runexe.frontend.windows.capsulepicker import CapsulePickerDialog

                dialog = CapsulePickerDialog(application=self, arg_exe=self.arg_exe)
                dialog.present()
                return 0
            except Exception as e:
                logging.error(_("Error while processing executable: {0}").format(e))
                return False

        self.do_activate()
        return 0

    def __process_uri(self, uri):
        """
        This function processes the URI passed to the application.
        e.g. xdg-open capsules:run/<capsule>/<program>
        """
        uri = uri[0]

        if uri.partition(":")[0].lower() == CALLBACK_SCHEME:
            if forward_identity_callback(
                uri,
                environ.get("XDG_ACTIVATION_TOKEN", ""),
            ):
                return 0
            logging.warning(_("Unable to deliver the Microsoft identity callback"))
            return 1

        logging.info(
            _("Launching with URI: {0}").format(uri),
        )

        if uri.startswith("capsules:run/"):
            if len(uri.split("/")) != 3:
                logging.error(_("Invalid URI (syntax: capsules:run/<capsule>/<program>)"))
                return False

            uri = uri.replace("capsules:run/", "")
            capsule, program = uri.split("/")

            import subprocess

            subprocess.Popen(["runexe-cli", "run", "-b", capsule, "-p", program])
            return 0

        try:
            from runexe.frontend.windows.capsulepicker import CapsulePickerDialog

            dialog = CapsulePickerDialog(application=self, arg_exe=uri)
            dialog.present()
            return 0
        except Exception as e:
            logging.error(
                _("Error while processing URI: {0}").format(e),
            )
            return False

    def do_shutdown(self):
        """
        This function is called when the application is shutting down.
        """
        logging.info(_("[Shutdown] cleanup starting."))
        if hasattr(self, "win") and self.win and hasattr(self.win, "manager"):
            if self.win.manager:
                self.win.manager.shutdown()
        Adw.Application.do_shutdown(self)

    def do_startup(self):
        """
        This function is called when the application is started.
        Here we register the application actions (shortcuts).
        See: __register_actions()
        """
        Adw.Application.do_startup(self)
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.IconTheme.get_for_display(display).add_resource_path(
                "/io/github/runexe/RunExe/icons"
            )
        self._font_scale_manager = FontScaleManager(Gio.Settings.new(APP_ID))

        logging.info(f"RunExe {APP_VERSION}")
        RunAsync(self.__log_environment)

    def __log_environment(self):
        try:
            logging.info(HealthChecker().get_results(plain=True))
        except Exception as e:
            logging.debug(f"Could not collect startup environment info: {e}")

    def do_activate(self):
        """
        This function is called when the application is activated.
        """

        logging.info(
            _("[Activate] request received."),
        )

        # create the main window
        Adw.Application.do_activate(self)
        win = self.props.active_window
        if not win:
            win = RunExeWindow(application=self, arg_capsule=self.arg_capsule)
        self.win = win

        win.present()

    def __quit(self, *args):
        """
        This function close the application.
        It is used by the [Ctrl+Q] shortcut.
        """
        logging.info(
            _("[Quit] request received."),
        )
        if hasattr(self, "win") and self.win:
            self.win.on_close_request()
            if hasattr(self.win, "manager") and self.win.manager:
                self.win.manager.shutdown()

        self.quit()

    @staticmethod
    def __help(action=None, param=None):
        """
        This function open the documentation in the user's default browser.
        It is used by the [F1] shortcut.
        """
        logging.info(
            _("[Help] request received."),
        )
        webbrowser.open_new_tab("https://github.com/hazem-hamsho/RunExe/tree/main/docs")

    def __refresh(self, action=None, param=None):
        """
        This function refresh the user capsule list.
        It is used by the [Ctrl+R] shortcut.
        """
        logging.info(
            _("[Refresh] request received."),
        )
        self.win.manager.update_capsules()

    def __show_preferences(self, *args):
        self.win.show_prefs_view()

    def __new_capsule(self, *args):
        self.win.show_add_view()

    def __show_importer_view(self, widget=False, *args):
        self.win.main_leaf.set_visible_child(self.win.page_importer)

    def __show_journal(self, *args):
        from runexe.frontend.windows.journal import JournalDialog

        if self.journal_window:
            self.journal_window.present()
            return

        self.journal_window = JournalDialog(application=self)
        self.journal_window.set_transient_for(self.win)
        self.journal_window.connect("close-request", self.__on_journal_close)
        self.journal_window.present()

    def __on_journal_close(self, *_args):
        self.journal_window = None
        return False

    def __show_about_dialog(self, *_args):
        developers = [
            "hazem-hamsho https://github.com/hazem-hamsho",
        ]

        artists = [
            "hazem-hamsho https://github.com/hazem-hamsho",
        ]

        about_dialog = Adw.AboutDialog.new_from_appdata(
            "/io/github/runexe/RunExe/appdata",
            f"{APP_MAJOR_VERSION}.{APP_MINOR_VERSION}",
        )
        version = f"{APP_MAJOR_VERSION}.{APP_MINOR_VERSION}"
        if is_official_package():
            about_dialog.set_version(f"{version} ({_('Official Package')})")
        else:
            about_dialog.set_version(f"{version} ({_('Unofficial Package')})")
            about_dialog.set_comments(_("Might not work as expected."))
        about_dialog.set_developers(developers)
        about_dialog.set_translator_credits(_("translator_credits"))
        about_dialog.set_artists(artists)
        about_dialog.set_debug_info(HealthChecker().get_results(plain=True))
        about_dialog.add_link(_("Source Code"), "https://github.com/hazem-hamsho/RunExe")
        about_dialog.add_legal_section(
            _("Eagle Intelligence Data"),
            _("Data derived from ProtonDB and winetricks"),
            Gtk.License.CUSTOM,
            _(
                "ProtonDB community reports: ODbL 1.0, contents DbCL 1.0.\n"
                "winetricks data: LGPL-2.1-or-later."
            ),
        )
        about_dialog.set_copyright(
            _("Copyright © 2026 {developer_name}").format(
                developer_name=about_dialog.get_developer_name()
            )
        )
        about_dialog.add_acknowledgement_section(
            _("Third-Party Libraries and Special Thanks"),
            [
                "DXVK https://github.com/doitsujin/dxvk",
                "VKD3D https://github.com/HansKristian-Work/vkd3d-proton",
                "Wine-TKG https://github.com/Frogging-Family/wine-tkg-git",
                "Wine https://www.winehq.org",
                "orjson https://github.com/ijl/orjson",
                "libadwaita https://gitlab.gnome.org/GNOME/libadwaita",
                "icoextract https://github.com/jlu5/icoextract",
                "FVS https://github.com/mirkobrombin/FVS",
                "pathvalidate https://github.com/thombashi/pathvalidate",
                "protondb-data https://github.com/bdefore/protondb-data",
                "winetricks https://github.com/Winetricks/winetricks",
            ],
        )
        about_dialog.present(self.win)

    def __create_action(self, name, callback, shortcuts=None, param=None):
        """Add an application action.

        Args:
            name: the name of the action
            callback: the function to be called when the action is
              activated
            shortcuts: an optional list of accelerators
            param: an optional list of parameters for the action
        """
        action = Gio.SimpleAction.new(name, param)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)


GObject.threads_init()


def main(version):
    app = RunExe()
    return app.run(sys.argv)
