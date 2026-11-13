pkgdatadir = join_paths(get_option('prefix'), get_option('datadir'), meson.project_name())
modelsdir = join_paths(pkgdatadir, 'runexe/backend/models')

runexe_sources = [
  '__init__.py',
  'result.py',
  'samples.py',
  'config.py',
  'enum.py',
  'registry_rule.py',
  'process.py'
]

install_data(runexe_sources, install_dir: modelsdir)
