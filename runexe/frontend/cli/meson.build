pkgdatadir = join_paths(get_option('prefix'), get_option('datadir'), meson.project_name())
clidir = join_paths(pkgdatadir, 'runexe/frontend/cli')

runexe_sources = [
  '__init__.py',
  'utils.py',
]

install_data(runexe_sources, install_dir: clidir)

configure_file(
  input: 'cli.py',
  output: 'runexe-cli',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: ['rwxr-xr-x']
)
