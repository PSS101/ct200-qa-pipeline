# Approach Document - CT-200 Document Intelligence API

## Honest scoping note (updated after getting the real manual)

**Update:** the real `ct200_manual_v2.pdf` became available after the
initial version of this document was written. Everything below the
original scoping note has been re-run and fixed against the real file -
this is no longer speculative. Three real bugs were found and fixed;
see `app/parser.py`'s module docstring and `tests/test_real_manual_regression.py`
for the full details of each, summarized in section 4 below. The original
honest-uncertainty framing is left below for the record, since it's part
of the actual engineering process this document is supposed to describe.

### Original note (written before the real PDF was available)

This was built in a compressed timeframe and without access to the actual
`ct200_manual.pdf` / `ct200_manual_v2.pdf` files - only the assignment
brief was available. Every module below is real, working code (imports
cleanly, the FastAPI app boots, all 3 required unit tests pass), but the
parser's heuristics have not been tuned or validated against the real
document's specific irregularities, because I haven't seen them. I'm
flagging this explicitly rather than presenting it as finished, per the
assignment's own framing that hand-waving is worse than visible rough
edges. Section "What I'd do next" says exactly what I'd run first once I
have the real PDF.

## 1. OCR / document parsing approach

**Choice: PyMuPDF (`fitz`), not a full OCR engine (e.g. Tesseract).**

Reasoning: PyMuPDF gives structured text extraction (spans with font size,
font weight, position) directly from a text-layer PDF, which is far more
reliable than OCR for a document that's very likely a generated/exported
PDF rather than a scanned image. OCR is a fallback I'd add (Tesseract via
`pytesseract`, rasterizing pages that come back with an empty text layer)
*if* inspection of the real file shows any scanned pages - I haven't seen
the file, so I can't confirm this either way, and I'm not going to bolt on
an OCR path I can't test just to check a box.

**Heading detection: numbering pattern first, font-size heuristic second.**
A line matching `N(.N)*  Title` is treated as a heading at a depth equal
to its numbering depth - this is the strongest, least ambiguous signal a
technical manual gives, and doesn't depend on font metadata varying
sensibly. Font-size clustering (built from sizes actually observed in
the document, not hardcoded point values) is the fallback for headings
that aren't numbered (e.g. a bolded "Important Safety Information" banner
type heading).

## 2. Hierarchy reconstruction strategy

A single-pass, stack-based walk over the page's lines in reading order:
classify each line as heading-or-not; when a heading is found, pop the
ancestor stack back to its level and attach it there; everything else is
appended as body text to the current deepest node. This is `O(n)` in
number of lines, doesn't need a second pass, and - importantly - never
*merges* nodes based on heading text similarity. Two nodes with identical
heading text are two different objects unless something upstream
(the version matcher, deliberately a separate concern) says otherwise.

## 3. Structural inconsistencies / edge cases (designed for, not yet confirmed against the real file)

- **Duplicate heading text under different parents** (e.g. "Warnings"
  appearing under both "Setup" and "Maintenance"). Handled: parenting is
  positional (stack-based), not keyed by heading text, so duplicates
  never collapse into one node. Covered by
  `test_duplicate_heading_text_produces_two_distinct_nodes_with_correct_parents`.
- **Skipped heading levels** (e.g. "1 Overview" followed directly by
  "1.1.1 Detail" with no "1.1" heading in between). Handled: no
  intermediate node is fabricated; the child attaches directly to the
  nearest actual ancestor, and the level number itself preserves the
  gap rather than hiding it. Covered by
  `test_skipped_heading_level_does_not_fabricate_intermediate_node`.
- **Unnumbered headings** identified only by visual weight/size, with no
  section_path. Handled: still recognized as headings (not swallowed into
  the previous section's body), section_path stored as `None` rather than
  a guessed value. Covered by
  `test_unnumbered_heading_detected_via_font_size_still_gets_body_text`.
- **Tables**: PyMuPDF's block/line extraction flattens simple grid tables
  into line-by-line text without column structure. Currently NOT specially
  detected - this is a known-weak spot, not a silently-passed one (see
  "what my initial implementation failed to handle" below).
- **Figures/captions**: not implemented. PyMuPDF can extract images
  (`page.get_images()`) and I'd associate a caption by proximity (nearest
  text block below/right of the image bounding box within some threshold),
  but this wasn't built out given time constraints - flagged, not hidden.

## 4a. What running the parser against the REAL manual found and fixed

This is the most important part of this document, honestly - it's the
actual validation the earlier draft could only promise to do later.
Running `parse_pdf_to_tree` against the real `ct200_manual_v2.pdf` and
dumping the tree by eye (not assuming it was correct) surfaced three real
bugs, none of which were caught by the synthetic unit tests, because the
synthetic tests only encode edge cases I *thought of* - not ones a real
document actually contains:

1. **Ligature corruption**: PyMuPDF's raw extraction returns typographic
   ligatures ("ﬁ", "ﬀ") as single codepoints, so "Specifications" came
   out as "Speciﬁcations". `unicodedata.normalize("NFKD", ...)` does NOT
   fix this (verified directly - these are presentation-form ligatures,
   not composed accent sequences). Fixed with an explicit replacement
   table in `_fix_ligatures`.
2. **Numbered list items misclassified as section headings**: the
   clinical classification list inside section 3.3 ("1. Normal:
   systolic < 120...", "2. Elevated: ...") matches the exact same
   `N. Title` numbering pattern as real section headings like "3. Device
   Operation", and was being promoted into 5 spurious top-level nodes -
   corrupting the tree structure, not just adding noise. Found by
   literally reading the dumped tree output and noticing "1. Normal:
   systolic..." sitting at the top level where it obviously didn't
   belong. Fixed by checking actual extracted font metadata: every real
   heading in this document is bold and/or a recognized larger heading
   size, while these list items are plain, non-bold, body-size text -
   a real and confirmed distinguishing signal, not a guess.
3. **Cover title split across three nodes**: "CardioTrack CT-200 Home
   Blood" / "Pressure Monitor — Technical &" / "User Manual" wraps across
   three lines at the same large font size and were each being promoted
   to their own top-level section. Fixed by accumulating consecutive
   non-numbered heading-styled lines seen before the first real numbered
   section into a single title node.

Also **confirmed as real, not hypothetical**, by this same manual: the
duplicate "Error Codes" heading (appears as both 4.2 and 7.1), the
skipped heading level (2.1 -> 2.1.1.1 with no 2.1.1), and out-of-order
section numbering in the document body (3.1, 3.2, 3.4, 3.3 appear in
exactly that order on the page) - all three were originally designed for
speculatively and turned out to genuinely occur in the real document.

All of the above are locked in as regression tests against the real file
in `tests/test_real_manual_regression.py` (6 tests), in addition to the
3 originally-required synthetic edge-case tests in `test_parser.py` and
the full end-to-end flow in `test_integration_smoke.py` - 11 tests total,
all passing.

**Still not fixed, and now confirmed real rather than hypothetical**:
the spec table (section 2.1) and error-code table (4.2) both extract as
flattened line-by-line text rather than structured rows - I saw this
directly in the dumped output and chose not to fix it given time, same
reasoning as before (would use `page.find_tables()`).



Initial cut only had the numbering-pattern heading detector - no font-size
fallback at all. Manually re-reading the assignment brief (which explicitly
warns "Do not assume the formatting is perfectly consistent"), I added the
font-size-based fallback specifically because a real technical manual
almost certainly has *some* headings that aren't part of the numbered
scheme (title page, "Important Safety Information" banners, appendix
labels) - even without seeing the file, betting against that would be
naive given the assignment's own framing. I also initially had `flatten()`
return `(node, parent)` tuples that were never actually consumed correctly
by the ingestion code - caught by writing the ingestion `_persist_tree`
function and realizing it didn't need that shape at all, since parent
linkage is already implicit in `node.children`. Simplified it away rather
than leaving dead/misleading code in.

## 4b. Real v1 -> v2 versioning validation (the part I originally flagged as untested)

**Update: both real files (`ct200_manual.pdf` and `ct200_manual_v2.pdf`)
became available and this has now actually been tested end to end** -
this was the single weakest part of the original submission (only v2 was
available, so the versioning/staleness flow had only been proven against
a synthetic stand-in). Running the real ingest -> re-ingest -> diff flow
against the two real files found and fixed one real bug, and confirmed
the core logic is correct on genuine content changes:

**Bug found: old/new version labels were backwards for added/removed
nodes.** When a node exists only in the newer version (e.g. the new
`5.3 Data Export` section, which genuinely doesn't exist in v1), the diff
endpoint originally labeled the query result using "whichever version you
queried from" as `old_version`, rather than actual chronological order -
so querying the v2-only node reported `old_version=2, new_version=1`,
backwards. Fixed in `app/routers/browse.py` to compare version numbers
directly rather than assuming the queried node is always the "old" side.
Locked in by `test_new_section_in_v2_detected_as_added_with_correct_version_order`.

**Confirmed correct on real content diffs between the two manuals:**
- `2.1.1.1 Battery Life`: v1 says 300 cycles / 15% threshold, v2 says 250
  cycles / 10% threshold (with wording explicitly noting the revision) -
  correctly flagged `changed=True`.
- `3.2 Cuff Inflation Sequence`: v1 says 40 mmHg increments, v2 says 30
  mmHg (also with explicit "reduced from... 40 mmHg" wording) - correctly
  flagged `changed=True`.
- `4.2 Error Codes`: v2 changes E3's auto-deflate timing (2s -> 1.5s) and
  adds a new E6 row (Bluetooth sync failure) not present in v1 - correctly
  flagged `changed=True`.
- `6.1 Cleaning Instructions`: byte-identical text in both files -
  correctly flagged `changed=False`, with matching hashes.
- `5.3 Data Export`: exists only in v2 - correctly detected as an added
  node (see bug above).

This is real, not synthetic, validation of the exact thing the assignment
is testing for (traceability surviving a document revision), and it's the
change I'm most confident actually matters, versus code that merely
imports cleanly.



## 5. How I identified failures

Originally, given the constraint of not having the real PDF, validation
here was: (a) writing unit tests encoding the assignment's own named edge
cases and running them for real, and (b) manually tracing through the
ingestion code path against constructed examples. That section explicitly
said the more important step - running against the real file and diffing
by eye - hadn't happened yet.

**It has now happened** (see sections 4a/4b above), and it's exactly what
found the three parser bugs and the version-label bug: the process was
literally "run `parse_pdf_to_tree` against the real PDF, print the tree,
and read it looking for anything that looks wrong" - which is how
"1. Normal: systolic < 120..." sitting at the top level jumped out
immediately as obviously incorrect. No amount of additional synthetic
unit tests would have caught that, because I hadn't thought to write a
synthetic case for "a numbered clinical classification list embedded in
body text" - it's not a case you'd invent, only one you'd find.

## 6. Version-matching strategy and where it breaks

Priority-ordered: (1) exact `section_path` match, since renumbering a
clause in a regulated document is disruptive and usually avoided even
when wording changes; (2) heading-text match among remaining unmatched
siblings as a fallback; (3) otherwise treat as new (old counterpart, if
any, is implicitly "removed" from the latest tree, though its row and
version are never deleted).

**Where it breaks:** a section that's both renumbered and reworded in the
same revision matches neither by path nor heading text, and gets
mis-classified as an unrelated add+delete rather than "this changed" -
losing exactly the traceability signal the whole staleness feature exists
to provide. This is a real, known limitation, not an edge case I'm
pretending doesn't exist. A fuzzy title-similarity (e.g. token overlap or
edit distance) fallback would catch more of this, at the cost of more
false-positive matches elsewhere - I chose not to add it given time, see
decision log Q2.

## 7. LLM prompt design + structured-output/retry strategy

Prompt asks explicitly for a JSON array only, with a fixed schema shown
inline, and instructs the model not to invent thresholds/behavior beyond
the provided text (to reduce hallucinated test cases that reference
numbers not in the manual). On parse/validation failure (bad JSON, wrong
field types, wrong array length), one corrective retry is issued that
includes the *specific* validation error and the model's own bad output -
this is meaningfully more likely to succeed than a blind identical retry.
If the retry also fails, the system stores an explicit `status:
"llm_failed"` record with the raw output preserved for debugging, rather
than either dropping the request silently or fabricating/truncating a
"good enough" partial result. See `app/llm.py` docstring for the full
reasoning and decision log Q1.

**Duplicate-submission policy:** resubmitting the same `selection_id`
without `force_regenerate=true` returns the existing generation rather
than calling the LLM again, since the underlying text is immutable
(selections are version-pinned) and there's no reason a second call
produces a "more correct" answer - it just costs money/time and creates
ambiguity about which output is authoritative. `force_regenerate=true`
creates a new record alongside the old one; nothing is ever overwritten,
since something downstream may already reference the earlier generation.

## 8. What I'd do differently with more time

1. ~~Get the real PDF, run the parser, and manually diff the output tree
   against the manual's actual TOC~~ - **done**, see section 4a/4b. Next
   would be running this against a third hypothetical version to see how
   well the matcher holds up over a longer version chain than just v1/v2.
2. Add a fuzzy-title-similarity fallback to the version matcher (rapidfuzz
   token-sort ratio) for the renumber+reword case described in section 6.
3. Add table detection (PyMuPDF's `page.find_tables()` API) instead of
   flattening the spec table and error-code table into prose body text -
   confirmed real and unaddressed against both actual manuals.
4. Add figure/caption extraction by spatial proximity.
5. Swap the JSON generation store for real MongoDB (or Postgres JSONB) if
   this needed to handle concurrent writes or scale past a single manual.

---

## Decision log

**1. What's the one part of this system most likely to silently give
wrong results without erroring? How would you catch it?**

The version matcher's heading-text fallback (pass 2 in `app/matcher.py`).
If a document has two *different* sections that happen to share heading
text under different parents in different versions (not the duplicate-
same-version case, which is handled, but a cross-version coincidence),
the matcher could confidently assign them the same `logical_id` and
report "unchanged" or "changed" against the wrong counterpart entirely -
no exception is raised, the output just looks plausible and is wrong. I'd
catch this by adding an assertion/warning when a heading-text match spans
a large `section_path` distance (e.g. matched under a completely different
top-level parent than before) and logging it for manual review rather than
silently accepting it.

**2. Where did you choose simplicity over correctness because of time,
and what would break first if this went to production as-is?**

The JSON-file generation store (`app/store.py`) uses a single global lock
and rewrites the entire file on every insert. It's correct for a single
process at this scale, and I said so explicitly in the file's own
docstring rather than pretending it scales. In production, this breaks
first under concurrent write load (multiple simultaneous generation
requests) - the whole-file rewrite is not a real transaction, and at
enough concurrent writers you'd eventually lose or corrupt records. It
would need to become an actual MongoDB/Postgres-backed store with
per-record writes before handling real traffic.

**3. Name one input (to your parser, your versioning matcher, or your LLM
call) that you did not handle, and what your system does when it sees
it.**

A body-text-only section that appears *before* the very first heading in
the document (e.g. cover page text, a preamble with no heading above it).
Looking at `parse_pdf_to_tree`, if `stack` is empty when a non-heading
line is encountered, that line is silently dropped (there's a comment
flagging this in the code) rather than attached anywhere or raising an
error. This is a real gap: front-matter content is currently lost, not
just mis-parented. The honest fix is a synthetic root "Front Matter" node
created before the walk begins, so pre-heading content always lands
somewhere real and inspectable instead of vanishing.
