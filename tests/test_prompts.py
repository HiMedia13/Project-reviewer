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


def test_orchestrator_enforces_json_array_final_output():
    # Locks the final-output contract so the orchestrator returns the
    # parseable JSON array, not a prose summary (root cause of a real
    # zero-findings run).
    assert "최종 출력 계약" in ORCHESTRATOR
    assert "JSON 배열" in ORCHESTRATOR
    assert "[" in ORCHESTRATOR and "코드펜스" in ORCHESTRATOR


def test_evaluator_mentions_websearch_and_hallucination():
    assert "할루시네이션" in EVALUATOR
    assert "tavily" in EVALUATOR.lower()


def test_scanner_defines_json_output():
    assert "JSON" in SCANNER
    assert "import_graph_summary" in SCANNER


def test_criteria_prompts_inject_their_key():
    for key, prompt in CRITERIA_PROMPTS.items():
        assert f'"criterion": "{key}"' in prompt
