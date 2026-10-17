# SPDX-License-Identifier: GPL-2.0-or-later
"""Codename to something buildable: the registry first, then pmaports, and the
eligibility filter that decides whether a pmaports phone can run ztMETRO."""
import re
from dataclasses import dataclass

from . import Exit
from .pmos import ACCEPTED, PmDevice, Pmaports
from .registry import Entry, Registry

# System on a chip -> the Mesa driver that runs its GPU. Matched on the start
# of the SoC name read from the dtb path (or the kernel/soc package names).
# Only SoCs whose GPU Mesa really drives; None marks a known dead end.
GPU_DRIVERS = [
    # Qualcomm: every Adreno 3xx and later is freedreno
    (r"(msm|apq|mdm)\d", "freedreno"), (r"sdm\d", "freedreno"), (r"sm\d", "freedreno"), (r"sda\d", "freedreno"),
    (r"sc\d", "freedreno"), (r"qcs\d", "freedreno"), (r"qcm\d", "freedreno"), (r"x1e", "freedreno"),
    (r"qcom$", "freedreno"),
    # Rockchip
    (r"rk3588", "panthor"), (r"rk3576", "panthor"),
    (r"rk3328", "lima"), (r"rk3036", "lima"), (r"rk3188", "lima"),
    (r"rk3(288|326|399|566|568)|px30", "panfrost"),
    # MediaTek (Mali); mt8173 is PowerVR, the mt6xxx phones have no mainline GPU
    (r"mt81(83|86|88|92|95)|mt8365", "panfrost"), (r"mt8173", None), (r"mt6", None),
    # Allwinner
    (r"sun50i-a64|sun50i-h5|sun8i|sun7i|sun4i|sun5i", "lima"), (r"sun50i-h6|sun50i-h616|sun50i-h618", "panfrost"),
    # Amlogic
    (r"meson-gx", "lima"), (r"meson-(g12|sm1|axg)", "panfrost"), (r"meson8", "lima"),
    # NXP i.MX
    (r"imx8|imx6|imx7", "etnaviv"),
    # NVIDIA Tegra
    (r"tegra(124|210|186|194)", "nouveau"),
    # Known without a working Mesa driver
    (r"exynos", None), (r"pxa", None), (r"sc(7731|9863)|ums", None),
    (r"gs\d", None),                            # Google Tensor: Mali-G78, panthor not there yet
]

# Alpine's own kernel packages: mainline, built by Alpine rather than pmaports
ALPINE_MAINLINE = {"linux-stable", "linux-lts", "linux-edge"}

DTB_VENDOR_SOC = {
    "allwinner": lambda b: "-".join(b.split("-")[:2]),     # sun50i-a64-pinephone -> sun50i-a64
    "amlogic": lambda b: "-".join(b.split("-")[:2]),       # meson-g12b-... -> meson-g12b
}


def soc_of(dev: PmDevice) -> str:
    """The SoC, as close to its own name as pmaports says it."""
    # dtb, or per-variant dtb_<variant> keys on multi-variant devices
    dtb = [d for k, v in sorted(dev.info.items()) if k == "dtb" or k.startswith("dtb_") for d in v.split()]
    if dtb and "/" in dtb[0]:
        parts = dtb[0].split("/")
        vendor, board = parts[0], parts[-1]          # exynos/google/gs101-oriole -> gs101-oriole
        pick = DTB_VENDOR_SOC.get(vendor, lambda b: b.split("-")[0])
        return pick(board)
    for kernel in [k for _, k in dev.kernels] + dev.apkbuild.soc_packages:
        for part in kernel.split("-")[::-1]:
            if gpu_driver(part)[0]:
                return part
    if dtb:
        return dtb[0].split("-")[0]
    for pkg in dev.apkbuild.soc_packages:          # soc-qcom: at least the vendor
        if pkg.startswith("soc-qcom"):
            return "qcom"
    return ""


def gpu_driver(soc: str) -> tuple[bool, str | None]:
    """(known, driver): known=False when the table has no rule for it."""
    for pattern, driver in GPU_DRIVERS:
        if re.match(pattern, soc):
            return True, driver
    return False, None


@dataclass
class Verdict:
    eligible: bool
    reason: str               # the rule that rejected it, or "eligible"
    dead_end: bool = False    # True: will never qualify; False: e.g. waiting on a driver
    soc: str = ""
    driver: str | None = None
    kernel_pkg: str = ""


def eligibility(dev: PmDevice, pmaports: Pmaports) -> Verdict:
    soc = soc_of(dev)
    if dev.category not in ACCEPTED:
        return Verdict(False, f"category '{dev.category}' is not main, community or testing", True, soc)
    if dev.arch != "aarch64":
        return Verdict(False, f"architecture '{dev.arch}' is not aarch64 (32-bit phones are out of scope for v1)",
                       True, soc)
    if not dev.kernels:
        return Verdict(False, "no kernel package found in the device APKBUILD", True, soc)
    kernel = _preferred_kernel(dev.kernels)
    where = pmaports.kernel_folder(kernel)
    if where is None and kernel not in ALPINE_MAINLINE:
        return Verdict(False, f"kernel package {kernel} is not in this pmaports checkout", True, soc, kernel_pkg=kernel)
    if where is not None and (where[0] == "downstream" or
                              "downstreamkernel_package" in (where[1] / "APKBUILD").read_text(errors="replace")):
        return Verdict(False, f"kernel {kernel} is a downstream (vendor) kernel", True, soc, kernel_pkg=kernel)
    known, driver = gpu_driver(soc)
    if not known:
        return Verdict(False, f"SoC '{soc or '?'}' is not in ztConflo's GPU table; add it if Mesa drives it",
                       False, soc, kernel_pkg=kernel)
    if driver is None:
        return Verdict(False, f"SoC '{soc}' has no working Mesa GPU driver", False, soc, kernel_pkg=kernel)
    return Verdict(True, "eligible", False, soc, driver, kernel)


def _preferred_kernel(kernels: list[tuple[str, str]]) -> str:
    """One kernel for a multi-kernel device: the dedicated one before the
    generic stable/lts/mainline ones."""
    for preferred in ("", "stable", "mainline", "lts"):
        for variant, pkg in kernels:
            if variant == preferred:
                return pkg
    return kernels[0][1]


@dataclass
class Resolution:
    source: str                    # "registry" or "pmos"
    entry: Entry | None = None
    device: PmDevice | None = None
    verdict: Verdict | None = None


def resolve(codename: str, registry: Registry, pmaports: Pmaports) -> Resolution:
    """The spec's order: the registry wins; otherwise pmaports, if eligible."""
    entry = registry.find(codename)
    if entry is not None:
        return Resolution("registry", entry=entry)
    if not pmaports.exists():
        raise Exit(Exit.NOT_FOUND, f"'{codename}' is not in the registry, and there is no pmaports checkout at "
                                   f"{pmaports.root} to fall back to")
    dev = pmaports.find(codename)
    if dev is None:
        raise Exit(Exit.NOT_FOUND, f"'{codename}' is in neither the ztOS registry nor pmaports")
    verdict = eligibility(dev, pmaports)
    if not verdict.eligible:
        kind = "a dead end" if verdict.dead_end else "not yet (it may qualify later)"
        raise Exit(Exit.NOT_ELIGIBLE, f"{dev.codename} ({dev.info.get('name', '?')}) is not eligible: "
                                      f"{verdict.reason}. This is {kind}.")
    return Resolution("pmos", device=dev, verdict=verdict)


def candidates(registry: Registry, pmaports: Pmaports) -> list[tuple[PmDevice, Verdict]]:
    known = {e.codename for e in registry.entries()}
    out = []
    for dev in pmaports.all():
        if dev.codename in known:
            continue
        v = eligibility(dev, pmaports)
        if v.eligible:
            out.append((dev, v))
    return out


# ---- a pmaports device as a draft registry entry -----------------------------------------

def _hex(value: str, default: str) -> str:
    value = value or default
    return value if value.startswith("0x") else f"0x{int(value):08x}"


def _rootfs_target(info: dict) -> str:
    method = info.get("flash_method", "")
    if method.startswith("heimdall"):
        return info.get("flash_heimdall_partition_rootfs", "USERDATA")
    return info.get("flash_fastboot_partition_rootfs", "userdata")


def draft_entry(dev: PmDevice, verdict: Verdict, pmaports_commit: str, kernel_version: str) -> dict:
    info = dev.info
    boot = {
        "flash_method": info.get("flash_method", "fastboot"),
        "bootimg_header_version": int(info.get("header_version", "0") or 0),
        "kernel_offset": _hex(info.get("flash_offset_kernel", ""), "0x00008000"),
        "ramdisk_offset": _hex(info.get("flash_offset_ramdisk", ""), "0x01000000"),
        "tags_offset": _hex(info.get("flash_offset_tags", ""), "0x00000100"),
        "base": _hex(info.get("flash_offset_base", ""), "0x10000000"),
        "second_offset": _hex(info.get("flash_offset_second", ""), "0x00f00000"),
        "pagesize": int(info.get("flash_pagesize", "2048") or 2048),
        "dtb": info.get("dtb", ""),
        "cmdline": dev.cmdline,
        "append_dtb": info.get("append_dtb", "false") == "true",
        "qcdt": info.get("bootimg_qcdt", "false") == "true",
        "sparse": info.get("flash_sparse", "false") == "true",
        "rootfs_target": _rootfs_target(info),
    }
    if boot["flash_method"].startswith("heimdall"):
        boot["heimdall_partition_kernel"] = info.get("flash_heimdall_partition_kernel", "BOOT")
        boot["heimdall_partition_initfs"] = info.get("flash_heimdall_partition_initfs", "RECOVERY")
    # fields pmaports leaves to mkbootimg's defaults, named so a person checks them
    defaulted = [k for k, src in (("bootimg_header_version", "header_version"),
                                  ("kernel_offset", "flash_offset_kernel"),
                                  ("ramdisk_offset", "flash_offset_ramdisk"),
                                  ("tags_offset", "flash_offset_tags"),
                                  ("pagesize", "flash_pagesize")) if src not in info]
    if not dev.cmdline:
        defaulted.append("cmdline")
    if defaulted:
        boot["defaulted"] = defaulted
    return {
        "device": {
            "codename": dev.codename,
            "name": info.get("name", dev.codename),
            "arch": dev.arch,
            "status": "untested",
            "origin": "pmos",
        },
        "source": {"pmos": {
            "pmaports_commit": pmaports_commit,
            "device_pkg": dev.device_pkg,
            "kernel_pkg": verdict.kernel_pkg,
            "firmware_pkgs": dev.apkbuild.firmware,
            "category": dev.category,
        }},
        "kernel": {"mainline": True, "version": kernel_version, "artifacts_sha256": "",
                   "soc": verdict.soc, "gpu_driver": verdict.driver or ""},
        "boot": boot,
        "userspace": {"daemons": default_daemons(verdict.soc)},
        "ztmetro": {"scale": round(int(info.get("screen_width", "1080") or 1080) / 432, 2),
                    "vibrator": "auto", "rotation": 0},
        "verified": {"date": "", "by": "", "checklist": {}},
    }


def default_daemons(soc: str) -> list[str]:
    """Qualcomm phones need their remote-processor helpers to reach Wi-Fi and the modem."""
    if gpu_driver(soc)[1] == "freedreno":
        return ["rmtfs", "pd-mapper", "tqftpserv"]
    return []
