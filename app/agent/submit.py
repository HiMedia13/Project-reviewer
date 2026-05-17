"""Schema-pinned ``submit_findings`` tool with a caller-owned capture holder.

Real runs showed the orchestrator LLM emitting its final findings as
unreliable free text, so regex parsing yielded zero findings. Instead the
agent calls this tool: the model is *guided* toward the target shape via
the tool/arg description, but the argument is intentionally typed
permissively (``list[dict]``) so that improvised / malformed model output
(e.g. ``criterion: 123``, ``criterion_score: 80.9``,
``findings: "garbage"``) NEVER triggers a pydantic ``ValidationError``
before the callback runs. ``normalize_rows`` is the SOLE coercion
authority: every payload the model emits reaches it, and it coerces /
drops defensively without ever raising.

The normalized row shape mirrors what ``app/db.py`` ``insert_finding``
and ``app/findings_parser.parse_findings`` consume.
"""

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

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

# Human/model-readable description of the intended row schema. This is the
# ONLY place the strict shape is asserted toward the model — strict typing
# is deliberately NOT used on the args schema (it would raise before the
# never-raising normalize_rows callback runs).
_ROW_SHAPE_DOC = (
    "Each row is an object with: "
    "file_path (string, required, repo-relative path); "
    "criterion (string, e.g. 'security'); "
    "findings (array of objects, each {severity, location, evidence, msg}); "
    "criterion_score (integer or null); "
    "verified (boolean); "
    "verify_note (string)."
)


class FindingItem(BaseModel):
    """Documentation-only model of one finding sub-item.

    NOT used in the args_schema validation path (strict typing there
    would raise on improvised model output). Kept purely as a structured
    description of the intended shape for callers/readers.
    """

    model_config = ConfigDict(extra="ignore")

    severity: str = "low"
    location: str = ""
    evidence: str = ""
    msg: str = ""


class FindingRow(BaseModel):
    """Documentation-only model of one findings row.

    NOT used in the args_schema validation path; see ``FindingItem``.
    """

    model_config = ConfigDict(extra="ignore")

    file_path: str = Field(..., description="Repo-relative path of the file.")
    criterion: str = "unknown"
    findings: list[FindingItem] = Field(default_factory=list)
    criterion_score: int | None = None
    verified: bool = False
    verify_note: str = ""


class SubmitFindingsArgs(BaseModel):
    """Permissive args schema for the submit_findings tool.

    ``findings`` is typed as ``list[Any]`` (NOT ``list[FindingRow]`` or
    even ``list[dict]``) so that any JSON the model emits passes pydantic
    validation and reaches the callback, where ``normalize_rows`` is the
    sole coercion authority.
    ``extra="ignore"`` is pinned explicitly so improvised top-level keys
    are dropped deterministically rather than relying on the default.
    """

    model_config = ConfigDict(extra="ignore")

    # ``list[Any]`` (not ``list[dict]``): even ``list[dict]`` raises a
    # pydantic ValidationError when an element is not a dict (e.g. the
    # model emits ``[123, "nope"]``). The never-raise contract requires
    # EVERY element to pass through to normalize_rows untouched.
    findings: list[Any] = Field(
        default_factory=list,
        description=(
            "The full list of per-file findings rows to record. "
            + _ROW_SHAPE_DOC
        ),
    )


def _as_dict(obj: Any) -> dict | None:
    """Best-effort coerce a pydantic model or mapping to a plain dict."""
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    if isinstance(obj, dict):
        return obj
    return None


def _coerce_int_or_none(value: Any) -> int | None:
    """Return an int when value is int-like, otherwise None. Never raises.

    Bools are explicitly NOT ints here (``True``/``False`` -> None).
    Float-like values are accepted only when they are exact integers
    ("80" -> 80, 80.0 -> 80, "3.5" -> None, "n/a" -> None).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        s = value.strip()
        try:
            return int(s)
        except (TypeError, ValueError):
            try:
                f = float(s)
            except (TypeError, ValueError):
                return None
            return int(f) if f.is_integer() else None
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
    """Pure, never-raising normalizer: the SOLE coercion authority.

    Used both by the tool callback and (later, task #21) directly by the
    deterministic finalize guard. Accepts a list of dicts / pydantic
    models and returns plain-dict rows with exactly ``_ROW_KEYS``. Rows
    lacking a non-empty string ``file_path`` are dropped. A row whose
    ``findings`` is not a list yields ``findings == []``. Anything that is
    not a list yields ``[]``. Never raises.
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

    The tool argument is validated only against the permissive
    ``SubmitFindingsArgs`` (``findings: list[Any]``), so any JSON the
    model emits reaches the callback. The callback runs ``normalize_rows``
    (the sole coercion authority), OVERWRITES ``holder["findings"]`` and
    sets ``holder["submitted"] = True``. It never raises on malformed
    input: unusable payloads record 0 rows.
    """

    def _submit(findings: list[Any] | None = None) -> str:
        try:
            rows = normalize_rows(findings if isinstance(findings, list)
                                  else [])
        except Exception:
            rows = []
        # Overwrite (not append): the holder reflects only the latest call.
        holder["findings"] = rows
        holder["submitted"] = True
        return f"OK: recorded {len(rows)} finding rows."

    return StructuredTool.from_function(
        func=_submit,
        name="submit_findings",
        description=(
            "Submit the final structured review findings. Call this exactly "
            "once with the complete list of per-file findings rows. "
            + _ROW_SHAPE_DOC
        ),
        args_schema=SubmitFindingsArgs,
    )
