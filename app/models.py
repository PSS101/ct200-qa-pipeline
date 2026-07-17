"""
Data model notes (see APPROACH.md for the full reasoning):

- Document: a logical document (e.g. "CT-200 Manual"), version-agnostic.
- DocumentVersion: one ingestion run. v1 and v2 are two rows here, both
  pointing at the same Document. Ingesting v2 never touches v1's rows.
- Node: one heading/section in the tree, scoped to a single DocumentVersion.
  A node belongs to exactly one version - it is NOT reused across versions.
- logical_id: a stable id shared by nodes across versions that we believe
  are "the same section". This is what makes cross-version tracking work -
  a Node's primary key changes every re-ingestion, but its logical_id
  persists if our matcher thinks it's the same node. See APPROACH.md for
  the matching strategy and where it breaks.
- content_hash: sha256 of normalized body_text. Used for staleness checks -
  if two Nodes share a logical_id but have different content_hash, the
  section changed between versions.
- Selection / SelectionNode: selections pin to a specific Node row (i.e. a
  specific node+version pair), not to a logical_id. That's what "version-
  pinned" means here - re-ingesting the doc cannot silently change what a
  selection resolves to.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Integer, Text, ForeignKey, DateTime, Boolean
)
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class Document(Base):
    __tablename__ = "documents"

    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    versions = relationship("DocumentVersion", back_populates="document")


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id = Column(String, primary_key=True, default=gen_uuid)
    document_id = Column(String, ForeignKey("documents.id"), nullable=False)
    version_number = Column(Integer, nullable=False)
    source_file = Column(String, nullable=False)
    ingested_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    # was this ingest actually diffed against a prior version?
    is_reingest = Column(Boolean, default=False)

    document = relationship("Document", back_populates="versions")
    nodes = relationship("Node", back_populates="version")


class Node(Base):
    __tablename__ = "nodes"

    id = Column(String, primary_key=True, default=gen_uuid)
    version_id = Column(String, ForeignKey("document_versions.id"), nullable=False)
    parent_id = Column(String, ForeignKey("nodes.id"), nullable=True)

    # stable across versions if the matcher believes it's the same section.
    # null only transiently during ingestion before matching runs.
    logical_id = Column(String, nullable=True, index=True)

    heading = Column(String, nullable=False)
    level = Column(Integer, nullable=False)  # 1 = top-level, 2 = subsection, ...
    order_index = Column(Integer, nullable=False)  # sibling order
    body_text = Column(Text, default="")
    content_hash = Column(String, nullable=False)

    # path like "3.2.1" reconstructed from doc numbering, when present.
    # nullable because not every section in CT-200 is numbered (see APPROACH.md).
    section_path = Column(String, nullable=True)

    version = relationship("DocumentVersion", back_populates="nodes")
    children = relationship("Node", backref="parent", remote_side=[id])


class Selection(Base):
    __tablename__ = "selections"

    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    node_links = relationship("SelectionNode", back_populates="selection")


class SelectionNode(Base):
    """
    Join table. Pins a selection to a specific Node row - and since a Node
    belongs to exactly one DocumentVersion, this is inherently a
    (node, version) pin, satisfying the "version-pinned selection" requirement.
    """
    __tablename__ = "selection_nodes"

    id = Column(String, primary_key=True, default=gen_uuid)
    selection_id = Column(String, ForeignKey("selections.id"), nullable=False)
    node_id = Column(String, ForeignKey("nodes.id"), nullable=False)

    selection = relationship("Selection", back_populates="node_links")
    node = relationship("Node")
