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
