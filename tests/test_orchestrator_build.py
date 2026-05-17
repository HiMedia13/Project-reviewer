import app.agent.orchestrator as orch


def test_build_agent_passes_subagents(monkeypatch):
    captured = {}

    def fake_create(**kw):
        captured.update(kw)
        return "AGENT"

    monkeypatch.setattr(orch, "create_deep_agent", fake_create)
    monkeypatch.setattr(orch, "ChatAnthropic", lambda **kw: "MODEL")
    agent = orch.build_agent(repo_path="/x", in_scope=["a.py"])
    assert agent == "AGENT"
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


def test_run_agent_passes_through_to_progress(monkeypatch):
    seen = {}

    def fake_rwp(agent, payload, **kw):
        seen["agent"] = agent
        seen["payload"] = payload
        seen.update(kw)
        return "RESULT"

    monkeypatch.setattr(orch, "run_with_progress", fake_rwp)
    out = orch.run_agent("AGENT", ["a.py", "b.py"], enabled=True, plain=True)

    assert out == "RESULT"
    assert seen["agent"] == "AGENT"
    assert seen["enabled"] is True and seen["plain"] is True
    assert seen["total_files"] == 2
    assert seen["model"] == orch.MODEL_NAME
    assert seen["base_config"] == {"recursion_limit": orch.RECURSION_LIMIT}
    content = seen["payload"]["messages"][0]["content"]
    assert "a.py" in content and "b.py" in content
