import os
import subprocess

from runexe.backend.logger import Logger
from runexe.backend.utils.manager import ManagerUtils
from runexe.backend.utils.proc import ProcUtils
from runexe.backend.wine.wineprogram import WineProgram

logging = Logger()


class WineServer(WineProgram):
    program = "Wine Server"
    command = "wineserver"

    def is_alive(self):
        config = self.config

        # If the config has no Runner, skip the execution
        if not config.Runner:
            return False

        capsule = ManagerUtils.get_capsule_path(config)

        if not os.path.isdir(capsule):
            return False

        try:
            res = self._run(["-w"])
        except FileNotFoundError:
            return False
        try:
            res.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            res.kill()
            res.wait()
            return True
        return False

    def _run(self, args):
        config = self.config
        capsule = ManagerUtils.get_capsule_path(config)
        runner = ManagerUtils.get_runner_path(config.Runner)

        env = os.environ.copy()
        env["WINEPREFIX"] = capsule
        command = "wineserver"
        if not config.Runner.startswith("sys-"):
            command = os.path.join(runner, "bin", "wineserver")
            env["PATH"] = f"{runner}/bin:{env['PATH']}"

        return subprocess.Popen(
            [command, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=capsule,
            env=env,
        )

    def _wait(self, args, timeout=None):
        process = self._run(args)
        try:
            if timeout is None:
                return process.wait()
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise

    def wait(self, timeout=None):
        return self._wait(["-w"], timeout)

    def kill(self, signal: int = -1, timeout=None):
        args = "-k"
        if signal != -1:
            args += str(signal)

        if not self.silent:
            logging.info("Using Wine Server -- sending signal to the wine server")
        self.returncode = self._wait([args], timeout)

    def stop(self, timeout=30):
        if self.config.Parameters.sandbox:
            from runexe.backend.managers.sandbox import SandboxManager

            capsule = ManagerUtils.get_capsule_path(self.config)
            SandboxManager.terminate_prefix(capsule, timeout=timeout)
            return True

        self.kill(timeout=timeout)
        return self.returncode == 0 and self.wait(timeout=timeout) == 0

    def force_kill(self):
        capsule = ManagerUtils.get_capsule_path(self.config)

        # Under a dedicated sandbox the processes are not visible on the host,
        # so kill the tracked sandbox launchers (their process groups) instead.
        if self.config.Parameters.sandbox:
            from runexe.backend.managers.sandbox import SandboxManager

            SandboxManager.terminate_prefix(capsule)
            return

        procs = ProcUtils.get_by_env(f"WINEPREFIX={capsule}")
        for proc in procs:
            proc.kill(9)

        self.kill(9)
