"""
SQLAlchemy engine/session wiring.

Using SQLite for the relational side (document tree, versions, selections)
per the assignment's suggested stack. Nothing fancy here on purpose -
this is boring infra, not where the interesting design decisions live.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = "sqlite:///./ct200.db"

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
