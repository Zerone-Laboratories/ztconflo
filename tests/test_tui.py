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
            await pilot.press(*"a72q")              # typing searches: no key to press first
            assert app.screen.query_one("DataTable").row_count == 1
            await pilot.press("enter")
            assert await _until(pilot, lambda: isinstance(app.screen, Phone))
            assert app.screen.codename == "samsung-a72q"
            await pilot.press("d")
            assert await _until(pilot, lambda: isinstance(app.screen, Build) and not app.screen.running)
            assert not app.screen.failed
        assert not (tmp_path / "reg").exists()      # a dry run writes nothing

    asyncio.run(go())


def test_arrow_keys_choose_while_the_search_has_the_keyboard(tmp_path):
    s = Settings(registry=tmp_path / "reg", pmaports=FIX, out=tmp_path / "out", work=tmp_path / "work")

    async def go():
        app = Conflo(s)
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()
            await pilot.press("p")
            assert await _until(pilot, lambda: isinstance(app.screen, Phones) and app.screen.rows)
            table = app.screen.query_one("DataTable")
            assert app.screen.query_one("#filter").has_focus
            await pilot.press("down", "down")
            assert table.cursor_row == 2
            await pilot.press("up")
            assert table.cursor_row == 1
            chosen = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
            await pilot.press("enter")
            assert await _until(pilot, lambda: isinstance(app.screen, Phone))
            assert app.screen.codename == chosen
            await pilot.press("escape")
            assert await _until(pilot, lambda: isinstance(app.screen, Phones))
            # the search still has the keyboard after coming back; Escape clears, then goes back
            await pilot.press(*"zzzz")
            assert table.row_count == 0
            await pilot.press("escape")
            assert app.screen.query_one("#filter").value == "" and isinstance(app.screen, Phones)
            assert table.row_count == len(app.screen.rows)
            await pilot.press("escape")
            assert await _until(pilot, lambda: isinstance(app.screen, Welcome))

    asyncio.run(go())


def test_a_misspelled_search_still_opens_the_phone(tmp_path):
    s = Settings(registry=tmp_path / "reg", pmaports=FIX, out=tmp_path / "out", work=tmp_path / "work")

    async def go():
        app = Conflo(s)
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()
            await pilot.press("p")
            assert await _until(pilot, lambda: isinstance(app.screen, Phones) and app.screen.rows)
            await pilot.press(*"xiomi mido")        # a letter dropped, as people type it
            table = app.screen.query_one("DataTable")
            assert table.row_count == 1
            await pilot.press("enter")
            assert await _until(pilot, lambda: isinstance(app.screen, Phone))
            assert app.screen.codename == "xiaomi-mido"

    asyncio.run(go())


def test_a_gsi_dry_run_shows_the_gsi_steps(tmp_path):
    from test_gsi import make_gsi
    from ztconflo.tui import GSI_STEPS
    s = Settings(registry=tmp_path / "reg", pmaports=FIX, out=tmp_path / "out", work=tmp_path / "work",
                 gsi=str(make_gsi(tmp_path / "dl")), gsi_cache=tmp_path / "cache")

    async def go():
        app = Conflo(s)
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()
            app.push_screen(Phone("oneplus-enchilada"))
            assert await _until(pilot, lambda: isinstance(app.screen, Phone))
            await pilot.press("d")
            assert await _until(pilot, lambda: isinstance(app.screen, Build) and not app.screen.running)
            assert not app.screen.failed and app.screen.steps == GSI_STEPS
            assert app.screen.now == len(GSI_STEPS) - 1
        assert not (tmp_path / "cache").exists()

    asyncio.run(go())
