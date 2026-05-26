"""
YAML catalog loader for AI-tell phrases and tone-aware replacements.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Literal, Optional

import yaml

Tone = Literal["formal", "casual", "professional", "friendly"]
VALID_TONES: tuple[str, ...] = ("formal", "casual", "professional", "friendly")

_CATALOG_PATH = Path(__file__).resolve().parent / "catalog.yaml"


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    type: str  # word | phrase | regex
    category: str
    pattern: str
    weight: float = 1.0
    replacements: Dict[str, List[str]] = field(default_factory=dict)
    llm_only: bool = False
    is_regex: bool = False

    @property
    def has_replacements(self) -> bool:
        if self.llm_only:
            return False
        for alts in self.replacements.values():
            if alts:
                return True
        return False


def _normalize_replacements(raw: Optional[Dict]) -> Dict[str, List[str]]:
    if not raw:
        return {}
    out: Dict[str, List[str]] = {}
    for key, val in raw.items():
        if isinstance(val, list):
            out[str(key)] = [str(v) for v in val if v]
        elif val:
            out[str(key)] = [str(val)]
    return out


@lru_cache(maxsize=1)
def load_catalog() -> List[CatalogEntry]:
    if not _CATALOG_PATH.is_file():
        return []
    with _CATALOG_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    entries: List[CatalogEntry] = []
    for item in data.get("entries") or []:
        if not item or not item.get("id") or not item.get("pattern"):
            continue
        entry_type = str(item.get("type", "phrase"))
        is_regex = entry_type == "regex" or bool(item.get("is_regex"))
        entries.append(
            CatalogEntry(
                id=str(item["id"]),
                type=entry_type,
                category=str(item.get("category", "buzzword")),
                pattern=str(item["pattern"]),
                weight=float(item.get("weight", 1.0)),
                replacements=_normalize_replacements(item.get("replacements")),
                llm_only=bool(item.get("llm_only", False)),
                is_regex=is_regex,
            )
        )
    return entries


def get_entries_sorted() -> List[CatalogEntry]:
    """Longest pattern first for non-overlapping phrase matches."""
    entries = [e for e in load_catalog() if not e.is_regex]
    return sorted(entries, key=lambda e: -len(e.pattern))


def get_regex_entries() -> List[CatalogEntry]:
    return [e for e in load_catalog() if e.is_regex]


def get_transition_words() -> set[str]:
    words = set()
    for e in load_catalog():
        if e.category == "transition" and e.type == "word":
            words.add(e.pattern.lower())
    return words or {
        "furthermore", "moreover", "additionally", "consequently",
        "nevertheless", "nonetheless", "accordingly", "hence", "thus",
        "therefore", "henceforth",
    }


def get_replacement(
    entry: CatalogEntry,
    tone: str = "professional",
    paragraph_index: int = 0,
) -> Optional[str]:
    """Pick a synonym with hash rotation to avoid repetition."""
    if entry.llm_only or not entry.has_replacements:
        return None
    reps = entry.replacements
    alts: List[str] = []
    if tone in reps and reps[tone]:
        alts = list(reps[tone])
    elif reps.get("default"):
        alts = list(reps["default"])
    else:
        for t in VALID_TONES:
            if reps.get(t):
                alts = list(reps[t])
                break
    if not alts:
        return None
    idx = hash((entry.id, paragraph_index)) % len(alts)
    return alts[idx]


def flag_only_ratio() -> float:
    """Fraction of non-llm_only entries lacking any replacement list."""
    entries = load_catalog()
    if not entries:
        return 0.0
    actionable = [e for e in entries if not e.is_regex and not e.llm_only]
    if not actionable:
        return 0.0
    flag_only = sum(1 for e in actionable if not e.has_replacements)
    return flag_only / len(actionable)


def get_entry_by_id(entry_id: str) -> Optional[CatalogEntry]:
    for e in load_catalog():
        if e.id == entry_id:
            return e
    return None


def compile_regex_entry(entry: CatalogEntry) -> re.Pattern[str]:
    flags = re.IGNORECASE if not entry.pattern.startswith("(?") else 0
    return re.compile(entry.pattern, flags)
