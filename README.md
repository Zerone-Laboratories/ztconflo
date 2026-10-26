# ztConflo

A phone's codename in, a flashable ztOS image out: Arch Linux ARM with ztMETRO, on a kernel and boot setup that already works for that phone. The ztOS device registry ([ztos-devices](../ztos-devices)) is used first; phones nobody has ported yet fall back to postmarketOS when they run a mainline kernel on a system on a chip with a Mesa GPU driver.

The full design is in `docs/spec.md`.

## The terminal UI

```sh
python -m ztconflo                      # or: python -m ztconflo tui (Textual is installed on first run)
```

A welcome screen that checks this computer for what a build needs, a searchable list of phones (registry first, then eligible postmarketOS phones), and a page per phone with dry run, build, flash and the hardware checklist. Builds stream their output live with the eight steps ticked off; sudo is asked once, in the plain terminal, before a real build starts. Flashing asks for the codename to be typed first.

Keys: `p` pick a phone, `i` install what's missing, `d` dry run, `b` build, `esc` back, `q` quit. In the phone list, just type to search. The search is fuzzy: typos, missing letters, word order and abbreviations are fine (`op6`, `pixl`, `xiomi mido`), and the best match comes first. `↑` `↓` choose a phone and `enter` opens it; `esc` clears the search first, then goes back. The look is in `ztconflo/tui.tcss`.

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
python -m ztconflo build oneplus-enchilada --gsi 0.2.0 --dry-run   # the GSI pipeline (ztOSIAL)
python -m ztconflo build samsung-starlte --gsi ./ztos-gsi-0.2.0-aarch64.tar.zst --device-layer ./starlte-layer.tar.zst
```

`--gsi <path|url|version>` switches `build` (and the TUI) to the GSI pipeline: the GSI tarball is checked against the `.sha256` published next to it and cached in `~/.cache/ztconflo/gsi/<version>/`, the kernel is checked against the kernel contract, and a device layer goes under `/vendor`. Without `--gsi` the build is the pre-GSI one, unchanged. A native phone's device layer comes from `--ztos-repo <pacman server>` (or `ZTCONFLO_ZTOS_REPO`) as `ztos-kernel-<codename>` and `ztos-vendor-<codename>`, or from a local tarball with `--device-layer <path>`. Exit 2 when the kernel misses a hard requirement, exit 3 when `ztos-ial check` fails.

Common flags: `--registry <path or git url>` (default `~/Documents/Projects/ztos-devices`), `--pmaports <path>` (default: cloned into `~/.cache/ztconflo/pmaports` on first use), `--jobs N`, `--dry-run`.

Exit codes: 0 success, 1 not found, 2 not eligible, 3 build failed, 4 hash mismatch on a tested entry, 5 checklist failed.

## Host

An x86_64 Arch-based host (Arch, CachyOS, EndeavourOS, Manjaro) with Python 3.12+. Everything else installs itself: the first time ztConflo runs, it checks for `pmbootstrap`, `arch-install-scripts`, `android-tools`, `e2fsprogs`, `rsync`, `git`, `qemu-user-static-binfmt`, `heimdall` and `python-textual`, and installs whatever is missing with one `sudo pacman -S --needed` (it asks for your password once). Run `ztconflo setup` to check or install again later; the TUI has an "install what's missing" tile (`i`).

ztMETRO, the SDK and the system apps are compiled from a ztOSMobile checkout inside the new root under qemu (`--ztos-src`, default `~/Documents/Projects/ztOSMobile`), until a ztOS pacman repository exists.

## Where it differs from the spec

- The postmarketOS initramfs is not a package: `mkinitfs` makes it when pmOS is installed. Step 1 runs `pmbootstrap install --no-image` and takes `/boot/initramfs` from that chroot.
- Step 3 builds ztMETRO from source instead of installing a `ztmetro` package (step 3b).
- Only ztOSMobile's generic overlay files are copied (session, USB networking, sudoers); everything device-specific belongs in the registry entry's `overlay/`.
- `[boot]` keeps a `defaulted` list naming fields pmaports did not set and mkbootimg's defaults filled in, for a person to check.
- GSI pipeline: the GSI is checked against a published `.sha256`, not a signature, until releases are signed. With no `.sha256` it is used and its hash recorded in `build.json`, marked unverified.
- GSI pipeline: the kernel config is looked for in the unpacked kernel side (`boot/config*`, `.config`, `usr/share/kernel/*/config`, `config.gz`); when none is found the contract is reported as unchecked and the build goes on. USB networking counts as met with any one of NCM, ECM or RNDIS. `zram` and PSI are a soft need, as in the design's table.
- The generated manifest also carries `[units] enable` (the entry's daemons, also written as `/vendor/ztos/systemd/90-ztos-vendor.preset`) and `[kernel]` (whether the contract was checked, and its soft gaps). A generic backend that needs a missing option (Haptics1 without `INPUT_FF_MEMLESS`) is left out of the manifest, so the GSI hides it.
- From the pmaports device package only its udev rules (`*.rules` in the package folder) are copied into `/vendor/ztos/udev`; UCM profiles are not yet taken.
- `system.img` and `vendor.img` (ztOSIAL version 2) are not made; both pipelines produce `boot.img` and `rootfs.img`.
- `lookup` prints the kernel contract only when an earlier build left the kernel side in its work folder.
- The checklist's ztOSIAL and Android rows are recorded as `n/a` on an image without `/vendor/ztos/manifest.toml` (or, for Android, without binder), so pre-GSI phones can still reach `tested`.

## Tests

```sh
python -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest
```

`tests/fixtures/pmaports` is a small tree of real pmaports files: one phone per eligibility rule.
