from app.findings_parser import parse_findings


def test_parses_json_array_with_fences():
    raw = """위 결과입니다:
```json
[{"file_path":"a.py","criterion":"deadcode",
  "findings":[{"severity":"low","location":"a.py:3",
               "evidence":"unused","msg":"dead"}],
  "criterion_score":70,"verified":true,"verify_note":"ok"}]
```"""
    rows = parse_findings(raw)
    assert len(rows) == 1
    assert rows[0]["file_path"] == "a.py"
    assert rows[0]["verified"] is True
    assert rows[0]["criterion_score"] == 70


def test_invalid_json_returns_empty():
    assert parse_findings("not json at all") == []


def test_missing_fields_get_defaults():
    rows = parse_findings('[{"file_path":"x.py","criterion":"eng"}]')
    assert rows[0]["criterion_score"] is None
    assert rows[0]["verified"] is False
    assert rows[0]["findings"] == []
