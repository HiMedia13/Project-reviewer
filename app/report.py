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


def terminal_summary(ctx: dict) -> str:
    console = Console(record=True, width=90)
    o = ctx["overall"]
    console.print(f"[bold]{ctx['repo_url']}[/bold] @ {ctx['commit_sha'][:10]} "
                  f"({ctx['mode']})")
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
