"""Demo SQLite compatibility check; replace for the application database."""
import os
import sqlite3
with sqlite3.connect(os.environ.get("DEMO_DATABASE", "/tmp/generic-ci-demo.db")) as db:
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
