# SPDX-License-Identifier: GPL-2.0-or-later
"""Installing what is missing: one pacman call, only for what is missing."""
import subprocess

from ztconflo import setup


def test_packages_are_deduplicated_and_complete():
    needs = [n for n in setup.NEEDS if n.name in ("pacstrap", "arch-chroot", "qemu aarch64", "mkbootimg",
                                                  "img2simg")]
    assert setup.packages_for(needs) == ["arch-install-scripts", "android-tools", "qemu-user-static-binfmt",
                                         "qemu-user-static"]


def test_install_runs_one_pacman_command(monkeypatch, tmp_path):
    monkeypatch.setattr(setup, "DONE", tmp_path / "done")
    monkeypatch.setattr(setup, "is_arch", lambda: True)
    monkeypatch.setattr(setup, "missing", lambda: [])
    calls = []
    need = next(n for n in setup.NEEDS if n.name == "pmbootstrap")
    ok = setup.install([need], out=lambda *_: None,
                       run=lambda cmd, **k: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    assert ok and calls == [["sudo", "pacman", "-S", "--needed", "--noconfirm", "pmbootstrap"]]
    assert (tmp_path / "done").exists()


def test_nothing_missing_installs_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(setup, "DONE", tmp_path / "done")
    calls = []
    assert setup.install([], run=lambda *a, **k: calls.append(a))
    assert calls == []


def test_not_arch_only_prints(monkeypatch, tmp_path):
    monkeypatch.setattr(setup, "DONE", tmp_path / "done")
    monkeypatch.setattr(setup, "is_arch", lambda: False)
    said = []
    assert not setup.install([setup.NEEDS[0]], out=said.append, run=lambda *a, **k: 1 / 0)
    assert "pmbootstrap" in said[-1]
