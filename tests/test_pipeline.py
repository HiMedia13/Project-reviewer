import json
import subprocess
from pathlib import Path

import main as cli


def _origin(tmp_path):
    o = tmp_path / "origin"
    o.mkdir()
    run = lambda *a: subprocess.run(a, cwd=o, check=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (o / "a.py").write_text("import os\nx = 1\n")
    (o / "b.py").write_text("y = 2\n")
    run("git", "add", ".")
    run("git", "commit", "-qm", "c1")
    return o


def _fake_agent_output(in_scope):
    return json.dumps([
        {"file_path": f, "criterion": "deadcode",
         "findings": [{"severity": "low", "location": f + ":1",
                       "evidence": "e", "msg": "m"}],
         "criterion_score": 80, "verified": True, "verify_note": "ok"}
        for f in in_scope
    ])


def test_full_then_incremental_reuses_cache(tmp_path, monkeypatch):
    origin = _origin(tmp_path)
    calls = []

    def fake_run_eval(repo_path, in_scope):
        calls.append(list(in_scope))
        return _fake_agent_output(in_scope)

    monkeypatch.setattr(cli, "run_evaluation", fake_run_eval)
    monkeypatch.setattr(
        cli, "aggregate_cost",
        lambda **kw: {"input_tokens": 1, "output_tokens": 1,
                      "cost_usd": 0.0, "run_url": None},
    )
    workdir = tmp_path / "wd"

    cli.review(str(origin), workdir=str(workdir), force=False)
    assert sorted(calls[0]) == ["a.py", "b.py"]   # full

    # modify only b.py and push a new commit
    run = lambda *a: subprocess.run(a, cwd=origin, check=True)
    (origin / "b.py").write_text("y = 3\n")
    run("git", "add", ".")
    run("git", "commit", "-qm", "c2")

    cli.review(str(origin), workdir=str(workdir), force=False)
    assert calls[1] == ["b.py"]                    # incremental: only b.py

    db = cli.db.connect(str(Path(workdir) / "reviewer.sqlite3"))
    repo = cli.db.get_repo_by_url(db, str(origin))
    latest = cli.db.latest_evaluation(db, repo["id"])
    rows = cli.db.findings_for_evaluation(db, latest["id"])
    paths = {r["file_path"] for r in rows}
    assert paths == {"a.py", "b.py"}               # a.py reused from cache
    assert latest["mode"] == "incremental"         # mode recorded
    reports = list((Path(workdir) / "output").glob("report-*.html"))
    assert len(reports) == 2                        # one HTML per run
