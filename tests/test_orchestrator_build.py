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
    names = {s["name"] for s in captured["subagents"]}
    assert names == {"scanner", "library", "eng", "deadcode",
                     "techstack", "evaluator"}
    assert captured["model"] == "MODEL"
