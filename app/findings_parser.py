import json
import re

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract_json(raw: str) -> str:
    m = _FENCE.search(raw)
    if m:
        return m.group(1).strip()
    start = raw.find("[")
    end = raw.rfind("]")
    if start != -1 and end != -1 and end > start:
        return raw[start:end + 1]
    return raw.strip()


def parse_findings(raw: str) -> list[dict]:
    try:
        data = json.loads(_extract_json(raw))
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    rows = []
    for item in data:
        if not isinstance(item, dict) or "file_path" not in item:
            continue
        rows.append({
            "file_path": item["file_path"],
            "criterion": item.get("criterion", "unknown"),
            "findings": item.get("findings", []),
            "criterion_score": item.get("criterion_score"),
            "verified": bool(item.get("verified", False)),
            "verify_note": item.get("verify_note", ""),
        })
    return rows
