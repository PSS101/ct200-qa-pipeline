from datetime import datetime
from pydantic import BaseModel, Field


class NodeOut(BaseModel):
    id: str
    parent_id: str | None
    logical_id: str | None
    heading: str
    level: int
    order_index: int
    section_path: str | None
    content_hash: str
    body_text: str | None = None  # omitted (None) in list views, present in detail view

    class Config:
        from_attributes = True


class NodeListItem(BaseModel):
    id: str
    heading: str
    level: int
    order_index: int
    section_path: str | None
    has_children: bool

    class Config:
        from_attributes = True


class DiffSummary(BaseModel):
    node_logical_id: str
    changed: bool
    old_version: int | None
    new_version: int | None
    old_hash: str | None
    new_hash: str | None
    old_excerpt: str | None
    new_excerpt: str | None


class SelectionCreate(BaseModel):
    name: str
    node_ids: list[str] = Field(min_length=1)


class SelectionOut(BaseModel):
    id: str
    name: str
    node_ids: list[str]


class TestCase(BaseModel):
    title: str
    preconditions: str
    steps: list[str]
    expected_result: str
    priority: str  # "high" | "medium" | "low"


class GenerationRequest(BaseModel):
    selection_id: str
    force_regenerate: bool = False


class GenerationOut(BaseModel):
    id: str
    selection_id: str
    status: str  # "ok" | "llm_failed"
    test_cases: list[TestCase]
    source_node_hashes: dict[str, str]  # node_id -> content_hash at generation time
    created_at: datetime
    is_stale: bool | None = None
    stale_nodes: list[str] | None = None
