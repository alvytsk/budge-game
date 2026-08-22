"""Where the test suite's database lives.

Owned here rather than by `Settings`: `Settings.database_url` has no default
precisely so an unset variable fails loudly. The test suite's own default
database is a test-suite concern, not something the production config type
should carry. It lives in an importable module rather than in a conftest so
a test file can import it without depending on how pytest happens to have
named the conftest's package.
"""

import os

TEST_DATABASE_URL = "postgresql+asyncpg://podvinsya:podvinsya@127.0.0.1:5434/podvinsya_test"

DATABASE_URL = os.environ.get("PODVINSYA_TEST_DATABASE_URL", TEST_DATABASE_URL)
