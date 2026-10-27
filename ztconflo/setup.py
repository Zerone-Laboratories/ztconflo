# SPDX-License-Identifier: GPL-2.0-or-later
"""What a build needs on this computer, and installing what is missing.

Every tool is an Arch package, so one `sudo pacman -S --needed` does it.
Runs by itself the first time ztConflo starts, and on `ztconflo setup`."""
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import CACHE

DONE = CACHE / "setup-done"


@dataclass
class Need:
    name: str            # what the person sees
    package: str         # the Arch package that brings it
    why: str
    required: bool = True

    def present(self) -> bool:
        if self.name == "qemu aarch64":
            return Path("/proc/sys/fs/binfmt_misc/qemu-aarch64").exists()
        if self.name == "textual":
            try:
                import textual  # noqa: F401
                return True
            except ImportError:
                return False
        if self.name == "mke2fs":
            # e2fsprogs' own: an Android SDK copy in PATH does not count
            return Path("/usr/bin/mke2fs").exists()
        return shutil.which(self.name) is not None

    def where(self) -> str:
        if self.name == "mke2fs":
            return "/usr/bin/mke2fs"
        if self.name in ("qemu aarch64", "textual"):
            return "installed"
        return shutil.which(self.name) or ""


NEEDS = [
    Need("pmbootstrap", "pmbootstrap", "builds the kernel side"),
    Need("pacstrap", "arch-install-scripts", "makes the root filesystem"),
    Need("arch-chroot", "arch-install-scripts", "builds ztMETRO inside it"),
    Need("mkbootimg", "android-tools", "makes boot.img"),
    Need("img2simg", "android-tools", "sparse images for fastboot"),
    Need("mke2fs", "e2fsprogs", "makes rootfs.img"),
    Need("fdtput", "dtc", "enables dtb nodes an entry names"),
    Need("rsync", "rsync", "copies the ztOS sources in"),
    Need("git", "git", "fetches pmaports"),
    Need("qemu aarch64", "qemu-user-static-binfmt", "runs aarch64 programs on this computer"),
    Need("fastboot", "android-tools", "flashing most phones", required=False),
    Need("heimdall", "heimdall", "flashing Samsung phones", required=False),
    Need("textual", "python-textual", "the terminal UI", required=False),
]
EXTRA_PACKAGES = {"qemu-user-static-binfmt": ["qemu-user-static"]}


def missing() -> list[Need]:
    return [n for n in NEEDS if not n.present()]


def packages_for(needs: list[Need]) -> list[str]:
    pkgs = []
    for n in needs:
        for p in [n.package, *EXTRA_PACKAGES.get(n.package, [])]:
            if p not in pkgs:
                pkgs.append(p)
    return pkgs


def is_arch() -> bool:
    """Arch, or a distribution built on it (CachyOS, EndeavourOS, Manjaro)."""
    try:
        text = Path("/etc/os-release").read_text()
    except OSError:
        return False
    ids = " ".join(line.split("=", 1)[1].strip('"') for line in text.splitlines()
                   if line.startswith(("ID=", "ID_LIKE=")))
    return "arch" in ids.split()


def install(needs: list[Need] | None = None, out=print, run=subprocess.run) -> bool:
    """Install what is missing with pacman. Prints what it does; returns
    whether everything is there afterwards."""
    needs = missing() if needs is None else needs
    if not needs:
        DONE.parent.mkdir(parents=True, exist_ok=True)
        DONE.touch()
        return True
    pkgs = packages_for(needs)
    out("ztConflo needs these on this computer: " + ", ".join(n.name for n in needs))
    if not is_arch():
        out("This is not an Arch-based system: install the equivalents of these packages by hand:\n  "
            + " ".join(pkgs))
        return False
    cmd = ["sudo", "pacman", "-S", "--needed", "--noconfirm", *pkgs]
    out("Installing them (needs your password for sudo):\n  " + " ".join(cmd))
    code = run(cmd).returncode
    if "qemu-user-static-binfmt" in pkgs and code == 0:
        # registers the binfmt handlers now, without a reboot
        run(["sudo", "systemctl", "restart", "systemd-binfmt"])
    left = [n for n in missing() if n.required]
    if code != 0 or left:
        out("Not everything installed" + (": " + ", ".join(n.name for n in left) if left else "")
            + f" (pacman exit {code}).")
        return False
    DONE.parent.mkdir(parents=True, exist_ok=True)
    DONE.touch()
    out("All set.")
    return True


def first_run(out=print) -> None:
    """The first time ztConflo starts on this computer: install what is
    missing. Later runs leave it to `ztconflo setup` (or the TUI's button)."""
    if DONE.exists():
        return
    if not sys.stdin.isatty():
        return          # never prompt for a password where no one can answer
    install(out=out)
