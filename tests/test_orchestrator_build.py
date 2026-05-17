import app.agent.orchestrator as orch


def _submit_named(tools):
    return [t for t in tools if getattr(t, "name", None) == "submit_findings"]


def _tech_named(tools):
    return [t for t in tools
            if getattr(t, "name", None) == "submit_tech_assessment"]


def test_build_agent_passes_subagents(monkeypatch):
    captured = {}

    def fake_create(**kw):
        captured.update(kw)
        return "AGENT"

    monkeypatch.setattr(orch, "create_deep_agent", fake_create)
    # Make the model identity observable so the hybrid wiring (Sonnet
    # orchestrator vs Haiku subagents) is verifiable.
    monkeypatch.setattr(
        orch, "ChatAnthropic", lambda **kw: ("CHAT", kw.get("model")))
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
    # Hybrid model: orchestrator on the Sonnet model id, every subagent
    # on the Haiku (REVIEWER_MODEL) id, and the two must actually differ.
    assert captured["model"] == ("CHAT", orch.ORCH_MODEL_NAME)
    for s in subs:
        assert s["model"] == ("CHAT", orch.MODEL_NAME)
    assert orch.ORCH_MODEL_NAME != orch.MODEL_NAME

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

    # submit_tech_assessment (PRIMARY deliverable) is wired into the
    # orchestrator top-level tools, the techstack subagent (project-level),
    # and the evaluator subagent — and into NO other criteria subagent.
    assert _tech_named(captured["tools"]), \
        "orchestrator tools missing submit_tech_assessment"
    techstack = next(s for s in subs if s["name"] == "techstack")
    assert _tech_named(techstack["tools"]), \
        "techstack tools missing submit_tech_assessment"
    assert _tech_named(evaluator["tools"]), \
        "evaluator tools missing submit_tech_assessment"
    for s in subs:
        if s["name"] in ("scanner", "library", "eng", "deadcode"):
            assert not _tech_named(s["tools"]), \
                f"{s['name']} should not have submit_tech_assessment"


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

    tech_seen = {}

    def fake_finalize_tech(holder, seed_text, *, model):
        tech_seen["holder"] = holder
        tech_seen["seed_text"] = seed_text
        tech_seen["model"] = model
        return {"purpose": "p", "stack": [{"tech": "x"}]}

    monkeypatch.setattr(orch, "run_with_progress", fake_rwp)
    monkeypatch.setattr(orch, "finalize_findings", fake_finalize)
    monkeypatch.setattr(orch, "finalize_tech", fake_finalize_tech)

    holder = {}
    rows, tech_assessment, raw = orch.run_agent(
        "AGENT", holder, ["a.py", "b.py"], enabled=True, plain=True)

    assert rows == [{"file_path": "fin.py"}]
    assert tech_assessment == {"purpose": "p", "stack": [{"tech": "x"}]}
    assert raw == "RESULT"
    # The PRIMARY deliverable's finalize fallback runs on the Sonnet
    # orchestrator model, seeded by the run's raw output.
    assert tech_seen["seed_text"] == "RESULT"
    assert tech_seen["model"] == orch.ORCH_MODEL_NAME
    assert tech_seen["holder"] is holder
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

    def boom_finalize_tech(*a, **kw):
        raise AssertionError("finalize_tech must not be called")

    monkeypatch.setattr(orch, "run_with_progress", fake_rwp)
    monkeypatch.setattr(orch, "finalize_findings", boom_finalize)
    monkeypatch.setattr(orch, "finalize_tech", boom_finalize_tech)

    holder = {
        "submitted": True,
        "findings": [{"file_path": "x"}],
        "tech_submitted": True,
        "tech_assessment": {"stack": [{"tech": "x"}], "purpose": "p"},
    }
    rows, tech_assessment, raw = orch.run_agent(
        "AGENT", holder, ["a.py"], enabled=False, plain=False)

    assert rows == [{"file_path": "x"}]
    assert tech_assessment == {"stack": [{"tech": "x"}], "purpose": "p"}
    assert raw == "RESULT"
    assert holder["raw"] == "RESULT"


def test_build_payload_consistent_with_parallel_contract():
    # The user-turn message must not contradict the system prompt's
    # parallel-dispatch contract (the P3/P6 prompt-vs-payload bug class).
    content = orch.build_payload(["a.py", "b.py"])["messages"][0]["content"]
    assert "병렬" in content                       # criteria run in parallel
    assert "submit_findings" in content            # terminate via the tool
    assert "scanner" in content                    # scanner first
    # the old "scanner → 4기준 → evaluator 순으로" ordering is gone
    assert "순으로" not in content
    assert "submit_tech_assessment" in content   # primary deliverable
    assert "a.py" in content and "b.py" in content
