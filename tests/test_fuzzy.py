# SPDX-License-Identifier: GPL-2.0-or-later
"""The phone list's fuzzy search, on rows shaped like the real list."""
import pytest

from ztconflo.fuzzy import rank

ROWS = [
    ("OnePlus 6", "oneplus-enchilada", "pmOS community", "sdm845", "freedreno", "fastboot"),
    ("OnePlus 6T", "oneplus-fajita", "pmOS community", "sdm845", "freedreno", "fastboot"),
    ("OnePlus Nord N100", "oneplus-billie2", "pmOS testing", "sm4250", "freedreno", "fastboot"),
    ("Google Pixel 3a", "google-sargo", "pmOS community", "sdm670", "freedreno", "fastboot"),
    ("Google Pixel 3a XL", "google-bonito", "pmOS community", "sdm670", "freedreno", "fastboot"),
    ("Google Pixel 6", "google-oriole", "pmOS testing", "gs101", "no GPU", "fastboot"),
    ("Samsung Galaxy S9", "samsung-starqltechn", "pmOS community", "sdm845", "freedreno", "fastboot"),
    ("Samsung Galaxy A72", "samsung-a72q", "pmOS testing", "sm7125", "freedreno", "heimdall-bootimg"),
    ("Xiaomi Redmi Note 4X", "xiaomi-mido", "pmOS archived", "msm8953", "freedreno", "fastboot"),
    ("Xiaomi Redmi Note 8", "xiaomi-ginkgo", "pmOS testing", "sm6125", "freedreno", "fastboot"),
    ("Xiaomi Poco F1", "xiaomi-beryllium", "pmOS community", "sdm845", "freedreno", "fastboot"),
    ("Fairphone 4", "fairphone-fp4", "pmOS community", "sm7225", "freedreno", "fastboot"),
    ("Fairphone 5", "fairphone-fp5", "pmOS testing", "qcm6490", "freedreno", "fastboot"),
]


def names(query):
    return [r[0] for r in rank(query, ROWS)]


def test_an_empty_query_keeps_everything_in_order():
    assert rank("", ROWS) == ROWS
    assert rank("   ", ROWS) == ROWS


@pytest.mark.parametrize("query, first", [
    ("op6", "OnePlus 6"),                    # abbreviation
    ("oneplus6", "OnePlus 6"),               # the space left out
    ("xiomi mido", "Xiaomi Redmi Note 4X"),  # a letter dropped, two words
    ("redmi note4", "Xiaomi Redmi Note 4X"),
    ("mido xiaomi", "Xiaomi Redmi Note 4X"), # any order
    ("enchilada", "OnePlus 6"),              # a codename
    ("fp4", "Fairphone 4"),
    ("s9", "Samsung Galaxy S9"),
    ("GALAXY", "Samsung Galaxy S9"),         # case does not matter
    ("pixel 3a", "Google Pixel 3a"),         # the shorter, tighter name before "3a XL"
])
def test_the_right_phone_comes_first(query, first):
    assert names(query)[0] == first


def test_a_dropped_letter_finds_every_pixel_before_anything_else():
    hits = names("pixl")
    assert set(hits[:3]) == {"Google Pixel 3a", "Google Pixel 3a XL", "Google Pixel 6"}


def test_short_words_only_match_names_not_chip_columns():
    assert names("pixel 6") == ["Google Pixel 6"]          # the "6" of sdm670 must not count


def test_a_swapped_letter_still_finds_the_phone():
    assert "Xiaomi Redmi Note 4X" in names("xaiomi")        # not a subsequence: near-match
    assert "Samsung Galaxy S9" in names("galxay")


def test_nonsense_finds_nothing():
    assert names("zzzz") == []
    assert names("qwxz pixel") == []


def test_the_other_columns_filter_by_whole_fragments():
    assert set(names("sdm845")) == {"OnePlus 6", "OnePlus 6T", "Samsung Galaxy S9", "Xiaomi Poco F1"}
    assert names("heimdall") == ["Samsung Galaxy A72"]
    assert names("archived") == ["Xiaomi Redmi Note 4X"]
    assert names("no gpu") == ["Google Pixel 6"]


def test_scattered_letters_in_a_long_column_do_not_match():
    # p-o-c-o can be picked out of "pmOS community"; that must not find the Fairphone
    assert names("poco") == ["Xiaomi Poco F1"]


def test_every_word_has_to_match():
    assert names("oneplus freedreno")[0].startswith("OnePlus")
    assert "Google Pixel 3a" not in names("pixel samsung")
