pkgdatadir = join_paths(get_option('prefix'), get_option('datadir'), meson.project_name())
dllsdir = join_paths(pkgdatadir, 'runexe/backend/dlls')

runexe_sources = [
  '__init__.py',
  'dll.py',
  'dxvk.py',
  'vkd3d.py',
]

install_data(runexe_sources, install_dir: dllsdir)
