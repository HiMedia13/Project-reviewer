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


def test_orchestrator_enforces_submit_findings_contract():
    # The final-output contract is now a tool call: finishing is only
    # possible via submit_findings, and JSON must not be emitted as text
    # (root cause of a real zero-findings run).
    assert "submit_findings" in ORCHESTRATOR
    assert "유일" in ORCHESTRATOR
    assert "텍스트" in ORCHESTRATOR
    assert "submit_findings" in EVALUATOR


def test_orchestrator_and_evaluator_require_tech_assessment():
    # The PRIMARY deliverable: both the orchestrator final contract and
    # the evaluator must require submit_tech_assessment (in addition to
    # the secondary submit_findings flow).
    assert "submit_tech_assessment" in ORCHESTRATOR
    assert "submit_tech_assessment" in EVALUATOR
    # The evaluator's hallucination/websearch validation still applies.
    assert "할루시네이션" in EVALUATOR
    assert "tavily" in EVALUATOR.lower()


def test_evaluator_mentions_websearch_and_hallucination():
    assert "할루시네이션" in EVALUATOR
    assert "tavily" in EVALUATOR.lower()


def test_scanner_defines_json_output():
    assert "JSON" in SCANNER
    assert "import_graph_summary" in SCANNER


def test_scanner_emits_purpose_and_stack():
    # SCANNER now also infers the project purpose and a raw stack
    # inventory, feeding the project-level tech assessment.
    assert "purpose" in SCANNER
    assert "stack" in SCANNER
    assert "import_graph_summary" in SCANNER
    assert "JSON" in SCANNER


def test_criteria_prompts_inject_their_key():
    # techstack is no longer a per-file "criterion": "techstack" prompt;
    # only library/eng/deadcode inject their criterion key.
    for key in ("library", "eng", "deadcode"):
        assert f'"criterion": "{key}"' in CRITERIA_PROMPTS[key]
    # techstack is now a PROJECT-LEVEL assessment that submits via the
    # submit_tech_assessment tool and reasons about project purpose.
    techstack = CRITERIA_PROMPTS["techstack"]
    assert '"criterion": "techstack"' not in techstack
    assert "submit_tech_assessment" in techstack
    assert "목적" in techstack
    assert "JSON" in techstack
    # Reviewer M2: guard against a future edit reintroducing a per-file
    # row schema — techstack is strictly PROJECT-LEVEL.
    assert '"file_path"' not in techstack
    assert "프로젝트 전체" in techstack


def test_orchestrator_parallelizes_criteria_then_evaluator():
    # P6: scanner first, the 4 criteria run in parallel in one turn,
    # evaluator only after all four finish.
    assert "병렬" in ORCHESTRATOR
    assert "동시" in ORCHESTRATOR
    # scanner must be instructed before the parallel-criteria dispatch.
    scanner_idx = ORCHESTRATOR.index("scanner")
    parallel_idx = ORCHESTRATOR.index("병렬")
    assert scanner_idx < parallel_idx
    # evaluator runs after all four criteria are done.
    assert "네 개의 기준 subagent가 모두 끝난 후에만 evaluator" in ORCHESTRATOR
    assert ORCHESTRATOR.index("모두 끝난 후") < ORCHESTRATOR.index(
        "evaluator의 검증 결과")
