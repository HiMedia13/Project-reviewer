from fastapi.testclient import TestClient
from app import db
from app.server import create_app


def _seed(tmp_path):
    dbp = str(tmp_path / "reviewer.sqlite3")
    c = db.connect(dbp)
    db.init_schema(c)
    rid = db.upsert_repo(c, "https://x/y.git", "/p", "main")
    e = db.create_evaluation(c, rid, "sha1", None, "full")
    db.finalize_evaluation(
        c, e, {"score": 80, "criteria": {"library": 80, "eng": None,
        "deadcode": None, "techstack": None}},
        {"input_tokens": 5, "output_tokens": 2, "cost_usd": 0.01,
         "run_url": None}, 3.0,
    )
    return dbp


def test_index_lists_repos(tmp_path):
    dbp = _seed(tmp_path)
    client = TestClient(create_app(dbp))
    r = client.get("/")
    assert r.status_code == 200
    assert "https://x/y.git" in r.text


def test_repo_history_page(tmp_path):
    dbp = _seed(tmp_path)
    client = TestClient(create_app(dbp))
    r = client.get("/repo/1")
    assert r.status_code == 200
    assert "80" in r.text


def test_missing_repo_returns_404(tmp_path):
    dbp = _seed(tmp_path)
    client = TestClient(create_app(dbp))
    r = client.get("/repo/999")
    assert r.status_code == 404


def test_index_escapes_hostile_remote_url(tmp_path):
    # remote_url is user-supplied (CLI arg); must be HTML-escaped.
    dbp = str(tmp_path / "reviewer.sqlite3")
    c = db.connect(dbp)
    db.init_schema(c)
    db.upsert_repo(c, "https://x/<script>alert(1)</script>", "/p", "main")
    client = TestClient(create_app(dbp))
    r = client.get("/")
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text
