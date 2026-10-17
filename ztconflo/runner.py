# SPDX-License-Identifier: GPL-2.0-or-later
"""Every command the build runs goes through here, so --dry-run prints exactly
what a real run would do, and tests can record it."""
import os
import shlex
import subprocess
import sys

from . import Exit


class Runner:
    def __init__(self, dry_run: bool = False, log=None):
        self.dry_run = dry_run
        self.log = log or (lambda line: print(line, file=sys.stderr))
        self.commands: list[list[str]] = []

    def step(self, title: str) -> None:
        self.log(f"==> {title}")

    def run(self, cmd: list, root: bool = False, check: bool = True, capture: bool = False,
            env: dict | None = None, cwd=None) -> subprocess.CompletedProcess:
        cmd = [str(c) for c in cmd]
        if root and os.geteuid() != 0:
            cmd = ["sudo", *cmd]
        self.commands.append(cmd)
        self.log("    " + " ".join(shlex.quote(c) for c in cmd))
        if self.dry_run:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        try:
            return subprocess.run(cmd, check=check, capture_output=capture, text=capture,
                                  env={**os.environ, **env} if env else None, cwd=cwd)
        except subprocess.CalledProcessError as e:
            raise Exit(Exit.BUILD_FAILED, f"command failed ({e.returncode}): {' '.join(cmd)}") from e
        except FileNotFoundError as e:
            raise Exit(Exit.BUILD_FAILED, f"{cmd[0]} is not installed") from e
