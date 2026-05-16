"""LangSmith trace cost aggregation.

Graceful-degradation contract: if LANGSMITH_API_KEY is not set, the
LangSmith package is missing, or any network/API error occurs, every
public function returns a zero-valued dict so the evaluation pipeline
never breaks due to missing cost data. Cost tracking is optional.
"""

import os


def _client():
    """Return an authenticated LangSmith Client, or None.

    Returns None when LANGSMITH_API_KEY is absent or when the
    langsmith package is unavailable — callers must treat None as
    'cost tracking disabled' and return the zero dict.
    """
    if not os.getenv("LANGSMITH_API_KEY"):
        return None
    try:
        from langsmith import Client
        return Client()
    except Exception:
        return None


def aggregate_cost(trace_id, project: str) -> dict:
    """Sum token counts and USD cost across all runs in a LangSmith trace.

    Returns a dict with keys input_tokens, output_tokens, cost_usd, run_url.
    If the client is unavailable (no API key, package missing) or trace_id
    is None or any error occurs during the LangSmith API calls, returns the
    zero dict immediately — the evaluation must never fail because of cost
    tracking being optional.
    """
    zero = {"input_tokens": 0, "output_tokens": 0,
            "cost_usd": 0.0, "run_url": None}
    client = _client()
    if client is None or not trace_id:
        return zero
    try:
        runs = list(client.list_runs(project_name=project, trace_id=trace_id))
    except Exception:
        return zero
    inp = sum(getattr(r, "prompt_tokens", 0) or 0 for r in runs)
    out = sum(getattr(r, "completion_tokens", 0) or 0 for r in runs)
    cost = sum(float(getattr(r, "total_cost", 0) or 0) for r in runs)
    run_url = None
    if runs:
        try:
            root = runs[0]
            run_url = getattr(client.read_run(getattr(root, "id", None)), "url", None)
        except Exception:
            run_url = None
    return {"input_tokens": inp, "output_tokens": out,
            "cost_usd": cost, "run_url": run_url}
