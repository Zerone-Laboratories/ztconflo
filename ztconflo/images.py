# SPDX-License-Identifier: GPL-2.0-or-later
"""The two images: an ext4 root filesystem and an Android boot image."""
import hashlib
import os
from pathlib import Path

from .runner import Runner

# e2fsprogs' own, by path: an Android SDK's mke2fs often comes first in PATH
MKE2FS = "/usr/bin/mke2fs" if os.path.exists("/usr/bin/mke2fs") else "mke2fs"
ROOT_LABEL = "pmOS_root"      # v1: what the reused postmarketOS initramfs looks for


def hash_tree(paths: list[Path]) -> str:
    """One sha256 over files in a stable order: names and contents, so a
    renamed or changed module both show."""
    h = hashlib.sha256()
    files = []
    for p in paths:
        p = Path(p)
        if p.is_file():
            files.append((p.name, p))
        elif p.is_dir():
            files += [(str(f.relative_to(p.parent)), f) for f in p.rglob("*") if f.is_file() and not f.is_symlink()]
    for name, f in sorted(files):
        h.update(name.encode() + b"\0")
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


def rootfs_image(run: Runner, tree: Path, out: Path, size: str = "", label: str = ROOT_LABEL) -> Path:
    """ext4 straight from a directory (mke2fs -d), no loop mount. The tree is
    owned by root, so this runs as root too."""
    size = size or _size_for(tree, run.dry_run)
    run.run(["rm", "-f", out])
    run.run([MKE2FS, "-q", "-t", "ext4", "-L", label, "-d", tree, "-E", "root_owner=0:0", out, size], root=True)
    run.run(["chown", f"{_uid()}:{_gid()}", out], root=True)
    return out


def _size_for(tree: Path, dry_run: bool) -> str:
    """The tree's size plus room to grow on first boot, in MiB."""
    if dry_run or not tree.exists():
        return "4096M"
    used = sum(f.stat().st_size for f in tree.rglob("*") if f.is_file() and not f.is_symlink())
    return f"{int(used / (1 << 20) * 1.3) + 512}M"


def _uid():
    return os.getuid()


def _gid():
    return os.getgid()


def sparse(run: Runner, image: Path) -> Path:
    out = image.with_suffix(".sparse.img")
    run.run(["img2simg", image, out])
    run.run(["mv", out, image])
    return image


def boot_image(run: Runner, boot: dict, kernel: Path, initramfs: Path, dtb: Path | None, out: Path) -> Path:
    """mkbootimg with every [boot] field. With append_dtb the dtb is glued
    onto the kernel, as these bootloaders expect."""
    if boot.get("append_dtb") and dtb:
        glued = out.parent / "kernel-dtb"
        if run.dry_run:
            run.log(f"    cat {kernel} {dtb} > {glued}")
        else:
            glued.write_bytes(Path(kernel).read_bytes() + Path(dtb).read_bytes())
        kernel = glued
    base = int(boot.get("base", "0x10000000"), 16)
    cmd = ["mkbootimg", "--kernel", kernel, "--ramdisk", initramfs,
           "--base", hex(base),
           "--kernel_offset", boot.get("kernel_offset", "0x00008000"),
           "--ramdisk_offset", boot.get("ramdisk_offset", "0x01000000"),
           "--second_offset", boot.get("second_offset", "0x00f00000"),
           "--tags_offset", boot.get("tags_offset", "0x00000100"),
           "--pagesize", str(boot.get("pagesize", 2048)),
           "--header_version", str(boot.get("bootimg_header_version", 0)),
           "--cmdline", boot.get("cmdline", ""),
           "-o", out]
    if dtb and not boot.get("append_dtb") and int(boot.get("bootimg_header_version", 0)) >= 2:
        cmd += ["--dtb", dtb, "--dtb_offset", boot.get("dtb_offset", "0x01f00000")]
    run.run(cmd)
    return out
