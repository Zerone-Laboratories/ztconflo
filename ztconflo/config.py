# SPDX-License-Identifier: GPL-2.0-or-later
"""Where things live, and the defaults every subcommand shares."""
import os
from dataclasses import dataclass, field
from pathlib import Path

CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "ztconflo"
PMAPORTS_URL = "https://gitlab.postmarketos.org/postmarketOS/pmaports.git"
ALARM_MIRROR = "http://mirror.archlinuxarm.org/$arch/$repo"
USB_HOST = "172.16.42.1"          # the phone, on postmarketOS-style USB networking
DEFAULT_USER = "ztos"


@dataclass
class Settings:
    registry: Path = Path.home() / "Documents/Projects/ztos-devices"
    pmaports: Path = CACHE / "pmaports"
    ztos_src: Path = Path.home() / "Documents/Projects/ztOSMobile"
    out: Path = Path.cwd() / "out"
    work: Path = CACHE / "work"
    jobs: int = os.cpu_count() or 4
    dry_run: bool = False
    extra: dict = field(default_factory=dict)
    # the GSI pipeline: off ("") until GSIs are published; a path, URL or version
    gsi: str = ""
    gsi_cache: Path = CACHE / "gsi"
    device_layer: Path | None = None       # a native phone's device layer as a tarball
    ztos_repo: str = os.environ.get("ZTCONFLO_ZTOS_REPO", "")   # the ztOS pacman repository, when there is one

    @property
    def pmbootstrap_work(self) -> Path:
        return CACHE / "pmbootstrap"
