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


def _origin3(tmp_path):
    o = _origin(tmp_path)
    run = lambda *a: subprocess.run(a, cwd=o, check=True)
    (o / "c.py").write_text("z = 3\n")
    run("git", "add", ".")
    run("git", "commit", "-qm", "c2")
    return o


def _fake_agent_rows(in_scope):
    """Normalized finding rows (the 6 keys) — what run_agent now returns."""
    return [
        {"file_path": f, "criterion": "deadcode",
         "findings": [{"severity": "low", "location": f + ":1",
                       "evidence": "e", "msg": "m"}],
         "criterion_score": 80, "verified": True, "verify_note": "ok"}
        for f in in_scope
    ]


def test_full_then_incremental_reuses_cache(tmp_path, monkeypatch):
    origin = _origin(tmp_path)
    calls = []

    def fake_run_eval(repo_path, in_scope):
        calls.append(list(in_scope))
        return _fake_agent_rows(in_scope), "<raw agent text>"

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


def test_max_files_caps_evaluation(tmp_path, monkeypatch):
    origin = _origin3(tmp_path)
    calls = []

    def fake_run_eval(repo_path, in_scope):
        calls.append(list(in_scope))
        return _fake_agent_rows(in_scope), "<raw agent text>"

    monkeypatch.setattr(cli, "run_evaluation", fake_run_eval)
    monkeypatch.setattr(
        cli, "aggregate_cost",
        lambda **kw: {"input_tokens": 1, "output_tokens": 1,
                      "cost_usd": 0.0, "run_url": None},
    )
    workdir = tmp_path / "wd"

    ctx = cli.review(str(origin), workdir=str(workdir), force=True,
                     max_files=1)

    # The cap limited the agent to exactly one in-scope file.
    assert len(calls) == 1
    assert len(calls[0]) == 1
    assert calls[0][0] in {"a.py", "b.py", "c.py"}

    # The run still completes: HTML written + evaluation row finalized.
    reports = list((Path(workdir) / "output").glob("report-*.html"))
    assert len(reports) == 1
    db = cli.db.connect(str(Path(workdir) / "reviewer.sqlite3"))
    repo = cli.db.get_repo_by_url(db, str(origin))
    latest = cli.db.latest_evaluation(db, repo["id"])
    assert latest["duration_sec"] is not None
    assert ctx["changed_files"] == calls[0]


def test_max_files_zero_evaluates_nothing(tmp_path, monkeypatch):
    origin = _origin3(tmp_path)
    calls = []

    def fake_run_eval(repo_path, in_scope):
        calls.append(list(in_scope))
        return _fake_agent_rows(in_scope), "<raw agent text>"

    monkeypatch.setattr(cli, "run_evaluation", fake_run_eval)
    monkeypatch.setattr(
        cli, "aggregate_cost",
        lambda **kw: {"input_tokens": 1, "output_tokens": 1,
                      "cost_usd": 0.0, "run_url": None},
    )
    workdir = tmp_path / "wd"

    cli.review(str(origin), workdir=str(workdir), force=True, max_files=0)

    # 0 means "evaluate nothing": the agent is never invoked.
    assert calls == []

    # The run still completes: evaluation row exists + finalized,
    # exactly one HTML report, and zero findings recorded.
    reports = list((Path(workdir) / "output").glob("report-*.html"))
    assert len(reports) == 1
    db = cli.db.connect(str(Path(workdir) / "reviewer.sqlite3"))
    repo = cli.db.get_repo_by_url(db, str(origin))
    latest = cli.db.latest_evaluation(db, repo["id"])
    assert latest["duration_sec"] is not None
    rows = cli.db.findings_for_evaluation(db, latest["id"])
    assert list(rows) == []


def test_max_files_preserves_cached_findings(tmp_path, monkeypatch):
    origin = _origin(tmp_path)              # a.py, b.py
    calls = []

    def fake_run_eval(repo_path, in_scope):
        calls.append(list(in_scope))
        return _fake_agent_rows(in_scope), "<raw agent text>"

    monkeypatch.setattr(cli, "run_evaluation", fake_run_eval)
    monkeypatch.setattr(
        cli, "aggregate_cost",
        lambda **kw: {"input_tokens": 1, "output_tokens": 1,
                      "cost_usd": 0.0, "run_url": None},
    )
    workdir = tmp_path / "wd"

    # (1) full run, no cap -> a.py & b.py both evaluated and stored.
    cli.review(str(origin), workdir=str(workdir), force=False)
    assert sorted(calls[0]) == ["a.py", "b.py"]

    # (2) modify only b.py, commit.
    run = lambda *a: subprocess.run(a, cwd=origin, check=True)
    (origin / "b.py").write_text("y = 3\n")
    run("git", "add", ".")
    run("git", "commit", "-qm", "c2")

    # (3) incremental run WITH max_files=1: the cap shrinks agent work...
    cli.review(str(origin), workdir=str(workdir), force=False,
               max_files=1)
    assert len(calls[1]) == 1                       # cap held on incremental
    assert calls[1] == ["b.py"]                     # only changed file

    # ...but cached findings (a.py) are preserved via
    # copy_unchanged_findings, NOT dropped by the cap.
    db = cli.db.connect(str(Path(workdir) / "reviewer.sqlite3"))
    repo = cli.db.get_repo_by_url(db, str(origin))
    latest = cli.db.latest_evaluation(db, repo["id"])
    rows = cli.db.findings_for_evaluation(db, latest["id"])
    paths = {r["file_path"] for r in rows}
    assert paths == {"a.py", "b.py"}                # a.py reused from cache
    assert latest["mode"] == "incremental"
