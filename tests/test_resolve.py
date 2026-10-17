# SPDX-License-Identifier: GPL-2.0-or-later
"""Resolution and eligibility against real pmaports files (tests/fixtures/pmaports)."""
from pathlib import Path

import pytest

from ztconflo import Exit
from ztconflo.pmos import Pmaports, parse_cmdline, parse_deviceinfo
from ztconflo.registry import Registry
from ztconflo.resolve import candidates, draft_entry, eligibility, gpu_driver, resolve

FIX = Path(__file__).parent / "fixtures" / "pmaports"


@pytest.fixture
def pm():
    return Pmaports(FIX)


@pytest.fixture
def empty_registry(tmp_path):
    return Registry(tmp_path / "registry")


def test_deviceinfo_parser_reads_values_and_skips_comments():
    info = parse_deviceinfo('# a comment\ndeviceinfo_name="OnePlus 6"\n'
                            'deviceinfo_flash_pagesize="4096" # trailing\nnot_ours="x"\n')
    assert info == {"name": "OnePlus 6", "flash_pagesize": "4096"}


def test_cmdline_drops_comments_and_removals():
    text = "console=ttyMSM0,115200\n# FIXME: a comment\n-quiet\nfoo=1 bar # tail\n"
    assert parse_cmdline(text) == "console=ttyMSM0,115200 foo=1 bar"


def test_enchilada_is_eligible(pm):
    dev = pm.find("oneplus-enchilada")
    v = eligibility(dev, pm)
    assert v.eligible, v.reason
    assert (v.soc, v.driver, v.kernel_pkg) == ("sdm845", "freedreno", "linux-postmarketos-qcom-sdm845")
    assert dev.cmdline == "console=ttyMSM0,115200"


def test_short_codename_matches(pm):
    assert pm.find("enchilada").codename == "oneplus-enchilada"


def test_heimdall_samsung_is_eligible(pm):
    v = eligibility(pm.find("samsung-a72q"), pm)
    assert v.eligible and v.driver == "freedreno"


@pytest.mark.parametrize("codename, words, dead_end", [
    ("amazon-sheldon", "category 'downstream'", True),
    ("samsung-starlte", "downstream (vendor) kernel", True),      # archived, and a vendor kernel
    ("asus-flo", "not aarch64", True),
    ("generic-x86_64", "not aarch64", True),
])
def test_rejections_name_their_rule(pm, codename, words, dead_end):
    v = eligibility(pm.find(codename), pm)
    assert not v.eligible
    assert words in v.reason
    assert v.dead_end is dead_end


def test_no_gpu_is_eligible_but_flagged(pm):
    v = eligibility(pm.find("google-oriole"), pm)
    assert v.eligible and not v.accelerated and v.soc == "gs101"


def test_no_gpu_warning_on_lookup(pm, tmp_path, capsys):
    from ztconflo.cli import main
    from ztconflo.resolve import GPU_WARNING
    assert main(["--pmaports", str(FIX), "--registry", str(tmp_path), "lookup", "google-oriole"]) == 0
    assert GPU_WARNING in capsys.readouterr().out
    assert GPU_WARNING == ("Unable to detect a Graphics Accelerator for the selected device, "
                           "the Metro Shell will not start or work properly !")


def test_archived_phone_with_a_mainline_kernel_is_eligible_but_flagged(pm):
    from ztconflo.resolve import ARCHIVED_WARNING, GPU_WARNING
    v = eligibility(pm.find("xiaomi-mido"), pm)
    assert v.eligible and v.unmaintained and v.accelerated
    assert (v.soc, v.driver) == ("msm8953", "freedreno")
    assert v.warnings == [ARCHIVED_WARNING]
    assert GPU_WARNING not in v.warnings


def test_maintained_phones_carry_no_archived_flag(pm):
    v = eligibility(pm.find("oneplus-enchilada"), pm)
    assert v.eligible and not v.unmaintained and v.warnings == []


def test_archived_and_no_gpu_gets_both_warnings():
    from ztconflo.resolve import ARCHIVED_WARNING, GPU_WARNING, Verdict
    v = Verdict(True, "eligible", False, "gs101", None, "linux-x", True)
    assert v.warnings == [GPU_WARNING, ARCHIVED_WARNING]


def test_registry_entries_warn_the_same_way(pm, empty_registry):
    from ztconflo.resolve import ARCHIVED_WARNING, entry_warnings
    dev = pm.find("xiaomi-mido")
    entry = draft_entry(dev, eligibility(dev, pm), "abc", "6.18")
    assert entry["source"]["pmos"]["category"] == "archived"
    assert entry_warnings(entry) == [ARCHIVED_WARNING]
    assert entry_warnings(draft_entry(pm.find("oneplus-enchilada"), eligibility(pm.find("oneplus-enchilada"), pm),
                                      "abc", "6.18")) == []


def test_archived_warning_on_lookup_and_candidates_last(pm, tmp_path, capsys):
    from ztconflo.cli import main
    from ztconflo.resolve import ARCHIVED_WARNING
    assert main(["--pmaports", str(FIX), "--registry", str(tmp_path), "lookup", "xiaomi-mido"]) == 0
    assert ARCHIVED_WARNING in capsys.readouterr().out
    assert main(["--pmaports", str(FIX), "--registry", str(tmp_path), "candidates"]) == 0
    out = capsys.readouterr()
    lines = [l.split()[0] for l in out.out.splitlines()]
    assert lines.index("xiaomi-mido") > lines.index("oneplus-enchilada")      # archived after maintained
    assert "archived by postmarketOS" in out.err


def test_multi_kernel_device_picks_a_kernel(pm):
    dev = pm.find("ayn-odin")
    assert len(dev.kernels) >= 1
    v = eligibility(dev, pm)
    assert v.kernel_pkg == "linux-ayn-odin"


def test_resolve_exit_codes(pm, empty_registry):
    assert resolve("oneplus-enchilada", empty_registry, pm).source == "pmos"
    with pytest.raises(Exit) as e:
        resolve("nosuchphone", empty_registry, pm)
    assert e.value.code == Exit.NOT_FOUND
    with pytest.raises(Exit) as e:
        resolve("amazon-sheldon", empty_registry, pm)
    assert e.value.code == Exit.NOT_ELIGIBLE


def test_registry_wins_over_pmaports(pm, empty_registry):
    dev = pm.find("oneplus-enchilada")
    empty_registry.write(draft_entry(dev, eligibility(dev, pm), "abc", "6.18"))
    assert resolve("oneplus-enchilada", empty_registry, pm).source == "registry"
    assert resolve("enchilada", empty_registry, pm).source == "registry"


def test_candidates_skip_registered_phones(pm, empty_registry):
    names = {d.codename for d, _ in candidates(empty_registry, pm)}
    assert {"oneplus-enchilada", "samsung-a72q", "ayn-odin", "google-oriole"} <= names
    dev = pm.find("oneplus-enchilada")
    empty_registry.write(draft_entry(dev, eligibility(dev, pm), "abc", "6.18"))
    assert "oneplus-enchilada" not in {d.codename for d, _ in candidates(empty_registry, pm)}


def test_draft_entry_freezes_boot_fields(pm):
    dev = pm.find("oneplus-enchilada")
    e = draft_entry(dev, eligibility(dev, pm), "deadbeef", "6.18.1")
    assert e["device"]["status"] == "untested" and e["device"]["origin"] == "pmos"
    assert e["source"]["pmos"]["pmaports_commit"] == "deadbeef"
    assert e["source"]["pmos"]["firmware_pkgs"] == ["firmware-oneplus-sdm845", "firmware-oneplus-sdm845-sensors"]
    b = e["boot"]
    assert (b["flash_method"], b["pagesize"], b["append_dtb"], b["rootfs_target"]) == \
        ("fastboot", 4096, True, "userdata")
    assert b["kernel_offset"] == "0x00008000" and b["dtb"] == "qcom/sdm845-oneplus-enchilada"
    assert "bootimg_header_version" in b["defaulted"]
    assert e["userspace"]["daemons"] == ["rmtfs", "pd-mapper", "tqftpserv"]


def test_heimdall_draft_names_partitions(pm):
    dev = pm.find("samsung-a72q")
    b = draft_entry(dev, eligibility(dev, pm), "x", "")["boot"]
    assert b["flash_method"] == "heimdall-bootimg"
    assert b["heimdall_partition_kernel"] and b["rootfs_target"]


@pytest.mark.parametrize("soc, driver", [
    ("sdm845", "freedreno"), ("rk3399", "panfrost"), ("rk3588", "panthor"), ("sun50i-a64", "lima"),
    ("imx8mq", "etnaviv"), ("mt8183", "panfrost"), ("exynos9810", None), ("gs101", None),
])
def test_gpu_table(soc, driver):
    known, got = gpu_driver(soc)
    assert known and got == driver


def test_unknown_soc_is_not_a_dead_end():
    assert gpu_driver("zz9000") == (False, None)
