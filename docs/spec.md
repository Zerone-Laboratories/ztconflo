# ztConflo — Spec

Oct 17, 2026

## Overview

ztConflo takes a phone's codename and produces a flashable ztOS image: Arch Linux ARM userspace with ztMETRO, on a kernel and boot setup that already works for that phone. It looks in the ztOS device registry first, and falls back to postmarketOS when no one has ported ztOS to that phone yet.

Goals:

- One command from codename to a boot image plus a root filesystem image, with the flash commands printed.
- Every build is reproducible: all sources are pinned to exact commits and checksums.
- Every postmarketOS fallback build leaves behind a draft registry entry, so the registry grows from real boots.
- No hand-maintained per-phone code in ztConflo itself. Per-phone knowledge lives only in registry entries.

Non-goals:

- Porting new hardware. ztConflo only reuses kernels and boot knowledge that already exist.
- Downstream (vendor) kernels. They have no Mesa GPU driver, so ztMETRO can't run on them.
- Shipping postmarketOS userspace. Alpine packages end at the kernel, modules, firmware and initramfs.
- Over-the-air updates. Updates after flashing come through pacman and a ztOS repository, handled elsewhere.

## Resolution order

The ztOS registry always wins. postmarketOS is only consulted when the registry has no entry for the codename, and a fallback build becomes a registry entry once it has booted on real hardware.

```
codename
  -> in ztOS registry?  yes -> build from registry entry
       no
  -> in pmaports?       no  -> stop: not found (exit 1)
       yes
  -> mainline + GPU?    no  -> stop: not eligible (exit 2)
       yes
  -> build via pmbootstrap, write draft entry (untested)
  -> promote after a real boot (boots / tested) -> joins the registry
```

A pmaports device is eligible only when all of these hold:

- Its category is `main`, `community` or `testing`. Downstream ports are rejected.
- Its kernel package is a mainline kernel, not a vendor fork.
- Its system on a chip has a working Mesa GPU driver (for example freedreno, panfrost or panthor). The filter reads this from a small table in ztConflo, mapping system on a chip to driver.
- Its architecture is `aarch64`. 32-bit `armv7` phones are out of scope for version 1.

`ztconflo lookup` prints which rule rejected a phone, so the person knows whether it is a dead end or only waiting on a driver.

## Device registry format

Each phone is one folder, `devices/<codename>/`, in the ztOS registry repository. The folder holds `device.toml`, and optionally `overlay/` (files copied into the root filesystem as-is) and `packages.txt` (extra Arch packages).

```toml
[device]
codename = "panther"
name = "Google Pixel 7"
arch = "aarch64"
status = "untested"            # untested | boots | tested
origin = "pmos"                 # pmos | native

[source.pmos]                   # only when origin = "pmos"
pmaports_commit = "<sha>"
device_pkg = "device-google-panther"
kernel_pkg = "linux-<name>"
firmware_pkgs = ["firmware-<name>"]
category = "testing"            # main | community | testing

[kernel]
mainline = true
version = "6.x.y"
artifacts_sha256 = "<sha of the extracted kernel, modules and dtb>"

[boot]                          # copied from deviceinfo, never re-read later
flash_method = "fastboot"       # fastboot | heimdall-bootimg | heimdall-isorec | ...
bootimg_header_version = 2
kernel_offset = "0x00008000"
ramdisk_offset = "0x01000000"
tags_offset = "0x00000100"
pagesize = 4096
dtb = "<vendor>/<board>"
cmdline = "<from deviceinfo>"
append_dtb = false
rootfs_target = "userdata"     # partition the root filesystem image is flashed to

[userspace]
daemons = ["rmtfs", "pd-mapper", "tqftpserv"]   # systemd units to enable

[ztmetro]
scale = 2.5
vibrator = "auto"               # or an explicit /dev/input/eventN match rule
rotation = 0

[verified]                      # filled by ztconflo promote
date = ""
by = ""
checklist = {}
```

Rules:

- `[boot]` is a frozen copy. Later pmaports changes never alter an existing entry. Changes only happen through `ztconflo refresh`, which shows a diff first.
- `status` moves forward only through `promote`: `untested` → `boots` (reaches ztMETRO) → `tested` (passes the full checklist).
- `origin = "native"` means a ztOS kernel package exists in the ztOS pacman repository, and `[source.pmos]` is absent. This is the end state for phones that get real maintenance.

## Build pipeline

The pipeline turns a resolved registry entry into two images: `boot.img` and `rootfs.img`. It runs on an x86_64 Arch host, cross-building through QEMU user emulation.

1. **Fetch kernel side.** For `origin = "pmos"`: check out pmaports at `pmaports_commit`, run `pmbootstrap build` for `kernel_pkg`, `firmware_pkgs` and `postmarketos-initramfs` only, then unpack the resulting `.apk` files. They are gzip tarballs, so `tar` is enough. For `origin = "native"`: download the ztOS kernel package from the pacman repository.
2. **Verify.** Hash the kernel, modules and dtb, and compare against `artifacts_sha256`. A mismatch on a `tested` entry stops the build. On an `untested` entry the hash is written in.
3. **Root filesystem.** Bootstrap Arch Linux ARM aarch64 with `pacstrap` into a fresh directory. Install `base`, `systemd`, `mesa`, `ztmetro`, the ztOS base group, and `packages.txt`. Then copy in `/lib/modules/<version>` and `/lib/firmware` from step 1, and copy `overlay/` last.
4. **Configure.** Enable `[userspace].daemons`, write `/etc/ztmetro/config` from `[ztmetro]`, set the hostname to `ztos-<codename>`, and create the default user. Set `zboot-session ztmetro` as the boot session.
5. **Initramfs.** Version 1 reuses the postmarketOS initramfs unchanged. It is busybox and shell, so musl versus glibc does not matter. It finds the root partition by the `pmOS_root` label and runs `switch_root` into `/sbin/init`, which is systemd. Version 2 replaces it with mkinitcpio hooks.
6. **Root image.** Create an ext4 image labelled `pmOS_root` in version 1 so the reused initramfs finds it. Convert it to Android sparse format when `flash_method = "fastboot"`.
7. **Boot image.** Run `mkbootimg` with the kernel, initramfs, dtb and every `[boot]` field. Append the dtb when `append_dtb = true`.
8. **Output.** Write to `out/<codename>/`: `boot.img`, `rootfs.img`, `build.json` (every input hash and commit), and `flash.sh`, generated from `flash_method`.

Flashing is never automatic. `ztconflo flash` only runs `flash.sh` after showing the target partitions and asking for confirmation, because it overwrites `userdata`.

## CLI

One binary, `ztconflo`, with these subcommands:

| Command | Does |
| --- | --- |
| `ztconflo lookup <codename>` | Resolves the phone and prints where it came from, its status and eligibility. Builds nothing. |
| `ztconflo build <codename>` | Resolves, then runs the full pipeline into `out/<codename>/`. |
| `ztconflo flash <codename>` | Shows target partitions, asks for confirmation, runs `flash.sh`. |
| `ztconflo promote <codename>` | Runs the hardware checklist on a connected phone and updates `status` and `[verified]`. |
| `ztconflo refresh <codename>` | Re-reads pmaports at a newer commit and shows a diff of `[boot]` and `[source.pmos]` before writing. |
| `ztconflo list [--status S] [--origin O]` | Lists registry entries. |
| `ztconflo candidates` | Lists pmaports devices that pass the eligibility filter but have no registry entry. |

Flags common to all: `--registry <path or git url>`, `--pmaports <path>`, `--jobs N`, `--dry-run`.

Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Phone not found in the registry or pmaports |
| 2 | Found but not eligible (downstream kernel, or no GPU driver) |
| 3 | Build failed |
| 4 | Hash mismatch on a `tested` entry |
| 5 | Checklist failed during `promote` |

## Hardware checklist and promotion

`promote` connects to the phone over SSH on USB networking, runs each check, and writes the results into `[verified].checklist`. Checks marked automatic run unattended. The rest ask the person holding the phone for a yes or no.

| Check | How | Needed for |
| --- | --- | --- |
| Boots to ztMETRO | `ztmetro` process alive, Start drawn | boots |
| Display | Automatic: an output exists at the expected mode | boots |
| Touch | Manual: tap three targets on screen | boots |
| GPU renderer | Automatic: renderer string is not `llvmpipe` | tested |
| Wi-Fi | Automatic: scan returns networks | tested |
| Vibrator | Automatic: force-feedback device found. Manual: pulse felt | tested |
| Audio | Manual: test tone heard | tested |
| Battery reporting | Automatic: `/sys/class/power_supply` reports capacity | tested |
| Suspend and resume | Manual: screen comes back with touch working | tested |
| Modem | Automatic: ModemManager sees a modem. Optional, recorded but not blocking | tested |

A phone becomes `boots` when the first three pass, and `tested` when all non-optional checks pass. `promote` writes the entry and opens a pull request against the registry repository. It never pushes to the main branch directly.

## Layout, dependencies and milestones

Repository layout:

```
ztconflo/
  ztconflo/            # Python package
    cli.py
    resolve.py         # registry lookup, pmaports lookup, eligibility
    pmos.py            # deviceinfo parser, pmbootstrap driver, apk unpacking
    rootfs.py          # pacstrap, overlay, configure
    images.py          # ext4, sparse, mkbootimg
    flash.py           # flash.sh generation per flash_method
    promote.py         # checklist runner, registry pull request
  tests/
    fixtures/deviceinfo/   # real deviceinfo files for parser tests
ztos-devices/          # separate registry repository
  devices/<codename>/device.toml
```

Host dependencies: Python 3.12 or later, `pmbootstrap`, `arch-install-scripts`, `qemu-user-static-binfmt`, `android-tools` (`mkbootimg`, `img2simg`, `fastboot`), `heimdall`, `e2fsprogs`, `git`.

Milestones:

1. `lookup` and `candidates`: registry and pmaports resolution plus the eligibility filter, tested against fixture `deviceinfo` files.
2. `build` for one fastboot phone end to end, using the reused postmarketOS initramfs.
3. `flash` and `promote`, with the first real entry reaching `boots`.
4. Heimdall flash methods, so older Samsung phones work.
5. mkinitcpio replaces the postmarketOS initramfs, which drops the `pmOS_root` label.
6. Native kernel packages in the ztOS pacman repository for phones that reach `tested`.

Open questions:

- Which phone is the first target, now that the S9 port is too weak? The eligibility filter's `candidates` output should decide it.
- Where do the ztOS registry and pacman repository live: GitHub, or self-hosted?
- Should the Arch builds of Qualcomm daemons (`rmtfs`, `pd-mapper`, `tqftpserv`, `hexagonrpcd`) live in the ztOS repository or come from the Arch User Repository?
