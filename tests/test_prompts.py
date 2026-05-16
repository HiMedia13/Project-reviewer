from app.agent.prompts import (
    ORCHESTRATOR, SCANNER, CRITERIA_PROMPTS, EVALUATOR,
)


def test_all_four_criteria_prompts_present():
    assert set(CRITERIA_PROMPTS) == {"library", "eng", "deadcode", "techstack"}
    for p in CRITERIA_PROMPTS.values():
        assert "JSON" in p


def test_orchestrator_mentions_not_runtime():
    assert "작동 여부" in ORCHESTRATOR
    assert "scanner" in ORCHESTRATOR


def test_evaluator_mentions_websearch_and_hallucination():
    assert "할루시네이션" in EVALUATOR
    assert "tavily" in EVALUATOR.lower()
    assert SCANNER
