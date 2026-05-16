"""LangChain tool factories for the DeepAgents subagents.

Provides:
  - make_file_tools: returns (list_scope_files, read_repo_file) closured over
    the cloned repo path and the in-scope file allowlist from the scope planner.
  - make_tavily_tool: returns a real TavilySearch tool when TAVILY_API_KEY is
    set, otherwise returns a graceful stub so the evaluator still functions.
"""

import os
from pathlib import Path

from langchain_core.tools import tool


def make_file_tools(repo_path: str, in_scope: list[str]):
    """Return (list_scope_files, read_repo_file) tools scoped to *repo_path*.

    Only files listed in *in_scope* may be read; path-traversal attempts are
    rejected by both the scope check and a commonpath defence-in-depth guard.
    """
    root = Path(repo_path).resolve()
    scope = set(in_scope)

    @tool
    def list_scope_files() -> str:
        """평가 대상(in-scope) 파일 경로 목록을 줄바꿈으로 반환한다."""
        return "\n".join(sorted(scope))

    @tool
    def read_repo_file(path: str) -> str:
        """in-scope인 레포 파일 하나의 텍스트 내용을 반환한다."""
        if path not in scope:
            return f"ERROR: '{path}' is not in scope."
        target = (root / path).resolve()
        if os.path.commonpath([str(root), str(target)]) != str(root):
            return f"ERROR: '{path}' is not in scope (path traversal)."
        try:
            return target.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            return f"ERROR: cannot read '{path}': {e}"

    return list_scope_files, read_repo_file


def make_tavily_tool():
    """Return a TavilySearch tool, or a stub when TAVILY_API_KEY is unset."""
    if not os.getenv("TAVILY_API_KEY"):
        @tool
        def tavily_search(query: str) -> str:
            """웹 검색 (비활성: TAVILY_API_KEY 없음)."""
            return "웹검색 미확인: TAVILY_API_KEY가 설정되지 않음."
        return tavily_search
    from langchain_tavily import TavilySearch
    return TavilySearch(max_results=3)
