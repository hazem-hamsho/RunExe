# Giving RunExe access to more folders

The Flatpak version of RunExe can only see a few folders by default. To let it
open files somewhere else (for example a second drive), grant access with:

```shell
flatpak override --user --filesystem=/path/to/folder io.github.runexe.RunExe
```

You can also use the graphical tool **Flatseal** to manage these permissions.
Restart RunExe after changing them.
