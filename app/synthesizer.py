"""Synthesizer module: aggregates per-criterion finding scores into an overall score.

Only verified findings contribute to scoring. A criterion score is the mean of
verified finding scores for that criterion (None if none exist). The overall score
is the mean of present criterion averages (None if all criteria have no verified scores).
``r["verified"]`` may be int 0/1 (from SQLite) or bool (from fresh parse) —
``if not r["verified"]`` handles both.
"""

CRITERIA = ["library", "eng", "deadcode", "techstack"]


def synthesize(finding_rows) -> dict:
    """Aggregate verified finding rows into per-criterion and overall scores.

    For each criterion in CRITERIA, computes the mean ``criterion_score`` of all
    rows where ``verified`` is truthy. Criteria with no verified scores yield None.
    The overall ``score`` is the mean of the **rounded** per-criterion averages
    (rounding is applied both at the per-criterion level and again to the overall),
    or None if all criteria are absent.

    Args:
        finding_rows: Iterable of dicts with keys ``criterion``, ``criterion_score``,
            and ``verified`` (int 0/1 or bool).

    Returns:
        Dict with ``score`` (int or None) and ``criteria`` (dict mapping each
        criterion name to its averaged score or None).
    """
    by_crit: dict[str, list[int]] = {c: [] for c in CRITERIA}
    for r in finding_rows:
        if not r["verified"]:
            continue
        c = r["criterion"]
        if c in by_crit and r["criterion_score"] is not None:
            by_crit[c].append(r["criterion_score"])
    criteria = {
        c: (round(sum(v) / len(v)) if v else None) for c, v in by_crit.items()
    }
    present = [s for s in criteria.values() if s is not None]
    overall = round(sum(present) / len(present)) if present else None
    return {"score": overall, "criteria": criteria}
