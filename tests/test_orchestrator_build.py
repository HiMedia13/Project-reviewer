import app.agent.orchestrator as orch


def _submit_named(tools):
    return [t for t in tools if getattr(t, "name", None) == "submit_findings"]


def test_build_agent_passes_subagents(monkeypatch):
    captured = {}

    def fake_create(**kw):
        captured.update(kw)
        return "AGENT"

    monkeypatch.setattr(orch, "create_deep_agent", fake_create)
    monkeypatch.setattr(orch, "ChatAnthropic", lambda **kw: "MODEL")
    agent, holder = orch.build_agent(repo_path="/x", in_scope=["a.py"])
    assert agent == "AGENT"
    assert holder == {}
    assert isinstance(holder, dict)
    subs = captured["subagents"]
    names = {s["name"] for s in subs}
    assert names == {"scanner", "library", "eng", "deadcode",
                     "techstack", "evaluator"}
    # deepagents' SubAgent schema requires name/description/system_prompt;
    # a non-empty system_prompt key (not "prompt") must be present on each.
    for s in subs:
        assert s["system_prompt"]
        assert "prompt" not in s
    assert captured["model"] == "MODEL"

    # submit_findings tool is wired into the orchestrator top-level tools
    # and the evaluator subagent's tools (only).
    assert _submit_named(captured["tools"]), \
        "orchestrator tools missing submit_findings"
    evaluator = next(s for s in subs if s["name"] == "evaluator")
    assert _submit_named(evaluator["tools"]), \
        "evaluator tools missing submit_findings"
    for s in subs:
        if s["name"] != "evaluator":
            assert not _submit_named(s["tools"]), \
                f"{s['name']} should not have submit_findings"


def test_run_agent_passes_through_to_progress(monkeypatch):
    seen = {}

    def fake_rwp(agent, payload, **kw):
        seen["agent"] = agent
        seen["payload"] = payload
        seen.update(kw)
        return "RESULT"

    fin_seen = {}

    def fake_finalize(holder, seed_text, *, model):
        fin_seen["holder"] = holder
        fin_seen["seed_text"] = seed_text
        fin_seen["model"] = model
        return [{"file_path": "fin.py"}]

    monkeypatch.setattr(orch, "run_with_progress", fake_rwp)
    monkeypatch.setattr(orch, "finalize_findings", fake_finalize)

    holder = {}
    out = orch.run_agent("AGENT", holder, ["a.py", "b.py"],
                         enabled=True, plain=True)

    assert out == [{"file_path": "fin.py"}]
    assert seen["agent"] == "AGENT"
    assert seen["enabled"] is True and seen["plain"] is True
    assert seen["total_files"] == 2
    assert seen["model"] == orch.MODEL_NAME
    assert seen["base_config"] == {"recursion_limit": orch.RECURSION_LIMIT}
    content = seen["payload"]["messages"][0]["content"]
    assert "a.py" in content and "b.py" in content
    # holder["raw"] is persisted and seeds the finalize guard.
    assert holder["raw"] == "RESULT"
    assert fin_seen["seed_text"] == "RESULT"
    assert fin_seen["model"] == orch.MODEL_NAME
    assert fin_seen["holder"] is holder


def test_run_agent_uses_holder_findings_when_submitted(monkeypatch):
    def fake_rwp(agent, payload, **kw):
        return "RESULT"

    def boom_finalize(*a, **kw):
        raise AssertionError("finalize_findings must not be called")

    monkeypatch.setattr(orch, "run_with_progress", fake_rwp)
    monkeypatch.setattr(orch, "finalize_findings", boom_finalize)

    holder = {"submitted": True, "findings": [{"file_path": "x"}]}
    out = orch.run_agent("AGENT", holder, ["a.py"],
                         enabled=False, plain=False)

    assert out == [{"file_path": "x"}]
    assert holder["raw"] == "RESULT"
