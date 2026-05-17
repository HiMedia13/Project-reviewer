"""Tests for the schema-pinned submit_findings tool and capture holder.

Pure unit tests: no LLM, no network. They exercise the normalization
helper directly and the tool via its LangChain ``.invoke`` interface.
"""

from app.agent.submit import make_submit_tool, normalize_rows

_KEYS = {
    "file_path",
    "criterion",
    "findings",
    "criterion_score",
    "verified",
    "verify_note",
}


def test_tool_name_and_invocable():
    holder: dict = {}
    tool = make_submit_tool(holder)
    assert tool.name == "submit_findings"
    out = tool.invoke({"findings": []})
    assert isinstance(out, str)


def test_happy_path_full_row():
    holder: dict = {}
    tool = make_submit_tool(holder)
    row = {
        "file_path": "src/app.py",
        "criterion": "security",
        "findings": [
            {
                "severity": "high",
                "location": "L10",
                "evidence": "eval(user_input)",
                "msg": "arbitrary code execution",
            }
        ],
        "criterion_score": 3,
        "verified": True,
        "verify_note": "confirmed by reading file",
    }
    msg = tool.invoke({"findings": [row]})

    assert holder["submitted"] is True
    assert len(holder["findings"]) == 1
    rec = holder["findings"][0]
    assert set(rec.keys()) == _KEYS
    assert rec["file_path"] == "src/app.py"
    assert rec["criterion"] == "security"
    assert rec["criterion_score"] == 3
    assert rec["verified"] is True
    assert rec["verify_note"] == "confirmed by reading file"
    assert rec["findings"] == [
        {
            "severity": "high",
            "location": "L10",
            "evidence": "eval(user_input)",
            "msg": "arbitrary code execution",
        }
    ]
    assert "1" in msg


def test_minimal_row_defaults():
    holder: dict = {}
    tool = make_submit_tool(holder)
    tool.invoke({"findings": [{"file_path": "a.py"}]})

    rec = holder["findings"][0]
    assert rec["file_path"] == "a.py"
    assert rec["criterion"] == "unknown"
    assert rec["findings"] == []
    assert rec["criterion_score"] is None
    assert rec["verified"] is False
    assert rec["verify_note"] == ""
    assert holder["submitted"] is True


def test_row_without_file_path_is_dropped_not_raised():
    holder: dict = {}
    tool = make_submit_tool(holder)
    tool.invoke(
        {
            "findings": [
                {"file_path": "", "criterion": "x"},
                {"file_path": "good.py", "criterion": "y"},
            ]
        }
    )

    assert len(holder["findings"]) == 1
    assert holder["findings"][0]["file_path"] == "good.py"
    assert holder["submitted"] is True


def test_normalize_rows_mixed_list():
    raw = [
        {
            "file_path": "ok.py",
            "criterion": "perf",
            "findings": [{"msg": "slow loop"}],  # missing subfields
            "extra_unknown": "ignored",  # extra key dropped
        },
        {"criterion": "no path here"},  # dropped (no file_path)
        {"file_path": "  "},  # dropped (whitespace-only)
        {"file_path": "second.py"},  # defaults applied
    ]
    out = normalize_rows(raw)

    assert len(out) == 2
    assert out[0]["file_path"] == "ok.py"
    assert set(out[0].keys()) == _KEYS
    assert "extra_unknown" not in out[0]
    assert out[0]["findings"] == [
        {"severity": "low", "location": "", "evidence": "", "msg": "slow loop"}
    ]
    assert out[1]["file_path"] == "second.py"
    assert out[1]["criterion"] == "unknown"
    assert out[1]["criterion_score"] is None


def test_normalize_rows_non_list_no_raise():
    assert normalize_rows(None) == []
    assert normalize_rows("garbage") == []
    assert normalize_rows(123) == []
    assert normalize_rows({"file_path": "x.py"}) == []


def test_tool_garbage_input_no_raise():
    holder: dict = {}
    tool = make_submit_tool(holder)
    msg = tool.invoke({"findings": []})

    assert holder["findings"] == []
    assert holder["submitted"] is True
    assert "0" in msg


def test_tool_exposes_args_schema():
    holder: dict = {}
    tool = make_submit_tool(holder)
    # arg name must be exactly "findings"
    assert "findings" in tool.args
    assert tool.args_schema is not None
