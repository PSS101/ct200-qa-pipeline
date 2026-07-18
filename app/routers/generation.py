from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.schemas import GenerationRequest, GenerationOut
from app.llm import generate_test_cases
from app.store import store

router = APIRouter(tags=["generation"])


def _reconstruct_text(db: Session, selection: models.Selection) -> tuple[str, dict[str, str]]:
    parts = []
    hashes = {}
    for link in selection.node_links:
        node = link.node
        parts.append(f"### {node.heading}\n{node.body_text}")
        hashes[node.id] = node.content_hash
    return "\n\n".join(parts), hashes


@router.post("/generations", response_model=GenerationOut)
def generate(payload: GenerationRequest, db: Session = Depends(get_db)):
    """
    DUPLICATE-SUBMISSION POLICY: submitting the same selection_id again
    without force_regenerate=True returns the most recent EXISTING
    generation for that selection instead of calling the LLM again.
    Rationale: selections are version-pinned (immutable underlying text),
    so re-running the same selection through the same prompt has no
    reason to produce a "more correct" answer - it just burns LLM calls
    and creates ambiguity about which output is authoritative. If the
    caller genuinely wants a fresh sample (e.g. they didn't like the
    output), force_regenerate=True creates a NEW generation record
    alongside the old one - old ones are never overwritten, since
    something may already reference them downstream.
    """
    selection = db.get(models.Selection, payload.selection_id)
    if not selection:
        raise HTTPException(404, "selection not found")

    if not payload.force_regenerate:
        existing = store.find_by_selection(payload.selection_id)
        if existing:
            latest = sorted(existing, key=lambda r: r["created_at"])[-1]
            return GenerationOut(**latest)

    source_text, hashes = _reconstruct_text(db, selection)
    if not source_text.strip():
        raise HTTPException(400, "selection resolves to empty text")

    result = generate_test_cases(source_text)

    record = {
        "selection_id": selection.id,
        "status": result.status,
        "test_cases": [tc.model_dump() for tc in result.test_cases],
        "source_node_hashes": hashes,
    }
    saved = store.insert(record)
    return GenerationOut(**saved)


def _compute_staleness(db: Session, source_node_hashes: dict[str, str]) -> tuple[bool, list[str]]:
    stale_nodes = []
    for node_id, hash_at_generation in source_node_hashes.items():
        node = db.get(models.Node, node_id)
        if not node:
            # node row itself always exists (versions are never deleted),
            # so this branch is defensive only.
            stale_nodes.append(node_id)
            continue
        if not node.logical_id:
            continue
        latest_version = (
            db.query(models.DocumentVersion)
            .filter_by(document_id=node.version.document_id)
            .order_by(models.DocumentVersion.version_number.desc())
            .first()
        )
        current = (
            db.query(models.Node)
            .filter_by(version_id=latest_version.id, logical_id=node.logical_id)
            .first()
        )
        if current is None or current.content_hash != hash_at_generation:
            stale_nodes.append(node_id)
    return bool(stale_nodes), stale_nodes


@router.get("/generations/by-selection/{selection_id}", response_model=list[GenerationOut])
def get_generations_by_selection(selection_id: str, db: Session = Depends(get_db)):
    records = store.find_by_selection(selection_id)
    out = []
    for r in records:
        is_stale, stale_nodes = _compute_staleness(db, r["source_node_hashes"])
        out.append(GenerationOut(**r, is_stale=is_stale, stale_nodes=stale_nodes))
    return out


@router.get("/generations/by-node/{node_id}", response_model=list[GenerationOut])
def get_generations_by_node(node_id: str, db: Session = Depends(get_db)):
    records = store.find_by_node(node_id)
    out = []
    for r in records:
        is_stale, stale_nodes = _compute_staleness(db, r["source_node_hashes"])
        out.append(GenerationOut(**r, is_stale=is_stale, stale_nodes=stale_nodes))
    return out
