pkgdatadir = join_paths(get_option('prefix'), get_option('datadir'), meson.project_name())
utilsdir = join_paths(pkgdatadir, 'runexe/backend/utils')

runexe_sources = [
  '__init__.py',
  'display.py',
  'gpu.py',
  'hidraw.py',
  'launch_options.py',
  'manager.py',
  'vulkan.py',
  'terminal.py',
  'file.py',
  'generic.py',
  'wine.py',
  'lnk.py',
  'decorators.py',
  'snake.py',
  'imagemagick.py',
  'proc.py',
  'yaml.py',
  'nvidia.py',
  'threading.py',
  'connection.py',
  'gsettings_stub.py',
  'json.py',
  'singleton.py'
]

install_data(runexe_sources, install_dir: utilsdir)
