<div align="center">
  <img src="data/icons/hicolor/scalable/apps/io.github.runexe.RunExe.svg" width="64">
  <h1 align="center">RunExe</h1>
  <p align="center">Run Windows Software on Linux</p>
</div>

<br/>

<div align="center">
  <a href="https://github.com/hazem-hamsho/RunExe/blob/main/COPYING.md"><img src="https://img.shields.io/badge/License-GPL--3.0-blue.svg"></a>

  <hr />

  <a href="docs/README.md">Documentation</a> ·
  <a href="https://github.com/hazem-hamsho/RunExe/issues">Issues</a> ·
  <a href="https://github.com/hazem-hamsho/RunExe/releases">Releases</a>
</div>

<br/>

![RunExe](docs/banner.svg)

## Installation

RunExe is built and installed as a Flatpak from this repository.

1. Install [`org.flatpak.Builder`](https://github.com/flathub/org.flatpak.Builder) from Flathub
2. Clone the repository:

   ```shell
   git clone https://github.com/hazem-hamsho/RunExe.git
   cd RunExe
   ```

3. Build and install:

   ```shell
   flatpak run org.flatpak.Builder --user --install --install-deps-from=flathub --force-clean build-dir build-aux/io.github.runexe.RunExe.Devel.json
   ```

4. Launch it:

   ```shell
   flatpak run io.github.runexe.RunExe.Devel
   ```

⚠️ Back up your data before testing development builds.

## Resources

Runners, components, dependencies and installers are downloaded from:

- [runexe-components](https://github.com/hazem-hamsho/runexe-components)
- [runexe-dependencies](https://github.com/hazem-hamsho/runexe-dependencies)
- [runexe-programs](https://github.com/hazem-hamsho/runexe-programs)

## Issues

Report problems on the [issues page](https://github.com/hazem-hamsho/RunExe/issues).

## License

RunExe is free software, released under the [GNU General Public License v3](COPYING.md).
