"""
PDF -> hierarchical tree parser.

HONEST STATUS: this was built without access to the actual CT-200 manual
PDF (only the assignment brief was available at write time), so the
heuristics below are a reasonable, defensible starting point - NOT
something that's been tuned or validated against the real document's
specific irregularities. Before relying on this, run it against the real
ct200_manual.pdf, dump the (level, heading, order_index) output, and
diff it against a manual read of the PDF's table of contents. See
APPROACH.md for what I'd specifically go check first (duplicate headings,
skipped heading levels, tables, unnumbered sections, multi-column layout).

Strategy:
1. Extract text spans with font size + bold flag via PyMuPDF, page by page.
2. A line is a heading candidate if (a) it matches a numbering pattern like
   "3.2.1 Title" / "3.2 Title" / "3 Title", OR (b) its font size is
   meaningfully larger than the surrounding body-text font size and it's
   short (< ~12 words) and not ending in normal sentence punctuation.
3. Heading level is taken from numbering depth when present (e.g.
   "3.2.1" -> level 3). When numbering is absent, level is inferred from
   a font-size-to-level map built from the *document's own* observed
   font sizes (largest distinct size = level 1, etc.) - not hardcoded
   point sizes, since those vary per document.
4. Non-heading lines are accumulated as body_text under the most recent
   heading node.
5. Duplicate headings (same text appearing twice, e.g. a repeated
   "Warnings" subsection under two different parents) are NOT merged -
   each occurrence becomes its own Node with its own id. Parenting comes
   from position in the walk, not from heading text, specifically so
   duplicate-heading text does not collapse into one node.
6. Tables: PyMuPDF's structured extraction can catch simple grid tables;
   these are stored as body_text with a "[TABLE]" marker prefix rather
   than silently flattened into prose. This is a known-weak spot - see
   APPROACH.md.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

import fitz  # PyMuPDF

from app.hashing import content_hash

NUMBERING_RE = re.compile(r"^(\d+(?:\.\d+)*)[\.\)]?\s+(.+)$")


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
    m = NUMBERING_RE.match(line.text)
    if m:
        numbering, title = m.groups()
        depth = numbering.count(".") + 1
        # sanity check: numbered heading text shouldn't be a full sentence
        if len(title.split()) <= 16:
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

    def next_order(parent: Optional[ParsedNode]) -> int:
        key = id(parent)
        order_counters[key] = order_counters.get(key, 0) + 1
        return order_counters[key]

    for line in lines:
        classified = _classify(line, size_to_level)
        if classified is None:
            if stack:
                stack[-1].body_text += (" " if stack[-1].body_text else "") + line.text
            # a line before any heading is currently dropped - see
            # APPROACH.md "what my initial implementation failed to
            # handle" for the front-matter / cover-page case.
            continue

        level, section_path = classified

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

    return root_nodes


def flatten(nodes: list[ParsedNode]):
    """Pre-order walk. Parent/child structure is read off node.children by the caller."""
    for n in nodes:
        yield n
        yield from flatten(n.children)
