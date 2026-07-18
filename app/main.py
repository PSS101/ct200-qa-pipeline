from fastapi import FastAPI

from app.database import Base, engine
from app.routers import ingestion, browse, selection, generation

# dev convenience: create tables on startup instead of using a migration
# tool (Alembic). Justified for an internship-scope, single-environment
# project - see APPROACH.md. Would NOT be acceptable for production.
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="CT-200 Document Intelligence API",
    description=(
        "Ingests the CT-200 manual into a versioned, browsable section tree, "
        "and generates QA test-case ideas from user-selected sections while "
        "tracking staleness across document revisions."
    ),
    version="0.1.0",
)

app.include_router(ingestion.router)
app.include_router(browse.router)
app.include_router(selection.router)
app.include_router(generation.router)


@app.get("/health")
def health():
    return {"status": "ok"}
