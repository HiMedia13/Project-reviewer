from app.synthesizer import synthesize

CRITERIA = ["library", "eng", "deadcode", "techstack"]


def test_synthesize_averages_per_criterion_and_overall():
    rows = [
        {"criterion": "library", "criterion_score": 80, "verified": 1},
        {"criterion": "library", "criterion_score": 60, "verified": 1},
        {"criterion": "deadcode", "criterion_score": 90, "verified": 1},
    ]
    out = synthesize(rows)
    assert out["criteria"]["library"] == 70
    assert out["criteria"]["deadcode"] == 90
    assert out["criteria"]["eng"] is None
    assert out["score"] == 80  # mean of present criteria (70, 90)


def test_unverified_findings_excluded_from_score():
    rows = [
        {"criterion": "deadcode", "criterion_score": 10, "verified": 0},
        {"criterion": "deadcode", "criterion_score": 90, "verified": 1},
    ]
    out = synthesize(rows)
    assert out["criteria"]["deadcode"] == 90


def test_empty_rows_yield_none_scores():
    out = synthesize([])
    assert out["score"] is None
    assert all(v is None for v in out["criteria"].values())


def test_verified_false_bool_is_excluded():
    rows = [
        {"criterion": "library", "criterion_score": 10, "verified": False},
        {"criterion": "library", "criterion_score": 88, "verified": True},
    ]
    out = synthesize(rows)
    assert out["criteria"]["library"] == 88
