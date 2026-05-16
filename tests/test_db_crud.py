from app.db import (
    connect,
    init_schema,
    upsert_repo,
    get_repo_by_url,
    create_evaluation,
    latest_evaluation,
)


def _conn(tmp_path):
    c = connect(str(tmp_path / "r.sqlite3"))
    init_schema(c)
    return c


def test_upsert_repo_is_idempotent(tmp_path):
    c = _conn(tmp_path)
    rid = upsert_repo(c, "https://x/y.git", "/tmp/y", "main")
    rid2 = upsert_repo(c, "https://x/y.git", "/tmp/y", "main")
    assert rid == rid2
    assert get_repo_by_url(c, "https://x/y.git")["id"] == rid
    count = c.execute("SELECT COUNT(*) AS n FROM repos").fetchone()["n"]
    assert count == 1


def test_latest_evaluation_returns_most_recent(tmp_path):
    c = _conn(tmp_path)
    rid = upsert_repo(c, "u", "p", "main")
    e1 = create_evaluation(c, rid, "sha1", None, "full")
    e2 = create_evaluation(c, rid, "sha2", e1, "incremental")
    assert latest_evaluation(c, rid)["id"] == e2


def test_latest_evaluation_none_when_empty(tmp_path):
    c = _conn(tmp_path)
    rid = upsert_repo(c, "u", "p", "main")
    assert latest_evaluation(c, rid) is None
