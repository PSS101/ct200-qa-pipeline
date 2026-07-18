from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.schemas import NodeListItem, NodeOut, DiffSummary

router = APIRouter(prefix="/documents/{document_id}", tags=["browse"])


def _resolve_version(db: Session, document_id: str, version: int | None) -> models.DocumentVersion:
    q = db.query(models.DocumentVersion).filter_by(document_id=document_id)
    if version is not None:
        v = q.filter_by(version_number=version).first()
    else:
        v = q.order_by(models.DocumentVersion.version_number.desc()).first()
    if not v:
        raise HTTPException(404, "document/version not found")
    return v


@router.get("/sections", response_model=list[NodeListItem])
def list_top_level_sections(
    document_id: str,
    version: int | None = Query(None, description="defaults to latest"),
    db: Session = Depends(get_db),
):
    v = _resolve_version(db, document_id, version)
    nodes = db.query(models.Node).filter_by(version_id=v.id, parent_id=None).order_by(models.Node.order_index).all()
    return [
        NodeListItem(
            id=n.id, heading=n.heading, level=n.level, order_index=n.order_index,
            section_path=n.section_path, has_children=bool(n.children),
        )
        for n in nodes
    ]


@router.get("/nodes/{node_id}", response_model=NodeOut)
def get_node(document_id: str, node_id: str, db: Session = Depends(get_db)):
    node = db.get(models.Node, node_id)
    if not node or node.version.document_id != document_id:
        raise HTTPException(404, "node not found")
    return NodeOut(
        id=node.id, parent_id=node.parent_id, logical_id=node.logical_id,
        heading=node.heading, level=node.level, order_index=node.order_index,
        section_path=node.section_path, content_hash=node.content_hash,
        body_text=node.body_text,
    )


@router.get("/search", response_model=list[NodeListItem])
def search_nodes(
    document_id: str,
    q: str,
    version: int | None = Query(None),
    db: Session = Depends(get_db),
):
    v = _resolve_version(db, document_id, version)
    like = f"%{q}%"
    nodes = (
        db.query(models.Node)
        .filter(models.Node.version_id == v.id)
        .filter((models.Node.heading.ilike(like)) | (models.Node.body_text.ilike(like)))
        .order_by(models.Node.order_index)
        .all()
    )
    return [
        NodeListItem(
            id=n.id, heading=n.heading, level=n.level, order_index=n.order_index,
            section_path=n.section_path, has_children=bool(n.children),
        )
        for n in nodes
    ]


@router.get("/nodes/{node_id}/diff", response_model=DiffSummary)
def node_diff(document_id: str, node_id: str, db: Session = Depends(get_db)):
    """
    Given a node in ANY version, find the node with the same logical_id in
    the adjacent version (prior if this is the latest, else next) and
    report whether content_hash differs.

    NOTE: this only compares against the immediately adjacent version, not
    "has this ever changed across all versions" - see APPROACH.md for why
    that's a reasonable scope boundary for now (v1/v2 is all the
    assignment requires, but a 3+ version chain would need this endpoint
    extended to walk the whole logical_id lineage).
    """
    node = db.get(models.Node, node_id)
    if not node or node.version.document_id != document_id:
        raise HTTPException(404, "node not found")

    all_versions = (
        db.query(models.DocumentVersion)
        .filter_by(document_id=document_id)
        .order_by(models.DocumentVersion.version_number)
        .all()
    )
    idx = next(i for i, v in enumerate(all_versions) if v.id == node.version_id)
    other_version = all_versions[idx + 1] if idx + 1 < len(all_versions) else (
        all_versions[idx - 1] if idx > 0 else None
    )

    if other_version is None or not node.logical_id:
        return DiffSummary(
            node_logical_id=node.logical_id or node.id, changed=False,
            old_version=node.version.version_number, new_version=None,
            old_hash=node.content_hash, new_hash=None,
            old_excerpt=node.body_text[:200], new_excerpt=None,
        )

    counterpart = (
        db.query(models.Node)
        .filter_by(version_id=other_version.id, logical_id=node.logical_id)
        .first()
    )
    if not counterpart:
        # logical_id has no match in the other version -> node was added or
        # removed. Label old/new by actual chronology (lower version
        # number = older), not by which side the query happened to be on -
        # a real bug found while testing against the real v1/v2 manuals:
        # querying a v2-only node originally reported old_version=2,
        # new_version=1, which is backwards.
        this_v = node.version.version_number
        other_v = other_version.version_number
        if this_v < other_v:
            # queried node is the older side; it's absent in other_v (removed)
            return DiffSummary(
                node_logical_id=node.logical_id, changed=True,
                old_version=this_v, new_version=other_v,
                old_hash=node.content_hash, new_hash=None,
                old_excerpt=node.body_text[:200], new_excerpt=None,
            )
        else:
            # queried node is the newer side; it's absent in other_v (added)
            return DiffSummary(
                node_logical_id=node.logical_id, changed=True,
                old_version=other_v, new_version=this_v,
                old_hash=None, new_hash=node.content_hash,
                old_excerpt=None, new_excerpt=node.body_text[:200],
            )

    older, newer = sorted(
        [node, counterpart], key=lambda n: n.version.version_number
    )
    return DiffSummary(
        node_logical_id=node.logical_id,
        changed=older.content_hash != newer.content_hash,
        old_version=older.version.version_number, new_version=newer.version.version_number,
        old_hash=older.content_hash, new_hash=newer.content_hash,
        old_excerpt=older.body_text[:200], new_excerpt=newer.body_text[:200],
    )
