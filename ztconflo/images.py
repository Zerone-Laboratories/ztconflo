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


def dtb_bytes(boot: dict, dtb: Path) -> bytes:
    """The dtb, with the nodes the entry's boot.dtb_enable names set to
    status = "okay" (fdtput): what mainline leaves disabled on a phone."""
    nodes = boot.get("dtb_enable", [])
    if not nodes:
        return Path(dtb).read_bytes()
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "dtb"
        copy.write_bytes(Path(dtb).read_bytes())
        for node in nodes:
            subprocess.run(["fdtput", "-t", "s", str(copy), node, "status", "okay"], check=True)
        return copy.read_bytes()


def boot_image(run: Runner, boot: dict, kernel: Path, initramfs: Path, dtb: Path | None, out: Path) -> Path:
    """mkbootimg with every [boot] field. With append_dtb the dtb is glued
    onto the kernel, as these bootloaders expect."""
    if boot.get("append_dtb") and dtb:
        glued = out.parent / "kernel-dtb"
        if run.dry_run:
            run.log(f"    cat {kernel} {dtb} > {glued}")
        else:
            glued.write_bytes(Path(kernel).read_bytes() + dtb_bytes(boot, dtb))
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


BOOT_LABEL = "pmOS_boot"      # the reused postmarketOS initramfs looks for this one too
BOOT_FS_MB = 256


def extlinux_disk(run: Runner, boot: dict, art: dict, root_img: Path, work: Path, out: Path) -> Path:
    """For phones booted by lk2nd: a disk image for userdata, as postmarketOS
    lays it out. An msdos table, an ext2 boot partition with extlinux.conf, the
    kernel, initramfs and dtb (lk2nd reads ext2, not ext4's extents), and the
    root. The boot image does not fit in BOOT behind lk2nd on these phones."""
    tree = work / "extlinux-boot"
    run.run(["rm", "-rf", tree])
    fdt = ""
    if not run.dry_run:
        (tree / "extlinux").mkdir(parents=True)
        (tree / "vmlinuz").write_bytes(Path(art["kernel"]).read_bytes())
        (tree / "initramfs").write_bytes(Path(art["initramfs"]).read_bytes())
        if art["dtb"]:
            (tree / Path(art["dtb"]).name).write_bytes(dtb_bytes(boot, art["dtb"]))
            fdt = f"\tfdt /{Path(art['dtb']).name}\n"
        (tree / "extlinux/extlinux.conf").write_text(
            "default ztos\ntimeout 1\n\nlabel ztos\n\tlinux /vmlinuz\n\tinitrd /initramfs\n"
            f"{fdt}\tappend {boot.get('cmdline', '')}\n")
    boot_fs = work / "boot.ext2"
    run.run(["rm", "-f", boot_fs])
    run.run([MKE2FS, "-q", "-t", "ext2", "-L", BOOT_LABEL, "-d", tree, "-E", "root_owner=0:0",
             boot_fs, f"{BOOT_FS_MB}M"])
    start = 2048                                # sectors: 1 MiB aligned, as pmbootstrap does
    boot_sectors = BOOT_FS_MB * 2048
    root_sectors = 0 if run.dry_run else -(-root_img.stat().st_size // 512)
    run.run(["rm", "-f", out])
    run.run(["truncate", "-s", str((start + boot_sectors + root_sectors) * 512), out])
    table = (f"label: dos\n{start},{boot_sectors},83,*\n"
             f"{start + boot_sectors},{root_sectors or ''},83\n")
    if run.dry_run:
        run.log(f"    sfdisk {out} <<< {table!r}")
    else:
        import subprocess
        subprocess.run(["sfdisk", "-q", str(out)], input=table, text=True, check=True)
    run.run(["dd", f"if={boot_fs}", f"of={out}", "bs=1M", f"seek={start // 2048}", "conv=notrunc", "status=none"])
    run.run(["dd", f"if={root_img}", f"of={out}", "bs=1M", f"seek={(start + boot_sectors) // 2048}",
             "conv=notrunc", "status=none"])
    run.run(["rm", "-f", boot_fs, root_img])
    return out
