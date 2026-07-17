"""
Cross-version node matching.

Given the freshly-parsed nodes for a new DocumentVersion and the nodes of
the immediately-previous version, decide which new nodes are "the same
logical section" as which old nodes, and carry the old logical_id forward.

Strategy (in priority order, per new node):
  1. Exact section_path match (e.g. both are "3.2.1") within a version
     that also has a plausible parent match. Numbering is the strongest
     signal a spec/manual gives us for "this is the same clause" - it's
     usually more stable than heading text across doc revisions in
     regulated documents, since renumbering is disruptive and editors
     avoid it. This is a bet, see failure mode below.
  2. If no section_path (or no match found by path), fall back to exact
     heading-text match among siblings under an already-matched parent.
  3. If still no match, the node is treated as NEW (fresh logical_id).
     Old nodes that had no match in the new version are treated as
     REMOVED (not deleted - v1 rows are untouched - just absent from v2's
     tree and reachable only via version=1 queries).

Known failure modes (see APPROACH.md decision log for the full discussion):
  - A section that gets renumbered AND reworded in the same revision
    (e.g. "3.2 Cuff Fitting" becomes "3.3 Applying the Cuff") will match
    neither by path nor by heading text, and will be (wrongly, from a
    human's point of view) treated as a brand-new node with the old one
    marked removed. This loses the "changed" signal and replaces it with
    "unrelated add + delete" - which is strictly worse for traceability,
    since a reviewer checking "did this become stale" won't be pointed at
    the old node at all.
  - Two sibling sections that swap positions but keep their numbering
    text embedded in a differently-restructured page could mismatch if
    numbering wasn't preserved exactly.
  - This is a greedy, single-pass matcher - no backtracking/global
    optimum matching (e.g. Hungarian algorithm on a similarity matrix).
    For a document this size that's a reasonable simplicity/correctness
    tradeoff; it would NOT be for a much larger, more volatile document.
"""
from dataclasses import dataclass


@dataclass
class MatchResult:
    new_node_id: str          # id of the freshly-inserted Node row
    logical_id: str           # logical_id to assign (old, reused, or new)
    matched_old_node_id: str | None  # None if this is a new/unmatched node


def match_versions(old_nodes: list, new_nodes: list) -> list[MatchResult]:
    """
    old_nodes / new_nodes: lists of ORM Node objects (old_nodes already
    persisted with logical_id set; new_nodes freshly persisted, logical_id
    still None). Returns match decisions; caller is responsible for
    writing logical_id back to the DB in one transaction.
    """
    import uuid

    results: list[MatchResult] = []
    old_by_path = {n.section_path: n for n in old_nodes if n.section_path}
    old_used: set[str] = set()

    # pass 1: section_path match
    unmatched_new = []
    for new_node in new_nodes:
        old = old_by_path.get(new_node.section_path) if new_node.section_path else None
        if old and old.id not in old_used:
            results.append(MatchResult(new_node.id, old.logical_id, old.id))
            old_used.add(old.id)
        else:
            unmatched_new.append(new_node)

    # pass 2: heading-text match among remaining old nodes, scoped to
    # same parent-logical-id when we can resolve it, else global fallback
    old_by_heading: dict[str, list] = {}
    for n in old_nodes:
        if n.id in old_used:
            continue
        old_by_heading.setdefault(n.heading.strip().lower(), []).append(n)

    still_unmatched = []
    for new_node in unmatched_new:
        key = new_node.heading.strip().lower()
        candidates = [c for c in old_by_heading.get(key, []) if c.id not in old_used]
        if candidates:
            old = candidates[0]  # first available; ambiguous duplicates go to APPROACH.md
            results.append(MatchResult(new_node.id, old.logical_id, old.id))
            old_used.add(old.id)
        else:
            still_unmatched.append(new_node)

    # pass 3: everything left is a new logical node
    for new_node in still_unmatched:
        results.append(MatchResult(new_node.id, str(uuid.uuid4()), None))

    return results
