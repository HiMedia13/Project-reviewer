from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from rich.console import Console
from rich.table import Table

_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def render_html(ctx: dict, out_path: str) -> None:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        # Template is always HTML; force autoescape. select_autoescape keys
        # off the final suffix (.j2), which would silently disable escaping
        # and let untrusted LLM-produced findings inject raw HTML/JS.
        autoescape=True,
    )
    html = env.get_template("report.html.j2").render(**ctx)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(html, encoding="utf-8")


def _truncate(text: str, limit: int = 50) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def terminal_summary(ctx: dict) -> str:
    console = Console(record=True, width=90)
    o = ctx["overall"]
    console.print(f"[bold]{ctx['repo_url']}[/bold] @ {ctx['commit_sha'][:10]} "
                  f"({ctx['mode']})")

    # LEAD: project-level tech-stack-fit assessment.
    ta = ctx.get("tech_assessment") or {}
    purpose = ta.get("purpose") or ""
    stack = ta.get("stack") or []
    if purpose or stack:
        if purpose:
            console.print(f"[bold]프로젝트 목적:[/bold] {purpose}")
        if stack:
            st = Table("기술", "의도대로", "목적적합", "근거")
            for s in stack:
                st.add_row(
                    _truncate(s.get("tech"), 24),
                    _truncate(s.get("used_well"), 14),
                    _truncate(s.get("purpose_fit"), 14),
                    _truncate(s.get("rationale"), 40),
                )
            console.print(st)
        score = ta.get("stack_score")
        console.print(
            f"[bold cyan]기술 스택: {ta.get('stack_verdict') or 'N/A'} "
            f"(score: {score if score is not None else 'N/A'})[/]"
        )
    else:
        console.print("기술 스택 평가 없음")

    # SECONDARY: per-file 4-criteria summary.
    console.print(f"[bold cyan]Overall: "
                  f"{o['score'] if o['score'] is not None else 'N/A'}[/]")
    t = Table("criterion", "score")
    for c, v in o["criteria"].items():
        t.add_row(c, "—" if v is None else str(v))
    console.print(t)
    cost = ctx["cost"]
    console.print(
        f"tokens {cost['input_tokens']}/{cost['output_tokens']} · "
        f"${cost['cost_usd']:.4f} · {ctx['duration_sec']:.1f}s"
    )
    return console.export_text()
