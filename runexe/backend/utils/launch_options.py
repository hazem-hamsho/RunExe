# launch_options.py
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

import shlex


def handle_launch_options(launch_options: str) -> tuple[str, str, dict[str, str]]:
    """
    Split program launch options. Supports the %command% pattern.
    Return prefix, arguments, and environment variables.
    """
    env_vars = {}
    prefix, args = "", ""
    if "%command%" in launch_options:
        _c = launch_options.split("%command%")
        prefix = _c[0] if len(_c) > 0 else ""
        args = _c[1] if len(_c) > 1 else ""
    else:
        args = launch_options

    try:
        prefix_list = shlex.split(prefix.strip())
    except ValueError:
        prefix_list = prefix.split(shlex.quote(prefix.strip()))

    for p in prefix_list.copy():
        if "=" in p:
            k, v = p.split("=", 1)
            v = shlex.quote(v) if " " in v else v
            env_vars[k] = v
            prefix_list.remove(p)

    prefix = " ".join(prefix_list)
    return prefix, args, env_vars
