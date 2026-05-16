"""Scope planner: decides full vs incremental evaluation for a review run."""

from dataclasses import dataclass
from typing import Literal

from app.repo_manager import changed_files, reverse_import_expand


@dataclass
class Scope:
    """Result of scope planning.

    Attributes:
        mode: "full" if all files are re-evaluated, "incremental" if only
              changed files and their dependents are sent to the agent.
        in_scope: Files to send to the LLM agent for evaluation.
        cached: Files whose prior evaluation results can be reused as-is.
    """

    mode: Literal["full", "incremental"]  # "full" | "incremental"
    in_scope: list[str]  # files to send to the agent
    cached: list[str]    # unchanged files reused from prior evaluation


def plan_scope(prior_sha, repo_path, all_files, force: bool) -> Scope:
    """Decide whether to run a full or incremental review.

    A full evaluation is performed when:
    - ``force`` is True, or
    - ``prior_sha`` is None (no previous run recorded), or
    - computing the git diff raises any exception (safe fallback).

    Otherwise an incremental evaluation is performed: only files that changed
    since ``prior_sha`` plus any files that import them are included in
    ``in_scope``; the rest go to ``cached``.

    Args:
        prior_sha: The commit SHA of the previous successful review, or None.
        repo_path: Filesystem path to the cloned repository.
        all_files: Full list of source file paths (repo-relative posix paths).
        force: When True, always run a full evaluation regardless of prior_sha.

    Returns:
        A :class:`Scope` instance describing which files to evaluate.
    """
    all_list = list(all_files)
    if force or not prior_sha:
        return Scope("full", all_list, [])
    try:
        changed = changed_files(repo_path, prior_sha)
        expanded = reverse_import_expand(repo_path, changed, all_list)
    except Exception:
        return Scope("full", all_list, [])
    in_scope = sorted(f for f in all_list if f in expanded)
    cached = sorted(f for f in all_list if f not in expanded)
    return Scope("incremental", in_scope, cached)
