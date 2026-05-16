import sqlite3

import pytest

from app.db import connect, init_schema


def test_init_schema_creates_tables(tmp_path):
    db_path = tmp_path / "r.sqlite3"
    conn = connect(str(db_path))
    init_schema(conn)
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"repos", "evaluations", "file_findings"} <= names


def test_init_schema_is_idempotent(tmp_path):
    conn = connect(str(tmp_path / "r.sqlite3"))
    init_schema(conn)
    init_schema(conn)  # must not raise


def test_foreign_keys_are_enforced(tmp_path):
    conn = connect(str(tmp_path / "r.sqlite3"))
    init_schema(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO file_findings (repo_id, evaluation_id, file_path, "
            "file_hash, criterion, findings_json) VALUES (?,?,?,?,?,?)",
            (999, 999, "a.py", "h", "deadcode", "[]"),
        )
        conn.commit()
