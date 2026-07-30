"""Offline knowledge lookup behind ``metaglens explain``.

The domain knowledge lives in ``knowledge/topics.yaml``; this module only loads
and searches it. Keeping it as data means a biologist can correct an entry
without reading Python, and the whole thing works with no network.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

_TOPICS_FILE = "topics.yaml"


def knowledge_path() -> Path:
    return Path(__file__).with_name("knowledge") / _TOPICS_FILE


def load_topics(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Load the knowledge base (empty dict when unavailable)."""
    import yaml
    target = Path(path) if path else knowledge_path()
    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def topics(path: Optional[Path] = None) -> List[str]:
    return sorted(load_topics(path))


def lookup(topic: str, path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Exact, then case-insensitive, then unique-prefix match."""
    data = load_topics(path)
    if topic in data:
        return dict(data[topic], topic=topic)
    lowered = topic.lower()
    for key, value in data.items():
        if key.lower() == lowered:
            return dict(value, topic=key)
    prefixed = [k for k in data if k.lower().startswith(lowered)]
    if len(prefixed) == 1:
        return dict(data[prefixed[0]], topic=prefixed[0])
    return None


def candidates(topic: str, path: Optional[Path] = None,
               limit: int = 5) -> List[str]:
    """Suggestions for a topic that was not found."""
    from .suggest import closest
    data = load_topics(path)
    matches = closest(topic, data.keys(), limit=limit, cutoff=0.5)
    if matches:
        return matches
    lowered = topic.lower()
    hits = [k for k in sorted(data) if lowered in k.lower()]
    if hits:
        return hits[:limit]
    # Last resort: anything whose text mentions the term.
    text_hits = []
    for key, value in sorted(data.items()):
        blob = " ".join(str(v) for v in value.values()).lower()
        if lowered in blob:
            text_hits.append(key)
    return text_hits[:limit]


def render_text(entry: Dict[str, Any]) -> str:
    """Plain-text rendering (also used for --json-free output and tests)."""
    lines: List[str] = []
    title = entry.get("title") or entry.get("topic", "")
    lines.append(str(title))
    lines.append("=" * min(len(str(title)), 72))
    summary = " ".join(str(entry.get("summary", "")).split())
    if summary:
        lines.append(summary)
    tradeoffs = " ".join(str(entry.get("tradeoffs", "")).split())
    if tradeoffs:
        lines.append("")
        lines.append("Trade-offs: " + tradeoffs)
    pitfalls = entry.get("pitfalls") or []
    if pitfalls:
        lines.append("")
        lines.append("Watch out for:")
        for item in pitfalls:
            lines.append("  - " + " ".join(str(item).split()))
    see_also = entry.get("see_also") or []
    if see_also:
        lines.append("")
        lines.append("See also: " + ", ".join(str(s) for s in see_also))
    return "\n".join(lines) + "\n"
