from sqlalchemy import text

from app.db.session import SessionLocal


def check_database() -> bool:
    with SessionLocal() as session:
        session.execute(text("SELECT 1"))
    return True
