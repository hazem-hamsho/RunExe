pkgdatadir = join_paths(get_option('prefix'), get_option('datadir'), meson.project_name())
viewsdir = join_paths(pkgdatadir, 'runexe/frontend/views')

runexe_sources = [
  '__init__.py',
  'new_capsule_dialog.py',
  'list.py',
  'library.py',
  'details.py',
  'preferences.py',
  'importer.py',
  'loading.py',

  'capsule_details.py',
  'capsule_installers.py',
  'capsule_dependencies.py',
  'capsule_registry_rules.py',
  'capsule_preferences.py',
  'capsule_versioning.py',
  'capsule_taskmanager.py',
  'eagle.py'
]

install_data(runexe_sources, install_dir: viewsdir)
