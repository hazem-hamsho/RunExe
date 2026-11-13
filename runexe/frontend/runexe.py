#!@PYTHON@

# capsules.in
#
# Copyright 2020 mirkobrombin <brombin94@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import gettext
import os
import signal
import sys

APP_VERSION = "@APP_VERSION@"
APP_ID = "@APP_ID@"
pkgdatadir = "@pkgdatadir@"
localedir = "@localedir@"
# noinspection DuplicatedCode
data_gresource_path = f"{pkgdatadir}/data.gresource"
capsules_gresource_path = f"{pkgdatadir}/runexe.gresource"
sys.path.insert(1, pkgdatadir)

# Remove GTK_THEME variable to prevent breakages
# REF: https://github.com/hazem-hamsho/RunExe/pull/2886
os.environ.pop("GTK_THEME", None)

signal.signal(signal.SIGINT, signal.SIG_DFL)

if __name__ == "__main__":
    from gi.repository import Gio

    from runexe.frontend.utils.localization import apply_ui_language

    apply_ui_language(Gio.Settings.new(APP_ID).get_string("ui-language"))
    gettext.install("io.github.runexe.RunExe", localedir)

    data_resource = Gio.Resource.load(data_gresource_path)
    capsules_resource = Gio.Resource.load(capsules_gresource_path)
    # noinspection PyProtectedMember
    data_resource._register()
    capsules_resource._register()

    from runexe.frontend import main

    sys.exit(main.main(APP_VERSION))
