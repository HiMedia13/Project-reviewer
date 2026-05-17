"""Tests for the schema-pinned submit_findings tool and capture holder.

Pure unit tests: no LLM, no network. They exercise the normalization
helper directly and the tool via its LangChain ``.invoke`` interface.
"""

import sys

import pytest

from app.agent.submit import (
    finalize_findings,
    make_submit_tool,
    normalize_rows,
)
from app.findings_parser import parse_findings

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


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("80", 80),
        (80.0, 80),
        (80, 80),
        ("n/a", None),
        ("3.5", None),
        (3.5, None),
        ("80.0", 80),
        (True, None),
        (False, None),
        (None, None),
        ("", None),
    ],
)
def test_criterion_score_coercion(raw, expected):
    out = normalize_rows([{"file_path": "x.py", "criterion_score": raw}])
    assert len(out) == 1
    assert out[0]["criterion_score"] == expected
    assert out[0]["criterion_score"] is expected or isinstance(
        out[0]["criterion_score"], int
    )


def test_tool_no_raise_on_realistic_malformed_rows():
    holder: dict = {}
    tool = make_submit_tool(holder)
    # These are the exact shapes a real model improvises; a strict
    # args_schema would ValidationError before the callback ran.
    malformed = [
        {"file_path": "a.py", "criterion": 123},
        {"file_path": "b.py", "criterion_score": 80.9},
        {"file_path": "c.py", "verify_note": 456},
        {"file_path": "d.py", "findings": ["just a string"]},
        {"file_path": "e.py", "findings": "garbage"},
    ]
    msg = tool.invoke({"findings": malformed})

    assert holder["submitted"] is True
    rows = {r["file_path"]: r for r in holder["findings"]}
    assert set(rows) == {"a.py", "b.py", "c.py", "d.py", "e.py"}
    assert rows["a.py"]["criterion"] == "unknown"  # int -> default
    assert rows["b.py"]["criterion_score"] is None  # 80.9 not integral
    assert rows["c.py"]["verify_note"] == "456"  # coerced to str
    assert rows["d.py"]["findings"] == [
        {"severity": "low", "location": "", "evidence": "", "msg": ""}
    ]
    assert rows["e.py"]["findings"] == []  # non-list -> []
    assert "5" in msg


def test_tool_no_raise_on_wrong_typed_findings():
    holder: dict = {}
    tool = make_submit_tool(holder)
    # findings entirely the wrong type still must not raise via the tool.
    msg = tool.invoke({"findings": [123, "nope", {"no_path": 1}]})
    assert holder["findings"] == []
    assert holder["submitted"] is True
    assert "0" in msg


def test_repeat_call_overwrites_holder():
    holder: dict = {}
    tool = make_submit_tool(holder)
    tool.invoke({"findings": [{"file_path": "first.py"}]})
    tool.invoke(
        {"findings": [{"file_path": "second.py"}, {"file_path": "third.py"}]}
    )

    paths = [r["file_path"] for r in holder["findings"]]
    assert paths == ["second.py", "third.py"]  # only the 2nd call
    assert "first.py" not in paths


def test_schema_fidelity_vs_parse_findings():
    # Lock the normalized key set against downstream drift relative to
    # app.findings_parser.parse_findings (which feeds db.py insert_finding).
    parsed = parse_findings('[{"file_path":"x.py","criterion":"eng"}]')
    out = normalize_rows([{"file_path": "x.py", "criterion": "eng"}])
    assert len(parsed) == 1
    assert len(out) == 1
    assert set(out[0].keys()) == set(parsed[0].keys())


def test_normalize_rows_non_list_findings_no_raise():
    out = normalize_rows([{"file_path": "x.py", "findings": "oops"}])
    assert len(out) == 1
    assert out[0]["findings"] == []


# --------------------------------------------------------------------------
# finalize_findings: deterministic finalize guard (P2). Pure: fake model via
# the ``_model_factory`` seam, never network / langchain_anthropic.
# --------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, tool_calls):
        # Mirrors LangChain AIMessage.tool_calls shape.
        if tool_calls is not _NO_ATTR:
            self.tool_calls = tool_calls


_NO_ATTR = object()


class _FakeChat:
    """Records bind_tools args; .invoke returns a preset response/raises."""

    def __init__(self, resp=None, raise_exc=None):
        self._resp = resp
        self._raise_exc = raise_exc
        self.bound_tool_choice = None
        self.bound_tools = None
        self.invoked_messages = None

    def bind_tools(self, tools, tool_choice=None):
        self.bound_tools = tools
        self.bound_tool_choice = tool_choice
        return self

    def invoke(self, messages):
        self.invoked_messages = messages
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._resp


def _factory_for(chat):
    captured = {}

    def _factory(model_name):
        captured["model"] = model_name
        return chat

    _factory.captured = captured
    return _factory


def test_finalize_happy_path_captures_via_normalize_rows():
    holder: dict = {}
    chat = _FakeChat(
        resp=_FakeResp(
            [
                {
                    "name": "submit_findings",
                    "args": {
                        "findings": [
                            {
                                "file_path": "a.py",
                                "criterion": "eng",
                                "verified": True,
                                "criterion_score": 80,
                            }
                        ]
                    },
                    "id": "call_1",
                }
            ]
        )
    )
    factory = _factory_for(chat)

    rows = finalize_findings(
        holder, "raw eval text", model="m", _model_factory=factory
    )

    assert rows is holder["findings"]
    assert holder["submitted"] is True
    assert len(rows) == 1
    rec = rows[0]
    assert set(rec.keys()) == _KEYS
    assert rec["file_path"] == "a.py"
    assert rec["criterion"] == "eng"
    assert rec["criterion_score"] == 80
    assert rec["verified"] is True
    # forced tool call
    assert chat.bound_tool_choice == "submit_findings"
    assert chat.bound_tools is not None and len(chat.bound_tools) == 1
    assert factory.captured["model"] == "m"


def test_finalize_noop_when_already_submitted_with_findings():
    existing = [
        {
            "file_path": "x.py",
            "criterion": "unknown",
            "findings": [],
            "criterion_score": None,
            "verified": False,
            "verify_note": "",
        }
    ]
    holder = {"submitted": True, "findings": existing}

    def _exploding_factory(model_name):
        raise AssertionError("model factory must NOT be called on no-op")

    rows = finalize_findings(
        holder, "seed", model="m", _model_factory=_exploding_factory
    )

    assert rows is existing
    assert holder["findings"] is existing
    assert holder["submitted"] is True


def test_finalize_attempts_guard_when_submitted_but_no_findings():
    holder = {"submitted": True, "findings": []}
    chat = _FakeChat(
        resp=_FakeResp(
            [
                {
                    "name": "submit_findings",
                    "args": {"findings": [{"file_path": "z.py"}]},
                    "id": "c",
                }
            ]
        )
    )
    rows = finalize_findings(
        holder, "seed", model="m", _model_factory=_factory_for(chat)
    )
    assert len(rows) == 1
    assert rows[0]["file_path"] == "z.py"
    assert holder["submitted"] is True


@pytest.mark.parametrize(
    "tool_calls",
    [
        [],  # no tool calls
        _NO_ATTR,  # response has no .tool_calls attribute at all
        [{"name": "submit_findings", "args": {}}],  # args missing findings
        [{"name": "submit_findings", "args": {"findings": "garbage"}}],
        [{"name": "submit_findings", "args": {"findings": 123}}],
        [{"name": "submit_findings", "args": {"findings": None}}],
        [{"name": "submit_findings"}],  # no args key
        [{"name": "other_tool", "args": {"findings": [{"file_path": "a"}]}}],
        [{"name": "submit_findings", "args": None}],  # args not a dict
    ],
)
def test_finalize_malformed_response_no_raise(tool_calls):
    holder: dict = {}
    chat = _FakeChat(resp=_FakeResp(tool_calls))
    rows = finalize_findings(
        holder, "seed", model="m", _model_factory=_factory_for(chat)
    )
    assert rows == []
    assert holder["findings"] == []
    assert holder["submitted"] is True


def test_finalize_other_tool_name_first_then_submit():
    # First call is a different tool; the submit_findings call is used.
    holder: dict = {}
    chat = _FakeChat(
        resp=_FakeResp(
            [
                {"name": "noise", "args": {"x": 1}},
                {
                    "name": "submit_findings",
                    "args": {"findings": [{"file_path": "good.py"}]},
                },
            ]
        )
    )
    rows = finalize_findings(
        holder, "seed", model="m", _model_factory=_factory_for(chat)
    )
    assert len(rows) == 1
    assert rows[0]["file_path"] == "good.py"


def test_finalize_exception_path_no_propagate():
    holder: dict = {}
    chat = _FakeChat(raise_exc=RuntimeError("boom"))
    rows = finalize_findings(
        holder, "seed", model="m", _model_factory=_factory_for(chat)
    )
    assert rows == []
    assert holder["findings"] == []
    assert holder["submitted"] is True


def test_finalize_exception_preserves_preexisting_findings():
    existing = [{"file_path": "p.py", "criterion": "unknown",
                 "findings": [], "criterion_score": None,
                 "verified": False, "verify_note": ""}]
    # submitted not True -> guard runs; model raises -> degrade gracefully
    holder = {"submitted": False, "findings": existing}
    chat = _FakeChat(raise_exc=RuntimeError("boom"))
    rows = finalize_findings(
        holder, "seed", model="m", _model_factory=_factory_for(chat)
    )
    assert rows == existing
    assert holder["submitted"] is True


@pytest.mark.parametrize(
    "seed", ["", "x" * 200000], ids=["empty", "large"]
)
def test_finalize_seed_text_variations(seed):
    holder: dict = {}
    chat = _FakeChat(
        resp=_FakeResp(
            [{"name": "submit_findings",
              "args": {"findings": [{"file_path": "s.py"}]}}]
        )
    )
    rows = finalize_findings(
        holder, seed, model="m", _model_factory=_factory_for(chat)
    )
    assert len(rows) == 1
    assert rows[0]["file_path"] == "s.py"


def test_finalize_does_not_import_langchain_anthropic():
    # The _model_factory seam must avoid the lazy ChatAnthropic import.
    sys.modules.pop("langchain_anthropic", None)
    holder: dict = {}
    chat = _FakeChat(
        resp=_FakeResp(
            [{"name": "submit_findings",
              "args": {"findings": [{"file_path": "n.py"}]}}]
        )
    )
    finalize_findings(
        holder, "seed", model="m", _model_factory=_factory_for(chat)
    )
    assert "langchain_anthropic" not in sys.modules
