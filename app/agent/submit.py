"""Schema-pinned ``submit_findings`` tool with a caller-owned capture holder.

Real runs showed the orchestrator LLM emitting its final findings as
unreliable free text, so regex parsing yielded zero findings. Instead the
agent calls this tool: its argument is schema-validated by
LangChain/Anthropic and the normalized rows are captured deterministically
into a holder dict the caller owns, so the run path reads structured data.

The pinned schema mirrors what ``app/db.py`` ``insert_finding`` and
``app/findings_parser.parse_findings`` consume.
"""

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

# Exactly the keys every normalized finding row must carry.
_ROW_KEYS = (
    "file_path",
    "criterion",
    "findings",
    "criterion_score",
    "verified",
    "verify_note",
)
_ITEM_KEYS = ("severity", "location", "evidence", "msg")


class FindingItem(BaseModel):
    """One issue found inside a file. All fields optional with defaults."""

    severity: str = "low"
    location: str = ""
    evidence: str = ""
    msg: str = ""


class FindingRow(BaseModel):
    """Per-file, per-criterion findings row. Mirrors the DB insert shape."""

    file_path: str = Field(..., description="Repo-relative path of the file.")
    criterion: str = "unknown"
    findings: list[FindingItem] = Field(default_factory=list)
    criterion_score: int | None = None
    verified: bool = False
    verify_note: str = ""


class SubmitFindingsArgs(BaseModel):
    """Args schema for the submit_findings tool."""

    findings: list[FindingRow] = Field(
        default_factory=list,
        description="The full list of per-file findings rows to record.",
    )


def _as_dict(obj: Any) -> dict | None:
    """Best-effort coerce a pydantic model or mapping to a plain dict."""
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    if isinstance(obj, dict):
        return obj
    return None


def _coerce_int_or_none(value: Any) -> int | None:
    """Return an int when value is int-like, otherwise None. Never raises."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_item(raw: Any) -> dict:
    """Normalize one finding sub-item to {severity,location,evidence,msg}."""
    d = _as_dict(raw) or {}
    return {
        "severity": str(d.get("severity", "low") or "low"),
        "location": str(d.get("location", "") or ""),
        "evidence": str(d.get("evidence", "") or ""),
        "msg": str(d.get("msg", "") or ""),
    }


def normalize_rows(raw_list: Any) -> list[dict]:
    """Pure, never-raising normalizer used by the tool and finalize guard.

    Accepts a list of dicts / pydantic models and returns plain-dict rows
    with exactly ``_ROW_KEYS``. Rows lacking a non-empty string
    ``file_path`` are dropped. Anything that is not a list yields ``[]``.
    """
    if not isinstance(raw_list, list):
        return []

    rows: list[dict] = []
    for entry in raw_list:
        d = _as_dict(entry)
        if d is None:
            continue

        file_path = d.get("file_path")
        if not isinstance(file_path, str) or not file_path.strip():
            continue

        raw_findings = d.get("findings")
        if not isinstance(raw_findings, list):
            raw_findings = []

        criterion = d.get("criterion", "unknown")
        if not isinstance(criterion, str) or not criterion:
            criterion = "unknown"

        verify_note = d.get("verify_note", "")
        if not isinstance(verify_note, str):
            verify_note = str(verify_note)

        rows.append(
            {
                "file_path": file_path,
                "criterion": criterion,
                "findings": [_normalize_item(f) for f in raw_findings],
                "criterion_score": _coerce_int_or_none(
                    d.get("criterion_score")
                ),
                "verified": bool(d.get("verified", False)),
                "verify_note": verify_note,
            }
        )
    return rows


def make_submit_tool(holder: dict) -> StructuredTool:
    """Return the ``submit_findings`` tool, capturing into *holder*.

    The tool's input is validated by LangChain/Anthropic against
    ``SubmitFindingsArgs``. On call it normalizes the rows, overwrites
    ``holder["findings"]`` and sets ``holder["submitted"] = True``. It
    never raises on malformed input: unusable payloads record 0 rows.
    """

    def _submit(findings: list[FindingRow] | None = None) -> str:
        try:
            rows = normalize_rows(list(findings) if findings else [])
        except Exception:
            rows = []
        holder["findings"] = rows
        holder["submitted"] = True
        return f"OK: recorded {len(rows)} finding rows."

    return StructuredTool.from_function(
        func=_submit,
        name="submit_findings",
        description=(
            "Submit the final structured review findings. Call this exactly "
            "once with the complete list of per-file findings rows. Each row: "
            "file_path (required), criterion, findings[] "
            "(severity/location/evidence/msg), criterion_score, verified, "
            "verify_note."
        ),
        args_schema=SubmitFindingsArgs,
    )
