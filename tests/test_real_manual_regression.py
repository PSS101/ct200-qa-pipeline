"""
Regression test against the REAL ct200_manual_v2.pdf.

Unlike test_parser.py (synthetic lines) and test_integration_smoke.py
(a synthetic PDF), this runs against the actual manual and locks in the
three real bugs found by doing so - see the module docstring in
app/parser.py for the full story of each. If any of these regress, it
means someone "fixed" something else and broke a real, confirmed case.
"""
import os

import pytest

from app.parser import parse_pdf_to_tree, flatten

REAL_MANUAL_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "ct200_manual_v2.pdf")

pytestmark = pytest.mark.skipif(
    not os.path.exists(REAL_MANUAL_PATH),
    reason="real ct200_manual_v2.pdf not present in data/ - see README",
)


@pytest.fixture(scope="module")
def real_tree():
    roots = parse_pdf_to_tree(REAL_MANUAL_PATH)
    return roots, list(flatten(roots))


def test_exactly_eight_top_level_numbered_sections(real_tree):
    roots, _ = real_tree
    numbered_roots = [r for r in roots if r.section_path is not None]
    assert len(numbered_roots) == 8
    assert [r.section_path for r in numbered_roots] == [str(i) for i in range(1, 9)]


def test_cover_title_is_one_node_not_split_across_wrapped_lines(real_tree):
    """Regression for bug #3: the 3-line wrapped cover title must be a
    single node, not three separate spurious top-level sections."""
    roots, _ = real_tree
    title_roots = [r for r in roots if r.section_path is None]
    assert len(title_roots) == 1
    assert "CardioTrack CT-200" in title_roots[0].heading
    assert "User Manual" in title_roots[0].heading


def test_classification_list_is_body_text_not_spurious_headings(real_tree):
    """Regression for bug #2: '1. Normal: systolic < 120...' etc. must
    NOT become top-level nodes - they're a list inside 3.3's body text."""
    roots, all_nodes = real_tree
    bogus = [n for n in all_nodes if n.heading.startswith(("1. Normal", "2. Elevated", "3. Hypertension Stage 1"))]
    assert bogus == []

    result_display = next(n for n in all_nodes if n.section_path == "3.3")
    assert "Normal: systolic < 120" in result_display.body_text
    assert "Hypertensive Crisis" in result_display.body_text


def test_ligatures_normalized_in_headings_and_body(real_tree):
    """Regression for bug #1: PyMuPDF ligature glyphs (ﬁ, ﬀ) must be
    normalized to plain ASCII, not left as presentation-form codepoints."""
    _, all_nodes = real_tree
    for n in all_nodes:
        assert "\ufb01" not in n.heading and "\ufb00" not in n.heading
        assert "\ufb01" not in n.body_text and "\ufb00" not in n.body_text
    specs = next(n for n in all_nodes if n.section_path == "2")
    assert "Specifications" in specs.heading  # not "Speciﬁcations"


def test_duplicate_heading_text_across_document_confirmed_real(real_tree):
    """The real manual genuinely reuses 'Error Codes' as both 4.2 and 7.1
    - confirms the duplicate-heading handling isn't just a synthetic
    worry, it's a real feature of this document."""
    _, all_nodes = real_tree
    error_code_nodes = [n for n in all_nodes if n.heading.strip().endswith("Error Codes")]
    assert len(error_code_nodes) == 2
    paths = sorted(n.section_path for n in error_code_nodes)
    assert paths == ["4.2", "7.1"]
    assert error_code_nodes[0] is not error_code_nodes[1]


def test_skipped_heading_level_real_case(real_tree):
    """Real case: 2.1 is followed directly by 2.1.1.1 with no 2.1.1 in
    between. Must attach under 2.1 without fabricating an intermediate."""
    _, all_nodes = real_tree
    battery = next(n for n in all_nodes if n.section_path == "2.1.1.1")
    general_specs = next(n for n in all_nodes if n.section_path == "2.1")
    assert battery in general_specs.children
    assert battery.level == 4


def test_out_of_order_section_numbering_preserved_as_document_order(real_tree):
    """Real case: section 3.4 (Auto Shutoff) physically appears BEFORE
    3.3 (Result Display) in the document. We preserve document order
    rather than silently re-sorting by numbering - see APPROACH.md for
    why re-sorting would be the wrong call here."""
    _, all_nodes = real_tree
    device_operation = next(n for n in all_nodes if n.section_path == "3")
    child_paths = [c.section_path for c in device_operation.children]
    assert child_paths == ["3.1", "3.2", "3.4", "3.3"]
