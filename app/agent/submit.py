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


# Korean instruction prefixed to the seed text for the finalize guard. The
# model is forced (tool_choice) to emit a submit_findings call, so this only
# needs to point it at the schema and forbid prose.
_FINALIZE_INSTRUCTION = (
    "아래는 코드 평가 결과 원문이다. 이를 submit_findings 스키마"
    "(file_path/criterion/findings/criterion_score/verified/verify_note)에 "
    "맞는 JSON으로 변환해 submit_findings 도구를 호출하라. 설명 금지."
)


def _default_chat(model: str):
    """Lazily construct the real chat model.

    Imported lazily so importing this module (and the ``_model_factory``
    test seam) needs no network / API key / langchain_anthropic install.
    """
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=model, max_tokens=8000)


def _extract_findings_arg(resp: Any) -> Any:
    """Pull the raw ``findings`` arg out of an AIMessage-like response.

    Defensive against: missing ``.tool_calls``, empty/non-list tool_calls,
    entries that are not dicts, missing/non-dict ``args``. Only a tool call
    actually named ``submit_findings`` is honored (a stray call to some
    other tool yields nothing). Never raises; returns whatever it finds
    (passed on to normalize_rows, the sole coercion authority, which
    itself never raises).
    """
    tool_calls = getattr(resp, "tool_calls", None)
    if not isinstance(tool_calls, list) or not tool_calls:
        return None

    chosen = None
    for tc in tool_calls:
        if isinstance(tc, dict) and tc.get("name") == "submit_findings":
            chosen = tc
            break
    if not isinstance(chosen, dict):
        return None

    args = chosen.get("args")
    if not isinstance(args, dict):
        return None
    return args.get("findings")


def finalize_findings(
    holder: dict,
    seed_text: str,
    *,
    model: str,
    _model_factory=None,
) -> list[dict]:
    """Deterministic finalize guard: force a structured capture.

    ISOLATED helper (P2): NOT wired into the orchestrator yet. When the
    agentic loop finishes without a proper ``submit_findings`` call the
    *holder* is empty. This makes ONE constrained model call forced (via
    ``tool_choice``) to emit a ``submit_findings`` tool call, then reads
    the structured arguments DIRECTLY off the response — it does NOT rely
    on the agent loop or on tool-execution side effects.

    No-op condition (documented choice): skip the model call ONLY when the
    holder already holds a NON-EMPTY findings list AND ``submitted`` is
    True. ``submitted`` True with an empty list still attempts the guard
    (an early/empty submission is exactly the failure mode this guards).

    NEVER raises: any failure (network, bad response shape, attribute
    errors) degrades to recording the pre-existing findings (or ``[]``)
    and marking submitted, so a finalize failure cannot abort the
    pipeline.
    """
    if holder.get("submitted") is True:
        existing = holder.get("findings")
        if isinstance(existing, list) and existing:
            return existing

    try:
        llm = (_model_factory or _default_chat)(model)
        tool = make_submit_tool(holder)
        bound = llm.bind_tools([tool], tool_choice="submit_findings")
        messages = [
            (
                "human",
                _FINALIZE_INSTRUCTION + "\n\n---\n" + (seed_text or ""),
            )
        ]
        resp = bound.invoke(messages)
        raw = _extract_findings_arg(resp)
        rows = normalize_rows(raw)
        holder["findings"] = rows
        holder["submitted"] = True
        return rows
    except Exception:
        # Degrade gracefully: never propagate. Preserve any pre-existing
        # findings list; otherwise record zero rows.
        fallback = holder.get("findings")
        if not isinstance(fallback, list):
            fallback = []
        holder["findings"] = fallback
        holder["submitted"] = True
        return fallback


# ==========================================================================
# P8: project-level tech-stack-fit assessment. The product's PRIMARY desired
# output. Mirrors EXACTLY the validated submit_findings / finalize_findings
# patterns: a permissive args schema, ``normalize_tech_assessment`` as the
# SOLE never-raising coercion authority, and a deterministic tool_choice-
# forced finalize fallback. ISOLATED — not wired into the orchestrator (P9).
# ==========================================================================

# Exactly the keys every normalized tech assessment must carry.
_TECH_KEYS = ("purpose", "stack", "stack_verdict", "stack_score")
# Exactly the keys every normalized stack item must carry.
_TECH_ITEM_KEYS = (
    "tech",
    "role",
    "used_well",
    "purpose_fit",
    "rationale",
    "evidence",
)

# Human/model-readable description of the intended assessment schema. As with
# _ROW_SHAPE_DOC this is the ONLY place the strict shape is asserted toward
# the model — strict typing is deliberately NOT used on the args schema.
_TECH_SHAPE_DOC = (
    "The assessment is an object with: "
    "purpose (string — the inferred overall purpose of the project); "
    "stack (array of objects, each "
    "{tech, role, used_well, purpose_fit, rationale, evidence} where "
    "used_well is one of 'good'/'mixed'/'poor', purpose_fit is one of "
    "'fit'/'questionable'/'misfit'); "
    "stack_verdict (string — short overall narrative); "
    "stack_score (integer 0-100 or null — overall tech-stack-fit score)."
)


class TechStackItem(BaseModel):
    """Documentation-only model of one stack item.

    NOT used in the args_schema validation path (strict typing there
    would raise on improvised model output). Kept purely as a structured
    description of the intended shape for callers/readers.
    """

    model_config = ConfigDict(extra="ignore")

    tech: str = ""
    role: str = ""
    used_well: str = ""
    purpose_fit: str = ""
    rationale: str = ""
    evidence: str = ""


class TechAssessment(BaseModel):
    """Documentation-only model of the project tech assessment.

    NOT used in the args_schema validation path; see ``TechStackItem``.
    """

    model_config = ConfigDict(extra="ignore")

    purpose: str = ""
    stack: list[TechStackItem] = Field(default_factory=list)
    stack_verdict: str = ""
    stack_score: int | None = None


class SubmitTechArgs(BaseModel):
    """Permissive args schema for the submit_tech_assessment tool.

    ``assessment`` is typed as ``Any`` (NOT ``TechAssessment`` or even
    ``dict``) so that any JSON the model emits — a bare string, an int, a
    list, a partially-typed object — passes pydantic validation and
    reaches the callback, where ``normalize_tech_assessment`` is the sole
    coercion authority. ``extra="ignore"`` is pinned explicitly so
    improvised top-level keys are dropped deterministically.
    """

    model_config = ConfigDict(extra="ignore")

    assessment: Any = Field(
        default=None,
        description=(
            "The full project tech-stack-fit assessment to record. "
            + _TECH_SHAPE_DOC
        ),
    )


def _str_or_empty(value: Any) -> str:
    """Coerce any scalar to a string; None / falsy-None -> ""."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _normalize_stack_item(raw: Any) -> dict | None:
    """Normalize one stack item to ``_TECH_ITEM_KEYS``.

    Returns None when *raw* is not dict-coercible or has an empty
    ``tech`` (such items are dropped by the caller). Never raises.
    """
    d = _as_dict(raw)
    if d is None:
        return None
    tech = _str_or_empty(d.get("tech", ""))
    if not tech.strip():
        return None
    return {
        "tech": tech,
        "role": _str_or_empty(d.get("role", "")),
        "used_well": _str_or_empty(d.get("used_well", "")),
        "purpose_fit": _str_or_empty(d.get("purpose_fit", "")),
        "rationale": _str_or_empty(d.get("rationale", "")),
        "evidence": _str_or_empty(d.get("evidence", "")),
    }


def normalize_tech_assessment(raw: Any) -> dict:
    """Pure, never-raising normalizer: the SOLE coercion authority.

    Accepts anything (dict, pydantic model, str, None, list, garbage)
    and returns a plain dict with exactly ``_TECH_KEYS``. ``stack`` is a
    list of dicts each with exactly ``_TECH_ITEM_KEYS``; stack items that
    are not dict-coercible or have an empty ``tech`` are dropped. A
    non-list ``stack`` (or a non-dict assessment) yields ``stack == []``.
    ``stack_score`` is coerced via the shared ``_coerce_int_or_none``
    (so "82" -> 82, 82.0 -> 82, "n/a"/3.5/bools -> None). Never raises.
    """
    d = _as_dict(raw) or {}

    raw_stack = d.get("stack")
    stack: list[dict] = []
    if isinstance(raw_stack, list):
        for entry in raw_stack:
            item = _normalize_stack_item(entry)
            if item is not None:
                stack.append(item)

    return {
        "purpose": _str_or_empty(d.get("purpose", "")),
        "stack": stack,
        "stack_verdict": _str_or_empty(d.get("stack_verdict", "")),
        "stack_score": _coerce_int_or_none(d.get("stack_score")),
    }


def make_tech_tool(holder: dict) -> StructuredTool:
    """Return the ``submit_tech_assessment`` tool, capturing into *holder*.

    The tool argument is validated only against the permissive
    ``SubmitTechArgs`` (``assessment: Any``), so any JSON the model emits
    reaches the callback. The callback runs ``normalize_tech_assessment``
    (the sole coercion authority), OVERWRITES
    ``holder["tech_assessment"]`` and sets
    ``holder["tech_submitted"] = True``. It never raises on malformed
    input: an unusable payload records a sane empty assessment.
    """

    def _submit(assessment: Any = None) -> str:
        try:
            ta = normalize_tech_assessment(assessment)
        except Exception:
            ta = normalize_tech_assessment(None)
        # Overwrite (not append): the holder reflects only the latest call.
        holder["tech_assessment"] = ta
        holder["tech_submitted"] = True
        return (
            f"OK: tech assessment recorded "
            f"({len(ta['stack'])} stack items)."
        )

    return StructuredTool.from_function(
        func=_submit,
        name="submit_tech_assessment",
        description=(
            "Submit the final project-level tech-stack-fit assessment: "
            "what tech is used, whether each is used per its intended "
            "design, and whether it is appropriate for the project's "
            "purpose. Call this exactly once with the complete "
            "assessment. " + _TECH_SHAPE_DOC
        ),
        args_schema=SubmitTechArgs,
    )


# Korean instruction prefixed to the seed text for the finalize-tech guard.
# The model is forced (tool_choice) to emit a submit_tech_assessment call,
# so this only needs to point it at the schema and forbid prose.
_TECH_FINALIZE_INSTRUCTION = (
    "아래는 프로젝트 기술 스택 적합성 평가 결과 원문이다. 이를 "
    "submit_tech_assessment 스키마(purpose/stack[tech,role,used_well,"
    "purpose_fit,rationale,evidence]/stack_verdict/stack_score)에 맞는 "
    "JSON으로 변환해 submit_tech_assessment 도구를 호출하라. 설명 금지."
)


def _extract_tech_arg(resp: Any) -> Any:
    """Pull the raw ``assessment`` arg out of an AIMessage-like response.

    Defensive against: missing ``.tool_calls``, empty/non-list
    tool_calls, entries that are not dicts, missing/non-dict ``args``.
    Only a tool call actually named ``submit_tech_assessment`` is honored.
    Never raises; returns whatever it finds (passed to
    ``normalize_tech_assessment``, which itself never raises).
    """
    tool_calls = getattr(resp, "tool_calls", None)
    if not isinstance(tool_calls, list) or not tool_calls:
        return None

    chosen = None
    for tc in tool_calls:
        if (
            isinstance(tc, dict)
            and tc.get("name") == "submit_tech_assessment"
        ):
            chosen = tc
            break
    if not isinstance(chosen, dict):
        return None

    args = chosen.get("args")
    if not isinstance(args, dict):
        return None
    return args.get("assessment")


def finalize_tech(
    holder: dict,
    seed_text: str,
    *,
    model: str,
    _model_factory=None,
) -> dict:
    """Deterministic finalize-tech guard: force a structured capture.

    ISOLATED helper (P8): NOT wired into the orchestrator yet. Mirrors
    ``finalize_findings``. When the agentic loop finishes without a proper
    ``submit_tech_assessment`` call the *holder* is empty / stub. This
    makes ONE constrained model call forced (via ``tool_choice``) to emit
    a ``submit_tech_assessment`` tool call, then reads the structured
    arguments DIRECTLY off the response — it does NOT rely on the agent
    loop or on tool-execution side effects.

    No-op condition (documented choice): skip the model call ONLY when
    ``tech_submitted`` is True AND ``tech_assessment`` is a dict that
    carries real content — a non-empty ``stack`` OR a non-empty
    ``purpose``. A truthy ``tech_submitted`` whose assessment is empty
    (no stack and no purpose) STILL attempts the guard: that early/empty
    submission is exactly the failure mode this guards.

    NEVER raises: any failure (network, bad response shape, attribute
    errors) degrades to the pre-existing assessment (or a freshly
    normalized empty one) and marks ``tech_submitted``, so a finalize
    failure cannot abort the pipeline.
    """
    if holder.get("tech_submitted") is True:
        existing = holder.get("tech_assessment")
        if isinstance(existing, dict) and (
            existing.get("stack") or existing.get("purpose")
        ):
            return existing

    try:
        llm = (_model_factory or _default_chat)(model)
        tool = make_tech_tool(holder)
        bound = llm.bind_tools(
            [tool], tool_choice="submit_tech_assessment"
        )
        messages = [
            (
                "human",
                _TECH_FINALIZE_INSTRUCTION
                + "\n\n---\n"
                + (seed_text or ""),
            )
        ]
        resp = bound.invoke(messages)
        raw = _extract_tech_arg(resp)
        ta = normalize_tech_assessment(raw)
        holder["tech_assessment"] = ta
        holder["tech_submitted"] = True
        return ta
    except Exception:
        # Degrade gracefully: never propagate. Preserve any pre-existing
        # assessment dict; otherwise record a sane empty assessment.
        fallback = holder.get("tech_assessment")
        if not isinstance(fallback, dict):
            fallback = normalize_tech_assessment(None)
        holder["tech_assessment"] = fallback
        holder["tech_submitted"] = True
        return fallback
