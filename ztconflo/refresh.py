# SPDX-License-Identifier: GPL-2.0-or-later
"""refresh: re-read pmaports at a newer commit and show what would change in
[boot] and [source.pmos] before writing anything."""
from .pmos import Pmaports
from .registry import Entry
from .resolve import draft_entry, eligibility

FROZEN = ("boot", "source.pmos")


def proposed(entry: Entry, pm: Pmaports) -> dict:
    dev = pm.find(entry.codename)
    verdict = eligibility(dev, pm)
    new = draft_entry(dev, verdict, pm.commit(), pm.kernel_version(verdict.kernel_pkg))
    return {"boot": new["boot"], "source.pmos": new["source"]["pmos"]}


def diff(entry: Entry, new: dict) -> list[str]:
    lines = []
    for section in FROZEN:
        old = entry.get("source", {}).get("pmos", {}) if section == "source.pmos" else entry.get(section, {})
        for key in sorted(set(old) | set(new[section])):
            a, b = old.get(key), new[section].get(key)
            if a != b:
                lines.append(f"[{section}] {key}: {a!r} -> {b!r}")
    return lines


def apply(entry: Entry, new: dict) -> dict:
    data = dict(entry)
    data["boot"] = new["boot"]
    data.setdefault("source", {})["pmos"] = new["source.pmos"]
    return data
