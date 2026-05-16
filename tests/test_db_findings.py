import json
from app.db import (
    connect, init_schema, upsert_repo, create_evaluation,
    insert_finding, findings_for_evaluation, copy_unchanged_findings,
)


def _conn(tmp_path):
    c = connect(str(tmp_path / "r.sqlite3"))
    init_schema(c)
    return c


def test_insert_and_read_findings(tmp_path):
    c = _conn(tmp_path)
    rid = upsert_repo(c, "u", "p", "main")
    eid = create_evaluation(c, rid, "sha1", None, "full")
    insert_finding(c, rid, eid, "a.py", "h1", "deadcode",
                    [{"severity": "low", "msg": "x"}], 80, True, "ok")
    rows = findings_for_evaluation(c, eid)
    assert len(rows) == 1
    assert json.loads(rows[0]["findings_json"]) == [{"severity": "low", "msg": "x"}]
    assert rows[0]["criterion_score"] == 80


def test_copy_unchanged_findings_only_for_listed_files(tmp_path):
    c = _conn(tmp_path)
    rid = upsert_repo(c, "u", "p", "main")
    old = create_evaluation(c, rid, "sha1", None, "full")
    insert_finding(c, rid, old, "keep.py", "h1", "deadcode", [], 90, True, "")
    insert_finding(c, rid, old, "changed.py", "h2", "deadcode", [], 50, True, "")
    new = create_evaluation(c, rid, "sha2", old, "incremental")
    n = copy_unchanged_findings(c, rid, old, new, ["keep.py"])
    rows = findings_for_evaluation(c, new)
    assert n == 1
    assert {r["file_path"] for r in rows} == {"keep.py"}
    assert rows[0]["evaluation_id"] == new
