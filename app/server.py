import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader

from app import db

_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def _env():
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        # Template is always HTML; force autoescape. select_autoescape keys
        # off the final suffix (.j2) and would silently disable escaping.
        autoescape=True,
    )


def create_app(db_path: str) -> FastAPI:
    app = FastAPI()
    tmpl = _env()

    @app.get("/", response_class=HTMLResponse)
    def index():
        c = db.connect(db_path)
        repos = c.execute("SELECT * FROM repos ORDER BY id").fetchall()
        return tmpl.get_template("history.html.j2").render(
            repos=[dict(r) for r in repos]
        )

    @app.get("/repo/{repo_id}", response_class=HTMLResponse)
    def repo_history(repo_id: int):
        c = db.connect(db_path)
        repo = c.execute(
            "SELECT * FROM repos WHERE id=?", (repo_id,)
        ).fetchone()
        if repo is None:
            return HTMLResponse(f"repo {repo_id} not found", status_code=404)
        rows = c.execute(
            "SELECT * FROM evaluations WHERE repo_id=? ORDER BY id DESC",
            (repo_id,),
        ).fetchall()
        evals = []
        for e in rows:
            overall = json.loads(e["overall_json"]) if e["overall_json"] else {}
            evals.append({
                "id": e["id"], "commit_sha": e["commit_sha"],
                "mode": e["mode"], "score": overall.get("score"),
                "cost": e["total_cost_usd"] or 0.0,
            })
        return tmpl.get_template("history.html.j2").render(
            repo=dict(repo), evals=evals
        )

    return app


def serve(db_path: str, host="127.0.0.1", port=8000):
    import uvicorn
    uvicorn.run(create_app(db_path), host=host, port=port)
