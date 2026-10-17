# SPDX-License-Identifier: GPL-2.0-or-later
"""Fuzzy search for the phone list: type roughly what you remember and the
best match comes first. No dependencies.

A query is words. Every word has to match, in any order. A word matches when
its letters appear in order in a field ("pixl" in "Pixel", "op6" in "OnePlus
6"), with only a few letters skipped between them, so scattered letters across
a long line do not count. Letters at the start of a word, or right after the
last matched one, score higher, so the tidy match wins. A word that nearly
matches (a swapped or wrong letter: "xaiomi") still finds the phone, ranked
last."""
import difflib
import re

MAX_GAP = 4                 # letters that may be skipped between two matched ones
GAP_COST = 0.3
RUN_BONUS = 1.5             # the next letter follows the last one
START_BONUS = 2.0           # first letter of a word, or a capital in OnePlus
NEG = -1e9
FIELD_WEIGHT = (1.0, 1.0, 0.6, 0.6, 0.6, 0.6)   # name, codename, where, chip, gpu, flash


def _score(term: str, text: str) -> float | None:
    """How well `term` matches inside `text`, or None. Higher is better."""
    t, h = term.lower(), text.lower()
    n, m = len(t), len(h)
    if n == 0:
        return 0.0
    if n > m:
        return None
    prev: list[float] = []
    for i, ch in enumerate(t):
        cur = [NEG] * m
        for j in range(m):
            if h[j] != ch:
                continue
            bonus = 1.0
            if j == 0 or not h[j - 1].isalnum() or (text[j].isupper() and text[j - 1].islower()):
                bonus += START_BONUS
            if i == 0:
                cur[j] = bonus - 0.05 * j           # a little for starting early
                continue
            best = NEG
            if j >= 1 and prev[j - 1] > NEG / 2:
                best = prev[j - 1] + RUN_BONUS
            for k in range(j - 2, max(-1, j - 2 - MAX_GAP), -1):
                if prev[k] > NEG / 2:
                    best = max(best, prev[k] - GAP_COST * (j - k - 1))
            if best > NEG / 2:
                cur[j] = bonus + best
        prev = cur
    top = max(prev)
    if top < NEG / 2:
        return None
    if t in h:                                       # the whole word, in a row
        top += 4.0 + (3.0 if re.search(r"(?<![a-z0-9])" + re.escape(t), h) else 0.0)
    return top - 0.01 * m                            # shorter text, tighter match


def _near(term: str, text: str) -> float | None:
    """A swapped or wrong letter: close to one whole word of `text`."""
    if len(term) < 4:
        return None
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if abs(len(word) - len(term)) <= 2 and difflib.SequenceMatcher(None, term.lower(), word).ratio() >= 0.75:
            return 1.3 * len(term)
    return None


def match(query: str, fields: tuple[str, ...]) -> float | None:
    """The score of a row (its fields) for the whole query, or None if a word misses."""
    phrase = " ".join(query.lower().split())
    if len(phrase) >= 3 and any(phrase in str(f).lower() for f in fields[2:]):
        return 8.0 + len(phrase)            # the whole query is one column, e.g. "no gpu"
    total = 0.0
    both = f"{fields[0]} {fields[1]}" if len(fields) > 1 else fields[0]
    for term in query.split():
        floor = 1.2 * len(term)
        best = None
        candidates = [(both, 1.0)] + [(f, FIELD_WEIGHT[i] if i < len(FIELD_WEIGHT) else 0.6)
                                      for i, f in enumerate(fields)]
        for index, (text, weight) in enumerate(candidates):
            # name and codename match loosely; the other columns (source, chip,
            # GPU, flash) only by a whole fragment of three letters or more, so
            # "poco" does not find a phone because p-o-c-o are scattered through
            # "pmOS community", and "6" does not find every sdm6xx chip
            if index >= 3 and (len(term) < 3 or term.lower() not in str(text).lower()):
                continue
            s = _score(term, str(text))
            if s is not None and s >= floor:
                best = max(best or NEG, s * weight)
        if best is None:
            near = max((x for x in (_near(term, str(f)) for f in fields[:2]) if x is not None), default=None)
            if near is None:
                return None
            best = near
        total += best
    return total


def rank(query: str, rows: list[tuple]) -> list[tuple]:
    """The rows that match, best first; all of them, as they were, for an empty query."""
    if not query.split():
        return list(rows)
    scored = []
    for order, row in enumerate(rows):
        s = match(query, row)
        if s is not None:
            scored.append((-s, order, row))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [row for _, _, row in scored]
