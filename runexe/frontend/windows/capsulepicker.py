# capsulepicker.py
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

import subprocess

from gi.repository import Adw, Gio, Gtk

from runexe.backend.managers.manager import Manager
from runexe.backend.models.config import CapsuleConfig
from runexe.frontend.params import APP_ID


class CapsuleEntry(Adw.ActionRow):
    def __init__(self, config: CapsuleConfig):
        super().__init__()
        self.capsule = config.Name
        self.set_title(config.Name)


@Gtk.Template(resource_path="/io/github/runexe/RunExe/dialog-capsule-picker.ui")
class CapsulePickerDialog(Adw.ApplicationWindow):
    """This class should not be called from the application GUI, only from CLI."""

    __gtype_name__ = "CapsulePickerDialog"
    settings = Gio.Settings.new(APP_ID)
    Adw.init()

    # region Widgets
    btn_cancel = Gtk.Template.Child()
    btn_select = Gtk.Template.Child()
    list_capsules = Gtk.Template.Child()
    btn_open = Gtk.Template.Child()

    # endregion

    def __init__(self, arg_exe, **kwargs):
        super().__init__(**kwargs)
        self.arg_exe = arg_exe
        mng = Manager(g_settings=self.settings, is_cli=True)
        mng.check_capsules()
        capsules = mng.local_capsules

        for _, config in capsules.items():
            self.list_capsules.append(CapsuleEntry(config))

        self.list_capsules.select_row(self.list_capsules.get_first_child())
        self.btn_cancel.connect("clicked", self.__close)
        self.btn_select.connect("clicked", self.__select)
        self.btn_open.connect("clicked", self.__open)

    @staticmethod
    def __close(*_args):
        quit()

    def __select(self, *_args):
        row = self.list_capsules.get_selected_row()
        if row:
            self.destroy()
            subprocess.Popen(
                [
                    "runexe-cli",
                    "run",
                    "-b",
                    f'"{row.capsule}"',
                    "-e",
                    f'"{self.arg_exe}"',
                ]
            )

    def __open(self, *_args):
        self.destroy()
        subprocess.Popen(["capsules"])
