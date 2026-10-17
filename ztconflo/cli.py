# SPDX-License-Identifier: GPL-2.0-or-later
"""ztconflo: lookup, build, flash, promote, refresh, list, candidates."""
import argparse
import subprocess
import sys
from pathlib import Path

from . import Exit, __version__
from .config import PMAPORTS_URL, USB_HOST, Settings
from .pmos import Pmaports
from .registry import Registry
from .resolve import candidates, resolve
from .runner import Runner


def _settings(args) -> Settings:
    s = Settings()
    if args.registry:
        s.registry = _registry_path(args.registry)
    if args.pmaports:
        s.pmaports = Path(args.pmaports).expanduser()
    if args.jobs:
        s.jobs = args.jobs
    s.dry_run = args.dry_run
    if getattr(args, "ztos_src", None):
        s.ztos_src = Path(args.ztos_src).expanduser()
    if getattr(args, "out", None):
        s.out = Path(args.out).expanduser()
    return s


def _registry_path(value: str) -> Path:
    """A path, or a git URL cloned into the cache."""
    if "://" in value or value.startswith("git@"):
        from .config import CACHE
        dest = CACHE / "registry" / value.rstrip("/").split("/")[-1].removesuffix(".git")
        if not dest.exists():
            subprocess.run(["git", "clone", value, str(dest)], check=True)
        else:
            subprocess.run(["git", "-C", str(dest), "pull", "--ff-only", "-q"], check=False)
        return dest
    return Path(value).expanduser()


def _pmaports(s: Settings, need: bool = True) -> Pmaports:
    pm = Pmaports(s.pmaports)
    if need and not pm.exists():
        print(f"fetching pmaports into {s.pmaports} ...", file=sys.stderr)
        pm.ensure(PMAPORTS_URL, dry_run=False)
    return pm


def cmd_lookup(args, s: Settings) -> int:
    reg = Registry(s.registry)
    if reg.find(args.codename) is None:
        _pmaports(s)
    res = resolve(args.codename, reg, Pmaports(s.pmaports))
    if res.source == "registry":
        e = res.entry
        print(f"{e.codename}: {e['device']['name']}")
        print(f"  from:    ztOS registry ({e.path})")
        print(f"  status:  {e.status}   origin: {e.origin}")
        print(f"  flash:   {e['boot']['flash_method']} -> {e['boot'].get('rootfs_target')}")
        return 0
    d, v = res.device, res.verdict
    print(f"{d.codename}: {d.info.get('name', '?')}")
    print(f"  from:    postmarketOS fallback (device/{d.category}/{d.folder.name})")
    print("  status:  not in the registry; a build writes a draft entry (untested)")
    print(f"  SoC:     {v.soc} -> Mesa {v.driver}")
    print(f"  kernel:  {v.kernel_pkg} (mainline)")
    print(f"  flash:   {d.info.get('flash_method', '?')}")
    print("  eligible: yes")
    return 0


def cmd_list(args, s: Settings) -> int:
    entries = Registry(s.registry).entries()
    shown = [e for e in entries if (not args.status or e.status == args.status)
             and (not args.origin or e.origin == args.origin)]
    if not shown:
        print(f"(no registry entries{' matching' if entries else ''} in {s.registry})")
    for e in shown:
        print(f"{e.codename:<32} {e.status:<9} {e.origin:<7} {e['device']['name']}")
    return 0


def cmd_candidates(args, s: Settings) -> int:
    found = candidates(Registry(s.registry), _pmaports(s))
    if not args.all:        # phones and tablets that ztConflo can flash
        found = [(d, v) for d, v in found if d.info.get("chassis") in ("handset", "tablet")
                 and d.info.get("flash_method") in ("fastboot", "heimdall-bootimg", "heimdall-isorec")]
    for d, v in sorted(found, key=lambda x: (x[0].category != "community", x[1].driver, x[0].codename)):
        print(f"{d.codename:<34} {d.category:<10} {v.soc:<12} {v.driver:<10} {d.info.get('flash_method', '?'):<17} "
              f"{d.info.get('name', '')}")
    print(f"{len(found)} eligible phones without a registry entry", file=sys.stderr)
    return 0


def cmd_build(args, s: Settings) -> int:
    from .build import build
    reg = Registry(s.registry)
    if reg.find(args.codename) is None:
        _pmaports(s)
    res = resolve(args.codename, reg, Pmaports(s.pmaports))
    out = build(res, s, reg, Runner(dry_run=s.dry_run))
    if s.dry_run:
        print("(dry run: nothing was built)")
    else:
        print(f"built into {out}:")
        for f in ("boot.img", "rootfs.img", "build.json", "flash.sh"):
            print(f"  {out / f}")
        print(f"flash with: ztconflo flash {args.codename}   (or read {out / 'flash.sh'})")
    return 0


def _entry(codename: str, s: Settings):
    e = Registry(s.registry).find(codename)
    if e is None:
        raise Exit(Exit.NOT_FOUND, f"'{codename}' has no registry entry; build it first")
    return e


def cmd_flash(args, s: Settings) -> int:
    from .flash import flash, script
    e = _entry(args.codename, s)
    if s.dry_run:
        print(script(e))
        return 0
    flash(s.out / e.codename, e)
    return 0


def cmd_promote(args, s: Settings) -> int:
    from .promote import Phone, promote
    e = _entry(args.codename, s)
    if s.dry_run:
        from .promote import CHECKS
        for c in CHECKS:
            kind = ("automatic" if c.command else "") + (" + manual" if c.command and c.question else
                                                          "manual" if c.question else "")
            print(f"{c.title:<22} {kind:<20} needed for {c.needed_for}{' (optional)' if c.optional else ''}")
        return 0
    promote(e, Registry(s.registry), Phone(args.host, args.user), pr=not args.no_pr)
    return 0


def cmd_refresh(args, s: Settings) -> int:
    from . import refresh
    reg = Registry(s.registry)
    e = _entry(args.codename, s)
    if e.origin != "pmos":
        raise Exit(Exit.NOT_FOUND, f"{e.codename} is a native entry; there is nothing in pmaports to refresh from")
    pm = _pmaports(s)
    if not args.no_fetch:
        subprocess.run(["git", "-C", str(pm.root), "fetch", "--depth", "1", "-q", "origin", "HEAD"], check=True)
        subprocess.run(["git", "-C", str(pm.root), "checkout", "-q", "FETCH_HEAD"], check=True)
    new = refresh.proposed(e, pm)
    changes = refresh.diff(e, new)
    if not changes:
        print(f"{e.codename}: no changes at pmaports {pm.commit()[:12]}")
        return 0
    print("\n".join(changes))
    if s.dry_run or input("Write these changes? [y/N] ").strip().lower() != "y":
        print("Not written.")
        return 0
    reg.write(refresh.apply(e, new))
    print(f"written: {e.path}")
    return 0


def parser() -> argparse.ArgumentParser:
    def common_flags(suppress: bool) -> argparse.ArgumentParser:
        # on the subcommands the defaults are suppressed, so a flag given
        # before the subcommand is not overwritten by the subcommand's default
        d = {"default": argparse.SUPPRESS} if suppress else {}
        c = argparse.ArgumentParser(add_help=False)
        c.add_argument("--registry", help="the ztOS device registry: a path or a git URL", **d)
        c.add_argument("--pmaports", help="a pmaports checkout (default: cloned into ~/.cache/ztconflo)", **d)
        c.add_argument("--jobs", type=int, help="parallel jobs for builds", **d)
        c.add_argument("--dry-run", action="store_true", help="print what would happen, change nothing", **d)
        return c

    common = common_flags(True)
    p = argparse.ArgumentParser(prog="ztconflo", description="A phone codename in, a flashable ztOS image out.",
                                parents=[common_flags(False)])
    p.add_argument("--version", action="version", version=f"ztconflo {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, func, help_text, codename=True):
        sp = sub.add_parser(name, help=help_text, parents=[common])
        if codename:
            sp.add_argument("codename")
        sp.set_defaults(func=func)
        return sp

    add("lookup", cmd_lookup, "resolve a phone and print where it comes from")
    b = add("build", cmd_build, "build boot.img and rootfs.img into out/<codename>/")
    b.add_argument("--ztos-src", help="the ztOSMobile checkout ztMETRO is built from")
    b.add_argument("--out", help="where out/<codename>/ goes (default: ./out)")
    f = add("flash", cmd_flash, "show the target partitions, confirm, run flash.sh")
    f.add_argument("--out", help="where out/<codename>/ is (default: ./out)")
    pr = add("promote", cmd_promote, "run the hardware checklist on a connected phone")
    pr.add_argument("--host", default=USB_HOST)
    pr.add_argument("--user", default="alarm")
    pr.add_argument("--no-pr", action="store_true", help="commit on a branch, but open no pull request")
    rf = add("refresh", cmd_refresh, "re-read pmaports and show a diff before writing")
    rf.add_argument("--no-fetch", action="store_true", help="use the pmaports checkout as it is")
    ls = add("list", cmd_list, "list registry entries", codename=False)
    ls.add_argument("--status", choices=["untested", "boots", "tested"])
    ls.add_argument("--origin", choices=["pmos", "native"])
    cd = add("candidates", cmd_candidates, "eligible pmaports phones with no registry entry", codename=False)
    cd.add_argument("--all", action="store_true", help="also boards, laptops and unsupported flash methods")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.func(args, _settings(args))
    except Exit as e:
        print(f"ztconflo: {e}", file=sys.stderr)
        return e.code
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
