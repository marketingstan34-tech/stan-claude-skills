"""Search-text normalization and tsquery building for the lexical baseline.

PostgreSQL has no Bulgarian stemmer. Instead of stemming the index we keep it
literal ('simple' config) and make query words prefix matches after stripping a
few common inflectional endings ("делбата" -> "делб:*"). This is a baseline to
be measured, not a claim of linguistic correctness.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_TOKEN = re.compile(r"[0-9a-zа-яѝ]+", re.IGNORECASE)

STOPWORDS = frozenset("""
и в във на за по от до с със се да че е са бил била било били ще не ли а но или ако
когато който която което които как какво кой коя кое кои при като към между след
пред под над без чрез това този тази тези той тя то те му ѝ им го я ги си също
вече още само дали може
""".split())

# Longest first. Applied once, only if a stem of >= 4 letters remains.
_SUFFIXES = (
    "ията", "ието", "ите", "ата", "ото", "ето", "ия", "ие",
    "ът", "ят", "та", "то", "те", "ен", "на", "но", "ни", "а", "я", "о", "е", "и", "у",
)
_REFERENCE_MARKERS = frozenset({"чл", "ал", "т", "пар", "тр", "дело"})
MIN_STEM = 4


def normalize_for_search(text: str) -> str:
    text = unicodedata.normalize("NFC", text).lower().replace("ѐ", "е")
    return " ".join(_TOKEN.findall(text))


def stem(word: str) -> str:
    for suf in _SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= MIN_STEM:
            return word[: -len(suf)]
    return word


@dataclass(frozen=True)
class Term:
    kind: str  # "word" | "phrase"
    value: str  # stem for words; space-joined tokens for phrases

    def to_tsquery(self) -> str:
        if self.kind == "phrase":
            return "(" + " <-> ".join(self.value.split()) + ")"
        return f"{self.value}:*"


def parse_query(query: str) -> list[Term]:
    """Reference pairs such as 'чл. 349' become exact phrases; other words become prefixes."""
    tokens = normalize_for_search(query).split()
    terms: list[Term] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _REFERENCE_MARKERS and i + 1 < len(tokens) and tokens[i + 1].isdigit():
            terms.append(Term("phrase", f"{tok} {tokens[i + 1]}"))
            i += 2
            continue
        if tok.isdigit():
            terms.append(Term("phrase", tok))
        elif tok not in STOPWORDS and len(tok) >= 3:
            terms.append(Term("word", stem(tok)))
        i += 1
    unique: list[Term] = []
    for t in terms:
        if t not in unique:
            unique.append(t)
    return unique


def build_tsquery(terms: list[Term], mode: str = "all") -> str:
    joiner = " & " if mode == "all" else " | "
    return joiner.join(t.to_tsquery() for t in terms)


def word_matches(word: str, terms: list[Term]) -> bool:
    """For display highlighting: does a normalized word match any query term?"""
    for t in terms:
        if t.kind == "word" and word.startswith(t.value):
            return True
        if t.kind == "phrase" and word.isdigit() and word in t.value.split():
            return True
    return False
