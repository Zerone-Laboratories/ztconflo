# SPDX-License-Identifier: GPL-2.0-or-later
"""The ztOS device registry: one folder per phone, devices/<codename>/device.toml.

Read with tomllib; written with a small emitter for this one fixed schema, so
ztConflo needs nothing outside the standard library."""
import subprocess
import tomllib
from pathlib import Path

from . import Exit

STATUSES = ("untested", "boots", "tested")
ORIGINS = ("pmos", "native")
SECTIONS = ("device", "source.pmos", "kernel", "boot", "userspace", "ztmetro", "verified")
REQUIRED = {
    "device": ("codename", "name", "arch", "status", "origin"),
    "boot": ("flash_method", "rootfs_target"),
}


class Entry(dict):
    """A device.toml as nested dicts, plus where it lives."""

    def __init__(self, data: dict, path: Path | None = None):
        super().__init__(data)
        self.path = path

    @property
    def codename(self) -> str:
        return self["device"]["codename"]

    @property
    def status(self) -> str:
        return self["device"]["status"]

    @property
    def origin(self) -> str:
        return self["device"]["origin"]

    @property
    def folder(self) -> Path | None:
        return self.path.parent if self.path else None


def validate(data: dict, where: str = "device.toml") -> None:
    for section, keys in REQUIRED.items():
        if section not in data:
            raise ValueError(f"{where}: missing [{section}]")
        for key in keys:
            if key not in data[section]:
                raise ValueError(f"{where}: missing [{section}].{key}")
    dev = data["device"]
    if dev["status"] not in STATUSES:
        raise ValueError(f"{where}: status must be one of {', '.join(STATUSES)}")
    if dev["origin"] not in ORIGINS:
        raise ValueError(f"{where}: origin must be one of {', '.join(ORIGINS)}")
    has_pmos = "pmos" in data.get("source", {})
    if dev["origin"] == "pmos" and not has_pmos:
        raise ValueError(f"{where}: origin = \"pmos\" needs [source.pmos]")
    if dev["origin"] == "native" and has_pmos:
        raise ValueError(f"{where}: origin = \"native\" must not have [source.pmos]")


def load(path: Path) -> Entry:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    validate(data, str(path))
    return Entry(data, path)


class Registry:
    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def devices(self) -> Path:
        return self.root / "devices"

    def entries(self) -> list[Entry]:
        if not self.devices.is_dir():
            return []
        return [load(p) for p in sorted(self.devices.glob("*/device.toml"))]

    def find(self, codename: str) -> Entry | None:
        """An exact codename, or a short one (enchilada for oneplus-enchilada)
        when exactly one entry ends with it."""
        exact = self.devices / codename / "device.toml"
        if exact.is_file():
            return load(exact)
        matches = [e for e in self.entries() if e.codename.endswith("-" + codename)]
        if len(matches) > 1:
            names = ", ".join(e.codename for e in matches)
            raise Exit(Exit.NOT_FOUND, f"'{codename}' is ambiguous in the registry: {names}")
        return matches[0] if matches else None

    def write(self, entry: dict) -> Path:
        validate(entry)
        folder = self.devices / entry["device"]["codename"]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "device.toml"
        path.write_text(dumps(entry))
        return path

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(self.root), *args], check=check,
                              capture_output=True, text=True)


def advance(current: str, new: str) -> str:
    """Status only moves forward: untested -> boots -> tested."""
    if STATUSES.index(new) < STATUSES.index(current):
        return current
    return new


# ---- a TOML emitter for this schema ------------------------------------------------------

def _value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{_key(k)} = {_value(x)}" for k, x in v.items()) + "}"
    raise TypeError(f"cannot write {type(v).__name__} to TOML")


def _key(k: str) -> str:
    return k if k.replace("_", "").replace("-", "").isalnum() else _value(k)


def dumps(data: dict) -> str:
    """Sections in the schema's order, then anything else, each key = value."""
    out = []
    order = [s for s in SECTIONS] + [k for k in data if k not in ("device", "source", "kernel", "boot",
                                                                   "userspace", "ztmetro", "verified")]
    for name in order:
        if name == "source.pmos":
            table = data.get("source", {}).get("pmos")
        else:
            table = data.get(name)
        if table is None:
            continue
        out.append(f"[{name}]")
        for k, v in table.items():
            out.append(f"{_key(k)} = {_value(v)}")
        out.append("")
    return "\n".join(out)
