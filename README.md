# ztConflo

A phone's codename in, a flashable ztOS image out: Arch Linux ARM with ztMETRO, on a kernel and boot setup that already works for that phone. The ztOS device registry ([ztos-devices](../ztos-devices)) is used first; phones nobody has ported yet fall back to postmarketOS when they run a mainline kernel on a system on a chip with a Mesa GPU driver.

The full design is in `docs/spec.md`.

## Use

```sh
python -m ztconflo candidates            # eligible phones with no registry entry yet
python -m ztconflo lookup enchilada      # where a phone comes from, and whether it is eligible
python -m ztconflo build oneplus-enchilada --dry-run   # every command the build would run
python -m ztconflo build oneplus-enchilada             # out/oneplus-enchilada/{boot.img,rootfs.img,build.json,flash.sh}
python -m ztconflo flash oneplus-enchilada             # shows the partitions, asks, then runs flash.sh
python -m ztconflo promote oneplus-enchilada           # hardware checklist over USB SSH, then a registry pull request
python -m ztconflo refresh oneplus-enchilada           # newer pmaports: a diff of [boot] and [source.pmos] first
python -m ztconflo list --status boots
```

Common flags: `--registry <path or git url>` (default `~/Documents/Projects/ztos-devices`), `--pmaports <path>` (default: cloned into `~/.cache/ztconflo/pmaports` on first use), `--jobs N`, `--dry-run`.

Exit codes: 0 success, 1 not found, 2 not eligible, 3 build failed, 4 hash mismatch on a tested entry, 5 checklist failed.

## Host

An x86_64 Arch host with Python 3.12+, `pmbootstrap`, `arch-install-scripts`, `qemu-user-static-binfmt`, `android-tools` (`mkbootimg`, `img2simg`, `fastboot`), `heimdall` for Samsung phones, `e2fsprogs`, `rsync` and `git`. Building the root filesystem needs `sudo` (pacstrap, arch-chroot).

ztMETRO, the SDK and the system apps are compiled from a ztOSMobile checkout inside the new root under qemu (`--ztos-src`, default `~/Documents/Projects/ztOSMobile`), until a ztOS pacman repository exists.

## Where it differs from the spec

- The postmarketOS initramfs is not a package: `mkinitfs` makes it when pmOS is installed. Step 1 runs `pmbootstrap install --no-image` and takes `/boot/initramfs` from that chroot.
- Step 3 builds ztMETRO from source instead of installing a `ztmetro` package (step 3b).
- Only ztOSMobile's generic overlay files are copied (session, USB networking, sudoers); everything device-specific belongs in the registry entry's `overlay/`.
- `[boot]` keeps a `defaulted` list naming fields pmaports did not set and mkbootimg's defaults filled in, for a person to check.

## Tests

```sh
python -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest
```

`tests/fixtures/pmaports` is a small tree of real pmaports files: one phone per eligibility rule.
