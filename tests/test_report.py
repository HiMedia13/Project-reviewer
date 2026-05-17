from pathlib import Path
from app.report import render_html, terminal_summary


def _ctx():
    return {
        "repo_url": "https://x/y.git",
        "commit_sha": "abc123",
        "mode": "incremental",
        "overall": {"score": 77,
                    "criteria": {"library": 70, "eng": 80,
                                 "deadcode": 90, "techstack": None}},
        "cost": {"input_tokens": 100, "output_tokens": 20,
                 "cost_usd": 0.012, "run_url": "http://ls/x"},
        "duration_sec": 9.5,
        "tech_assessment": {
            "purpose": "A CLI tool that reviews GitHub repos",
            "stack": [
                {"tech": "FastAPI", "role": "HTTP API layer",
                 "used_well": "yes", "purpose_fit": "good",
                 "rationale": "Async endpoints suit IO-bound review work",
                 "evidence": "app/main.py defines the routes"},
            ],
            "stack_verdict": "Stack is well-matched to the project goals",
            "stack_score": 84,
        },
        "findings": [
            {"file_path": "a.py", "criterion": "deadcode", "verified": True,
             "verify_note": "ok",
             "findings": [{"severity": "high", "location": "a.py:3",
                           "evidence": "e", "msg": "dead"}]},
        ],
        "changed_files": ["a.py"],
    }


def test_render_html_writes_self_contained_file(tmp_path):
    out = tmp_path / "r.html"
    render_html(_ctx(), str(out))
    html = out.read_text(encoding="utf-8")
    assert "<style>" in html          # inline CSS, self-contained
    assert "<link" not in html        # no external stylesheet
    assert "<script" not in html      # no script tags at all
    # tech-stack-fit section is the headline
    assert "기술 스택 적합성" in html
    assert "A CLI tool that reviews GitHub repos" in html  # purpose
    assert "FastAPI" in html                                # stack tech
    assert "Stack is well-matched to the project goals" in html  # verdict
    assert "84" in html                                     # stack score
    # tech section appears before the per-file findings details
    assert html.index("기술 스택 적합성") < html.index("<details>")
    # per-file findings demoted into a collapsible secondary section
    assert "<details>" in html
    assert "파일별 상세 findings (보조)" in html
    assert "a.py:3" in html           # a finding still rendered inside
    assert html.index("<details>") < html.index("a.py:3")
    assert "incremental" in html


def test_terminal_summary_returns_text():
    s = terminal_summary(_ctx())
    # leads with the tech-stack-fit block
    assert "프로젝트 목적" in s
    assert "A CLI tool that reviews GitHub repos" in s
    assert "FastAPI" in s
    assert "기술 스택" in s
    assert "84" in s
    # secondary per-file summary still present
    assert "Overall" in s
    assert "77" in s
    assert "deadcode" in s
    # tech block precedes the secondary Overall
    assert s.index("프로젝트 목적") < s.index("Overall")


def test_render_html_escapes_untrusted_findings(tmp_path):
    # findings come from the LLM agent (untrusted) — must be HTML-escaped.
    ctx = _ctx()
    ctx["findings"][0]["file_path"] = "<script>alert(1)</script>"
    ctx["findings"][0]["findings"][0]["msg"] = '"><img src=x onerror=alert(2)>'
    out = tmp_path / "r.html"
    render_html(ctx, str(out))
    html = out.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    # the <img> payload must be inert escaped text, not a live tag
    assert "<img src=x onerror=alert(2)>" not in html
    assert "&lt;img src=x onerror=alert(2)&gt;" in html


def test_render_html_escapes_untrusted_tech_assessment(tmp_path):
    # tech_assessment is now untrusted LLM content in the HEADLINE — escape it.
    ctx = _ctx()
    ctx["tech_assessment"]["stack"][0]["rationale"] = \
        "<script>alert(1)</script>"
    ctx["tech_assessment"]["purpose"] = '"><img src=x onerror=alert(3)>'
    out = tmp_path / "r.html"
    render_html(ctx, str(out))
    html = out.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<img src=x onerror=alert(3)>" not in html
    assert "&lt;img src=x onerror=alert(3)&gt;" in html


def test_render_html_no_tech_assessment_graceful(tmp_path):
    ctx = _ctx()
    ctx["tech_assessment"] = {}
    out = tmp_path / "r.html"
    render_html(ctx, str(out))  # must not crash
    html = out.read_text(encoding="utf-8")
    assert "기술 스택 평가 없음" in html
    assert "기술 스택 적합성" not in html
    # secondary findings/summary still rendered
    assert "<details>" in html
    assert "a.py:3" in html
    assert "77" in html


def test_terminal_summary_no_tech_assessment_graceful():
    ctx = _ctx()
    ctx["tech_assessment"] = {}
    s = terminal_summary(ctx)  # must not crash / KeyError
    assert "기술 스택 평가 없음" in s
    assert "Overall" in s
    assert "77" in s
