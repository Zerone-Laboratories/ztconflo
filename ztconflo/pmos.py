# SPDX-License-Identifier: GPL-2.0-or-later
"""postmarketOS: reading pmaports, and building the kernel side with pmbootstrap.

Nothing here sources a shell file: deviceinfo and APKBUILD are read as text,
so a pmaports checkout is never run as code on the host."""
import re
import shlex
import shutil
import subprocess
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

from . import Exit

CATEGORIES = ("main", "community", "testing", "downstream", "archived")
ACCEPTED = ("main", "community", "testing")

_ASSIGN = re.compile(r'^(deviceinfo_\w+)="(.*)"\s*(?:#.*)?$')


def parse_deviceinfo(text: str) -> dict:
    """deviceinfo_key="value" lines, as {key: value} without the prefix."""
    info = {}
    for raw in text.splitlines():
        line = raw.strip()
        m = _ASSIGN.match(line)
        if m:
            info[m.group(1)[len("deviceinfo_"):]] = m.group(2)
    return info


def parse_cmdline(text: str) -> str:
    """kernel-cmdline.conf: one or more arguments per line, # comments, and
    -word lines that take out an argument pmOS would add by default (ztOS adds
    none, so those are simply dropped)."""
    words = []
    for line in text.splitlines():
        for word in line.split("#", 1)[0].split():
            if not word.startswith("-"):
                words.append(word)
    return " ".join(words)


def _shell_list(apkbuild: str, var: str, start: int = 0) -> list[str]:
    """The words of var="..." (possibly over several lines) at or after `start`."""
    m = re.compile(rf'^\s*{var}="(.*?)"', re.S | re.M).search(apkbuild, start)
    if not m:
        return []
    return [w for w in m.group(1).split() if w and not w.startswith("#")]


@dataclass
class Apkbuild:
    text: str

    @property
    def depends(self) -> list[str]:
        return _shell_list(self.text, "depends")

    @property
    def subpackages(self) -> list[str]:
        return _shell_list(self.text, "subpackages")

    def function_depends(self, func: str) -> list[str]:
        m = re.search(rf'^{re.escape(func)}\(\)\s*\{{', self.text, re.M)
        if not m:
            return []
        end = self.text.find("\n}", m.end())
        body = self.text[m.end(): end if end > 0 else None]
        return _shell_list(body, "depends")

    def kernels(self) -> list[tuple[str, str]]:
        """(variant, kernel package) pairs: ("", pkg) for a single kernel in
        depends, or one per $pkgname-kernel-<variant>:<func> subpackage."""
        found = []
        for sub in self.subpackages:
            m = re.match(r'\$pkgname-kernel-([\w.-]+):(\w+)', sub)
            if m:
                for dep in self.function_depends(m.group(2)):
                    if _is_kernel(dep):
                        found.append((m.group(1), _bare(dep)))
        if not found:
            found = [("", _bare(d)) for d in self.depends if _is_kernel(d)]
        return found

    @property
    def firmware(self) -> list[str]:
        deps = list(self.depends)
        for sub in self.subpackages:
            m = re.match(r'\$pkgname-[\w.-]+:(\w+)', sub)
            if m and "firmware" in sub:
                deps += self.function_depends(m.group(1))
        return sorted({_bare(d) for d in deps if _bare(d).startswith("firmware-")})

    @property
    def soc_packages(self) -> list[str]:
        return [_bare(d) for d in self.depends if _bare(d).startswith("soc-")]

    @property
    def uses_downstream_kernel_helper(self) -> bool:
        return "downstreamkernel_package" in self.text


def _bare(dep: str) -> str:
    return re.split(r"[<>=~]", dep, maxsplit=1)[0]


def _is_kernel(dep: str) -> bool:
    name = _bare(dep)
    return name.startswith("linux-") and not name.startswith(("linux-firmware", "linux-tools", "linux-headers"))


@dataclass
class PmDevice:
    codename: str            # oneplus-enchilada
    category: str            # community
    folder: Path
    info: dict
    apkbuild: Apkbuild
    cmdline: str = ""
    kernels: list = field(default_factory=list)

    @property
    def device_pkg(self) -> str:
        return "device-" + self.codename

    @property
    def arch(self) -> str:
        return self.info.get("arch", "")


class Pmaports:
    def __init__(self, root: Path):
        self.root = Path(root)

    def exists(self) -> bool:
        return (self.root / "device").is_dir()

    def ensure(self, url: str, dry_run: bool = False) -> None:
        if self.exists():
            return
        if dry_run:
            raise Exit(Exit.NOT_FOUND, f"no pmaports checkout at {self.root} (clone {url} there, or drop --dry-run)")
        self.root.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", url, str(self.root)], check=True)

    def commit(self) -> str:
        """The checkout's commit; "unknown" for a tree that is not its own git
        checkout (never the commit of some repository it happens to sit in)."""
        if not (self.root / ".git").exists():
            return "unknown"
        return subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True).stdout.strip()

    def checkout(self, commit: str) -> None:
        """The exact commit an entry was frozen at (fetched when the clone is shallow)."""
        def git(*a, check=True):
            return subprocess.run(["git", "-C", str(self.root), *a], check=check, capture_output=True, text=True)
        if git("cat-file", "-e", commit + "^{commit}", check=False).returncode != 0:
            git("fetch", "--depth", "1", "origin", commit)
        git("checkout", "--quiet", commit)

    def device_folders(self):
        for category in CATEGORIES:
            base = self.root / "device" / category
            if base.is_dir():
                for folder in sorted(base.glob("device-*")):
                    if (folder / "deviceinfo").is_file():
                        yield category, folder

    def load(self, category: str, folder: Path) -> PmDevice:
        info = parse_deviceinfo((folder / "deviceinfo").read_text(errors="replace"))
        apk = Apkbuild((folder / "APKBUILD").read_text(errors="replace") if (folder / "APKBUILD").is_file() else "")
        cmdline_file = folder / "kernel-cmdline.conf"
        if cmdline_file.is_file():
            cmdline = parse_cmdline(cmdline_file.read_text(errors="replace"))
        else:
            cmdline = " ".join(info.get("kernel_cmdline", "").split())
        codename = info.get("codename") or folder.name[len("device-"):]
        return PmDevice(codename, category, folder, info, apk, cmdline, apk.kernels())

    def all(self) -> list[PmDevice]:
        return [self.load(c, f) for c, f in self.device_folders()]

    def find(self, codename: str) -> PmDevice | None:
        exact = [(c, f) for c, f in self.device_folders() if f.name == "device-" + codename]
        if not exact:
            exact = [(c, f) for c, f in self.device_folders() if f.name.endswith("-" + codename)]
        if len(exact) > 1:
            names = ", ".join(f.name[len("device-"):] for _, f in exact)
            raise Exit(Exit.NOT_FOUND, f"'{codename}' is ambiguous in pmaports: {names}")
        return self.load(*exact[0]) if exact else None

    def kernel_folder(self, kernel_pkg: str) -> tuple[str, Path] | None:
        for category in CATEGORIES:
            folder = self.root / "device" / category / kernel_pkg
            if (folder / "APKBUILD").is_file():
                return category, folder
        return None

    def kernel_version(self, kernel_pkg: str) -> str:
        found = self.kernel_folder(kernel_pkg)
        if not found:
            return ""
        m = re.search(r'^pkgver=(\S+)', (found[1] / "APKBUILD").read_text(errors="replace"), re.M)
        return m.group(1).strip('"') if m else ""


# ---- building the kernel side -----------------------------------------------------------

def pmbootstrap(work: Path, aports: Path, *args: str, jobs: int = 4, run=subprocess.run):
    tool = shutil.which("pmbootstrap")
    if not tool:
        raise Exit(Exit.BUILD_FAILED, "pmbootstrap is not installed (pip install --user pmbootstrap, or the AUR package)")
    cmd = [tool, "--work", str(work), "--aports", str(aports), "--details-to-stdout", *args]
    return run(cmd, check=True)


def work_version() -> str:
    """The work folder version the installed pmbootstrap expects, asked of its own
    interpreter: `pmbootstrap init` writes it, and without it pmbootstrap takes a
    fresh work folder for an old one it cannot migrate."""
    tool = shutil.which("pmbootstrap")
    if not tool:
        raise Exit(Exit.BUILD_FAILED, "pmbootstrap is not installed (pip install --user pmbootstrap, or the AUR package)")
    with open(tool, "rb") as f:
        line = f.readline().decode(errors="replace")
    python = shlex.split(line[2:])[0] if line.startswith("#!") else "python3"
    out = subprocess.run([python, "-c", "import pmb.config; print(pmb.config.work_version)"],
                         capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        raise Exit(Exit.BUILD_FAILED, f"cannot read pmbootstrap's work folder version: {out.stderr.strip()}")
    return out.stdout.strip()


def build_packages(work: Path, aports: Path, packages: list[str], jobs: int, run=subprocess.run) -> list[Path]:
    """pmbootstrap build for each package, for aarch64; the .apk files it made."""
    pmbootstrap(work, aports, "build", "--arch", "aarch64", "--force", *packages, jobs=jobs, run=run)
    repo = work / "packages"
    apks = []
    for pkg in packages:
        found = sorted(repo.glob(f"*/aarch64/{pkg}-[0-9]*.apk"))
        if not found and run is subprocess.run:
            raise Exit(Exit.BUILD_FAILED, f"pmbootstrap built no .apk for {pkg} under {repo}")
        apks += found[-1:]
    return apks


def unpack_apk(apk: Path, dest: Path) -> None:
    """An .apk is concatenated gzip tarballs: tar reads straight through."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(apk, "r:gz") as tar:
        members = [m for m in tar.getmembers() if not m.name.startswith(".") or m.name.startswith("./")]
        members = [m for m in members if not Path(m.name).name.startswith(".SIGN.") and m.name not in (".PKGINFO",)]
        tar.extractall(dest, members=members, filter="tar")


def quote(cmd: list) -> str:
    return " ".join(shlex.quote(str(c)) for c in cmd)
