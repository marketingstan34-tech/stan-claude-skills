"""Deterministic citation check: does the quote exist verbatim at the given offsets?

This proves textual identity only, never legal interpretation.
"""

from __future__ import annotations

import hashlib
from enum import Enum


class TextStatus(str, Enum):
    TEXT_VERIFIED = "text_verified"
    INVALID = "invalid"
    SOURCE_UNAVAILABLE = "source_unavailable"


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _squash_ws(s: str) -> str:
    return " ".join(s.split())


def verify_quote(
    canonical_text: str | None,
    start: int,
    end: int,
    quote: str,
    expected_hash: str | None = None,
) -> TextStatus:
    """Offsets are Unicode code points into the canonical text, [start, end).

    Only whitespace differences are tolerated (documented normalization).
    """
    if canonical_text is None:
        return TextStatus.SOURCE_UNAVAILABLE
    if expected_hash is not None and text_hash(canonical_text) != expected_hash:
        return TextStatus.INVALID
    if not (0 <= start < end <= len(canonical_text)):
        return TextStatus.INVALID
    if _squash_ws(canonical_text[start:end]) != _squash_ws(quote):
        return TextStatus.INVALID
    return TextStatus.TEXT_VERIFIED


def locate_quote(canonical_text: str, quote: str) -> tuple[int, int] | None:
    """Find a quote in the text tolerating only whitespace differences.

    Returns code-point offsets [start, end) into canonical_text, or None. The result is
    meant to be re-checked with verify_quote().
    """
    words = quote.split()
    if not words:
        return None
    # Map positions of the whitespace-squashed text back to the original.
    squashed_chars: list[str] = []
    origin: list[int] = []
    prev_space = True
    for i, ch in enumerate(canonical_text):
        if ch.isspace():
            if not prev_space:
                squashed_chars.append(" ")
                origin.append(i)
            prev_space = True
        else:
            squashed_chars.append(ch)
            origin.append(i)
            prev_space = False
    squashed = "".join(squashed_chars)
    needle = " ".join(words)
    pos = squashed.find(needle)
    if pos < 0:
        return None
    start = origin[pos]
    end = origin[pos + len(needle) - 1] + 1
    return start, end
