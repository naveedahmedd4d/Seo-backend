"""Inject internal member links into cluster hub article bodies."""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


def normalize_person_key(name: str) -> str:
    """NFC + fold curly apostrophes for heading match."""
    s = unicodedata.normalize("NFC", (name or "").strip())
    s = s.replace("\u2019", "'").replace("\u2018", "'").replace("`", "'")
    return re.sub(r"\s+", " ", s).lower()


def normalize_internal_path(path: str) -> str:
    p = (path or "").strip()
    if not p:
        return ""
    if p.startswith("http://") or p.startswith("https://"):
        return p
    if not p.startswith("/"):
        p = "/" + p.lstrip("/")
    return p.rstrip("/") or "/"


def slugify_member_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9\s-]", "", (name or "").lower())
    slug = re.sub(r"[-\s]+", "-", slug).strip("-")
    return slug[:80] or "article"


def fallback_member_path(name: str, content_type: str = "net_worth") -> str:
    """Deterministic path when a completed workflow has an empty slug."""
    base = slugify_member_name(name)
    if content_type == "biography":
        return f"/{base}"
    return f"/{base}-net-worth"


def content_types_to_generate(
    blog_type: str,
    bio_workflow_id: Optional[int] = None,
    net_worth_workflow_id: Optional[int] = None,
) -> List[str]:
    """Return content types still needed for a cluster sub (skip if already linked)."""
    bt = (blog_type or "").strip()
    needed: List[str] = []
    if bt == "both":
        if bio_workflow_id is None:
            needed.append("biography")
        if net_worth_workflow_id is None:
            needed.append("net_worth")
        return needed
    if bt == "biography":
        return [] if bio_workflow_id is not None else ["biography"]
    if bt == "net_worth":
        return [] if net_worth_workflow_id is not None else ["net_worth"]
    return []


def prefer_member_link_path(
    *,
    net_worth_path: Optional[str] = None,
    biography_path: Optional[str] = None,
) -> Optional[str]:
    """Prefer net worth path when both exist (pillar hubs are net-worth themed)."""
    nw = normalize_internal_path(net_worth_path or "")
    bio = normalize_internal_path(biography_path or "")
    if nw:
        return nw
    if bio:
        return bio
    return None


def _heading_level(line: str) -> int:
    if line.startswith("### "):
        return 3
    if line.startswith("## "):
        return 2
    return 0


def _heading_text(line: str) -> str:
    level = _heading_level(line)
    if level == 3:
        return line[4:].strip()
    if level == 2:
        return line[3:].strip()
    return ""


def _find_insert_index_after_first_paragraph(lines: List[str], heading_idx: int) -> int:
    """Insert after the first paragraph under a heading (before next ##/###)."""
    j = heading_idx + 1
    while j < len(lines) and not lines[j].strip():
        j += 1

    if j >= len(lines) or _heading_level(lines[j]) > 0:
        return heading_idx + 1

    insert_at = j
    k = j
    while k < len(lines):
        if _heading_level(lines[k]) > 0:
            insert_at = k
            break
        if k > j and lines[k].strip() == "":
            insert_at = k
            break
        k += 1
    else:
        insert_at = len(lines)
    return insert_at


def _section_already_has_path(lines: List[str], heading_idx: int, path: str) -> bool:
    end = len(lines)
    for i in range(heading_idx + 1, len(lines)):
        if _heading_level(lines[i]) > 0:
            end = i
            break
    chunk = "\n".join(lines[heading_idx:end])
    return path in chunk


def inject_cluster_hub_member_links(
    body: str,
    links: Sequence[Dict[str, str]] | Iterable[Tuple[str, str]],
    *,
    link_label: str = "Full net worth profile",
) -> str:
    """
    Ensure each member heading has one markdown internal link.

    links: iterable of {"name": "...", "path": "/slug"} or (name, path) tuples.
    Missing names are skipped (no invented paths).
    """
    if not body:
        return body or ""

    mapping: Dict[str, str] = {}
    for item in links:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            path = normalize_internal_path(str(item.get("path") or ""))
        else:
            name = str(item[0] or "").strip()
            path = normalize_internal_path(str(item[1] or ""))
        if not name or not path:
            continue
        mapping[normalize_person_key(name)] = path

    if not mapping:
        return body

    lines = body.split("\n")
    # Process from bottom to top so insert indices stay valid
    heading_matches: List[Tuple[int, str, str]] = []
    for i, line in enumerate(lines):
        if _heading_level(line) not in (2, 3):
            continue
        text = _heading_text(line)
        key = normalize_person_key(text)
        if key in mapping:
            heading_matches.append((i, text, mapping[key]))
        else:
            # Fuzzy: mapping key contained in heading or vice versa
            for map_key, map_path in mapping.items():
                if map_key in key or key in map_key:
                    heading_matches.append((i, text, map_path))
                    break

    for heading_idx, _text, path in reversed(heading_matches):
        if _section_already_has_path(lines, heading_idx, path):
            continue
        insert_at = _find_insert_index_after_first_paragraph(lines, heading_idx)
        link_line = f"[{link_label}]({path})"
        lines = lines[:insert_at] + ["", link_line, ""] + lines[insert_at:]

    return "\n".join(lines)


def inject_sibling_article_link(
    body: str,
    *,
    label: str,
    path: str,
) -> str:
    """Append a sibling bio/net-worth markdown link near the top if missing."""
    path = normalize_internal_path(path)
    if not body or not path:
        return body or ""
    if path in body:
        return body
    link_line = f"[{label}]({path})"
    lines = body.split("\n")
    # After first paragraph / before first H2
    insert_at = 0
    for i, line in enumerate(lines):
        if line.startswith("## "):
            insert_at = i
            break
        if line.strip():
            insert_at = i + 1
    return "\n".join(lines[:insert_at] + ["", link_line, ""] + lines[insert_at:])
