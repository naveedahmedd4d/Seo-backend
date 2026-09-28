"""Auto-generated thumbnail infographics for Technology & AI articles only.

Each infographic is a 16:9 graphic: an eyebrow, headline and subtitle framing content
built from the article (cards, numbered steps, checklist, comparison, stats, or a themed
cycle/wheel/tree/bar diagram), with a source + brand footer. The render route in the
Next.js app draws it in headless Chromium (the backend has no Node runtime). Every step
is non-fatal: a failure degrades to fewer (or zero) infographics plus a warning.
"""

import html
import logging
import re
from typing import Any, Awaitable, Callable, Optional

import httpx

from app.config import settings
from app.services.blog_image_service import _markdown_heading_level, _markdown_heading_text
from app.services.http_ssl import httpx_verify
from app.services.infographic_storage import public_infographic_url, save_png_base64
from app.services.technology_ai_content import parse_object

logger = logging.getLogger(__name__)

TARGET_INFOGRAPHIC_COUNT = 3
# Ask for more than we need: some proposals fail grounding/layout checks.
MAX_PROPOSALS = 6
RENDER_TIMEOUT_SECONDS = 60.0
# Every infographic is a fixed 16:9 canvas, screenshotted at 2x.
CANVAS_WIDTH = 1200
CANVAS_HEIGHT = 675
EYEBROW = "Technology & AI"

MAX_HEADLINE_CHARS = 64
MAX_SUBTITLE_CHARS = 120
MAX_EYEBROW_CHARS = 40
MAX_TREE_NODES = 10
# Variety across one article's graphics: one checklist at most, no layout used 3 times.
MAX_PER_TEMPLATE = {"checklist": 1, "takeaway": 1}
MAX_SAME_TEMPLATE = 2
# Two graphics whose item labels share this much vocabulary say the same thing.
DUPLICATE_LABEL_OVERLAP = 0.6

# Subtitles that restate the headline or give an instruction instead of information.
_FILLER_SUBTITLE = re.compile(
    r"^(follow these|assess your|explore|discover|learn|understand(ing)?|ensure|different|various|here are|here's"
    r"|this (infographic|graphic|chart)"
    r"|a (look|guide|breakdown|comparison|checklist) (at|to|of)|key (steps|factors|elements|components|features))\b",
    re.I,
)

# Placeholder-style labels the model tends to copy instead of using real article content.
_GENERIC_LABEL = re.compile(r"^(step|item|phase|stage|option|point|part|tip|category)\s*\d+$", re.I)
# Framing sections make weak infographics (a one-line call to action, a recap).
_SKIP_HEADING = re.compile(
    r"^(next steps?|conclusion|introduction|final thoughts|summary|faqs?|frequently asked questions)\b", re.I
)

# A closing section's key line still makes a good takeaway card (never a diagram).
_TAKEAWAY_FRAMING = re.compile(r"^(next steps?|conclusion|final thoughts|summary)\b", re.I)


def _skipped(heading: str, template_id: str = "") -> bool:
    heading = heading.strip()
    if template_id == "takeaway" and _TAKEAWAY_FRAMING.match(heading):
        return False
    return bool(_SKIP_HEADING.match(heading))


# Icons the model may use, by simple name -> Iconify "mdi" icon id. Picking from a fixed
# list (instead of the library's fuzzy icon search) avoids mismatches like "chat" -> a cat.
ICONS: dict[str, str] = {
    "ai": "head-cog-outline", "brain": "brain", "robot": "robot-outline", "chat": "chat-processing-outline",
    "message": "message-text-outline", "code": "code-braces", "database": "database-outline",
    "server": "server", "cloud": "cloud-outline", "security": "shield-check-outline", "lock": "lock-outline",
    "key": "key-outline", "privacy": "eye-off-outline", "fingerprint": "fingerprint", "user": "account-outline",
    "team": "account-group-outline", "customer": "account-heart-outline", "manager": "account-tie-outline",
    "chart": "chart-line", "bar-chart": "chart-bar", "growth": "trending-up", "money": "cash-multiple",
    "dollar": "currency-usd", "time": "clock-outline", "speed": "speedometer", "launch": "rocket-launch-outline",
    "settings": "cog-outline", "tools": "tools", "bug": "bug-outline", "search": "magnify",
    "document": "file-document-outline", "compliance": "file-check-outline", "checklist": "format-list-checks",
    "network": "lan", "api": "api", "integration": "puzzle-outline", "layers": "layers-outline", "chip": "chip",
    "gpu": "expansion-card", "memory": "memory", "mobile": "cellphone", "laptop": "laptop", "globe": "earth",
    "web": "web", "wifi": "wifi", "email": "email-outline", "alert": "bell-outline", "warning": "alert-outline",
    "check": "check-circle-outline", "target": "target", "idea": "lightbulb-outline", "monitor": "eye-outline",
    "image": "image-outline", "vision": "image-search-outline", "video": "video-outline",
    "voice": "microphone-outline", "link": "link-variant", "sync": "sync", "workflow": "sitemap-outline",
    "filter": "filter-outline", "calendar": "calendar-outline", "balance": "scale-balance", "legal": "gavel",
    "sustainability": "leaf", "energy": "lightning-bolt-outline", "partnership": "handshake-outline",
    "learning": "school-outline", "knowledge": "book-open-variant", "storage": "harddisk",
    "backup": "backup-restore", "upload": "cloud-upload-outline", "shopping": "cart-outline",
    "favorite": "heart-outline", "quality": "star-outline", "milestone": "flag-outline", "roadmap": "map-outline",
    "detection": "radar", "version-control": "source-branch", "testing": "test-tube",
    "dashboard": "monitor-dashboard", "translation": "translate", "model": "cube-outline",
    "package": "package-variant-closed", "logistics": "truck-outline", "healthcare": "hospital-box-outline",
    "finance": "bank-outline", "retail": "store-outline", "manufacturing": "factory",
    "review": "clipboard-check-outline", "waiting": "timer-sand", "reuse": "recycle", "tag": "tag-outline",
    "edit": "pencil-outline", "design": "palette-outline", "home": "home-outline",
}
DEFAULT_ICON = "idea"
# Words in an item's label/description -> icon name, used when the model omits an icon or
# names one that isn't in ICONS. Keys are singular; plurals match after stripping "s".
ICON_KEYWORDS: dict[str, str] = {
    "accuracy": "check", "correct": "check", "verify": "check", "validation": "check", "approval": "check",
    "satisfaction": "customer", "experience": "customer", "personalization": "customer",
    "user": "user", "identity": "user", "people": "team", "team": "team", "collaboration": "team",
    "rate": "chart", "metric": "chart", "kpi": "chart", "analytic": "chart", "trend": "growth", "forecast": "chart",
    "revenue": "money", "cost": "money", "price": "dollar", "budget": "money", "roi": "growth", "saving": "money",
    "latency": "speed", "performance": "speed", "fast": "speed", "throughput": "speed",
    "security": "security", "access": "lock", "encryption": "lock", "permission": "key", "auth": "key",
    "privacy": "privacy", "redaction": "privacy", "compliance": "compliance", "audit": "review", "log": "document",
    "regulation": "legal", "policy": "legal", "risk": "warning", "threat": "warning", "incident": "alert",
    "data": "database", "database": "database", "dataset": "database", "storage": "storage", "backup": "backup",
    "cloud": "cloud", "server": "server", "infrastructure": "server", "network": "network", "api": "api",
    "integration": "integration", "plugin": "integration", "model": "model", "training": "learning",
    "fine": "settings", "tuning": "settings", "prompt": "chat", "chat": "chat", "chatbot": "robot",
    "assistant": "robot", "agent": "robot", "automation": "workflow", "workflow": "workflow", "pipeline": "workflow",
    "code": "code", "developer": "code", "software": "code", "bug": "bug", "test": "testing", "testing": "testing",
    "deploy": "launch", "deployment": "launch", "launch": "launch", "rollout": "launch", "release": "milestone",
    "search": "search", "retrieval": "search", "discovery": "search", "document": "document", "knowledge": "knowledge",
    "image": "image", "vision": "vision", "video": "video", "voice": "voice", "speech": "voice", "language": "translation",
    "translation": "translation", "gpu": "gpu", "chip": "chip", "hardware": "chip", "memory": "memory",
    "monitor": "monitor", "monitoring": "monitor", "observability": "dashboard", "dashboard": "dashboard",
    "detection": "detection", "anomaly": "detection", "alert": "alert", "feedback": "message", "support": "chat",
    "goal": "target", "focus": "target", "priority": "target", "strategy": "target", "plan": "roadmap",
    "roadmap": "roadmap", "idea": "idea", "innovation": "idea", "time": "time", "schedule": "calendar",
    "energy": "energy", "sustainability": "sustainability", "quality": "quality", "scale": "growth",
    "mobile": "mobile", "web": "web", "email": "email", "sync": "sync", "version": "version-control",
    "design": "design", "edit": "edit", "learn": "learning", "education": "learning", "health": "healthcare",
    "finance": "finance", "bank": "finance", "retail": "retail", "manufacturing": "manufacturing",
}

# Two families of layout:
# - "html": drawn by our own HTML/CSS templates in the render route (full control over the
#   type scale, spacing and item counts). Used for the common, simple shapes.
# - "antv": drawn by @antv/infographic, themed to the brand. Used only for real diagrams.
# Layouts the library draws badly or that look templated (3D pucks, zig-zag timelines,
# candy/badge cards, its checklist, compare-binary-*, dagre flow, mind map, vertical
# roadmap) are deliberately excluded. `counts` are item counts that lay out cleanly;
# label/desc limits were measured by rendering on the 1200x675 canvas.
TEMPLATES: dict[str, dict[str, Any]] = {
    "cards": {
        "kind": "html",
        "shape": "Icon cards for 2-6 parallel options, use cases, sectors, benefits, or risks.",
        "data_key": "lists", "counts": {2, 3, 4, 5, 6}, "label_chars": 26, "desc_chars": 140,
        # Measured on the 1200x675 canvas: six cards wrap to two rows and leave little room.
        "desc_by_count": {2: 140, 3: 120, 4: 100, 5: 80, 6: 42},
        "needs_desc": True, "icons": True,
        "example": (
            "infographic cards\n"
            "data\n"
            "  lists\n"
            "    - label Faster Onboarding\n"
            "      desc New hires find answers without waiting for a colleague\n"
            "      icon learning\n"
            "    - label Fewer Tickets\n"
            "      desc Routine questions resolve themselves\n"
            "      icon check\n"
            "    - label Better Search\n"
            "      desc Every answer cites the source document\n"
            "      icon search"
        ),
    },
    "steps": {
        "kind": "html",
        "shape": "Numbered steps for an ordered process or a dated sequence (3-6 steps; put a year "
                 "in the label for timelines).",
        "data_key": "sequences", "counts": {3, 4, 5, 6}, "label_chars": 28, "desc_chars": 120,
        "desc_by_count": {3: 120, 4: 110, 5: 90, 6: 80},
        "needs_desc": True, "icons": False,
        "example": (
            "infographic steps\n"
            "data\n"
            "  sequences\n"
            "    - label Scope the Pilot\n"
            "      desc Pick one workflow with a clear metric\n"
            "    - label Prepare Data\n"
            "      desc Clean the documents it will cite\n"
            "    - label Roll Out\n"
            "      desc Expand while watching error rates"
        ),
    },
    "checklist": {
        "kind": "html",
        "shape": "A to-do or readiness checklist of 3-6 actions or yes/no questions. Only when the "
                 "items really are things to do or check; never for categories or examples.",
        "data_key": "lists", "counts": {3, 4, 5, 6}, "label_chars": 60, "desc_chars": 70,
        "needs_desc": False, "icons": False,
        "example": (
            "infographic checklist\n"
            "data\n"
            "  lists\n"
            "    - label Is your data clean and structured?\n"
            "      desc Quality data is the biggest dependency\n"
            "    - label Can your stack host an agent?\n"
            "      desc Cloud, storage and integrations in place\n"
            "    - label Do you know what success looks like?\n"
            "      desc One problem, one measurable outcome"
        ),
    },
    "comparison": {
        "kind": "html",
        "shape": "A side-by-side comparison of exactly 2 options (X vs Y).",
        "data_key": "lists", "counts": {2}, "label_chars": 26, "desc_chars": 140,
        "needs_desc": True, "icons": False,
        "example": (
            "infographic comparison\n"
            "data\n"
            "  lists\n"
            "    - label Rules Engine\n"
            "      desc Deterministic, cheap and easy to audit, but brittle when inputs vary\n"
            "    - label ML Model\n"
            "      desc Adapts to new patterns, but needs training data and monitoring"
        ),
    },
    "stats": {
        "kind": "html",
        "shape": "2-4 key numbers that appear verbatim in the article, each with a short label.",
        "data_key": "values", "counts": {2, 3, 4}, "label_chars": 28, "desc_chars": 70,
        "needs_desc": False, "icons": False,
        "example": (
            "infographic stats\n"
            "data\n"
            "  values\n"
            "    - label <what the number measures>\n"
            "      value <number with its unit, copied from the article, e.g. 42%>\n"
            "      desc <short context from the article>"
        ),
    },
    # Last resort, built only from the article (never offered to the model): one verbatim
    # sentence set large, for prose sections with nothing list-like to draw.
    "takeaway": {
        "kind": "html", "structural_only": True,
        "shape": "One key sentence from the section.",
        "data_key": "lists", "counts": {1}, "label_chars": 170, "desc_chars": 0,
        "needs_desc": False, "icons": False,
        "example": "infographic takeaway\ndata\n  lists\n    - label Most failed pilots skip the data cleaning step.",
    },
    "sequence-circular": {
        "kind": "antv",
        "shape": "A cycle of 4-6 stages that loop back to the start (lifecycles, feedback loops).",
        "data_key": "sequences", "counts": {4, 5, 6}, "label_chars": 22, "desc_chars": 50,
        "needs_desc": True, "icons": True,
        "example": (
            "infographic sequence-circular\n"
            "data\n"
            "  sequences\n"
            "    - label Collect Feedback\n"
            "      desc Log failed answers from users\n"
            "      icon message\n"
            "    - label Label Errors\n"
            "      desc Tag each failure by cause\n"
            "      icon tag\n"
            "    - label Retrain\n"
            "      desc Update the model on new examples\n"
            "      icon model\n"
            "    - label Redeploy\n"
            "      desc Ship behind a feature flag\n"
            "      icon launch"
        ),
    },
    "list-sector": {
        "kind": "antv",
        "shape": "A wheel: one central idea with 4-6 facets around it (types of, uses of, parts of X). "
                 "Needs a short center 'title' and one-word labels.",
        "data_key": "lists", "counts": {4, 5, 6}, "label_chars": 12, "desc_chars": 18,
        "needs_desc": True, "icons": True, "center_title": True,
        "example": (
            "infographic list-sector\n"
            "data\n"
            "  title Zero Trust\n"
            "  lists\n"
            "    - label Identity\n"
            "      desc Verify every user\n"
            "      icon user\n"
            "    - label Devices\n"
            "      desc Check each device\n"
            "      icon laptop\n"
            "    - label Network\n"
            "      desc Segment access\n"
            "      icon network\n"
            "    - label Data\n"
            "      desc Encrypt at rest\n"
            "      icon lock"
        ),
    },
    "chart-bar-plain-text": {
        "kind": "antv",
        "shape": "A bar chart of 3-6 numbers that appear verbatim in the article.",
        "data_key": "values", "counts": {3, 4, 5, 6}, "label_chars": 22, "desc_chars": 0,
        "needs_desc": False, "icons": False,
        "example": (
            "infographic chart-bar-plain-text\n"
            "data\n"
            "  values\n"
            "    - label <category from the article>\n"
            "      value <number copied from the article>\n"
            "    - label <category from the article>\n"
            "      value <number copied from the article>"
        ),
    },
    "hierarchy-tree": {
        "kind": "antv",
        "shape": "A structural breakdown: one root with 2-4 branches (optionally one more level).",
        "data_key": "root", "counts": None, "label_chars": 22, "desc_chars": 0,
        "needs_desc": False, "icons": False,
        "example": (
            "infographic hierarchy-tree\n"
            "data\n"
            "  root\n"
            "    label RAG Pipeline\n"
            "    children\n"
            "      - label Retrieval\n"
            "        children\n"
            "          - label Chunking\n"
            "          - label Vector Search\n"
            "      - label Generation\n"
            "        children\n"
            "          - label Prompt Assembly\n"
            "          - label Answer Synthesis"
        ),
    },
}

# When an AntV diagram fails its render checks, its items are re-drawn as HTML cards.
FALLBACK_TEMPLATE = "cards"
# Prefix for issues fixable by switching to another layout (item count, text length,
# missing descriptions), as opposed to content problems that must be rejected.
_LAYOUT_ISSUE = "layout: "

_NUMERIC_TOKEN = re.compile(r"\d[\d,.]*\s*(?:%|percent|billion|million)|\$\s*\d[\d,.]*", re.I)
_CHART_VALUE = re.compile(r"^\s*value\s+([\d,.]+)", re.I | re.M)
_BARE_DIGITS = re.compile(r"\d[\d,.]*")
_TEMPLATE_LINE = re.compile(r"^\s*infographic\s+(\S+)\s*$")
_TITLE_LINE = re.compile(r"^\s*title\s+(.+?)\s*$", re.M)
_LABEL_LINE = re.compile(r"^(\s*)(?:- )?label\s+(.+?)\s*$")
_DESC_LINE = re.compile(r"^\s*desc\s+(.+?)\s*$", re.M)
_ICON_LINE = re.compile(r"^(\s*)icon\s+(.+?)\s*$")
_VALUE_LINE = re.compile(r"^\s*value\s+(.+?)\s*$", re.M)
_PLAIN_NUMBER = re.compile(r"^\d[\d,]*(?:\.\d+)?$")
# "value 420 ms" -> number 420, unit "ms". The chart can't parse units, and the bars collapse.
_VALUE_WITH_UNIT = re.compile(r"^(\s*value\s+)(\d[\d,]*(?:\.\d+)?)\s*([A-Za-z%$€£/][^\n]*?)\s*$", re.M)


def _template_guide() -> str:
    lines = []
    for tid, info in TEMPLATES.items():
        if info.get("structural_only"):
            continue
        counts = "root + 2-4 branches" if info["counts"] is None else ", ".join(map(str, sorted(info["counts"])))
        limits = f"labels <= {info['label_chars']} chars"
        if info.get("desc_by_count"):
            by_count = info["desc_by_count"]
            limits += (f", desc <= {'/'.join(str(by_count[n]) for n in sorted(by_count))} chars for "
                       f"{'/'.join(str(n) for n in sorted(by_count))} items")
        elif info["desc_chars"]:
            limits += f", desc <= {info['desc_chars']} chars"
        extra = "; every item needs an icon" if info["icons"] else "; no icons"
        if tid == "stats":
            extra += "; every item needs a value"
        example = info["example"].replace("\n", "\n  ")
        lines.append(f'- "{tid}": {info["shape"]} Items: {counts}; {limits}{extra}.\n  Example:\n  {example}')
    return "\n".join(lines)


def article_headings(body: str) -> list[str]:
    """The ## / ### headings an infographic may be tied to (framing sections excluded)."""
    headings = []
    for line in body.split("\n"):
        if _markdown_heading_level(line) in (2, 3):
            text = _markdown_heading_text(line).strip()
            if text and not _SKIP_HEADING.match(text):
                headings.append(text)
    return headings


def extraction_prompt(topic: str, body: str, chosen: list[dict[str, str]] = (), tried: set[str] = frozenset()) -> str:
    """The proposal prompt. With `chosen`/`tried`, it's a top-up round: only the headings not
    yet used or tried are offered, and the graphics already chosen are named so the model
    doesn't repeat their items or layouts."""
    skip = {c["heading"].lower() for c in chosen} | {h.lower() for h in tried}
    heading_list = "\n".join(f"- {h}" for h in article_headings(body) if h.lower() not in skip)
    if chosen or tried:
        need = TARGET_INFOGRAPHIC_COUNT - len(chosen)
        already = "; ".join(f'"{c["heading"]}" as {c["template_id"]}' for c in chosen) or "none"
        opening = f"""This article still needs {need} more infographic(s). Already chosen: {already}.
Propose {need + 2} to {MAX_PROPOSALS} NEW infographics, ranked best first, for sections not listed above.
Earlier proposals for other sections broke the rules below, so follow them exactly: keep every label
and description within its layout's limits, and use a different layout from the chosen ones where
the content allows. Each is a 16:9 editorial graphic: eyebrow, headline, subtitle, and content from
one section."""
    else:
        opening = f"""Propose 4 to {MAX_PROPOSALS} infographics for this FINAL Technology & AI article, ranked
best first ({TARGET_INFOGRAPHIC_COUNT} will be used; extras replace any that fail checks, so propose
at least 4 whenever the article has 4 sections with usable content). Each is a
16:9 editorial graphic: eyebrow, headline, subtitle, and content from one section."""
    return f"""{opening}

Each must be tied to a different heading, copied EXACTLY (same words) from this list:
{heading_list}
Visualize only content that exists under that heading. Never invent a fact, statistic, label, or
number. Do not calculate, estimate, round, or unit-convert any number; copy figures verbatim.

Choose the layout that matches what the content IS. Indentation is significant; match the examples
exactly. The examples show the format only: do NOT reuse their wording.

{_template_guide()}

Icons: for layouts that use icons, give every item an "icon" line using ONE of these names:
{", ".join(sorted(ICONS))}

Writing rules (graphics breaking these are discarded or stripped):
- "eyebrow": topic and angle in 2-4 words, e.g. "AI agents · Implementation" (max {MAX_EYEBROW_CHARS}
  chars). Never the site category ("Technology & AI").
- "headline": states the point, not the topic. "Five steps to put an AI agent into production", not
  "Steps for AI Agent Implementation". Sentence case, at most {MAX_HEADLINE_CHARS} characters.
- "subtitle": ONE fact or takeaway from the article that the headline doesn't already say (max
  {MAX_SUBTITLE_CHARS} chars). Never "Follow these steps...", "Explore...", "Discover...", "Learn...",
  "Key factors..." or a restatement of the headline.
- Labels use the article's own terms. Never generic labels like "Step 1" or "Option A".
- Keep descriptions for every item that has one in the article.
- Numbers first: if a section gives figures for 2-4 things, use "stats" (3-6 figures: the bar chart),
  not a comparison or cards. A stat "value" must be the figure itself ("420 ms", "42%"), never words.
- "comparison" is for exactly 2 options. Three or more options are "cards".
- Use a checklist only when the article itself tells the reader to do or check those things, and at
  most once. Never rewrite plain statements or factors as questions to make a checklist.
- Don't propose two graphics with the same items (e.g. a process and a checklist of the same steps).
- Prefer different layouts across the graphics.
- Tie each graphic to the most specific heading (the "###" subsection when the content lives there).
- Do not put a "title" line in the syntax, except the short center title the wheel needs.
- Chart values must be plain numbers ("value 420"); put the unit in the headline.

Return ONLY JSON: {{"infographics": [{{"heading": "<verbatim heading, no # marks>",
"template_id": "<layout id>", "eyebrow": "...", "headline": "...", "subtitle": "...",
"syntax": "<complete syntax>", "alt_text": "<specific description, under 120 chars>"}}]}}
If only a few sections genuinely support a good graphic, return fewer rather than inventing.
Topic: {topic}
Article:
{body}
"""


def _numbers_are_supported(text: str, body: str) -> bool:
    body_numbers = set(_NUMERIC_TOKEN.findall(body))
    for token in _NUMERIC_TOKEN.findall(text):
        if token not in body_numbers:
            return False
    # Chart "value N" fields are bare numbers (no %/currency/scale suffix) — verify the
    # digit sequence itself appears somewhere in the body (e.g. body has "42%", syntax has
    # "value 42"), so a fabricated chart figure can't slip past the check above.
    body_digit_runs = {m.strip(",.") for m in _BARE_DIGITS.findall(body)}
    for value in _CHART_VALUE.findall(text):
        if value.strip(",.") not in body_digit_runs:
            return False
    return True


def _candidate_numbers_are_supported(candidate: dict[str, str], body: str) -> bool:
    text = "\n".join([candidate["syntax"], candidate.get("headline", ""), candidate.get("subtitle", "")])
    return _numbers_are_supported(text, body)


def _heading_exists(heading: str, body: str) -> bool:
    heading_l = (heading or "").strip().lower()
    if not heading_l:
        return False
    for line in body.split("\n"):
        if _markdown_heading_level(line) in (2, 3) and heading_l in _markdown_heading_text(line).lower():
            return True
    return False


_STOPWORDS = {"a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "with", "your", "how", "why", "what", "key"}


def resolve_heading(heading: str, body: str) -> Optional[str]:
    """Map the model's heading to a real one, tolerating light paraphrase.

    Exact/substring matches win. Otherwise pick the real heading sharing the most content
    words, requiring at least half of the shorter heading's words to match, so a
    paraphrase like "Refactoring Legacy Code Steps" still lands on "Refactoring and Code
    Cleanup" but an unrelated heading does not.
    """
    if _heading_exists(heading, body):
        return heading
    words = lambda s: {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", s.lower())} - _STOPWORDS
    wanted = words(heading)
    best, best_score = None, 0.0
    for real in article_headings(body):
        have = words(real)
        if not wanted or not have:
            continue
        shared = len(wanted & have)
        shortest = min(len(wanted), len(have))
        score = shared / shortest
        # Two shared words (one for a one-word heading), so "Data Security" can't land on
        # "Data Pipeline" through "data" alone.
        if shared >= min(2, shortest) and score >= 0.5 and score > best_score:
            best, best_score = real, score
    return best


def _item_count(labels: list[tuple[int, str]]) -> int:
    item_indent = labels[0][0]
    return sum(1 for indent, _ in labels if indent == item_indent)


def desc_limit(template_id: str, count: int) -> int:
    """Description length that fits: fewer items leave each one more room."""
    info = TEMPLATES[template_id]
    return info.get("desc_by_count", {}).get(count, info["desc_chars"])


def syntax_issues(template_id: str, syntax: str) -> list[str]:
    """Deterministic layout/content checks; an empty list means the syntax is usable."""
    info = TEMPLATES[template_id]
    lines = syntax.split("\n")
    first = _TEMPLATE_LINE.match(lines[0]) if lines else None
    if not first or first.group(1) != template_id:
        return ["syntax does not start with its template id"]
    issues: list[str] = []

    if info.get("center_title"):
        title = _TITLE_LINE.search(syntax)
        if not title:
            issues.append("missing center title")
        elif len(title.group(1)) > 18 or any(len(w) > 10 for w in title.group(1).split()):
            # The center circle fits ~10 characters per line; longer words break mid-word.
            issues.append(f"{_LAYOUT_ISSUE}center title too long")

    labels = [(len(m.group(1)), m.group(2)) for line in lines if (m := _LABEL_LINE.match(line))]
    if not labels:
        return issues + ["no labelled items"]
    for _, label in labels:
        if _GENERIC_LABEL.match(label):
            issues.append(f"generic label '{label}'")
        elif len(label) > info["label_chars"]:
            issues.append(f"{_LAYOUT_ISSUE}label too long '{label}'")
    descs = _DESC_LINE.findall(syntax)
    if info["desc_chars"] and any(len(d) > desc_limit(template_id, _item_count(labels)) for d in descs):
        issues.append(f"{_LAYOUT_ISSUE}description too long")

    if template_id == "chart-bar-plain-text":
        values = _VALUE_LINE.findall(syntax)
        if len(values) != len(labels) or not all(_PLAIN_NUMBER.match(v) for v in values):
            issues.append("chart values must all be numbers")
    if template_id == "stats":
        values = _VALUE_LINE.findall(syntax)
        if len(values) != len(labels) or not all(re.search(r"\d", v) and len(v) <= 12 for v in values):
            issues.append("every stat needs a short value containing a number")

    if info["counts"] is None:
        if len(labels) > MAX_TREE_NODES:
            issues.append("too many nodes")
        elif len(labels) < 3:
            issues.append("too few nodes")
    else:
        count = _item_count(labels)
        if count not in info["counts"]:
            issues.append(f"{_LAYOUT_ISSUE}{count} items don't fit")
        # Card layouts with label-only items render as mostly empty boxes.
        if info.get("needs_desc") and len(descs) < count:
            issues.append(f"{_LAYOUT_ISSUE}items missing descriptions")
    return issues


def _strip_title(syntax: str) -> str:
    return "\n".join(line for line in syntax.split("\n") if not _TITLE_LINE.match(line))


def normalize_icons(template_id: str, syntax: str) -> str:
    """Map icon names to exact Iconify icons; give icon layouts an icon for every item.

    A missing or unknown icon is guessed from the item's own words. Layouts without icon
    slots get their icon lines removed, so the library never falls back to its fuzzy
    third-party icon search.
    """
    uses_icons = TEMPLATES[template_id]["icons"]
    lines = syntax.split("\n")
    starts = [i for i, line in enumerate(lines) if line.lstrip().startswith("- ") and _LABEL_LINE.match(line)]
    item_indent = len(_LABEL_LINE.match(lines[starts[0]]).group(1)) if starts else None
    item_starts = [i for i in starts if len(_LABEL_LINE.match(lines[i]).group(1)) == item_indent]

    out: list[str] = []
    used: set[str] = set()
    for n, start in enumerate([0] + item_starts):
        end = item_starts[n] if n < len(item_starts) else len(lines)
        block = lines[start:end]
        if n == 0:  # header lines before the first item
            out.extend(l for l in block if not _ICON_LINE.match(l))
            continue
        if not uses_icons:
            out.extend(l for l in block if not _ICON_LINE.match(l))
            continue
        label = " ".join(m.group(2) for l in block if (m := _LABEL_LINE.match(l)))
        desc = " ".join(m.group(1) for l in block if (m := _DESC_LINE.match(l)))
        named = next((m.group(2) for l in block if (m := _ICON_LINE.match(l))), "")
        kept = [l for l in block if not _ICON_LINE.match(l)]
        out.extend(kept)
        ref = _icon_ref(named, label, desc)
        if ref in used:
            # Repeated icons make the items look identical; pick another the words support.
            alternative = _guess_icon(label, desc, exclude={r.rsplit("/", 1)[-1][:-4] for r in used})
            if alternative != DEFAULT_ICON:
                ref = _icon_ref(alternative)
        used.add(ref)
        out.append(f"{' ' * (item_indent + 2)}icon {ref}")
    return "\n".join(out)


def _guess_icon(label: str, desc: str = "", exclude: set[str] = frozenset()) -> str:
    # The head noun of a label usually comes last ("User Satisfaction", "Deflection Rate"),
    # so scan the label from the end before falling back to the description.
    words = list(reversed(re.findall(r"[a-z]+", label.lower()))) + re.findall(r"[a-z]+", desc.lower())
    for word in words:
        for candidate in (word, word.rstrip("s"), word[:-2] if word.endswith("es") else word):
            name = ICON_KEYWORDS.get(candidate) or (candidate if candidate in ICONS else None)
            if name and ICONS[name] not in exclude:
                return name
    return DEFAULT_ICON


def _icon_ref(name: str, label: str = "", desc: str = "") -> str:
    key = name.strip().strip(".,;:!").lower()
    if key.startswith("ref:") or key not in ICONS:
        # Never pass model-chosen URLs through; fall back to the item's own words.
        key = _guess_icon(label, desc)
    return f"ref:remote:https://api.iconify.design/mdi/{ICONS[key]}.svg"


def normalize_chart_units(syntax: str, headline: str) -> tuple[str, str]:
    """Move units out of chart values ("value 420 ms") into the headline ("... (ms)")."""
    units: set[str] = set()

    def strip_unit(match: re.Match) -> str:
        units.add(match.group(3).strip())
        return match.group(1) + match.group(2)

    out = _VALUE_WITH_UNIT.sub(strip_unit, syntax)
    if len(units) == 1:
        unit = next(iter(units))
        if unit.lower() not in headline.lower() and len(headline) + len(unit) + 3 <= MAX_HEADLINE_CHARS:
            headline = f"{headline} ({unit})"
    return out, headline


def convert_syntax(syntax: str, from_id: str, to_id: str, center_title: str = "") -> str:
    """Re-express list/sequence syntax in another list/sequence template."""
    source_key = TEMPLATES[from_id]["data_key"]
    target_key = TEMPLATES[to_id]["data_key"]
    drop_desc = not TEMPLATES[to_id]["desc_chars"]
    drop_icon = not TEMPLATES[to_id]["icons"]
    wants_center = bool(TEMPLATES[to_id].get("center_title"))
    has_title = bool(_TITLE_LINE.search(syntax))
    out = [f"infographic {to_id}"]
    for line in syntax.split("\n")[1:]:
        if drop_desc and _DESC_LINE.match(line):
            continue
        if drop_icon and _ICON_LINE.match(line):
            continue
        if _TITLE_LINE.match(line) and not wants_center:
            continue
        if line.strip() == source_key:
            if wants_center and not has_title and center_title:
                out.append(line.replace(source_key, f"title {center_title}"))
            line = line.replace(source_key, target_key)
        out.append(line)
    return "\n".join(out)


def fallback_syntax(template_id: str, syntax: str) -> Optional[str]:
    """Re-express an AntV list/sequence diagram as HTML cards, for when the diagram fails
    its render checks. HTML templates have their own compact-density retry instead."""
    info = TEMPLATES[template_id]
    if info["kind"] != "antv" or info["data_key"] not in ("lists", "sequences"):
        return None
    converted = normalize_icons(FALLBACK_TEMPLATE, convert_syntax(syntax, template_id, FALLBACK_TEMPLATE))
    return converted if not syntax_issues(FALLBACK_TEMPLATE, converted) else None


# When the model picks a layout that can't show its items cleanly (wrong count, text too
# long for the layout, missing descriptions), try these alternatives in order.
_RETEMPLATE_ORDER = {
    # Keep the order visible: numbered steps first.
    "sequences": ["steps", "comparison", "checklist"],
    # Never turn unordered items into a sequence (that implies an order the article doesn't
    # state) or into a checklist (checkboxes on "Finance" or "Healthcare" read as nonsense).
    "lists": ["cards", "list-sector", "comparison"],
    "values": ["stats", "chart-bar-plain-text"],
}


# "420 ms median response time" -> value "420 ms", desc "median response time".
_LEADING_FIGURE = re.compile(
    r"^(\s*)desc\s+(\$?\d[\d,.]*(?:\s*(?:%|x|ms|s|gb|tb|mb|k|m|bn|billion|million|percent)(?![a-z]))?)(?![\w])[\s,:;-]*(.*)$", re.I
)


def as_stats(template_id: str, syntax: str) -> Optional[str]:
    """Items whose every description opens with a figure are really stats; redraw them so.

    Models often put figures for 2-4 options into cards or a (3-way) comparison, burying
    the numbers in body text. Returns None unless every item starts with a figure.
    """
    if TEMPLATES[template_id]["data_key"] not in ("lists", "sequences"):
        return None
    out = ["infographic stats"]
    figures = 0
    for line in syntax.split("\n")[1:]:
        if _ICON_LINE.match(line):
            continue
        if line.strip() in ("lists", "sequences"):
            line = line.replace(line.strip(), "values")
        m = _LEADING_FIGURE.match(line)
        if m:
            figures += 1
            out.append(f"{m.group(1)}value {m.group(2).strip()}")
            rest = m.group(3).strip()
            if rest:
                out.append(f"{m.group(1)}desc {rest[0].upper()}{rest[1:]}")
            continue
        out.append(line)
    converted = "\n".join(out)
    if figures != _count_items(syntax) or syntax_issues("stats", converted):
        return None
    return converted


def retemplate(template_id: str, syntax: str, heading: str = "") -> Optional[tuple[str, str]]:
    """Return (template_id, syntax) using a layout that fits the content, if one exists.

    A wheel needs a short center label; the section heading is used when it fits.
    """
    source_key = TEMPLATES[template_id]["data_key"]
    for alternative in _RETEMPLATE_ORDER.get(source_key, []):
        if alternative == template_id:
            continue
        converted = convert_syntax(syntax, template_id, alternative, center_title=heading)
        if not syntax_issues(alternative, converted):
            return alternative, converted
    return None


def parse_candidates(raw: str) -> list[dict[str, str]]:
    """Parse and structurally validate the extraction LLM response. Never raises past caller."""
    data = parse_object(raw)
    items = data.get("infographics")
    if not isinstance(items, list):
        return []
    out: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        fields = {k: item.get(k) for k in ("heading", "template_id", "headline", "subtitle", "syntax", "alt_text")}
        if not all(isinstance(v, str) and v.strip() for v in fields.values()):
            continue
        # Models sometimes echo the syntax's first line ("infographic steps") as the id.
        template_id = re.sub(r"^infographic\s+", "", fields["template_id"].strip())
        if template_id not in TEMPLATES or TEMPLATES[template_id].get("structural_only"):
            continue
        eyebrow = item.get("eyebrow")
        out.append({
            "heading": fields["heading"].strip(),
            "template_id": template_id,
            "headline": fields["headline"].strip(),
            "subtitle": fields["subtitle"].strip(),
            "eyebrow": eyebrow.strip() if isinstance(eyebrow, str) else "",
            "syntax": fields["syntax"].strip(),
            "alt_text": fields["alt_text"].strip()[:120],
        })
    return out


def _splice_items(original: str, fixed: str) -> Optional[str]:
    """Accept a shortened syntax whether or not the model kept the header lines.

    Models often return only the "- label ..." item lines; those are re-indented and put
    back under the original header so the fix isn't wasted.
    """
    fixed = fixed.strip("\n")
    if fixed.lstrip().startswith("infographic "):
        return fixed.strip()
    orig_lines = original.split("\n")
    first_item = next((i for i, l in enumerate(orig_lines) if l.lstrip().startswith("- ") and _LABEL_LINE.match(l)), None)
    fixed_lines = [l for l in fixed.split("\n") if l.strip()]
    fixed_first = next((l for l in fixed_lines if l.lstrip().startswith("- ") and _LABEL_LINE.match(l)), None)
    if first_item is None or fixed_first is None:
        return None
    shift = (len(orig_lines[first_item]) - len(orig_lines[first_item].lstrip())) - (len(fixed_first) - len(fixed_first.lstrip()))
    items = [(" " * shift + l) if shift >= 0 else l[min(-shift, len(l) - len(l.lstrip())):] for l in fixed_lines]
    return "\n".join(orig_lines[:first_item] + items)


def _tidy(syntax: str) -> str:
    """Drop trailing full stops from descriptions; infographic captions read cleaner without."""
    return re.sub(r"^(\s*desc\s+.+?)\.\s*$", r"\1", syntax, flags=re.M)


def _count_items(syntax: str) -> int:
    labels = [(len(m.group(1)), m.group(2)) for line in syntax.split("\n") if (m := _LABEL_LINE.match(line))]
    return _item_count(labels) if labels else 0


def _shorten_target(candidate: dict[str, str]) -> tuple[str, list[str]]:
    """Which layout's limits the text must fit, and what's too long for it.

    Usually the chosen layout. But when the item count forces a switch (a 3-item cycle
    becomes 3D steps), shorten for the layout it will switch to, so it isn't downgraded to
    a plain checklist over a couple of characters.
    """
    template_id = candidate["template_id"]
    syntax = _tidy(candidate["syntax"])
    if not TEMPLATES[template_id].get("center_title"):
        syntax = _strip_title(syntax)
    issues = syntax_issues(template_id, syntax)
    if any("don't fit" in i for i in issues):
        for alternative in _RETEMPLATE_ORDER.get(TEMPLATES[template_id]["data_key"], []):
            if alternative in (template_id, "comparison", "checklist"):
                continue
            converted = convert_syntax(syntax, template_id, alternative, center_title=candidate["heading"])
            alt_issues = syntax_issues(alternative, converted)
            if not any("don't fit" in i for i in alt_issues):
                return alternative, [i for i in alt_issues if "too long" in i]
    return template_id, [i for i in issues if "too long" in i]


def _too_long(candidate: dict[str, str]) -> list[str]:
    return _shorten_target(candidate)[1]


async def shorten_overlong(
    candidates: list[dict[str, str]],
    generate: Callable[..., Awaitable[str]],
    delay: Callable[[], Awaitable[None]],
) -> list[dict[str, str]]:
    """Ask the model once to condense labels/descriptions that don't fit their layout.

    Without this, an otherwise good wheel or card grid falls back to a plain checklist just
    because a label is a few characters too long. Never raises; on any failure the
    candidates come back unchanged and validation handles them as before.
    """
    targets = {i: target for i, c in enumerate(candidates) if (target := _shorten_target(c))[1]}
    if not targets:
        return candidates
    blocks = []
    for i, (target_id, issues) in targets.items():
        c = candidates[i]
        info = TEMPLATES[target_id]
        limits = f"labels <= {info['label_chars']} characters"
        if info["desc_chars"]:
            limits += f", descriptions <= {desc_limit(target_id, _count_items(c['syntax']))} characters"
        if info.get("center_title"):
            limits += ", center title <= 18 characters"
        blocks.append(f"[{i}] template {c['template_id']} ({limits}); problems: {'; '.join(issues)}\n{c['syntax']}")
    try:
        await delay()
        raw = await generate(
            "Shorten the text in these infographic syntax blocks so every label, description, and "
            "center title fits its limit. Keep the meaning and the article's own terms, keep the same "
            "items, order, icons, numbers, and indentation. Never add facts. Return ONLY JSON: "
            '{"fixes": [{"index": <number>, "syntax": "<complete corrected syntax>"}]}\n\n'
            + "\n\n".join(blocks),
            max_tokens=3000, temperature=0.2,
        )
        fixes = parse_object(raw).get("fixes")
        if not isinstance(fixes, list):
            return candidates
        out = list(candidates)
        for fix in fixes:
            if not isinstance(fix, dict) or not isinstance(fix.get("syntax"), str):
                continue
            try:
                index = int(fix.get("index"))
            except (TypeError, ValueError):
                continue
            original = candidates[index] if index in targets else None
            if not original:
                continue
            syntax = _splice_items(original["syntax"], fix["syntax"])
            # A fix must only shorten text: same template, same number of items.
            if (syntax and syntax.startswith(f"infographic {original['template_id']}")
                    and _count_items(syntax) == _count_items(original["syntax"])):
                out[index] = {**original, "syntax": syntax}
        return out
    except Exception as e:
        logger.info("Infographic text shortening was unavailable: %s", e)
        return candidates


def _words(text: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", text.lower())} - _STOPWORDS


def _clean_frame_text(candidate: dict[str, str]) -> dict[str, str]:
    """Drop a subtitle that's filler or restates the headline; tidy the eyebrow."""
    subtitle = candidate.get("subtitle", "").strip()
    headline_words = _words(candidate.get("headline", ""))
    subtitle_words = _words(subtitle)
    restates = bool(subtitle_words) and len(subtitle_words & headline_words) / len(subtitle_words) >= 0.7
    if _FILLER_SUBTITLE.match(subtitle) or restates:
        subtitle = ""
    eyebrow = candidate.get("eyebrow", "").strip()
    if len(eyebrow) > MAX_EYEBROW_CHARS or eyebrow.lower() in ("technology & ai", "technology and ai"):
        eyebrow = ""
    return {"subtitle": subtitle, "eyebrow": eyebrow}


def _label_words(candidate: dict[str, str]) -> set[str]:
    labels = " ".join(m.group(2) for line in candidate["syntax"].split("\n") if (m := _LABEL_LINE.match(line)))
    return _words(labels)


def _duplicates_existing(candidate: dict[str, str], survivors: list[dict[str, str]]) -> bool:
    mine = _label_words(candidate)
    for other in survivors:
        theirs = _label_words(other)
        if mine and theirs and len(mine & theirs) / min(len(mine), len(theirs)) >= DUPLICATE_LABEL_OVERLAP:
            return True
    return False


def _layout_allowed(template_id: str, survivors: list[dict[str, str]]) -> bool:
    used = sum(1 for s in survivors if s["template_id"] == template_id)
    return used < MAX_PER_TEMPLATE.get(template_id, MAX_SAME_TEMPLATE)


def validate_candidates(
    candidates: list[dict[str, str]],
    body: str,
    existing: list[dict[str, str]] = (),
    limit: int = TARGET_INFOGRAPHIC_COUNT,
) -> tuple[list[dict[str, str]], list[str]]:
    """Drop any candidate that isn't grounded in the article or won't lay out cleanly.

    `existing` are graphics already chosen in an earlier round: new ones must not reuse
    their headings, repeat their items, or overuse their layouts. Returns only new ones.
    """
    chosen: list[dict[str, str]] = list(existing)
    survivors: list[dict[str, str]] = []
    warnings: list[str] = []
    seen_headings: set[str] = {c["heading"].lower() for c in existing}
    for candidate in candidates:
        heading = candidate["heading"]
        if heading.lower() in seen_headings or _skipped(heading, candidate["template_id"]):
            continue
        if len(candidate["headline"]) > MAX_HEADLINE_CHARS or len(candidate["subtitle"]) > MAX_SUBTITLE_CHARS:
            logger.info("Infographic for '%s' rejected: headline or subtitle too long", heading)
            continue
        candidate = {**candidate, "syntax": _tidy(candidate["syntax"]), **_clean_frame_text(candidate)}
        if not TEMPLATES[candidate["template_id"]].get("center_title"):
            candidate = {**candidate, "syntax": _strip_title(candidate["syntax"])}
        if candidate["template_id"] == "chart-bar-plain-text":
            syntax, headline = normalize_chart_units(candidate["syntax"], candidate["headline"])
            candidate = {**candidate, "syntax": syntax, "headline": headline}
        real_heading = resolve_heading(heading, body)
        if not real_heading:
            warnings.append(f"Infographic for '{heading}' referenced a heading not found in the article and was skipped.")
            continue
        if real_heading.lower() in seen_headings or _skipped(real_heading, candidate["template_id"]):
            continue
        heading = real_heading
        candidate = {**candidate, "heading": heading}
        if not _candidate_numbers_are_supported(candidate, body):
            warnings.append(f"Infographic for '{heading}' introduced a number not present in the article and was skipped.")
            continue
        stats = as_stats(candidate["template_id"], candidate["syntax"])
        if stats:
            candidate = {**candidate, "template_id": "stats", "syntax": stats}
        issues = syntax_issues(candidate["template_id"], candidate["syntax"])
        if issues and all(issue.startswith(_LAYOUT_ISSUE) for issue in issues):
            swapped = retemplate(candidate["template_id"], candidate["syntax"], heading)
            if swapped:
                candidate = {**candidate, "template_id": swapped[0], "syntax": swapped[1]}
                issues = []
        if issues:
            logger.info("Infographic for '%s' rejected: %s", heading, "; ".join(issues))
            continue
        candidate = {**candidate, "syntax": normalize_icons(candidate["template_id"], candidate["syntax"])}
        if _duplicates_existing(candidate, chosen):
            logger.info("Infographic for '%s' skipped: repeats another graphic's items", heading)
            continue
        if not _layout_allowed(candidate["template_id"], chosen):
            logger.info("Infographic for '%s' skipped: layout %s already used enough", heading, candidate["template_id"])
            continue
        seen_headings.add(heading.lower())
        survivors.append(candidate)
        chosen.append(candidate)
        if len(survivors) >= limit:
            break
    return survivors, warnings


# --- Deterministic fallback: graphics built straight from the article's own structure ---

_LIST_ITEM = re.compile(r"^\s*(?:([-*+])|(\d+)[.)])\s+(.+?)\s*$")
# "**Label**: desc", "**Label.** desc", "**Label** - desc", "Label: desc"
_BOLD_LEAD = re.compile(r"^\*\*(.+?)\*\*\s*[:.–—-]?\s*(.*)$")
_PLAIN_LEAD = re.compile(r"^([^:]{2,40}):\s+(.+)$")
_CLAUSE_BREAK = re.compile(
    r"(?<=[A-Za-z0-9)])(?:[.;:!?]\s|,\s(?=(?:such as|including|for example|e\.g\.|which|while|so that)\b)|\s[–—-]\s)"
)
# A label-only checklist needs items that are things to do or check, not topics.
_ACTION_VERBS = {
    "identify", "define", "set", "review", "check", "confirm", "measure", "estimate", "plan", "test", "audit",
    "assess", "document", "train", "map", "choose", "build", "monitor", "update", "ensure", "verify", "evaluate",
    "prioritize", "prioritise", "establish", "create", "run", "track", "use", "avoid", "secure", "limit", "apply",
    "log", "collect", "prepare", "deploy", "schedule", "benchmark", "engage", "involve", "communicate", "simplify",
    "break", "remove", "migrate", "refactor", "automate", "integrate", "adopt", "start", "keep", "write", "restrict",
    "redact", "encrypt", "back", "align", "analyze", "analyse", "select", "compare", "pilot", "roll", "scale",
    "expand", "add", "fix", "clean", "label", "retrain", "validate", "design", "implement", "enable", "require",
}


def _is_action(label: str) -> bool:
    words = label.split()
    return label.endswith("?") or bool(words and words[0].lower() in _ACTION_VERBS)


def _plain(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # links
    text = re.sub(r"[*_`]+", "", text)
    return re.sub(r"\s+", " ", text).strip().rstrip(".")


def _split_item(text: str) -> tuple[str, str]:
    m = _BOLD_LEAD.match(text.strip()) or _PLAIN_LEAD.match(_plain(text))
    if m:
        return _plain(m.group(1)).rstrip(":"), _plain(m.group(2))
    return _plain(text), ""


def _short_desc(text: str, limit: int) -> str:
    """The article's own text cut at a natural boundary so it fits, or "" if it can't be.

    Only cuts where the first part still reads as a complete phrase (sentence end, "; ",
    ", such as", a dash): "Look for indicators of poor design, such as duplicated code" ->
    "Look for indicators of poor design". Never cuts at a plain comma ("large, monolithic").
    """
    text = _plain(text)
    if len(text) <= limit:
        return text
    best = ""
    for m in _CLAUSE_BREAK.finditer(text):
        head = text[:m.start()].strip()
        if len(head) > limit:
            break
        if len(head.split()) >= 3:
            best = head
    return best


def _sections(body: str) -> list[tuple[int, str, list[str]]]:
    """(level, heading, own lines up to the next heading of any level)."""
    out: list[tuple[int, str, list[str]]] = []
    for line in body.split("\n"):
        level = _markdown_heading_level(line)
        if level:
            out.append((level, _markdown_heading_text(line).strip(), []))
        elif out:
            out[-1][2].append(line)
    return out


def _list_blocks(lines: list[str]) -> list[tuple[bool, list[str]]]:
    """Runs of 3-6 list items (blank lines between items allowed): (numbered, item texts)."""
    blocks: list[tuple[bool, list[str]]] = []
    current: list[str] = []
    numbered = False

    def flush() -> None:
        if 3 <= len(current) <= 6:
            blocks.append((numbered, list(current)))
        current.clear()

    for line in lines:
        m = _LIST_ITEM.match(line)
        if m:
            if current and bool(m.group(2)) != numbered:
                flush()
            numbered = bool(m.group(2))
            current.append(m.group(3))
        elif line.strip():  # prose ends the list; blank lines don't
            flush()
    flush()
    return blocks


def _structural(heading: str, template_id: str, items: list[tuple[str, str]]) -> dict[str, str]:
    key = TEMPLATES[template_id]["data_key"]
    rows = "".join(
        f"    - label {label}\n" + (f"      desc {desc}\n" if desc else "") for label, desc in items
    )
    labels = ", ".join(label for label, _ in items)
    return {
        "heading": heading, "template_id": template_id, "headline": heading, "subtitle": "", "eyebrow": "",
        "syntax": f"infographic {template_id}\ndata\n  {key}\n{rows}".rstrip(),
        "alt_text": f"{heading}: {labels}"[:120],
    }


_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def _takeaway_sentence(lines: list[str]) -> str:
    """The section's most informative sentence that fits: one with a figure, else the first."""
    prose = " ".join(l.strip() for l in lines if l.strip() and not _LIST_ITEM.match(l) and not l.lstrip().startswith(("<", "|", ">")))
    sentences = [s.strip() for s in _SENTENCE.split(re.sub(r"[*_`]+", "", prose)) if s.strip()]
    sentences = [s for s in sentences if 50 <= len(s) <= TEMPLATES["takeaway"]["label_chars"] and not s.endswith("?")]
    return next((s for s in sentences if re.search(r"\d", s)), sentences[0] if sentences else "")


def structural_candidates(body: str) -> list[dict[str, str]]:
    """Graphics taken directly from the article: its 3-6 item lists, and sections split into
    3-6 subsections. No model involved, so every word is the article's own. Used only when
    the model's proposals don't reach the target count."""
    out: list[dict[str, str]] = []
    sections = _sections(body)
    for n, (level, heading, lines) in enumerate(sections):
        if level not in (2, 3) or _SKIP_HEADING.match(heading) or len(heading) > MAX_HEADLINE_CHARS:
            continue
        for numbered, texts in _list_blocks(lines):
            items = [_split_item(t) for t in texts]
            if all(desc for _, desc in items):
                # Numbered lists are often just enumerations ("1. Skipping Assessment"); only
                # action items read as an ordered process.
                is_process = numbered and all(_is_action(label) for label, _ in items)
                template_id = "steps" if is_process else "cards"
                limit = desc_limit(template_id, len(items))
                items = [(label, _short_desc(desc, limit)) for label, desc in items]
                if all(desc for _, desc in items):
                    out.append(_structural(heading, template_id, items))
                    continue
            if all(len(label) <= TEMPLATES["checklist"]["label_chars"] and _is_action(label) for label, _ in items):
                out.append(_structural(heading, "checklist", [(label, "") for label, _ in items]))
        if level == 2:
            children = []
            for sub_level, sub_heading, sub_lines in sections[n + 1:]:
                if sub_level <= 2:
                    break
                if sub_level == 3:
                    children.append((sub_heading, _short_desc(" ".join(l for l in sub_lines if l.strip() and not _LIST_ITEM.match(l)), 80)))
            if 3 <= len(children) <= 6 and all(desc for _, desc in children):
                out.append(_structural(heading, "cards", [(_plain(h), d) for h, d in children]))
    # Lowest priority: a key sentence from a prose section.
    for level, heading, lines in sections:
        if level in (2, 3) and not _skipped(heading, "takeaway") and len(heading) <= MAX_HEADLINE_CHARS:
            sentence = _takeaway_sentence(lines)
            if sentence:
                candidate = _structural(heading, "takeaway", [(sentence, "")])
                out.append({**candidate, "alt_text": f"Key takeaway: {sentence}"[:120]})
    return out


def _frame(candidate: dict[str, str]) -> dict[str, str]:
    return {
        "headline": candidate["headline"],
        "subtitle": candidate.get("subtitle", ""),
        "eyebrow": candidate.get("eyebrow") or EYEBROW,
        "source": candidate.get("source", ""),
        "brand_color": settings.infographic_brand_color,
        "brand_name": settings.infographic_brand_name or "",
        "brand_logo_url": settings.infographic_brand_logo_url or "",
    }


def parse_items(syntax: str) -> list[dict[str, str]]:
    """Top-level items of a list/sequence/values syntax, for the HTML templates."""
    lines = syntax.split("\n")
    starts = [i for i, l in enumerate(lines) if l.lstrip().startswith("- ") and _LABEL_LINE.match(l)]
    if not starts:
        return []
    indent = len(_LABEL_LINE.match(lines[starts[0]]).group(1))
    starts = [i for i in starts if len(_LABEL_LINE.match(lines[i]).group(1)) == indent]
    items = []
    for n, start in enumerate(starts):
        block = lines[start:starts[n + 1] if n + 1 < len(starts) else len(lines)]
        item = {"label": _LABEL_LINE.match(block[0]).group(2), "desc": "", "icon": "", "value": ""}
        for line in block[1:]:
            if (m := _DESC_LINE.match(line)):
                item["desc"] = m.group(1)
            elif (m := _ICON_LINE.match(line)):
                item["icon"] = m.group(2).replace("ref:remote:", "")
            elif (m := _VALUE_LINE.match(line)):
                item["value"] = m.group(1)
        items.append(item)
    return items


def render_spec(template_id: str, syntax: str) -> dict[str, Any]:
    if TEMPLATES[template_id]["kind"] == "html":
        return {"kind": "html", "template": template_id, "items": parse_items(syntax)}
    return {"kind": "antv", "template": template_id, "syntax": syntax}


async def render_infographics(candidates: list[dict[str, str]]) -> dict[int, dict[str, Any]]:
    """POST a batch to the Next.js render route. Returns index -> {ok, png_base64|error}."""
    if not candidates:
        return {}
    frontend_url = (settings.frontend_url or "").rstrip("/")
    secret = settings.infographic_render_secret
    if not frontend_url or not secret:
        return {i: {"ok": False, "error": "Infographic rendering is not configured"} for i in range(len(candidates))}

    items = []
    for i, c in enumerate(candidates):
        fallback = fallback_syntax(c["template_id"], c["syntax"])
        items.append({
            "id": str(i),
            "primary": render_spec(c["template_id"], c["syntax"]),
            "fallback": render_spec(FALLBACK_TEMPLATE, fallback) if fallback else None,
            "frame": _frame(c),
        })
    try:
        async with httpx.AsyncClient(verify=httpx_verify(), timeout=RENDER_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{frontend_url}/api/infographic/render",
                json={"items": items},
                headers={"x-infographic-secret": secret},
            )
        if response.status_code != 200:
            logger.warning("Infographic render route returned %s: %s", response.status_code, response.text[:300])
            return {i: {"ok": False, "error": f"Render service returned {response.status_code}"} for i in range(len(candidates))}
        payload = response.json()
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            return {i: {"ok": False, "error": "Malformed render response"} for i in range(len(candidates))}
        out: dict[int, dict[str, Any]] = {}
        for result in results:
            if not isinstance(result, dict):
                continue
            try:
                idx = int(result.get("id"))
            except (TypeError, ValueError):
                continue
            out[idx] = result
        return out
    except Exception as e:
        logger.warning("Infographic render call failed: %s", e)
        return {i: {"ok": False, "error": str(e)} for i in range(len(candidates))}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def image_tag(url: str, alt: str) -> str:
    return (
        f'<img src="{html.escape(url, quote=True)}" alt="{html.escape(alt, quote=True)}" '
        f'width="{CANVAS_WIDTH}" height="{CANVAS_HEIGHT}" loading="lazy" '
        f'class="max-w-full h-auto rounded-md my-4" />'
    )


def insert_at_section_end(body: str, heading: str, tag: str) -> str:
    """Insert after the matched heading's own content, before the next heading.

    If the heading has no content of its own (it goes straight into subsections), the
    image goes directly under the heading as an overview of the section — placing it at
    the end of all the subsections would separate it from what it summarizes. Returns
    body unchanged if the heading isn't found.
    """
    lines = body.split("\n")
    target = heading.strip().lower()
    for i, line in enumerate(lines):
        level = _markdown_heading_level(line)
        if level not in (2, 3) or target not in _markdown_heading_text(line).lower():
            continue
        end = len(lines)
        for j in range(i + 1, len(lines)):
            if _markdown_heading_level(lines[j]) > 0:
                end = j
                break
        insert_at = end
        while insert_at > i + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        after = [] if insert_at < len(lines) and not lines[insert_at].strip() else [""]
        return "\n".join(lines[:insert_at] + ["", tag] + after + lines[insert_at:])
    return body


def insert_infographics(
    body: str,
    candidates: list[dict[str, str]],
    render_results: dict[int, dict[str, Any]],
) -> tuple[str, list[str], Optional[dict[str, str]]]:
    """Save and place rendered infographics. Returns (body, warnings, featured_image).

    The first infographic that renders (candidates are ranked best first) doubles as the
    post's featured image; its 16:9 shape suits social preview cards.
    """
    warnings: list[str] = []
    featured: Optional[dict[str, str]] = None
    for i, candidate in enumerate(candidates):
        result = render_results.get(i) or {"ok": False, "error": "No render result"}
        if not result.get("ok"):
            logger.info("Infographic for '%s' failed to render: %s", candidate["heading"], result.get("error"))
            warnings.append(f"Infographic for '{candidate['heading']}' could not be rendered and was skipped.")
            continue
        png_base64 = result.get("png_base64")
        if not isinstance(png_base64, str) or not png_base64.strip():
            warnings.append(f"Infographic for '{candidate['heading']}' returned no image and was skipped.")
            continue
        try:
            filename = save_png_base64(png_base64, _slug(candidate["headline"]))
        except Exception as e:
            logger.warning("Infographic save failed for '%s': %s", candidate["heading"], e)
            warnings.append(f"Infographic for '{candidate['heading']}' could not be saved and was skipped.")
            continue
        url = public_infographic_url(filename)
        new_body = insert_at_section_end(body, candidate["heading"], image_tag(url, candidate["alt_text"]))
        if new_body == body:
            warnings.append(f"Infographic for '{candidate['heading']}' could not be placed and was skipped.")
            continue
        body = new_body
        if featured is None:
            featured = {"url": url, "thumbnail": url, "title": candidate["headline"], "alt": candidate["alt_text"]}
    return body, warnings, featured


async def add_infographics_to_article(
    topic: str,
    body: str,
    generate: Callable[..., Awaitable[str]],
    delay: Callable[[], Awaitable[None]],
    source_title: str = "",
) -> tuple[str, list[str], Optional[dict[str, str]]]:
    """Extract, validate, render, and insert 3 infographics. Never raises.

    To reach 3, candidates come from (in order): the model's ranked proposals, one top-up
    model round for the sections not yet used, then graphics built directly from the
    article's lists and subsections. Valid extras are kept in reserve and rendered when a
    chosen graphic fails to render. Returns (body, warnings, featured_image).
    """
    try:
        pool, warnings = await _candidate_pool(topic, body, generate, delay)
        if not (settings.frontend_url or "").strip() or not settings.infographic_render_secret:
            # A deployment without the render service simply has no infographics.
            logger.info("Infographic rendering is not configured; skipping %s candidate(s).", len(pool))
            return body, [], None
        if not pool:
            _log_shortfall(0)
            return body, warnings, None
        source = (source_title or topic).strip()
        pool = [{**c, "source": source} for c in pool]
        # A takeaway's section name ("Next Step") makes a weak headline: it becomes the
        # eyebrow, and the article's own title heads the card.
        pool = [
            {**c, "eyebrow": c["heading"][:MAX_EYEBROW_CHARS], "headline": source}
            if c["template_id"] == "takeaway" and len(source) <= MAX_HEADLINE_CHARS else c
            for c in pool
        ]
        chosen, results = await _render_with_reserve(pool)
        new_body, insert_warnings, featured = insert_infographics(body, chosen, results)
        placed = new_body.count(f'width="{CANVAS_WIDTH}" height="{CANVAS_HEIGHT}"') - body.count(
            f'width="{CANVAS_WIDTH}" height="{CANVAS_HEIGHT}"')
        if placed >= TARGET_INFOGRAPHIC_COUNT:
            # Rejected or replaced proposals don't matter once the article has its graphics.
            for w in warnings + insert_warnings:
                logger.info("%s", w)
            return new_body, [], featured
        _log_shortfall(placed)
        return new_body, warnings + insert_warnings, featured
    except Exception as e:
        logger.warning("Infographic generation was unavailable: %s", e)
        return body, [], None


def _log_shortfall(count: int) -> None:
    # Not a quality warning: missing graphics shouldn't send an article back for revision.
    logger.info("Only %s of %s infographics could be generated; the article has too few sections "
                "with list-like content.", count, TARGET_INFOGRAPHIC_COUNT)


async def _candidate_pool(
    topic: str,
    body: str,
    generate: Callable[..., Awaitable[str]],
    delay: Callable[[], Awaitable[None]],
) -> tuple[list[dict[str, str]], list[str]]:
    """Ranked, validated candidates: the target count plus any valid spares."""
    warnings: list[str] = []
    pool: list[dict[str, str]] = []
    tried: set[str] = set()
    for round_no in range(2):
        if len(pool) >= TARGET_INFOGRAPHIC_COUNT:
            break
        try:
            await delay()
            prompt = extraction_prompt(topic, body, chosen=pool, tried=tried) if round_no else extraction_prompt(topic, body)
            candidates = parse_candidates(await generate(prompt, max_tokens=5000, temperature=0.3))
        except Exception as e:
            logger.info("Infographic proposals were unavailable: %s", e)
            break
        if not candidates:
            break
        tried |= {c["heading"] for c in candidates}
        candidates = await shorten_overlong(candidates, generate, delay)
        more, round_warnings = validate_candidates(candidates, body, existing=pool, limit=MAX_PROPOSALS)
        pool += more
        warnings += round_warnings
    if len(pool) < TARGET_INFOGRAPHIC_COUNT:
        more, _ = validate_candidates(structural_candidates(body), body, existing=pool, limit=MAX_PROPOSALS)
        pool += more
    return pool, warnings


async def _render_with_reserve(pool: list[dict[str, str]]) -> tuple[list[dict[str, str]], dict[int, dict[str, Any]]]:
    """Render the top candidates; replace any that fail with the next spare (one refill)."""
    chosen: list[dict[str, str]] = []
    results: dict[int, dict[str, Any]] = {}
    failed: list[dict[str, str]] = []
    queue = list(pool)
    for _ in range(2):
        batch, queue = queue[:TARGET_INFOGRAPHIC_COUNT - len(chosen)], queue[TARGET_INFOGRAPHIC_COUNT - len(chosen):]
        if not batch:
            break
        rendered = await render_infographics(batch)
        for i, candidate in enumerate(batch):
            result = rendered.get(i) or {"ok": False, "error": "No render result"}
            if result.get("ok") and isinstance(result.get("png_base64"), str) and result["png_base64"].strip():
                results[len(chosen)] = result
                chosen.append(candidate)
            else:
                logger.info("Infographic for '%s' failed to render: %s", candidate["heading"], result.get("error"))
                failed.append(candidate)
        if len(chosen) >= TARGET_INFOGRAPHIC_COUNT:
            break
    # Keep article order stable: rank decides which graphics are used, not where they go.
    for candidate in failed:
        results[len(chosen)] = {"ok": False, "error": "render failed"}
        chosen.append(candidate)
    return chosen, results
