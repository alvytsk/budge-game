"""Fixtures for the runtime suite.

The database-backed tests here reuse `tests/db`'s engine, session and
schema fixtures rather than building a second set: importing them into this
conftest's namespace is enough for pytest to find them, the same way any
conftest re-exporting a fixture works. Modules that touch the database
carry `pytest.mark.integration`; modules that do not — the clock and the
ports, for instance — carry no mark and run in the fast lane.
"""

from db.conftest import clean_db, engine, migrated_schema, sessions

__all__ = ["clean_db", "engine", "migrated_schema", "sessions"]
