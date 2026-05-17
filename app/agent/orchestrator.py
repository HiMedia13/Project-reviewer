import os

from deepagents import create_deep_agent
from langchain_anthropic import ChatAnthropic

from app.agent.prompts import (
    ORCHESTRATOR, SCANNER, CRITERIA_PROMPTS, EVALUATOR,
)
from app.agent.tools import make_file_tools, make_tavily_tool
from app.agent.progress import run_with_progress

MODEL_NAME = os.getenv("REVIEWER_MODEL", "claude-opus-4-7")
# Safety bound on the orchestrator graph's supersteps so a runaway
# multi-agent loop terminates instead of burning tokens indefinitely.
RECURSION_LIMIT = int(os.getenv("REVIEWER_RECURSION_LIMIT", "60"))


def build_agent(repo_path: str, in_scope: list[str]):
    list_tool, read_tool = make_file_tools(repo_path, in_scope)
    tavily = make_tavily_tool()
    model = ChatAnthropic(model=MODEL_NAME, max_tokens=8000)

    subagents = [
        {"name": "scanner", "description": "프로젝트 구조 스캐너",
         "system_prompt": SCANNER, "tools": [list_tool, read_tool]},
    ]
    for crit, prompt in CRITERIA_PROMPTS.items():
        subagents.append({
            "name": crit,
            "description": f"{crit} 기준 평가자",
            "system_prompt": prompt,
            "tools": [list_tool, read_tool],
        })
    subagents.append({
        "name": "evaluator",
        "description": "findings 검증자(critic), 신기술은 웹검색",
        "system_prompt": EVALUATOR,
        "tools": [read_tool, tavily],
    })

    return create_deep_agent(
        tools=[list_tool, read_tool],
        system_prompt=ORCHESTRATOR,
        subagents=subagents,
        model=model,
    )


def build_payload(in_scope: list[str]) -> dict:
    msg = (
        "다음 in-scope 파일들을 평가하라. 워크플로우대로 scanner → 4기준 → "
        "evaluator 순으로 진행하고, 최종적으로 evaluator가 검증한 결과를 "
        "JSON 배열로만 반환하라.\n파일:\n" + "\n".join(in_scope)
    )
    return {"messages": [{"role": "user", "content": msg}]}


def run_agent(agent, in_scope: list[str], *, enabled: bool = False,
              plain: bool = False) -> str:
    return run_with_progress(
        agent, build_payload(in_scope),
        enabled=enabled, plain=plain,
        total_files=len(in_scope), model=MODEL_NAME,
        base_config={"recursion_limit": RECURSION_LIMIT},
    )
