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
    assert "77" in html
    assert "a.py:3" in html
    assert "incremental" in html


def test_terminal_summary_returns_text():
    s = terminal_summary(_ctx())
    assert "77" in s
    assert "deadcode" in s
