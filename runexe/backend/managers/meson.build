pkgdatadir = join_paths(get_option('prefix'), get_option('datadir'), meson.project_name())
managersdir = join_paths(pkgdatadir, 'runexe/backend/managers')

runexe_sources = [
  '__init__.py',
  'backup.py',
  'component.py',
  'dependency.py',
  'installer.py',
  'library.py',
  'manager.py',
  'versioning.py',
  'data.py',
  'runtime.py',
  'importer.py',
  'conf.py',
  'journal.py',
  'repository.py',
  'template.py',
  'sandbox.py',
  'queue.py',
  'registry_rule.py',
  'thumbnail.py',
  'playtime.py',
  'eagle.py',
  'eaglenative.py',
  'eagletracing.py',
  'intel.py'
]

install_data(runexe_sources, install_dir: managersdir)
install_data('eagle.yar', install_dir: managersdir)
