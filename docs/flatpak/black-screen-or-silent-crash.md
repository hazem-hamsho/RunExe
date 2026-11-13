# Black screen or silent crash

When a program shows a black window or closes without an error:

1. Make sure your graphics drivers are up to date, including the Flatpak
   GL runtime: `flatpak update`.
2. In the capsule settings, try turning **DXVK** or **VKD3D** off and on
   again, or switch to another runner version.
3. Start the program from a terminal to see its output:

   ```shell
   flatpak run --command=runexe-cli io.github.runexe.RunExe run -b "CAPSULE" -p "PROGRAM"
   ```

4. If it still fails, open an issue on the
   [RunExe issues page](https://github.com/hazem-hamsho/RunExe/issues) and
   attach the output.
