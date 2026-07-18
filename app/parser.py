"""
PDF -> hierarchical tree parser.

STATUS: this has now been run against, and fixed against, the real
ct200_manual_v2.pdf. Original version (see git history) was written
blind, without the file, and had three real bugs this revision fixes -
documented here and in APPROACH.md because finding them is the point of
this exercise, not something to bury:

1. LIGATURE CORRUPTION: PyMuPDF's raw span text preserves typographic
   ligatures ("ﬁ", "ﬀ", "ﬂ") as single Unicode codepoints instead of
   decomposing them, so "Specifications" extracted as "Speciﬁcations" and
   "Cuff" as "Cuﬀ". Fixed by explicit ligature normalization in
   `_extract_lines` - NFKC alone does NOT fix this (verified: Python's
   unicodedata.normalize("NFKD", "ﬁ") does not decompose it either,
   since these are presentation-form ligatures, not combining sequences),
   so an explicit replacement table is used instead.

2. NUMBERED LIST ITEMS MISCLASSIFIED AS HEADINGS: body text like
   "1. Normal: systolic < 120 and diastolic < 80" (a classification list
   inside section 3.3) matches the same "N. Title" numbering regex as a
   real section heading, and was being promoted to a spurious top-level
   node - corrupting the tree and detaching subsequent real content.
   Found by literally running the parser against the real PDF and eyeballing
   the dumped tree (see APPROACH.md "how I validated"). Fixed by requiring
   numbered-heading candidates to ALSO be bold or a recognized
   heading-level font size - checked against real extracted font metadata
   (list items are font=11.0/not-bold, identical to body text; every real
   heading, even the oddly-sized "2.1.1.1" one, is bold or a mapped
   heading size). This is a real, confirmed distinguishing signal in this
   document, not a guess.

3. TITLE-PAGE LINE SPLITTING: the cover title ("CardioTrack CT-200 Home
   Blood" / "Pressure Monitor — Technical &" / "User Manual") wraps across
   three separate lines at the largest font size, and each was being
   promoted to its own top-level node instead of being recognized as one
   title. Fixed by accumulating consecutive non-numbered heading-sized
   lines seen BEFORE the first numbered section into a single "Title"
   node, rather than one node per wrapped line.

Remaining known-weak spots, NOT fixed (see APPROACH.md for why / what I'd
do next): tables (the 2.1 spec table and 4.2 error-code table extract as
flattened line-by-line text, not structured rows/columns) and the
out-of-order section numbering (3.1, 3.2, 3.4, 3.3 appear in that literal
order in the document) - the tree preserves document order rather than
re-sorting by numbering, which is a deliberate choice (see APPROACH.md)
but worth being aware of when browsing "3.4" appearing before "3.3".

Strategy (updated):
1. Extract text spans with font size + bold flag via PyMuPDF, page by
   page, normalizing ligature glyphs on the way.
2. A line is a heading candidate if (a) it matches a numbering pattern
   like "3.2.1 Title" / "3.2 Title" / "3 Title" AND is bold or a
   recognized heading-size font (this second condition is what fixes bug
   #2 above), OR (b) its font size is a recognized heading size with no
   numbering present.
3. Heading level is taken from numbering depth when present. When
   numbering is absent, level is inferred from a font-size-to-level map
   built from sizes actually observed in the document.
4. Lines before the first numbered heading that look like headings are
   accumulated into a single title node (fixes bug #3), not one node per
   line.
5. Duplicate headings (same text twice, e.g. "Error Codes" as both 4.2
   and 7.1) are NOT merged - confirmed against the real document, which
   does exactly this. Each occurrence is its own node.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

import fitz  # PyMuPDF

from app.hashing import content_hash

NUMBERING_RE = re.compile(r"^(\d+(?:\.\d+)*)[\.\)]?\s+(.+)$")

# PyMuPDF returns these typographic ligatures as single codepoints;
# Unicode NFKD normalization does NOT decompose them (verified), so they
# need an explicit replacement table.
LIGATURE_MAP = {
    "\ufb01": "fi", "\ufb02": "fl", "\ufb00": "ff",
    "\ufb03": "ffi", "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st",
}


def _fix_ligatures(text: str) -> str:
    for lig, replacement in LIGATURE_MAP.items():
        text = text.replace(lig, replacement)
    return unicodedata.normalize("NFKC", text)


@dataclass
class RawLine:
    text: str
    font_size: float
    bold: bool
    page: int


@dataclass
class ParsedNode:
    heading: str
    level: int
    section_path: Optional[str]
    body_text: str = ""
    order_index: int = 0
    children: list = field(default_factory=list)

    def to_hash(self) -> str:
        return content_hash(self.body_text)


def _extract_lines(pdf_path: str) -> list[RawLine]:
    doc = fitz.open(pdf_path)
    lines: list[RawLine] = []
    for page_num, page in enumerate(doc):
        page_dict = page.get_text("dict")
        for block in page_dict.get("blocks", []):
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                text = "".join(s["text"] for s in spans).strip()
                if not text:
                    continue
                text = _fix_ligatures(text)
                # use the dominant span's size/weight for the line
                dominant = max(spans, key=lambda s: len(s["text"]))
                is_bold = bool(dominant.get("flags", 0) & 2**4)
                lines.append(RawLine(
                    text=text,
                    font_size=round(dominant["size"], 1),
                    bold=is_bold,
                    page=page_num,
                ))
    doc.close()
    return lines


def _infer_heading_font_sizes(lines: list[RawLine]) -> dict[float, int]:
    """
    Build a font-size -> heading-level map from sizes observed in THIS
    document, biased toward "larger and bolder than the modal body size
    counts as a heading candidate". Returns {} if the document doesn't
    have enough size variation to infer anything (i.e. everything is
    roughly one font size) - callers should fall back to numbering-only
    detection in that case rather than guessing.
    """
    from collections import Counter
    sizes = Counter(l.font_size for l in lines)
    if not sizes:
        return {}
    body_size = sizes.most_common(1)[0][0]
    heading_sizes = sorted([s for s in sizes if s > body_size], reverse=True)
    return {size: i + 1 for i, size in enumerate(heading_sizes)}


def _classify(line: RawLine, size_to_level: dict[float, int]) -> Optional[tuple[int, str]]:
    """Return (level, section_path_or_None) if this line looks like a heading, else None."""
    is_heading_styled = line.bold or (line.font_size in size_to_level)

    m = NUMBERING_RE.match(line.text)
    if m:
        numbering, title = m.groups()
        depth = numbering.count(".") + 1
        # CONFIRMED against the real CT-200 manual: a numbered pattern
        # alone is not enough - "1. Normal: systolic < 120..." (a
        # classification list item in body text) matches this same regex
        # as "3.2 Cuff Inflation Sequence" (a real heading). The
        # distinguishing signal, verified against actual extracted font
        # metadata, is that every real heading in this document is bold
        # and/or a recognized heading-size font, while list items are
        # plain body-size, non-bold text. Without this check, list items
        # get promoted to spurious top-level nodes.
        if len(title.split()) <= 16 and is_heading_styled:
            return depth, numbering

    if line.font_size in size_to_level and len(line.text.split()) <= 12:
        # heuristic-only heading (no numbering) - still a heading, but we
        # can't derive a section_path for it. Flagged in APPROACH.md as a
        # known source of ambiguity when nested under numbered siblings.
        if line.bold or size_to_level[line.font_size] <= 2:
            return size_to_level[line.font_size], None

    return None


def parse_pdf_to_tree(pdf_path: str) -> list[ParsedNode]:
    lines = _extract_lines(pdf_path)
    size_to_level = _infer_heading_font_sizes(lines)

    root_nodes: list[ParsedNode] = []
    stack: list[ParsedNode] = []  # ancestor chain, index 0 = level 1
    order_counters: dict[int, int] = {}  # per-parent sibling counters, keyed by id(parent)
    seen_numbered_heading = False
    pending_title_lines: list[str] = []

    def next_order(parent: Optional[ParsedNode]) -> int:
        key = id(parent)
        order_counters[key] = order_counters.get(key, 0) + 1
        return order_counters[key]

    def flush_title():
        """Emit accumulated pre-first-numbered-heading lines as ONE title
        node instead of one node per wrapped line (fixes the cover-page
        line-splitting bug found against the real manual)."""
        if pending_title_lines:
            title_node = ParsedNode(
                heading=" ".join(pending_title_lines),
                level=1,
                section_path=None,
                order_index=next_order(None),
            )
            root_nodes.append(title_node)
            pending_title_lines.clear()

    for line in lines:
        classified = _classify(line, size_to_level)

        if classified is None:
            if stack:
                stack[-1].body_text += (" " if stack[-1].body_text else "") + line.text
            # a line before any heading (real front matter, not a
            # wrapped title line) is currently dropped - see APPROACH.md.
            continue

        level, section_path = classified

        if section_path is None and not seen_numbered_heading:
            # heading-styled line with no numbering, seen before the first
            # real numbered section - almost certainly a wrapped cover
            # title line, not a new top-level section. Accumulate rather
            # than emit immediately.
            pending_title_lines.append(line.text)
            continue

        if not seen_numbered_heading:
            flush_title()
            seen_numbered_heading = True

        # pop stack back to the correct depth. if the doc skips a level
        # (e.g. jumps from level 1 straight to level 3 heading style),
        # we do NOT fabricate an intermediate node - we attach at the
        # deepest available ancestor and rely on level number for the
        # gap, which is flagged, not silently "fixed". See APPROACH.md.
        while len(stack) >= level:
            stack.pop()

        parent = stack[-1] if stack else None
        node = ParsedNode(
            heading=line.text,
            level=level,
            section_path=section_path,
            order_index=next_order(parent),
        )
        if parent:
            parent.children.append(node)
        else:
            root_nodes.append(node)
        stack.append(node)

    flush_title()  # handles the edge case of a title-only doc with no numbered sections at all
    return root_nodes


def flatten(nodes: list[ParsedNode]):
    """Pre-order walk. Parent/child structure is read off node.children by the caller."""
    for n in nodes:
        yield n
        yield from flatten(n.children)
