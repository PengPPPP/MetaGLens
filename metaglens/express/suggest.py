"""Did-you-mean suggestions for mistyped names.

A typo in a step id, a route name, or a config key should produce a nudge, not a
wall of valid values. ``difflib`` is stdlib, so this costs nothing.
"""

from __future__ import annotations

import difflib
from typing import Iterable, List, Optional


def closest(word: str, candidates: Iterable[str], limit: int = 3,
            cutoff: float = 0.6) -> List[str]:
    """Return the closest candidates to ``word``, best first (possibly empty)."""
    options = [c for c in candidates if c]
    if not word or not options:
        return []
    matches = difflib.get_close_matches(word, options, n=limit, cutoff=cutoff)
    if matches:
        return matches
    # Fall back to substring containment, which catches truncations that the
    # ratio-based cutoff rejects (e.g. "taxonomy" for "07_taxonomy").
    lowered = word.lower()
    return [c for c in options if lowered in c.lower()][:limit]


def suggest(word: str, candidates: Iterable[str], limit: int = 3) -> str:
    """A ready-to-append hint, or an empty string when nothing is close."""
    matches = closest(word, candidates, limit=limit)
    if not matches:
        return ""
    if len(matches) == 1:
        return f"Did you mean '{matches[0]}'?"
    quoted = ", ".join(f"'{m}'" for m in matches)
    return f"Did you mean one of {quoted}?"


def describe_unknown(word: str, candidates: Iterable[str], kind: str = "value",
                     show_all_limit: Optional[int] = 12) -> str:
    """Full message: what was wrong, a suggestion, and the valid values."""
    options = sorted({c for c in candidates if c})
    parts = [f"Unknown {kind} '{word}'."]
    hint = suggest(word, options)
    if hint:
        parts.append(hint)
    if options and (show_all_limit is None or len(options) <= show_all_limit):
        parts.append(f"Valid: {', '.join(options)}.")
    elif options:
        parts.append(f"{len(options)} valid values; see the docs or --help.")
    return " ".join(parts)
