import argparse
import os
import sys
import time
import uuid
from pathlib import Path

from app import db
from app.repo_manager import (
    ensure_repo, head_sha, default_branch, inventory_files, file_hash,
)
from app.scope import plan_scope
from app.synthesizer import synthesize
from app.findings_parser import parse_findings
from app.langsmith_cost import aggregate_cost
from app.report import render_html, terminal_summary

from dotenv import load_dotenv

# Load .env before any module reads env vars (orchestrator reads
# REVIEWER_MODEL at import time; ChatAnthropic reads ANTHROPIC_API_KEY).
load_dotenv()


def run_evaluation(repo_path: str, in_scope: list[str]) -> str:
    from app.agent.orchestrator import build_agent, run_agent
    # TTY -> Rich Live TUI; non-TTY stays suppressed unless --progress
    # (REVIEWER_PROGRESS) forces plain one-line status into the log.
    forced = os.getenv("REVIEWER_PROGRESS") == "1"
    is_tty = sys.stdout.isatty()
    enabled = is_tty or forced
    plain = enabled and not is_tty
    agent = build_agent(repo_path, in_scope)
    return run_agent(agent, in_scope, enabled=enabled, plain=plain)


def review(remote_url: str, workdir: str, force: bool) -> dict:
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    cache_root = str(work / ".repocache")
    out_dir = work / "output"

    repo_path = ensure_repo(remote_url, cache_root)
    sha = head_sha(repo_path)
    branch = default_branch(repo_path)
    all_files = inventory_files(repo_path)

    conn = db.connect(str(work / "reviewer.sqlite3"))
    db.init_schema(conn)
    repo_id = db.upsert_repo(conn, remote_url, repo_path, branch)
    prior = db.latest_evaluation(conn, repo_id)
    prior_sha = prior["commit_sha"] if prior else None

    scope = plan_scope(prior_sha, repo_path, all_files, force)
    eval_id = db.create_evaluation(
        conn, repo_id, sha, prior["id"] if prior else None, scope.mode
    )

    if scope.mode == "incremental" and prior:
        db.copy_unchanged_findings(
            conn, repo_id, prior["id"], eval_id, scope.cached
        )

    trace_id = str(uuid.uuid4())
    os.environ.setdefault("LANGSMITH_PROJECT", "project-reviewer")
    started = time.time()
    if scope.in_scope:
        raw = run_evaluation(repo_path, scope.in_scope)
        for r in parse_findings(raw):
            h = file_hash(str(Path(repo_path) / r["file_path"])) \
                if (Path(repo_path) / r["file_path"]).exists() else ""
            db.insert_finding(
                conn, repo_id, eval_id, r["file_path"], h, r["criterion"],
                r["findings"], r["criterion_score"], r["verified"],
                r["verify_note"],
            )
    duration = time.time() - started

    all_rows = list(db.findings_for_evaluation(conn, eval_id))
    overall = synthesize(all_rows)
    cost = aggregate_cost(
        trace_id=trace_id, project=os.environ["LANGSMITH_PROJECT"]
    )
    db.finalize_evaluation(conn, eval_id, overall, cost, duration)

    import json as _json
    ctx = {
        "repo_url": remote_url, "commit_sha": sha, "mode": scope.mode,
        "overall": overall, "cost": cost, "duration_sec": duration,
        "changed_files": scope.in_scope,
        "findings": [
            {"file_path": r["file_path"], "criterion": r["criterion"],
             "verified": bool(r["verified"]),
             "verify_note": r["verify_note"] or "",
             "findings": _json.loads(r["findings_json"])}
            for r in all_rows
        ],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"report-{eval_id}.html"
    render_html(ctx, str(html_path))
    print(terminal_summary(ctx))
    print(f"\nHTML report: {html_path}")
    return ctx


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Project-Reviewer")
    parser.add_argument("github_url", nargs="?")
    parser.add_argument("--workdir", default=".reviewer")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--serve", action="store_true",
                        help="이력 탐색 웹 서버 실행")
    parser.add_argument("--progress", action="store_true",
                        help="진행상황 표시(비-TTY에서도 평문 한 줄 로그)")
    args = parser.parse_args(argv)
    if args.serve:
        from app.server import serve
        serve(str(Path(args.workdir) / "reviewer.sqlite3"))
        return 0
    if args.progress:
        os.environ["REVIEWER_PROGRESS"] = "1"
    review(args.github_url, args.workdir, args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
