"""
End-to-end integration smoke test.

Builds a small SYNTHETIC PDF (not the real CT-200 manual, which wasn't
available in this environment) with fitz, then drives the actual FastAPI
app through the full required flow: ingest v1 -> browse -> select ->
generate (LLM call monkeypatched, no real API key needed to run this) ->
re-ingest v2 -> confirm node-level diff and generation-level staleness
both correctly detect the changed section.

This does NOT prove the parser handles the real CT-200 manual's actual
quirks - it proves the plumbing (persistence, matching, staleness,
generation storage) is wired correctly end to end. See APPROACH.md.
"""
import os
import tempfile

import fitz
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("GENERATIONS_STORE_PATH", tempfile.mktemp(suffix=".json"))


def _make_pdf(path: str, warning_text: str):
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    page.insert_text((72, y), "1 Setup", fontsize=18, fontname="helv"); y += 24
    page.insert_text((72, y), "Connect the cuff to the unit before use.", fontsize=11); y += 20
    page.insert_text((72, y), "1.1 Warnings", fontsize=14, fontname="helv"); y += 24
    page.insert_text((72, y), warning_text, fontsize=11); y += 20
    doc.save(path)
    doc.close()


@pytest.fixture
def client(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL_OVERRIDE", f"sqlite:///{db_path}")

    # avoid needing a real Groq key / network call in this smoke test -
    # the LLM wrapper's retry/validation logic is unit-testable separately
    # from whether a real network call succeeds.
    def fake_call_groq(prompt):
        return (
            '[{"title": "Overinflation cutoff", '
            '"preconditions": "Device powered on, cuff attached", '
            '"steps": ["Inflate cuff past stated safe limit"], '
            '"expected_result": "Device stops inflation and displays a warning", '
            '"priority": "high"}]'
        )

    import app.llm as llm_module
    monkeypatch.setattr(llm_module, "_call_groq", fake_call_groq)

    from app.main import app
    return TestClient(app)


def test_full_ingest_select_generate_reingest_staleness_flow(client, tmp_path):
    v1_path = str(tmp_path / "v1.pdf")
    v2_path = str(tmp_path / "v2.pdf")
    _make_pdf(v1_path, "Do not overinflate the cuff above 300 mmHg.")
    _make_pdf(v2_path, "Do not overinflate the cuff above 280 mmHg.")  # changed threshold

    doc_resp = client.post("/documents", data={"name": "CT-200 Smoke Test"})
    assert doc_resp.status_code == 200
    document_id = doc_resp.json()["id"]

    ingest1 = client.post(f"/documents/{document_id}/ingest", data={"pdf_path": v1_path})
    assert ingest1.status_code == 200, ingest1.text
    assert ingest1.json()["version_number"] == 1

    sections = client.get(f"/documents/{document_id}/sections").json()
    assert len(sections) == 1 and sections[0]["heading"] == "1 Setup"
    setup_id = sections[0]["id"]

    setup_detail = client.get(f"/documents/{document_id}/nodes/{setup_id}").json()
    assert setup_detail["body_text"]

    # the "Warnings" subsection is where the actual mmHg threshold text
    # lives (and where v1->v2 changes it) - find it via search rather than
    # the top-level node, since content_hash is per-node, not aggregated
    # up the tree (see APPROACH.md: staleness granularity is per-node).
    search_results = client.get(f"/documents/{document_id}/search", params={"q": "Warnings"}).json()
    assert len(search_results) == 1
    warnings_id = search_results[0]["id"]

    sel_resp = client.post("/selections", json={"name": "cuff test", "node_ids": [warnings_id]})
    assert sel_resp.status_code == 200
    selection_id = sel_resp.json()["id"]

    gen_resp = client.post("/generations", json={"selection_id": selection_id})
    assert gen_resp.status_code == 200
    gen = gen_resp.json()
    assert gen["status"] == "ok"
    assert 1 <= len(gen["test_cases"]) <= 5

    # resubmitting without force_regenerate should return the SAME generation
    gen_resp2 = client.post("/generations", json={"selection_id": selection_id})
    assert gen_resp2.json()["id"] == gen["id"]

    # re-ingest v2 with a changed warning threshold
    ingest2 = client.post(f"/documents/{document_id}/ingest", data={"pdf_path": v2_path})
    assert ingest2.status_code == 200
    assert ingest2.json()["version_number"] == 2
    assert ingest2.json()["is_reingest"] is True

    # the OLD "Warnings" node (from v1) should now report changed=True against v2
    diff = client.get(f"/documents/{document_id}/nodes/{warnings_id}/diff").json()
    assert diff["changed"] is True

    # and the previously generated test case should now show as stale
    stale_check = client.get(f"/generations/by-selection/{selection_id}").json()
    assert len(stale_check) == 1
    assert stale_check[0]["is_stale"] is True
    assert warnings_id in stale_check[0]["stale_nodes"]
