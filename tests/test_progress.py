"""Tests for the live progress TUI callback handler.

Pure synthetic events — no real LLM, no network, no Rich Live loop.
"""

import pytest

from app.agent.progress import (
    PHASES,
    MODEL_PRICES,
    _price_for,
    ReviewProgress,
    ReviewInterrupted,
    _InterruptState,
    run_with_progress,
)


def _handler(total_files=4, model="claude-haiku-4-5-20251001", max_log=12):
    return ReviewProgress(total_files=total_files, model=model, max_log=max_log)


# --- _price_for ----------------------------------------------------------

def test_price_for_known_prefixes():
    assert _price_for("claude-haiku-4-5-20251001") is not None
    assert _price_for("claude-sonnet-4-20250101") is not None
    assert _price_for("claude-opus-4-7") is not None


def test_price_for_unknown_returns_none():
    assert _price_for("gpt-4o-mini") is None
    assert _price_for("") is None


def test_price_for_longest_prefix_match():
    # A dated haiku id must resolve to the haiku-4-5 entry (longest
    # matching registered prefix), not a shorter/wrong one.
    chosen = _price_for("claude-haiku-4-5-20251001")
    assert chosen == MODEL_PRICES["claude-haiku-4-5"]
    assert _price_for("claude-opus-4-7") == MODEL_PRICES["claude-opus-4"]


# --- phase transitions ---------------------------------------------------

def test_phase_transition_activates_and_completes():
    h = _handler()
    h.on_tool_start({"name": "task"},
                    inputs={"subagent_type": "library", "description": "x"})
    assert h.phase_status["library"] == "active"
    assert h.current == "library"

    h.on_tool_start({"name": "task"},
                    inputs={"subagent_type": "eng", "description": "y"})
    assert h.phase_status["library"] == "done"
    assert h.phase_status["eng"] == "active"
    assert h.current == "eng"


def test_phase_unknown_subagent_ignored():
    h = _handler()
    h.on_tool_start({"name": "task"}, inputs={"subagent_type": "bogus"})
    assert all(v == "pending" for v in h.phase_status.values())
    assert h.current is None


# --- file progress -------------------------------------------------------

def test_file_progress_distinct_paths():
    h = _handler()
    h.on_tool_start({"name": "read_repo_file"}, inputs={"path": "a.py"})
    h.on_tool_start({"name": "read_repo_file"}, inputs={"path": "b.py"})
    h.on_tool_start({"name": "read_repo_file"}, inputs={"path": "a.py"})
    assert h.read_paths == {"a.py", "b.py"}
    assert len(h.read_paths) == 2


def test_input_str_fallback_shape():
    # When inputs= is absent, on_tool_start must still not raise and
    # should record activity from the best-effort input_str path.
    h = _handler()
    h.on_tool_start({"name": "list_scope_files"}, input_str="{}")
    assert any("list_scope_files" in a for a in h.activity)


# --- activity log bounded ------------------------------------------------

def test_activity_log_bounded():
    h = _handler(max_log=5)
    for i in range(20):
        h.on_tool_start({"name": "list_scope_files"}, inputs={})
    assert len(h.activity) == 5


def test_tavily_activity_truncated():
    h = _handler()
    long_q = "x" * 200
    h.on_tool_start({"name": "tavily_search"}, inputs={"query": long_q})
    line = h.activity[-1]
    assert line.startswith("tavily:")
    assert len(line) < 60


# --- token accumulation --------------------------------------------------

def test_token_accumulation_llm_output_usage():
    h = _handler()
    resp = type("R", (), {
        "llm_output": {"usage": {"input_tokens": 10, "output_tokens": 3}},
        "generations": [],
    })()
    h.on_llm_end(resp)
    assert h.in_tokens == 10
    assert h.out_tokens == 3


def test_token_accumulation_generations_usage_metadata():
    h = _handler()

    class _Msg:
        usage_metadata = {"input_tokens": 7, "output_tokens": 2}

    class _Gen:
        message = _Msg()

    resp = type("R", (), {"llm_output": None,
                          "generations": [[_Gen()]]})()
    h.on_llm_end(resp)
    assert h.in_tokens == 7
    assert h.out_tokens == 2

    # Second variant: token_usage with prompt/completion keys accumulates.
    resp2 = type("R", (), {
        "llm_output": {"token_usage": {"prompt_tokens": 5,
                                       "completion_tokens": 1}},
        "generations": [],
    })()
    h.on_llm_end(resp2)
    assert h.in_tokens == 12
    assert h.out_tokens == 3


def test_on_llm_end_never_raises_on_garbage():
    h = _handler()
    h.on_llm_end(object())  # no attrs at all
    assert h.in_tokens == 0
    assert h.out_tokens == 0


def test_cost_usd_known_and_unknown():
    h = _handler(model="claude-haiku-4-5-20251001")
    h.in_tokens = 1_000_000
    h.out_tokens = 1_000_000
    cost = h.cost_usd()
    assert isinstance(cost, float)
    assert cost > 0

    h2 = _handler(model="some-unknown-model")
    assert h2.cost_usd() is None


# --- elapsed -------------------------------------------------------------

def test_elapsed_is_non_negative_float():
    h = _handler()
    assert isinstance(h.elapsed(), float)
    assert h.elapsed() >= 0.0


# --- render --------------------------------------------------------------

def test_render_fresh_handler_does_not_raise():
    from rich.console import Console

    h = _handler(model="claude-opus-4-7")
    r = h.render()
    console = Console(record=True, width=100)
    console.print(r)
    text = console.export_text()
    assert "claude-opus-4-7" in text
    assert "scanner" in text


def test_render_after_events_contains_state():
    from rich.console import Console

    h = _handler(model="claude-opus-4-7")
    h.on_tool_start({"name": "task"}, inputs={"subagent_type": "scanner"})
    h.on_tool_start({"name": "read_repo_file"}, inputs={"path": "a.py"})
    resp = type("R", (), {
        "llm_output": {"usage": {"input_tokens": 100, "output_tokens": 20}},
        "generations": [],
    })()
    h.on_llm_end(resp)

    console = Console(record=True, width=100)
    console.print(h.render())
    text = console.export_text()
    assert "scanner" in text
    assert "claude-opus-4-7" in text


# --- run_with_progress disabled contract --------------------------------

class _FakeAgent:
    def __init__(self):
        self.called_with_config = None
        self.invoke_count = 0
        self.last_kwargs = None

    def invoke(self, payload, **kwargs):
        self.invoke_count += 1
        self.last_kwargs = kwargs
        self.called_with_config = "config" in kwargs
        return {"messages": [type("M", (), {"content": "OUT"})()]}


def test_run_with_progress_disabled_no_callbacks():
    agent = _FakeAgent()
    out = run_with_progress(agent, {"messages": []},
                            enabled=False, total_files=3,
                            model="claude-opus-4-7")
    assert out == "OUT"
    assert agent.invoke_count == 1
    assert agent.called_with_config is False
    assert agent.last_kwargs == {}


# --- cooperative interrupt -----------------------------------------------

def test_interrupt_state_first_graceful_second_hard():
    s = _InterruptState()
    assert s.requested is False
    assert s.signal() == "graceful"
    assert s.requested is True
    assert s.signal() == "hard"           # second press → hard kill
    assert s.signal() == "hard"           # stays hard


def test_review_interrupted_is_keyboardinterrupt():
    # Subclassing KeyboardInterrupt (BaseException) means the handler's
    # `except Exception` guards never swallow a requested stop.
    assert issubclass(ReviewInterrupted, KeyboardInterrupt)


def test_progress_check_interrupt_raises_at_boundaries():
    h = _handler()
    st = _InterruptState()
    h._interrupt = st
    # Not requested → callbacks behave normally, no raise.
    h.on_tool_start({"name": "read_repo_file"}, inputs={"path": "a.py"})
    h.on_chain_start({}, {})
    # Requested → every step boundary raises ReviewInterrupted.
    st.requested = True
    with pytest.raises(ReviewInterrupted):
        h.on_tool_start({"name": "read_repo_file"}, inputs={"path": "b.py"})
    with pytest.raises(ReviewInterrupted):
        h.on_chain_start({}, {})
    with pytest.raises(ReviewInterrupted):
        h.on_llm_end(object())


class _InterruptingAgent:
    """Fake agent: simulates a graceful Ctrl+C mid-run by tripping the
    handler's interrupt state, then hitting the next callback boundary."""

    def __init__(self):
        self.config = None

    def invoke(self, payload, **kwargs):
        self.config = kwargs.get("config")
        handler = kwargs["config"]["callbacks"][0]
        handler._interrupt.requested = True
        handler.on_tool_start({"name": "read_repo_file"},
                              inputs={"path": "a.py"})  # raises
        raise AssertionError("unreachable: callback must have raised")


def test_run_with_progress_graceful_interrupt_returns_partial_live():
    holder = {}

    def factory(renderable, **kwargs):
        return _FakeLive(renderable, **kwargs)

    out = run_with_progress(_InterruptingAgent(), {"messages": []},
                            enabled=True, total_files=2,
                            model="claude-haiku-4-5-20251001",
                            _live_factory=factory, interrupt_holder=holder)
    assert out == ""
    assert holder["interrupted"] is True


def test_run_with_progress_graceful_interrupt_returns_partial_plain(capsys):
    holder = {}
    out = run_with_progress(_InterruptingAgent(), {"messages": []},
                            enabled=True, plain=True, total_files=2,
                            model="claude-haiku-4-5-20251001",
                            interrupt_holder=holder)
    assert out == ""
    assert holder["interrupted"] is True
    # the final plain status line is still flushed on interrupt
    assert "phase=" in capsys.readouterr().out


def test_run_with_progress_disabled_interrupt_returns_partial():
    holder = {}

    class _A:
        def invoke(self, payload, **kw):
            raise ReviewInterrupted()

    out = run_with_progress(_A(), {"messages": []}, enabled=False,
                            total_files=1, model="claude-haiku-4-5-20251001",
                            interrupt_holder=holder)
    assert out == ""
    assert holder["interrupted"] is True


def test_phases_constant_shape():
    assert PHASES == ["scanner", "library", "eng",
                      "deadcode", "techstack", "evaluator"]


class _FakeLive:
    def __init__(self, renderable, **kwargs):
        self.kwargs = kwargs
        self.updates = 0
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *exc):
        self.exited = True
        return False

    def update(self, renderable):
        self.updates += 1


class _EventAgent:
    """Fake agent that fires one callback event during invoke."""

    def __init__(self):
        self.config = None

    def invoke(self, payload, **kwargs):
        self.config = kwargs.get("config")
        handler = kwargs["config"]["callbacks"][0]
        handler.on_tool_start({"name": "read_repo_file"},
                              inputs={"path": "a.py"})
        return {"messages": [type("M", (), {"content": "OUT"})()]}


def test_run_with_progress_enabled_wires_handler_and_live():
    agent = _EventAgent()
    live_ref = {}

    def factory(renderable, **kwargs):
        live_ref["live"] = _FakeLive(renderable, **kwargs)
        return live_ref["live"]

    out = run_with_progress(agent, {"messages": []}, enabled=True,
                            total_files=2, model="claude-haiku-4-5-20251001",
                            _live_factory=factory)

    assert out == "OUT"
    live = live_ref["live"]
    assert live.entered and live.exited            # context-managed
    # config carried exactly one ReviewProgress callback
    cbs = agent.config["callbacks"]
    assert len(cbs) == 1 and isinstance(cbs[0], ReviewProgress)
    # the wired handler is the live one: it recorded the simulated read
    assert cbs[0].read_paths == {"a.py"}
    # _on_change fired during the event + the final update in finally
    assert live.updates >= 2


def test_run_with_progress_disabled_passes_base_config():
    agent = _FakeAgent()
    out = run_with_progress(agent, {"messages": []}, enabled=False,
                            total_files=3, model="claude-opus-4-7",
                            base_config={"recursion_limit": 7})
    assert out == "OUT"
    assert agent.last_kwargs == {"config": {"recursion_limit": 7}}
    assert agent.called_with_config is True


def test_run_with_progress_plain_emits_and_wires(capsys):
    agent = _EventAgent()
    out = run_with_progress(agent, {"messages": []}, enabled=True,
                            plain=True, total_files=2,
                            model="claude-haiku-4-5-20251001",
                            base_config={"recursion_limit": 5})
    assert out == "OUT"
    # base_config merged with the callbacks (no Live constructed)
    cfg = agent.config
    assert cfg["recursion_limit"] == 5
    assert len(cfg["callbacks"]) == 1
    assert isinstance(cfg["callbacks"][0], ReviewProgress)
    captured = capsys.readouterr().out
    assert "phase=" in captured and "files=" in captured
