# SPDX-License-Identifier: GPL-2.0-or-later
"""Every command the build runs goes through here, so --dry-run prints exactly
what a real run would do, and tests can record it."""
import os
import shlex
import subprocess
import sys

from . import Exit


class Runner:
    """`stream`: send each command's output line by line to `log` instead of
    the terminal (the TUI owns the terminal while it runs)."""

    def __init__(self, dry_run: bool = False, log=None, stream: bool = False):
        self.dry_run = dry_run
        self.log = log or (lambda line: print(line, file=sys.stderr))
        self.stream = stream
        self.commands: list[list[str]] = []

    def step(self, title: str) -> None:
        self.log(f"==> {title}")

    def run(self, cmd: list, root: bool = False, check: bool = True, capture: bool = False,
            env: dict | None = None, cwd=None) -> subprocess.CompletedProcess:
        cmd = [str(c) for c in cmd]
        if root and os.geteuid() != 0:
            # -n: never stop for a password; the CLI asks once up front (sudo -v)
            cmd = ["sudo", "-n", *cmd] if self.stream else ["sudo", *cmd]
        self.commands.append(cmd)
        self.log("    " + " ".join(shlex.quote(c) for c in cmd))
        if self.dry_run:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        full_env = {**os.environ, **env} if env else None
        try:
            if self.stream and not capture:
                return self._streamed(cmd, check, full_env, cwd)
            return subprocess.run(cmd, check=check, capture_output=capture, text=capture,
                                  env=full_env, cwd=cwd)
        except subprocess.CalledProcessError as e:
            raise Exit(Exit.BUILD_FAILED, f"command failed ({e.returncode}): {' '.join(cmd)}") from e
        except FileNotFoundError as e:
            raise Exit(Exit.BUILD_FAILED, f"{cmd[0]} is not installed") from e

    def _streamed(self, cmd, check, env, cwd) -> subprocess.CompletedProcess:
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                              text=True, errors="replace", env=env, cwd=cwd, bufsize=1) as p:
            for line in p.stdout:
                self.log("      " + line.rstrip("\n"))
            code = p.wait()
        if check and code != 0:
            raise subprocess.CalledProcessError(code, cmd)
        return subprocess.CompletedProcess(cmd, code, "", "")
