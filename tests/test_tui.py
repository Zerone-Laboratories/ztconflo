# SPDX-License-Identifier: GPL-2.0-or-later
"""The TUI, driven headless: welcome -> phones -> a phone -> a dry run."""
import asyncio
from pathlib import Path

import pytest

textual = pytest.importorskip("textual")

from ztconflo.config import Settings  # noqa: E402
from ztconflo.tui import Build, Conflo, Phone, Phones, Welcome  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "pmaports"


async def _until(pilot, cond, tries=100):
    for _ in range(tries):
        await pilot.pause(0.05)
        if cond():
            return True
    return False


def test_walk_through_to_a_dry_run(tmp_path):
    s = Settings(registry=tmp_path / "reg", pmaports=FIX, out=tmp_path / "out", work=tmp_path / "work")

    async def go():
        app = Conflo(s)
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, Welcome)
            await pilot.press("p")
            assert await _until(pilot, lambda: isinstance(app.screen, Phones) and app.screen.rows)
            codes = [r[1] for r in app.screen.rows]
            assert "oneplus-enchilada" in codes and "amazon-sheldon" not in codes
            await pilot.press("/", *"a72q")
            assert app.screen.query_one("DataTable").row_count == 1
            await pilot.press("enter", "enter")
            assert await _until(pilot, lambda: isinstance(app.screen, Phone))
            assert app.screen.codename == "samsung-a72q"
            await pilot.press("d")
            assert await _until(pilot, lambda: isinstance(app.screen, Build) and not app.screen.running)
            assert not app.screen.failed
        assert not (tmp_path / "reg").exists()      # a dry run writes nothing

    asyncio.run(go())
