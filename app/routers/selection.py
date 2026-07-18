from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.schemas import SelectionCreate, SelectionOut

router = APIRouter(prefix="/selections", tags=["selection"])


@router.post("", response_model=SelectionOut)
def create_selection(payload: SelectionCreate, db: Session = Depends(get_db)):
    nodes = db.query(models.Node).filter(models.Node.id.in_(payload.node_ids)).all()
    if len(nodes) != len(set(payload.node_ids)):
        found = {n.id for n in nodes}
        missing = set(payload.node_ids) - found
        raise HTTPException(400, f"unknown node ids: {missing}")

    selection = models.Selection(name=payload.name)
    db.add(selection)
    db.flush()
    for node in nodes:
        db.add(models.SelectionNode(selection_id=selection.id, node_id=node.id))
    db.commit()

    return SelectionOut(id=selection.id, name=selection.name, node_ids=payload.node_ids)


@router.get("/{selection_id}", response_model=SelectionOut)
def get_selection(selection_id: str, db: Session = Depends(get_db)):
    selection = db.get(models.Selection, selection_id)
    if not selection:
        raise HTTPException(404, "selection not found")
    node_ids = [link.node_id for link in selection.node_links]
    return SelectionOut(id=selection.id, name=selection.name, node_ids=node_ids)
