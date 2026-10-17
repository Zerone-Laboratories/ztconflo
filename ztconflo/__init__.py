# SPDX-License-Identifier: GPL-2.0-or-later
"""ztConflo: a phone codename in, a flashable ztOS image out."""
__version__ = "0.1.0"


class Exit(Exception):
    """Stop with one of the spec's exit codes and a message for the person."""
    NOT_FOUND, NOT_ELIGIBLE, BUILD_FAILED, HASH_MISMATCH, CHECKLIST_FAILED = 1, 2, 3, 4, 5

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
