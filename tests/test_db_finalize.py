import json
from app.db import (
    connect, init_schema, upsert_repo, create_evaluation,
    finalize_evaluation, latest_evaluation,
)


def test_finalize_evaluation_writes_overall_and_cost(tmp_path):
    c = connect(str(tmp_path / "r.sqlite3"))
    init_schema(c)
    rid = upsert_repo(c, "u", "p", "main")
    eid = create_evaluation(c, rid, "sha1", None, "full")
    finalize_evaluation(
        c, eid,
        overall={"score": 77, "criteria": {"deadcode": 80}},
        cost={"input_tokens": 100, "output_tokens": 20,
              "cost_usd": 0.01, "run_url": "http://ls/x"},
        duration_sec=12.5,
    )
    row = latest_evaluation(c, rid)
    assert json.loads(row["overall_json"])["score"] == 77
    assert row["total_input_tokens"] == 100
    assert row["total_output_tokens"] == 20
    assert row["total_cost_usd"] == 0.01
    assert row["langsmith_run_url"] == "http://ls/x"
    assert row["duration_sec"] == 12.5


def test_finalize_evaluation_handles_empty_cost(tmp_path):
    c = connect(str(tmp_path / "r.sqlite3"))
    init_schema(c)
    rid = upsert_repo(c, "u", "p", "main")
    eid = create_evaluation(c, rid, "sha1", None, "full")
    finalize_evaluation(c, eid, overall={"score": None, "criteria": {}},
                         cost={}, duration_sec=0.0)
    row = latest_evaluation(c, rid)
    assert row["total_input_tokens"] == 0
    assert row["total_output_tokens"] == 0
    assert row["total_cost_usd"] == 0.0
    assert row["langsmith_run_url"] is None
