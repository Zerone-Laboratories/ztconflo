# SPDX-License-Identifier: GPL-2.0-or-later
"""ztconflo's terminal UI: a welcome, a phone picker, and the build, flash
and checklist run with their progress in front of you. Same code paths as
the command line; this only shows them."""
import shutil
import subprocess
import threading
from pathlib import Path

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from rich.text import Text
from textual.widgets import Button, DataTable, Footer, Input, LoadingIndicator, RichLog, Static

from . import Exit
from .config import PMAPORTS_URL, Settings
from .pmos import Pmaports
from .registry import Registry
from .resolve import candidates, resolve
from .runner import Runner

WORDMARK = r"""
     _    ____             __ _
 ___| |_ / ___|___  _ __  / _| | ___
|_  / __| |   / _ \| '_ \| |_| |/ _ \
 / /| |_| |__| (_) | | | |  _| | (_) |
/___|\__|\____\___/|_| |_|_| |_|\___/
"""

STEPS = ["kernel side", "verify", "root filesystem", "ztMETRO from source", "configure",
         "initramfs", "root image", "boot image", "output"]


def host_checks(settings: Settings) -> list[tuple[str, str, str]]:
    """(state, name, note): ok, warn (only some phones need it) or bad."""
    out = []
    for tool, why, needed in [("pmbootstrap", "builds the kernel side", True),
                              ("pacstrap", "arch-install-scripts", True),
                              ("arch-chroot", "arch-install-scripts", True),
                              ("mkbootimg", "android-tools", True),
                              ("img2simg", "android-tools", True),
                              ("mke2fs", "e2fsprogs", True),
                              ("rsync", "rsync", True),
                              ("fastboot", "flashing most phones", False),
                              ("heimdall", "flashing Samsung phones", False)]:
        # e2fsprogs' mke2fs by path: an Android SDK copy can come first in PATH
        found = "/usr/bin/mke2fs" if tool == "mke2fs" and Path("/usr/bin/mke2fs").exists() else shutil.which(tool)
        out.append(("ok" if found else "bad" if needed else "warn", tool, why if not found else found))
    binfmt = Path("/proc/sys/fs/binfmt_misc/qemu-aarch64").exists()
    out.append(("ok" if binfmt else "bad", "qemu aarch64", "runs aarch64 programs" if not binfmt else "registered"))
    pm = Pmaports(settings.pmaports).exists()
    out.append(("ok" if pm else "warn", "pmaports", str(settings.pmaports) if pm else "fetched on first use"))
    reg = Registry(settings.registry)
    out.append(("ok" if reg.devices.is_dir() else "warn", "registry",
                f"{len(reg.entries())} phones in {settings.registry}" if reg.devices.is_dir() else
                f"none at {settings.registry}"))
    return out


# ---- questions -------------------------------------------------------------------------

class Ask(ModalScreen):
    """A yes/no question, or one that needs a word typed (the codename before a flash)."""

    def __init__(self, text: str, typed: str | None = None, yes: str = "yes", no: str = "no"):
        super().__init__()
        self.text, self.typed, self.yes, self.no = text, typed, yes, no

    def compose(self) -> ComposeResult:
        with Vertical(id="ask-box"):
            yield Static(self.text, id="ask-text")
            if self.typed is not None:
                yield Input(placeholder=self.typed, id="ask-input")
            with Horizontal(id="ask-buttons"):
                yield Button(self.yes, id="yes")
                yield Button(self.no, id="no", classes="-quiet")

    @on(Button.Pressed, "#yes")
    @on(Input.Submitted, "#ask-input")
    def _yes(self) -> None:
        if self.typed is not None and self.query_one("#ask-input", Input).value.strip() != self.typed:
            self.notify(f"type {self.typed} to go on", severity="warning")
            return
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def _no(self) -> None:
        self.dismiss(False)

    def key_escape(self) -> None:
        self.dismiss(False)


# ---- welcome ---------------------------------------------------------------------------

class Welcome(Screen):
    BINDINGS = [Binding("p", "phones", "pick a phone"), Binding("r", "registry", "registry"),
                Binding("q", "app.quit", "quit")]

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            # a zero-width space first: the block's leading whitespace would
            # otherwise be stripped, shifting its first line left
            yield Static(Text("\u200b" + WORDMARK.strip("\n")), id="wordmark")
            yield Static("welcome. a phone's codename in, a ztOS image out.", id="tagline")
            yield Static("ztConflo builds Arch Linux ARM with ztMETRO for a phone, on a kernel that already "
                         "works for it: from the ztOS registry first, from postmarketOS when nobody has "
                         "ported ztOS to it yet.", classes="lede")
            with Grid(id="tiles"):
                yield Button("pick a phone", id="phones", classes="tile")
                yield Button("the registry", id="registry", classes="tile -quiet")
                yield Button("quit", id="quit", classes="tile -quiet")
            yield Static("THIS COMPUTER", classes="caps")
            yield Static(id="checks")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_checks()

    def refresh_checks(self) -> None:
        marks = {"ok": ("✓", "check-ok"), "warn": ("·", "check-warn"), "bad": ("✗", "check-bad")}
        lines = []
        missing = 0
        for state, name, note in host_checks(self.app.settings):
            mark, style = marks[state]
            missing += state == "bad"
            lines.append(f"[{_color(style)}]{mark} [/] {name:<14} [#8a8a8a]{_short(note)}[/]")
        if missing:
            lines.append("")
            lines.append(f"[#f0a30a]{missing} thing(s) missing: you can look phones up, but a build needs them. "
                         "The README lists the packages.[/]")
        self.query_one("#checks", Static).update("\n".join(lines))

    @on(Button.Pressed, "#phones")
    def action_phones(self) -> None:
        self.app.push_screen(Phones())

    @on(Button.Pressed, "#registry")
    def action_registry(self) -> None:
        self.app.push_screen(Phones(registry_only=True))

    @on(Button.Pressed, "#quit")
    def _quit(self) -> None:
        self.app.exit()


def _short(text: str) -> str:
    """Home as ~, and a path too long for a line cut in the middle."""
    text = text.replace(str(Path.home()), "~")
    return text if len(text) <= 70 else text[:30] + "…" + text[-38:]


def _color(style: str) -> str:
    return {"check-ok": "#60a917", "check-warn": "#f0a30a", "check-bad": "#e51400"}[style]


# ---- phones ----------------------------------------------------------------------------

class Phones(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "back"), Binding("/", "focus_filter", "search")]

    def __init__(self, registry_only: bool = False):
        super().__init__()
        self.registry_only = registry_only
        self.rows: list[tuple] = []

    def compose(self) -> ComposeResult:
        yield Static("ZTCONFLO", classes="caps")
        yield Static("the registry" if self.registry_only else "pick a phone", classes="title")
        yield Static("Phones already in the ztOS registry come first; below them, postmarketOS phones that "
                     "pass the filter: mainline kernel, aarch64, a GPU Mesa drives." if not self.registry_only
                     else "Every phone ztOS knows, and how far it has got.", classes="lede")
        yield Input(placeholder="search: a name, a codename, a chip…", id="filter")
        yield LoadingIndicator()
        yield DataTable(cursor_type="row", zebra_stripes=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("phone", "codename", "where", "chip", "gpu", "flash")
        table.display = False
        self.load()

    @work(thread=True, exclusive=True)
    def load(self) -> None:
        s = self.app.settings
        reg = Registry(s.registry)
        rows = [(e["device"]["name"], e.codename, f"registry · {e.status}", e["kernel"].get("soc", ""),
                 e["kernel"].get("gpu_driver", ""), e["boot"]["flash_method"]) for e in reg.entries()]
        if not self.registry_only:
            pm = Pmaports(s.pmaports)
            if not pm.exists():
                self.app.call_from_thread(self.notify, "fetching pmaports, once (a minute or two)…")
                pm.ensure(PMAPORTS_URL)
            for d, v in candidates(reg, pm):
                if d.info.get("chassis") in ("handset", "tablet") and \
                        d.info.get("flash_method") in ("fastboot", "heimdall-bootimg", "heimdall-isorec"):
                    rows.append((d.info.get("name", d.codename), d.codename, f"postmarketOS · {d.category}",
                                 v.soc, v.driver or "", d.info.get("flash_method", "")))
        self.app.call_from_thread(self.show, rows)

    def show(self, rows: list[tuple]) -> None:
        self.rows = rows
        self.query_one(LoadingIndicator).display = False
        table = self.query_one(DataTable)
        table.display = True
        self.fill(self.query_one("#filter", Input).value.lstrip("/"))
        table.focus()
        if not rows:
            self.notify("nothing here yet" if self.registry_only else "no phones found", severity="warning")

    def fill(self, text: str) -> None:
        table = self.query_one(DataTable)
        table.clear()
        needle = text.lower().strip()
        for row in self.rows:
            if not needle or any(needle in str(cell).lower() for cell in row):
                table.add_row(*row, key=row[1])

    @on(Input.Changed, "#filter")
    def _filter(self, event: Input.Changed) -> None:
        self.fill(event.value.lstrip("/"))

    @on(Input.Submitted, "#filter")
    def _to_table(self) -> None:
        self.query_one(DataTable).focus()

    def action_focus_filter(self) -> None:
        box = self.query_one("#filter", Input)
        if not box.has_focus:
            box.focus()

    @on(DataTable.RowSelected)
    def _picked(self, event: DataTable.RowSelected) -> None:
        self.app.push_screen(Phone(str(event.row_key.value)))


# ---- one phone -------------------------------------------------------------------------

class Phone(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "back"), Binding("d", "build(True)", "dry run"),
                Binding("b", "build(False)", "build")]

    def __init__(self, codename: str):
        super().__init__()
        self.codename = codename
        self.res = None

    def compose(self) -> ComposeResult:
        yield Static("ZTCONFLO", classes="caps")
        yield Static(self.codename, classes="title", id="name")
        yield Static(id="facts")
        yield Static(id="verdict", classes="verdict")
        with Horizontal(id="actions"):
            yield Button("dry run", id="dry")
            yield Button("build", id="build")
            yield Button("flash", id="flash", classes="-danger")
            yield Button("checklist", id="promote", classes="-quiet")
        yield Footer()

    def on_mount(self) -> None:
        s = self.app.settings
        out = s.out / self.codename
        try:
            self.res = resolve(self.codename, Registry(s.registry), Pmaports(s.pmaports))
        except Exit as e:
            self.query_one("#verdict", Static).update(f"[#e51400 bold]{e}[/]")
            for b in ("dry", "build", "flash", "promote"):
                self.query_one(f"#{b}", Button).disabled = True
            return
        facts = []
        if self.res.source == "registry":
            e = self.res.entry
            self.query_one("#name", Static).update(e["device"]["name"].lower())
            facts += [("from", "the ztOS registry"), ("status", e.status), ("origin", e.origin),
                      ("chip", f"{e['kernel'].get('soc', '?')} · Mesa {e['kernel'].get('gpu_driver', '?')}"),
                      ("kernel", e.get("source", {}).get("pmos", {}).get("kernel_pkg", "ztOS package")),
                      ("flash", f"{e['boot']['flash_method']} → {e['boot'].get('rootfs_target')}")]
            verdict = "[#60a917 bold]in the registry: ready to build[/]"
        else:
            d, v = self.res.device, self.res.verdict
            self.query_one("#name", Static).update(d.info.get("name", d.codename).lower())
            facts += [("from", f"postmarketOS ({d.category})"), ("codename", d.codename),
                      ("chip", f"{v.soc} · Mesa {v.driver}"), ("kernel", f"{v.kernel_pkg} (mainline)"),
                      ("flash", d.info.get("flash_method", "?")),
                      ("note", "a build writes a draft registry entry, untested until it boots")]
            verdict = "[#60a917 bold]eligible[/]"
        if (out / "flash.sh").is_file():
            facts.append(("built", str(out)))
        self.query_one("#facts", Static).update(
            "\n".join(f"[#8a8a8a]{k:<12}[/]{v}" for k, v in facts))
        self.query_one("#verdict", Static).update(verdict)
        self.query_one("#flash", Button).disabled = not (out / "flash.sh").is_file() or self.res.entry is None
        self.query_one("#promote", Button).disabled = self.res.entry is None

    @on(Button.Pressed, "#dry")
    def _dry(self) -> None:
        self.action_build(True)

    @on(Button.Pressed, "#build")
    def _build(self) -> None:
        self.action_build(False)

    def action_build(self, dry: bool) -> None:
        if self.res is None:
            return
        if not dry:
            # sudo asks once, here, in the plain terminal; the build keeps it alive
            with self.app.suspend():
                print("ztConflo needs sudo for pacstrap and arch-chroot.")
                ok = subprocess.run(["sudo", "-v"]).returncode == 0
            if not ok:
                self.notify("no sudo: not building", severity="error")
                return
        self.app.push_screen(Build(self.res, dry))

    @on(Button.Pressed, "#flash")
    def _flash(self) -> None:
        from .flash import targets
        e = Registry(self.app.settings.registry).find(self.codename)
        if e is None:
            return
        lines = "\n".join(f"  {part:<16} ← {img}" for img, part in targets(e["boot"]))
        text = (f"Flash {e['device']['name']} with {e['boot']['flash_method']}?\n\n{lines}\n\n"
                f"This erases everything on those partitions, including all data on "
                f"{e['boot'].get('rootfs_target')}. Type the codename to go on.")

        def go(yes: bool) -> None:
            if not yes:
                return
            with self.app.suspend():
                print(f"Flashing {e.codename}. Keep the phone connected.\n")
                code = subprocess.run(["sh", str(self.app.settings.out / e.codename / "flash.sh")]).returncode
                input(f"\nflash.sh finished ({'ok' if code == 0 else f'exit {code}'}). Enter to go back… ")
            self.notify("flashed" if code == 0 else "flash failed", severity="information" if code == 0 else "error")

        self.app.push_screen(Ask(text, typed=e.codename, yes="flash", no="cancel"), go)

    @on(Button.Pressed, "#promote")
    def _promote(self) -> None:
        e = Registry(self.app.settings.registry).find(self.codename)
        if e is not None:
            self.app.push_screen(Checklist(e))


# ---- running a build -------------------------------------------------------------------

class Build(Screen):
    BINDINGS = [Binding("escape", "back", "back")]

    def __init__(self, res, dry: bool):
        super().__init__()
        self.res, self.dry = res, dry
        self.running = True

    def compose(self) -> ComposeResult:
        name = self.res.entry.codename if self.res.entry else self.res.device.codename
        yield Static("DRY RUN" if self.dry else "BUILDING", classes="caps")
        yield Static(name, classes="title")
        yield Static(id="steps")
        yield RichLog(highlight=False, markup=False, wrap=True, max_lines=5000)
        yield Static(id="result")
        yield Footer()

    def on_mount(self) -> None:
        self.now = -1
        self.failed = False
        self.draw_steps()
        self.run_build()

    def draw_steps(self) -> None:
        parts = []
        for i, title in enumerate(STEPS):
            if i < self.now or (not self.running and not self.failed and self.now >= 0):
                parts.append(f"[#60a917]✓ {title}[/]")
            elif i == self.now:
                parts.append(f"[#e51400 bold]✗ {title}[/]" if self.failed else f"[#0050ef bold]▸ {title}[/]")
            else:
                parts.append(f"[#555555]· {title}[/]")
        self.query_one("#steps", Static).update("   ".join(parts))

    def line(self, text: str) -> None:
        if text.startswith("==> "):
            head = text[4:].split(" ", 1)[0].rstrip(".")
            index = {"1": 0, "2": 1, "3": 2, "3b": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8}.get(head)
            if index is not None:
                self.now = index
                self.draw_steps()
        self.query_one(RichLog).write(text)

    @work(thread=True, exclusive=True)
    def run_build(self) -> None:
        from .build import build
        s = self.app.settings
        keep = threading.Event()
        if not self.dry:
            threading.Thread(target=_sudo_keepalive, args=(keep,), daemon=True).start()
        runner = Runner(dry_run=self.dry, stream=True, log=lambda t: self.app.call_from_thread(self.line, t))
        try:
            out = build(self.res, s, Registry(s.registry), runner)
            msg = (f"[#60a917 bold]done. [/]nothing was built: this was the plan." if self.dry else
                   f"[#60a917 bold]built. [/]{out}/boot.img and rootfs.img are ready; flash from the phone's page.")
        except Exit as e:
            self.failed = True
            msg = f"[#e51400 bold]stopped: [/]{e}"
        except Exception as e:      # shown, not a crash of the whole UI
            self.failed = True
            msg = f"[#e51400 bold]stopped: [/]{type(e).__name__}: {e}"
        finally:
            keep.set()
        self.app.call_from_thread(self.finish, msg)

    def finish(self, msg: str) -> None:
        self.running = False
        if not self.failed:
            self.now = len(STEPS) - 1
        self.draw_steps()
        self.query_one("#result", Static).update(msg + "   [#8a8a8a]esc to go back[/]")

    def action_back(self) -> None:
        if self.running:
            self.notify("still running: it stops on its own, or quit with ctrl+q", severity="warning")
            return
        self.app.pop_screen()


def _sudo_keepalive(stop: threading.Event) -> None:
    while not stop.wait(60):
        subprocess.run(["sudo", "-n", "-v"], capture_output=True)


# ---- the hardware checklist ------------------------------------------------------------

class Checklist(Screen):
    BINDINGS = [Binding("escape", "back", "back")]

    def __init__(self, entry):
        super().__init__()
        self.entry = entry
        self.running = True

    def compose(self) -> ComposeResult:
        yield Static("HARDWARE CHECKLIST", classes="caps")
        yield Static(self.entry["device"]["name"].lower(), classes="title")
        yield Static("Connect the phone by USB with ztOS running. Checks marked automatic run on their own; "
                     "for the rest you are asked what you saw, heard or felt.", classes="lede")
        yield RichLog(highlight=False, markup=True, wrap=True)
        yield Static(id="result")
        yield Footer()

    def on_mount(self) -> None:
        self.run_checks()

    def ask(self, question: str) -> str:
        """From the worker thread: show a question, wait for the answer."""
        done = threading.Event()
        answer = {}

        def got(yes: bool) -> None:
            answer["v"] = "y" if yes else "n"
            done.set()
        self.app.call_from_thread(self.app.push_screen, Ask(question.rstrip(" [y/n]").strip()), got)
        done.wait()
        return answer["v"]

    @work(thread=True, exclusive=True)
    def run_checks(self) -> None:
        from .promote import Phone as Device, promote
        log = self.query_one(RichLog)

        def say(text: str) -> None:
            text = text.replace("automatic: pass", "[#60a917]automatic: pass[/]") \
                       .replace("automatic: fail", "[#e51400]automatic: fail[/]")
            self.app.call_from_thread(log.write, text)
        try:
            status = promote(self.entry, Registry(self.app.settings.registry), Device(), ask=self.ask, log=say,
                             pr=True)
            msg = f"[#60a917 bold]{status}. [/]the registry entry is updated, and a pull request is open."
        except Exit as e:
            msg = f"[#e51400 bold]{e}[/]"
        except Exception as e:
            msg = f"[#e51400 bold]stopped: [/]{type(e).__name__}: {e}"
        self.running = False
        self.app.call_from_thread(self.query_one("#result", Static).update, msg + "   [#8a8a8a]esc to go back[/]")

    def action_back(self) -> None:
        if self.running:
            self.notify("the checklist is still running", severity="warning")
            return
        self.app.pop_screen()


# ---- the app ---------------------------------------------------------------------------

class Conflo(App):
    CSS_PATH = "tui.tcss"
    TITLE = "ztConflo"
    BINDINGS = [Binding("ctrl+q", "quit", "quit", show=False)]

    def __init__(self, settings: Settings | None = None):
        super().__init__()
        self.settings = settings or Settings()

    def on_mount(self) -> None:
        self.push_screen(Welcome())


def run(settings: Settings | None = None) -> int:
    Conflo(settings).run()
    return 0
