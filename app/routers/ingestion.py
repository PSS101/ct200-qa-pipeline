from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.parser import parse_pdf_to_tree, flatten, ParsedNode
from app.hashing import content_hash
from app.matcher import match_versions

router = APIRouter(prefix="/documents", tags=["ingestion"])


def _persist_tree(db: Session, version: models.DocumentVersion, parsed_roots: list[ParsedNode]):
    """Insert ParsedNode tree as Node rows, wiring parent_id. logical_id left
    null here - set afterward by the matcher (or trivially self-assigned
    for a brand new document with no prior version)."""
    id_map: dict[int, models.Node] = {}  # id(parsed_node) -> orm node

    def insert(node: ParsedNode, parent_orm: models.Node | None):
        orm_node = models.Node(
            version_id=version.id,
            parent_id=parent_orm.id if parent_orm else None,
            heading=node.heading,
            level=node.level,
            order_index=node.order_index,
            body_text=node.body_text,
            content_hash=content_hash(node.body_text),
            section_path=node.section_path,
        )
        db.add(orm_node)
        db.flush()  # get orm_node.id without committing
        id_map[id(node)] = orm_node
        for child in node.children:
            insert(child, orm_node)

    for root in parsed_roots:
        insert(root, None)

    return list(id_map.values())


@router.post("/{document_id}/ingest")
def ingest_version(
    document_id: str,
    pdf_path: str = Form(..., description="Server-side path to the PDF, e.g. data/ct200_manual.pdf"),
    db: Session = Depends(get_db),
):
    doc = db.get(models.Document, document_id)
    if not doc:
        raise HTTPException(404, "document not found")

    prior_version = (
        db.query(models.DocumentVersion)
        .filter_by(document_id=document_id)
        .order_by(models.DocumentVersion.version_number.desc())
        .first()
    )
    next_version_number = (prior_version.version_number + 1) if prior_version else 1

    new_version = models.DocumentVersion(
        document_id=document_id,
        version_number=next_version_number,
        source_file=pdf_path,
        is_reingest=prior_version is not None,
    )
    db.add(new_version)
    db.flush()

    parsed_roots = parse_pdf_to_tree(pdf_path)
    new_nodes = _persist_tree(db, new_version, parsed_roots)

    if prior_version is None:
        # first ingestion: every node is its own new logical node
        for n in new_nodes:
            n.logical_id = n.id
    else:
        old_nodes = db.query(models.Node).filter_by(version_id=prior_version.id).all()
        matches = match_versions(old_nodes, new_nodes)
        by_id = {n.id: n for n in new_nodes}
        for m in matches:
            by_id[m.new_node_id].logical_id = m.logical_id

    db.commit()
    return {
        "document_id": document_id,
        "version_id": new_version.id,
        "version_number": new_version.version_number,
        "node_count": len(new_nodes),
        "is_reingest": new_version.is_reingest,
    }


@router.post("")
def create_document(name: str = Form(...), db: Session = Depends(get_db)):
    doc = models.Document(name=name)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return {"id": doc.id, "name": doc.name}
