# CT-200 Document Intelligence API

Turns the CardioTrack CT-200 manual into a versioned, browsable section
tree, and generates QA test-case ideas from user-selected sections while
tracking staleness across document revisions.

**Status note:** both real manuals are now in `data/` and the full
ingest -> re-ingest -> diff -> staleness flow has been run end-to-end
against them (not just synthetic stand-ins). This found and fixed a real
version-labeling bug (old/new version numbers were swapped for
added/removed nodes) in addition to the 3 parser bugs found earlier -
see `APPROACH.md` sections 4a and 4b, and
`tests/test_real_versioning_regression.py` for the regression tests.
15 tests total, all passing.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Set your LLM API key (Groq, free tier):

```bash
export GROQ_API_KEY=your_key_here
```

Put the manuals here:

```
data/ct200_manual.pdf
data/ct200_manual_v2.pdf
```

## Run

```bash
uvicorn app.main:app --reload
```

API docs (interactive): http://localhost:8000/docs

## Run tests

```bash
pytest tests/ -v
```

## End-to-end flow (v1 -> v2 reingestion + staleness)

```bash
# 1. Create a document
curl -X POST localhost:8000/documents -F "name=CT-200 Manual"
# -> {"id": "<DOC_ID>", ...}

# 2. Ingest v1
curl -X POST localhost:8000/documents/<DOC_ID>/ingest -F "pdf_path=data/ct200_manual.pdf"

# 3. Browse the tree
curl localhost:8000/documents/<DOC_ID>/sections
curl localhost:8000/documents/<DOC_ID>/nodes/<NODE_ID>

# 4. Create a version-pinned selection
curl -X POST localhost:8000/selections \
  -H "Content-Type: application/json" \
  -d '{"name": "cuff pressure tests", "node_ids": ["<NODE_ID>"]}'

# 5. Generate QA test cases from the selection
curl -X POST localhost:8000/generations \
  -H "Content-Type: application/json" \
  -d '{"selection_id": "<SELECTION_ID>"}'

# 6. Re-ingest v2 - THIS is the versioning flow to specifically demo.
#    v1's nodes are untouched; v2 gets its own node rows; matching
#    nodes across versions share a logical_id.
curl -X POST localhost:8000/documents/<DOC_ID>/ingest -F "pdf_path=data/ct200_manual_v2.pdf"

# 7. Ask whether a specific node changed between v1 and v2
curl localhost:8000/documents/<DOC_ID>/nodes/<NODE_ID>/diff

# 8. Retrieve the earlier generation again - staleness is computed live
#    at retrieval time by comparing the hash captured at generation time
#    against the node's CURRENT hash under its logical_id.
curl localhost:8000/generations/by-selection/<SELECTION_ID>
```

## Storage

- SQLite (`ct200.db`) for the document tree, versions, and selections
  (SQLAlchemy models in `app/models.py`).
- A flat JSON file (`generations_store.json`) for LLM-generated test
  cases, in place of the suggested MongoDB - justified in `APPROACH.md`.

## What's NOT implemented / explicitly out of scope

- Auth, a UI, and a generic arbitrary-PDF parser - all explicitly out of
  scope per the assignment.
- Auto-regeneration of stale test cases - also explicitly out of scope;
  staleness is surfaced, not auto-fixed.
- Table structure extraction (spec table, error-code table extract as
  flattened text, not rows/columns) and figure/caption extraction - known
  gaps, confirmed real against both actual manuals, not fixed given time.
  See `APPROACH.md`.
- The version matcher has only been tested across one revision (v1->v2).
  A longer version chain (v1->v2->v3...), or a section that's both
  renumbered and reworded in the same revision, would likely expose the
  matcher's known heading-text-fallback limitation - see `APPROACH.md`
  section 6.
