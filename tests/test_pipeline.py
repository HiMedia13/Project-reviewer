import json
import subprocess
from pathlib import Path

import main as cli

_FAKE_TECH = {"purpose": "P", "stack": [{"tech": "Python"}],
              "stack_verdict": "ok", "stack_score": 80}


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
        return _fake_agent_rows(in_scope), dict(_FAKE_TECH), "<raw agent text>"

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
        return _fake_agent_rows(in_scope), dict(_FAKE_TECH), "<raw agent text>"

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
        return _fake_agent_rows(in_scope), dict(_FAKE_TECH), "<raw agent text>"

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
        return _fake_agent_rows(in_scope), dict(_FAKE_TECH), "<raw agent text>"

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


def test_tech_assessment_persisted_in_overall_json(tmp_path, monkeypatch):
    origin = _origin(tmp_path)

    def fake_run_eval(repo_path, in_scope):
        return _fake_agent_rows(in_scope), dict(_FAKE_TECH), "<raw>"

    monkeypatch.setattr(cli, "run_evaluation", fake_run_eval)
    monkeypatch.setattr(
        cli, "aggregate_cost",
        lambda **kw: {"input_tokens": 1, "output_tokens": 1,
                      "cost_usd": 0.0, "run_url": None},
    )
    workdir = tmp_path / "wd"

    cli.review(str(origin), workdir=str(workdir), force=True)

    db = cli.db.connect(str(Path(workdir) / "reviewer.sqlite3"))
    repo = cli.db.get_repo_by_url(db, str(origin))
    latest = cli.db.latest_evaluation(db, repo["id"])
    overall = json.loads(latest["overall_json"])

    # The tech assessment round-trips through finalize_evaluation's
    # overall_json json.dumps with no DB schema change.
    assert overall["tech_assessment"]["stack_score"] == 80
    assert overall["tech_assessment"]["purpose"] == "P"
    # synthesize's SECONDARY 4-criteria summary stays intact alongside it.
    assert "score" in overall
    assert "criteria" in overall


def test_run_evaluation_unpacks_run_agent_3tuple(tmp_path, monkeypatch):
    # Reviewer M1: exercise the REAL run_agent<->main seam (the pipeline
    # tests above stub cli.run_evaluation, so the arity contract between
    # run_agent and run_evaluation is otherwise untested). Patch at the
    # orchestrator import boundary used INSIDE run_evaluation; build_agent
    # / run_agent are imported there lazily, so patching the module attrs
    # before the call takes effect.
    import app.agent.orchestrator as orch

    monkeypatch.setattr(orch, "build_agent", lambda rp, sc: ("AGENT", {}))
    monkeypatch.setattr(
        orch, "run_agent",
        lambda agent, holder, in_scope, *, enabled, plain: (
            [{"file_path": "a.py", "criterion": "eng", "findings": [],
              "criterion_score": 80, "verified": True, "verify_note": ""}],
            {"purpose": "P", "stack": [], "stack_verdict": "v",
             "stack_score": 70},
            "RAW",
        ),
    )

    result = cli.run_evaluation("/x", ["a.py"])

    assert isinstance(result, tuple) and len(result) == 3
    rows, tech, raw = result
    # First element is the LIST of dict rows (NOT a nested tuple): this is
    # exactly the C1 regression guard (main.py must unpack run_agent's
    # 3-arity, not bind the whole tuple to `rows`).
    assert isinstance(rows, list)
    assert rows and isinstance(rows[0], dict)
    assert rows[0]["file_path"] == "a.py"
    assert tech == {"purpose": "P", "stack": [], "stack_verdict": "v",
                    "stack_score": 70}
    assert raw == "RAW"


def test_failed_empty_prior_is_not_used_as_incremental_baseline(
        tmp_path, monkeypatch):
    # Reviewer P13: a crashed/empty prior eval (0 findings, no
    # tech_assessment) left at HEAD sha must NOT be treated as an
    # incremental baseline. Without the guard, run 2 (same sha, no
    # force) goes incremental, finds 0 in-scope files, skips the agent
    # and carries forward the empty prior forever (Overall: N/A).
    origin = _origin(tmp_path)

    # Run 1: an EMPTY failed result — no findings, empty tech_assessment.
    def empty_run_eval(repo_path, in_scope):
        return [], {}, "raw"

    monkeypatch.setattr(cli, "run_evaluation", empty_run_eval)
    monkeypatch.setattr(
        cli, "aggregate_cost",
        lambda **kw: {"input_tokens": 0, "output_tokens": 0,
                      "cost_usd": 0.0, "run_url": None},
    )
    workdir = tmp_path / "wd"
    cli.review(str(origin), workdir=str(workdir), force=False)

    # Run 2: same sha (no new commit), no force. Non-empty fake that
    # records its calls.
    calls = []

    def good_run_eval(repo_path, in_scope):
        calls.append(list(in_scope))
        return _fake_agent_rows(in_scope), dict(_FAKE_TECH), "raw"

    monkeypatch.setattr(cli, "run_evaluation", good_run_eval)
    cli.review(str(origin), workdir=str(workdir), force=False)

    # The guard forced a FULL re-evaluation: run_evaluation WAS called
    # (without the guard run 2 would skip and calls would be empty).
    assert calls, "run 2 must invoke run_evaluation (full re-eval)"

    db = cli.db.connect(str(Path(workdir) / "reviewer.sqlite3"))
    repo = cli.db.get_repo_by_url(db, str(origin))
    latest = cli.db.latest_evaluation(db, repo["id"])
    rows = list(cli.db.findings_for_evaluation(db, latest["id"]))
    assert rows, "latest eval must now have real findings"
    overall = json.loads(latest["overall_json"])
    assert overall["tech_assessment"]["stack_score"] == 80
    # Guard forced full because the prior was unusable.
    assert latest["mode"] == "full"


def test_usable_prior_still_enables_incremental_skip(tmp_path, monkeypatch):
    # Reviewer P13 regression: the guard must NOT over-trigger. A USABLE
    # prior (real findings + tech_assessment) still enables the normal
    # incremental no-change skip — run 2 must not re-run the agent.
    origin = _origin(tmp_path)
    calls = []

    def fake_run_eval(repo_path, in_scope):
        calls.append(list(in_scope))
        return _fake_agent_rows(in_scope), dict(_FAKE_TECH), "raw"

    monkeypatch.setattr(cli, "run_evaluation", fake_run_eval)
    monkeypatch.setattr(
        cli, "aggregate_cost",
        lambda **kw: {"input_tokens": 1, "output_tokens": 1,
                      "cost_usd": 0.0, "run_url": None},
    )
    workdir = tmp_path / "wd"

    # Run 1: full run with a non-empty result at sha1.
    cli.review(str(origin), workdir=str(workdir), force=True)
    assert len(calls) == 1

    # Run 2: no new commit, no force — usable prior so incremental skips.
    cli.review(str(origin), workdir=str(workdir), force=False)
    assert len(calls) == 1                          # no second agent run

    db = cli.db.connect(str(Path(workdir) / "reviewer.sqlite3"))
    repo = cli.db.get_repo_by_url(db, str(origin))
    latest = cli.db.latest_evaluation(db, repo["id"])
    overall = json.loads(latest["overall_json"])
    assert latest["mode"] == "incremental"
    # carried forward from run 1's usable prior, not emptied.
    assert overall["tech_assessment"]["stack_score"] == 80
    assert overall["tech_assessment"]["purpose"] == "P"


def test_tech_assessment_carried_forward_on_no_agent_run(tmp_path,
                                                         monkeypatch):
    # Reviewer P10 follow-up: the carry-forward HAPPY path. Run 1 produces
    # a non-empty tech_assessment; run 2 (incremental, no file changes ->
    # no agent run) must reuse run 1's tech_assessment from the prior
    # overall_json, not leave it empty.
    origin = _origin(tmp_path)
    calls = []

    def fake_run_eval(repo_path, in_scope):
        calls.append(list(in_scope))
        return _fake_agent_rows(in_scope), dict(_FAKE_TECH), "<raw>"

    monkeypatch.setattr(cli, "run_evaluation", fake_run_eval)
    monkeypatch.setattr(
        cli, "aggregate_cost",
        lambda **kw: {"input_tokens": 1, "output_tokens": 1,
                      "cost_usd": 0.0, "run_url": None},
    )
    workdir = tmp_path / "wd"

    # Run 1: full run, tech_assessment persisted.
    cli.review(str(origin), workdir=str(workdir), force=True)
    assert len(calls) == 1

    # Run 2: no new commits -> incremental, no in-scope files -> no agent
    # run (run_evaluation not called again).
    cli.review(str(origin), workdir=str(workdir), force=False)
    assert len(calls) == 1                          # no second agent run

    db = cli.db.connect(str(Path(workdir) / "reviewer.sqlite3"))
    repo = cli.db.get_repo_by_url(db, str(origin))
    latest = cli.db.latest_evaluation(db, repo["id"])
    overall = json.loads(latest["overall_json"])
    assert latest["mode"] == "incremental"
    # carried forward from run 1's overall_json, not empty
    assert overall["tech_assessment"]["stack_score"] == 80
    assert overall["tech_assessment"]["purpose"] == "P"


def test_zero_findings_but_real_tech_assessment_prior_is_usable(
        tmp_path, monkeypatch):
    # A legit run that found ZERO file findings but produced a real
    # project-level tech_assessment is still a USABLE baseline — the
    # _prior_is_usable tech_assessment branch must keep incremental
    # caching working (guard must not over-trigger on findings==0).
    origin = _origin(tmp_path)
    calls = []

    def fake_run_eval(repo_path, in_scope):
        calls.append(list(in_scope))
        return [], dict(_FAKE_TECH), "<raw>"   # 0 findings, real tech

    monkeypatch.setattr(cli, "run_evaluation", fake_run_eval)
    monkeypatch.setattr(
        cli, "aggregate_cost",
        lambda **kw: {"input_tokens": 1, "output_tokens": 1,
                      "cost_usd": 0.0, "run_url": None},
    )
    workdir = tmp_path / "wd"

    cli.review(str(origin), workdir=str(workdir), force=True)
    assert len(calls) == 1                           # run 1 evaluated

    # Run 2: same sha, no force. Prior has 0 findings but a real
    # tech_assessment -> usable -> incremental skip (NOT re-run full).
    cli.review(str(origin), workdir=str(workdir), force=False)
    assert len(calls) == 1                           # no second agent run

    db = cli.db.connect(str(Path(workdir) / "reviewer.sqlite3"))
    repo = cli.db.get_repo_by_url(db, str(origin))
    latest = cli.db.latest_evaluation(db, repo["id"])
    overall = json.loads(latest["overall_json"])
    assert latest["mode"] == "incremental"
    assert overall["tech_assessment"]["stack_score"] == 80
