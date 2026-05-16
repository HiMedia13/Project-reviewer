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
