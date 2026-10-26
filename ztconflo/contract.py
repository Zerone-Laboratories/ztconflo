# SPDX-License-Identifier: GPL-2.0-or-later
"""The GSI's kernel contract: what every device kernel must (hard) or should
(soft) have, checked from the kernel's config. The table is ztOSIAL.md's
"Kernel contract", kept here as data so it changes in one place."""
import gzip
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Need:
    key: str
    title: str
    options: tuple            # every item must be set; a tuple item means any one of it
    hard: bool
    without: str              # what the phone loses without it


# the Mesa driver -> its kernel side
GPU_KERNEL = {
    "freedreno": "DRM_MSM", "panfrost": "DRM_PANFROST", "panthor": "DRM_PANTHOR", "lima": "DRM_LIMA",
    "etnaviv": "DRM_ETNAVIV", "nouveau": "DRM_NOUVEAU",
}

NEEDS = [
    Need("systemd", "systemd, cgroups v2, namespaces", ("CGROUPS", "MEMCG", "CPUSETS", "NAMESPACES", "PSI"), True,
         "no boot"),
    # the GPU need is filled in per phone from [kernel].gpu_driver, see gpu_need()
    Need("zram", "zram, PSI memory pressure", ("ZRAM", "PSI"), False, "memory policy off"),
    Need("waydroid", "Waydroid", ("ANDROID_BINDER_IPC", "ANDROID_BINDERFS", "MEMFD_CREATE", "VETH", "BRIDGE",
                                  "NF_TABLES"), False, "Waydroid hidden"),
    Need("usbnet", "USB networking", (("USB_CONFIGFS_NCM", "USB_CONFIGFS_ECM", "USB_CONFIGFS_RNDIS"),), False,
         "no SSH over USB"),
    Need("haptics", "evdev force feedback", ("INPUT_FF_MEMLESS",), False, "generic haptics off"),
]

# where a kernel package leaves its config, relative to the unpacked tree
CONFIG_PLACES = ["boot/config-*", "boot/config", "boot/.config", ".config", "usr/share/kernel/*/config",
                 "usr/lib/modules/*/config", "lib/modules/*/config", "boot/config*.gz", "proc/config.gz"]


def gpu_need(driver: str | None) -> Need:
    option = GPU_KERNEL.get(driver or "")
    return Need("gpu", f"GPU ({driver or 'no Mesa driver'})", (option,) if option else (), True, "no ztMETRO")


def extra_needs(spec: dict) -> list[Need]:
    """What a GSI asks for beyond the table: gsi.json's kernel_contract,
    {"hard": [options], "soft": [options]}."""
    out = []
    for kind in ("hard", "soft"):
        options = tuple(o.removeprefix("CONFIG_") for o in spec.get(kind, []))
        if options:
            out.append(Need(f"gsi-{kind}", f"required by the GSI ({kind})", options, kind == "hard",
                            "no boot" if kind == "hard" else "a GSI feature off"))
    return out


def find_config(*trees: Path) -> Path | None:
    for tree in trees:
        if tree is None or not Path(tree).is_dir():
            continue
        for pattern in CONFIG_PLACES:
            hits = sorted(p for p in Path(tree).glob(pattern) if p.is_file())
            if hits:
                return hits[-1]
    return None


def parse_config(text: str) -> dict:
    """CONFIG_X=y|m|value lines as {X: value}; '# CONFIG_X is not set' is
    simply absent."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            out[key[len("CONFIG_"):]] = value.strip().strip('"')
    return out


def read_config(path: Path) -> dict:
    data = Path(path).read_bytes()
    if path.suffix == ".gz" or data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return parse_config(data.decode(errors="replace"))


@dataclass
class Result:
    config: Path | None
    state: dict = field(default_factory=dict)      # need key -> "ok" | "missing" | "unknown"
    missing: dict = field(default_factory=dict)    # need key -> options not set
    needs: list = field(default_factory=list)

    @property
    def known(self) -> bool:
        return self.config is not None

    @property
    def hard_failures(self) -> list[Need]:
        return [n for n in self.needs if n.hard and self.state[n.key] == "missing"]

    @property
    def soft_gaps(self) -> list[Need]:
        return [n for n in self.needs if not n.hard and self.state[n.key] == "missing"]

    def has(self, key: str) -> bool:
        """True unless the need is known to be missing (unknown counts as there)."""
        return self.state.get(key) != "missing"

    def lines(self) -> list[str]:
        if not self.known:
            return ["kernel config not found: the contract is unchecked"]
        out = [f"kernel config: {self.config}"]
        for n in self.needs:
            mark = {"ok": "ok", "unknown": "??", "missing": "!!" if n.hard else "--"}[self.state[n.key]]
            note = ""
            if self.state[n.key] == "missing":
                note = f"  missing {', '.join(self.missing[n.key])}: {n.without}"
            elif self.state[n.key] == "unknown":
                note = "  (cannot tell)"
            out.append(f"  {mark} {n.title}{note}")
        return out

    def to_dict(self) -> dict:
        return {"config": str(self.config) if self.config else "", "state": self.state,
                "missing": self.missing, "hard_failures": [n.key for n in self.hard_failures],
                "soft_gaps": [n.key for n in self.soft_gaps]}


def _option_set(config: dict, option: str) -> bool:
    return config.get(option, "n") not in ("n", "")


def check(config_path: Path | None, gpu_driver: str | None, extra: dict | None = None) -> Result:
    needs = [NEEDS[0], gpu_need(gpu_driver), *NEEDS[1:], *extra_needs(extra or {})]
    res = Result(config_path, needs=needs)
    config = read_config(config_path) if config_path else None
    for n in needs:
        if config is None or not n.options:
            res.state[n.key] = "unknown"        # no config, or no GPU driver to look for
            continue
        missing = []
        for item in n.options:
            group = item if isinstance(item, tuple) else (item,)
            if not any(_option_set(config, o) for o in group):
                missing.append(" or ".join(group))
        res.state[n.key] = "missing" if missing else "ok"
        if missing:
            res.missing[n.key] = missing
    return res
