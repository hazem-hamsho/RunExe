project(
	'runexe',
	version: '1.0',
	meson_version: '>= 1.5.0',
	default_options: [
		'warning_level=2',
	],
	license: 'GPL-3.0-only'
)


APP_NAME = 'RunExe'
DEVELOPER_ID = 'io.github.runexe'
BASE_ID = DEVELOPER_ID + '.RunExe'
APP_ID = BASE_ID
APP_VERSION = meson.project_version()
_version_array = APP_VERSION.split('.')
APP_MAJOR_VERSION = _version_array[0]
APP_MINOR_VERSION = _version_array[1]
PROFILE = get_option('profile')

if PROFILE == 'development'
	git_revision = run_command(
		'git', 'rev-parse', '--short', 'HEAD',
		check: false
	)
	if git_revision.returncode() == 0
		APP_VERSION += '-' + git_revision.stdout().strip()
	else
		APP_VERSION += '-devel'
	endif
	APP_NAME += ' (Development)'
	APP_ID += '.' + 'Devel'
endif


gnome = import('gnome')
i18n = import('i18n')
localedir = get_option('localedir')

subdir('po')
subdir('runexe')
subdir('data')

gnome.post_install(
	glib_compile_schemas: true,
	gtk_update_icon_cache: true,
	update_desktop_database: true,
)
