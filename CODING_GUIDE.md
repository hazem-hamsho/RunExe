## Build & Run locally

### use flatpak

#### Build & install

```bash
flatpak-builder --install --user --force-clean ./.flatpak-builder/out ./build-aux/io.github.runexe.RunExe.Devel.json
```

#### Run

```bash
flatpak run io.github.runexe.RunExe.Devel
```

#### Uninstall devel version

```bash
flatpak uninstall io.github.runexe.RunExe.Devel
```

## Unit Test

### run all tests

```bash
pytest .
```

## Dependencies

Regenerate PYPI dependency manifest when requirements.txt changed

```bash
python ./build-aux/flatpak-pip-generator.py --runtime org.gnome.Sdk -r requirements.txt -o io.github.runexe.RunExe.pypi-deps --yaml
```

## I18n files

### `po/POTFILES`

List of source files containing translatable strings.
Regenerate this file when you added/moved/removed/renamed files
that contains translatable strings.

```bash
cat > po/POTFILES <<EOF
# List of source files containing translatable strings.
# Please keep this file sorted alphabetically.
EOF
grep -rlP "_\(['\"]" capsules | sort >> po/POTFILES
cat >> po/POTFILES <<EOF
data/io.github.runexe.RunExe.desktop.in.in
data/io.github.runexe.RunExe.gschema.xml
data/io.github.runexe.RunExe.metainfo.xml.in.in
EOF
```

### `po/runexe.pot` and `po/*.po`

We have a main pot file, which is template for other `.po` files
And for each language listed in `po/LINGUAS` we have a corresponding `.po` file
Regenerate these files when any translatable string added/changed/removed

```bash
# make sure you have `meson` and `blueprint-compiler` installed
meson setup /tmp/i18n-build
meson compile -C /tmp/i18n-build/ runexe-pot
meson compile -C /tmp/i18n-build/ runexe-update-po
```
