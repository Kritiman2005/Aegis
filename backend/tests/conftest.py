import pytest


@pytest.fixture(scope="session", autouse=True)
def _init_real_db():
    """
    Several tests here instantiate a real ChatAgent (test_state_machine.py,
    parts of test_harness_fixes.py), which persists chat history through the
    app's real SessionLocal/aegis.db — not the isolated in-memory SQLite
    some other tests set up for themselves (see test_crud_dedup.py). On a
    fresh checkout with no prior app run (a clean CI job, or a brand new
    clone) that database file has no tables yet, failing every one of those
    tests with "no such table: chat_messages" before any real assertion
    even runs — confirmed by reproducing it locally with AEGIS_DATA_DIR
    pointed at an empty directory. Ensuring the schema exists once, up
    front, for the whole test session fixes this without changing what any
    individual test actually verifies.
    """
    from app.db.database import init_db
    init_db()
