# SPDX-License-Identifier: GPL-2.0-or-later
"""The pipeline: a resolved entry in, out/<codename>/ with boot.img, rootfs.img,
build.json and flash.sh out."""
import json
import shutil
import time
from pathlib import Path

from . import Exit, flash, images, pmos, rootfs
from .config import PMAPORTS_URL, Settings
from .registry import Entry, Registry
from .resolve import Resolution, draft_entry
from .runner import Runner


def entry_for(res: Resolution, settings: Settings, registry: Registry, run: Runner) -> Entry:
    """The registry's entry, or a fresh draft one from pmaports, written to the
    registry as untested (the spec: every fallback build leaves an entry)."""
    if res.source == "registry":
        return res.entry
    pm = pmos.Pmaports(settings.pmaports)
    commit = pm.commit() if pm.exists() else "unknown"
    data = draft_entry(res.device, res.verdict, commit, pm.kernel_version(res.verdict.kernel_pkg))
    if run.dry_run:
        run.log(f"    (would write draft entry {registry.devices / res.device.codename / 'device.toml'})")
        return Entry(data)
    path = registry.write(data)
    run.log(f"    draft registry entry: {path}")
    return Entry(data, path)


def _pmbootstrap_config(work: Path, aports: Path, codename: str) -> Path:
    cfg = work / "pmbootstrap.cfg"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(f"[pmbootstrap]\naports = {aports}\ndevice = {codename}\nwork = {work}\n"
                   "ui = none\nuser = user\nkernel = \nis_default_channel = False\n")
    return cfg


def fetch_kernel(run: Runner, entry: Entry, settings: Settings, tree: Path) -> dict:
    """Step 1: kernel, modules, firmware and initramfs, unpacked under `tree`."""
    run.step("1. kernel side")
    if entry.origin == "native":
        raise Exit(Exit.BUILD_FAILED, "origin = \"native\" needs the ztOS pacman repository, which does not exist "
                                      "yet; this entry cannot be built until it does")
    src = entry["source"]["pmos"]
    pm = pmos.Pmaports(settings.pmaports)
    pm.ensure(PMAPORTS_URL, run.dry_run)
    if not run.dry_run and src["pmaports_commit"] not in ("", "unknown") and pm.commit() != src["pmaports_commit"]:
        pm.checkout(src["pmaports_commit"])
    work = settings.pmbootstrap_work
    cfg = _pmbootstrap_config(work, settings.pmaports, entry.codename) if not run.dry_run else work / "pmbootstrap.cfg"
    base = ["pmbootstrap", "-c", cfg, "--work", work, "--aports", settings.pmaports, "-y"]
    pkgs = [src["kernel_pkg"], *src.get("firmware_pkgs", [])]
    run.run([*base, "build", "--arch", "aarch64", *pkgs])
    # the initramfs is made by mkinitfs at install time, not shipped as a package
    run.run([*base, "install", "--no-image", "--password", "ztos"])
    if run.dry_run:
        return {"kernel": tree / "boot/vmlinuz", "initramfs": tree / "boot/initramfs", "dtb": tree / "dtb",
                "version": entry["kernel"].get("version", "")}
    for pkg in pkgs:
        apks = sorted((work / "packages").glob(f"*/aarch64/{pkg}-[0-9]*.apk"))
        if not apks:
            raise Exit(Exit.BUILD_FAILED, f"pmbootstrap left no {pkg}-*.apk in {work / 'packages'}")
        pmos.unpack_apk(apks[-1], tree)
    chroot = work / f"chroot_rootfs_{entry.codename}"
    initramfs = chroot / "boot/initramfs"
    if not initramfs.is_file():
        raise Exit(Exit.BUILD_FAILED, f"no initramfs at {initramfs} after pmbootstrap install")
    (tree / "boot").mkdir(parents=True, exist_ok=True)
    shutil.copy(initramfs, tree / "boot/initramfs")
    return _find_kernel(tree, entry)


def _find_kernel(tree: Path, entry: Entry) -> dict:
    kernels = sorted((tree / "boot").glob("vmlinuz*"))
    if not kernels:
        raise Exit(Exit.BUILD_FAILED, f"no boot/vmlinuz* in the kernel package (looked in {tree / 'boot'})")
    dtb = None
    name = entry["boot"].get("dtb", "").split()
    if name:
        for base in (tree / "boot/dtbs", tree / "usr/share/dtb", tree / "boot"):
            hit = list(base.glob(f"**/{name[0]}.dtb")) if base.is_dir() else []
            if hit:
                dtb = hit[0]
                break
        if dtb is None:
            raise Exit(Exit.BUILD_FAILED, f"dtb {name[0]}.dtb not found in the kernel package")
    modules = sorted((tree / "lib/modules").glob("*")) if (tree / "lib/modules").is_dir() else []
    return {"kernel": kernels[0], "initramfs": tree / "boot/initramfs", "dtb": dtb,
            "version": modules[0].name if modules else entry["kernel"].get("version", "")}


def verify(run: Runner, entry: Entry, art: dict, tree: Path, registry: Registry) -> str:
    """Step 2: one hash over kernel, modules and dtb."""
    run.step("2. verify")
    if run.dry_run:
        return entry["kernel"].get("artifacts_sha256", "")
    parts = [art["kernel"], tree / "lib/modules"] + ([art["dtb"]] if art["dtb"] else [])
    digest = images.hash_tree(parts)
    want = entry["kernel"].get("artifacts_sha256", "")
    if want and want != digest:
        if entry.status == "tested":
            raise Exit(Exit.HASH_MISMATCH, f"kernel artifacts changed for a tested phone:\n  entry {want}\n"
                                           f"  built {digest}\nRefusing to build; run `ztconflo refresh` "
                                           f"if this is intended.")
        run.log(f"    hash changed on a {entry.status} entry: {want[:12]} -> {digest[:12]}")
    if want != digest and entry.status != "tested" and entry.path:
        entry["kernel"]["artifacts_sha256"] = digest
        entry["kernel"]["version"] = art["version"] or entry["kernel"].get("version", "")
        registry.write(dict(entry))
        run.log("    artifacts_sha256 written into the entry")
    return digest


def build(res: Resolution, settings: Settings, registry: Registry, run: Runner) -> Path:
    start = time.time()
    entry = entry_for(res, settings, registry, run)
    codename = entry.codename
    out = settings.out / codename
    work = settings.work / codename
    tree, root = work / "kernel", work / "root"
    if not run.dry_run:
        out.mkdir(parents=True, exist_ok=True)
        if tree.exists():
            shutil.rmtree(tree)
        tree.mkdir(parents=True)
    art = fetch_kernel(run, entry, settings, tree)
    digest = verify(run, entry, art, tree, registry)

    run.step("3. root filesystem")
    run.run(["rm", "-rf", root], root=True)
    work.mkdir(parents=True, exist_ok=True) if not run.dry_run else None
    conf = rootfs.pacman_conf(work) if not run.dry_run else work / "pacman-aarch64.conf"
    rootfs.keyring(run, work)
    rootfs.pacstrap(run, root, conf, rootfs.packages_txt(entry.folder), settings.jobs)
    rootfs.kernel_side(run, root, tree)
    run.step("3b. ztMETRO, the SDK and the system apps, from source")
    rootfs.ztmetro_from_source(run, root, settings.ztos_src, settings.jobs)
    rootfs.overlay(run, root, settings.ztos_src, entry.folder / "overlay" if entry.folder else None)

    run.step("4. configure")
    rootfs.configure(run, root, entry, Path.home() / ".ssh/id_ed25519.pub")

    run.step("5. initramfs (postmarketOS's, reused unchanged)")
    run.step("6. root image")
    rootfs_img = images.rootfs_image(run, root, out / "rootfs.img")
    if entry["boot"]["flash_method"] == "fastboot" or entry["boot"].get("sparse"):
        images.sparse(run, rootfs_img)

    run.step("7. boot image")
    images.boot_image(run, entry["boot"], art["kernel"], art["initramfs"], art["dtb"], out / "boot.img")
    if entry["boot"]["flash_method"] == "heimdall-isorec" and not run.dry_run:
        shutil.copy(art["kernel"], out / "kernel")
        shutil.copy(art["initramfs"], out / "initramfs")

    run.step("8. output")
    if not run.dry_run:
        sh = out / "flash.sh"
        sh.write_text(flash.script(entry))
        sh.chmod(0o755)
        record = {
            "codename": codename, "status": entry.status, "origin": entry.origin,
            "built": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "seconds": round(time.time() - start),
            "source": entry.get("source", {}), "kernel": {**entry["kernel"], "artifacts_sha256": digest},
            "boot": entry["boot"], "ztos_src_commit": _git_head(settings.ztos_src),
            "images": {name: images.hash_tree([out / name]) for name in ("boot.img", "rootfs.img")},
        }
        (out / "build.json").write_text(json.dumps(record, indent=2) + "\n")
    else:
        run.log(f"    (would write {out}/flash.sh and build.json)\n" + flash.script(entry))
    return out


def _git_head(path: Path) -> str:
    import subprocess
    r = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""
