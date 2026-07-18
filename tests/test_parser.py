"""
Unit tests for hierarchy reconstruction edge cases.

HONEST NOTE: these run against synthetically constructed RawLine sequences
rather than real pages from the CT-200 PDF, since that file wasn't
available in this environment. They test the classify/tree-building logic
in isolation, which is real and exercised - but they are NOT a substitute
for running the parser against the actual manual and eyeballing the
output. See APPROACH.md "how I validated correctness" for what running it
against the real PDF should additionally check.
"""
from app.parser import RawLine, _classify, _infer_heading_font_sizes


def _build_tree_from_lines(lines: list[RawLine]):
    """Mirrors the tree-building loop in parse_pdf_to_tree, but takes
    pre-built RawLines directly instead of extracting them from a PDF,
    so we can test hierarchy logic without a real file on disk."""
    from app.parser import ParsedNode

    size_to_level = _infer_heading_font_sizes(lines)
    root_nodes = []
    stack = []
    order_counters = {}

    def next_order(parent):
        key = id(parent)
        order_counters[key] = order_counters.get(key, 0) + 1
        return order_counters[key]

    for line in lines:
        classified = _classify(line, size_to_level)
        if classified is None:
            if stack:
                stack[-1].body_text += (" " if stack[-1].body_text else "") + line.text
            continue
        level, section_path = classified
        while len(stack) >= level:
            stack.pop()
        parent = stack[-1] if stack else None
        node = ParsedNode(
            heading=line.text, level=level, section_path=section_path,
            order_index=next_order(parent),
        )
        if parent:
            parent.children.append(node)
        else:
            root_nodes.append(node)
        stack.append(node)

    return root_nodes


def test_duplicate_heading_text_produces_two_distinct_nodes_with_correct_parents():
    """
    Two sections literally titled "Warnings" nested under two different
    parents ("Setup" and "Maintenance") must NOT be merged into one node -
    each occurrence is its own node, correctly parented to its own section.
    This is the specific failure mode the assignment calls out by name.
    """
    lines = [
        RawLine("1 Setup", 18.0, True, 0),
        RawLine("Connect the cuff to the unit.", 11.0, False, 0),
        RawLine("1.1 Warnings", 14.0, True, 0),
        RawLine("Do not overinflate the cuff.", 11.0, False, 0),
        RawLine("2 Maintenance", 18.0, True, 1),
        RawLine("Clean the device monthly.", 11.0, False, 1),
        RawLine("2.1 Warnings", 14.0, True, 1),
        RawLine("Do not submerge the device in water.", 11.0, False, 1),
    ]
    tree = _build_tree_from_lines(lines)

    assert len(tree) == 2
    setup, maintenance = tree
    assert setup.heading == "1 Setup" and maintenance.heading == "2 Maintenance"

    setup_warnings = setup.children[0]
    maint_warnings = maintenance.children[0]
    # both are literally "Warnings"-titled subsections but are distinct objects
    assert setup_warnings is not maint_warnings
    assert "overinflate" in setup_warnings.body_text
    assert "submerge" in maint_warnings.body_text
    # each has the correct, DIFFERENT parent
    assert setup_warnings in setup.children
    assert maint_warnings in maintenance.children
    assert maint_warnings not in setup.children


def test_skipped_heading_level_does_not_fabricate_intermediate_node():
    """
    Document jumps from a level-1 heading straight to a level-3-numbered
    heading (e.g. "1 Overview" -> "1.1.1 Detail", no "1.1" in between).
    The parser must attach 1.1.1 under 1 without inventing a fake "1.1"
    node to fill the gap - the gap should be visible/inspectable, not
    silently patched over.
    """
    lines = [
        RawLine("1 Overview", 18.0, True, 0),
        RawLine("General description.", 11.0, False, 0),
        RawLine("1.1.1 Detail", 12.0, True, 0),
        RawLine("Fine-grained detail text.", 11.0, False, 0),
    ]
    tree = _build_tree_from_lines(lines)

    assert len(tree) == 1
    overview = tree[0]
    assert overview.heading == "1 Overview"
    # exactly one child - no fabricated intermediate "1.1" node
    assert len(overview.children) == 1
    detail = overview.children[0]
    assert detail.heading == "1.1.1 Detail"
    assert detail.level == 3
    assert detail.section_path == "1.1.1"


def test_unnumbered_heading_detected_via_font_size_still_gets_body_text():
    """
    Some sections in real-world manuals are headed by visual style alone
    (larger/bold text) with no "N.N" numbering at all. These should still
    be recognized as headings (not swallowed into the previous section's
    body text) and their section_path should be None rather than a
    fabricated guess.
    """
    lines = [
        RawLine("1 Introduction", 18.0, True, 0),
        RawLine("This device measures blood pressure.", 11.0, False, 0),
        RawLine("Important Safety Information", 16.0, True, 0),  # no numbering
        RawLine("Consult your physician before use.", 11.0, False, 0),
    ]
    tree = _build_tree_from_lines(lines)

    intro = tree[0]
    assert intro.heading == "1 Introduction"
    assert len(intro.children) == 1
    safety = intro.children[0]
    assert safety.heading == "Important Safety Information"
    assert safety.section_path is None
    assert "physician" in safety.body_text
    # and it must NOT have been merged into the intro's own body text
    assert "physician" not in intro.body_text
