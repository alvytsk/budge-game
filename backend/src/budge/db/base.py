from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for every table in this schema.

    Alembic's autogenerate compares `Base.metadata` against the live
    database, so every model must reach it by importing this class.
    """
