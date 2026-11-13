pkgdatadir = join_paths(get_option('prefix'), get_option('datadir'), meson.project_name())
reposdir = join_paths(pkgdatadir, 'runexe/backend/repos')

runexe_sources = [
  '__init__.py',
  'repo.py',
  'dependency.py',
  'component.py',
  'installer.py',
]

install_data(runexe_sources, install_dir: reposdir)
