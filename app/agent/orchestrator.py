import os

from deepagents import create_deep_agent
from langchain_anthropic import ChatAnthropic

from app.agent.prompts import (
    ORCHESTRATOR, SCANNER, CRITERIA_PROMPTS, EVALUATOR,
)
from app.agent.tools import make_file_tools, make_tavily_tool
from app.agent.progress import run_with_progress
from app.agent.submit import make_submit_tool, finalize_findings

MODEL_NAME = os.getenv("REVIEWER_MODEL", "claude-opus-4-7")
# Safety bound on the orchestrator graph's supersteps so a runaway
# multi-agent loop terminates instead of burning tokens indefinitely.
RECURSION_LIMIT = int(os.getenv("REVIEWER_RECURSION_LIMIT", "60"))


def build_agent(repo_path: str, in_scope: list[str]):
    list_tool, read_tool = make_file_tools(repo_path, in_scope)
    tavily = make_tavily_tool()
    model = ChatAnthropic(model=MODEL_NAME, max_tokens=8000)

    holder: dict = {}
    submit_tool = make_submit_tool(holder)

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
        "tools": [read_tool, tavily, submit_tool],
    })

    return create_deep_agent(
        tools=[list_tool, read_tool, submit_tool],
        system_prompt=ORCHESTRATOR,
        subagents=subagents,
        model=model,
    ), holder


def build_payload(in_scope: list[str]) -> dict:
    msg = (
        "다음 in-scope 파일들을 평가하라. 워크플로우대로 scanner → 4기준 → "
        "evaluator 순으로 진행하고, evaluator가 검증한 최종 결과를 "
        "submit_findings 도구를 호출해 제출하라(종료의 유일한 방법). "
        "JSON을 메시지 본문으로 출력하지 말 것.\n파일:\n" + "\n".join(in_scope)
    )
    return {"messages": [{"role": "user", "content": msg}]}


def run_agent(agent, holder, in_scope: list[str], *, enabled: bool = False,
              plain: bool = False) -> list[dict]:
    raw = run_with_progress(
        agent, build_payload(in_scope),
        enabled=enabled, plain=plain,
        total_files=len(in_scope), model=MODEL_NAME,
        base_config={"recursion_limit": RECURSION_LIMIT},
    )
    # Persist raw output so P4/main can write raw-<id>.txt for diagnostics.
    holder["raw"] = raw if isinstance(raw, str) else repr(raw)

    if (holder.get("submitted")
            and isinstance(holder.get("findings"), list)
            and holder["findings"]):
        rows = holder["findings"]
    else:
        rows = finalize_findings(
            holder, seed_text=holder.get("raw", ""), model=MODEL_NAME,
        )
    return rows
