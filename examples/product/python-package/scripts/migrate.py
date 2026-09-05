"""Idempotent demo SQLite migration; replace for the application database."""
import os
import sqlite3
with sqlite3.connect(os.environ.get("DEMO_DATABASE", "/tmp/generic-ci-demo.db")) as db:
    db.execute("CREATE TABLE IF NOT EXISTS example (id INTEGER PRIMARY KEY)")
