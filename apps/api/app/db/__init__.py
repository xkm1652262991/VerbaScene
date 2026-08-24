"""Database package."""

from app.db.base import Base

__all__ = ["Base", "SessionLocal", "engine", "get_db"]


def __getattr__(name: str):
    if name not in {"SessionLocal", "engine", "get_db"}:
        raise AttributeError(name)
    from app.db import session

    return getattr(session, name)
