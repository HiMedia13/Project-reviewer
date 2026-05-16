# Project-Reviewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a multi-agent system that clones a GitHub repo, evaluates its code quality on 4 qualitative criteria via LangChain DeepAgents, caches results in SQLite to re-evaluate only git-diff changes, and outputs a terminal summary + HTML report (with optional FastAPI history server) plus LangSmith cost.

**Architecture:** A deterministic Python layer (repo_manager, db) decides scope via `git diff` and reuses cached findings to save tokens. A single DeepAgents orchestrator runs 6 subagents (scanner + 4 criteria + evaluator-critic). A deterministic synthesizer merges new + cached findings into scores. Output is Rich terminal + self-contained HTML; LangSmith SDK aggregates trace cost.

**Tech Stack:** Python 3.13, `deepagents`, `langchain`, `langchain-anthropic`, `langsmith`, `tavily-python`, `GitPython`, `rich`, `jinja2`, `fastapi`+`uvicorn`, stdlib `sqlite3`, `pytest`. conda env, `uv pip install`.

**Spec:** `docs/superpowers/specs/2026-05-16-project-reviewer-design.md`

**Conventions:** Arrow-style/functional Python where natural, small focused modules, frequent commits. Run tests with `python -m pytest`. All LLM/Tavily/LangSmith network calls are mocked in tests.

---

## Task 1: Project scaffolding & dependencies

**Files:**
- Create: `requirements.txt`
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `app/__init__.py`
- Create: `app/agent/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create `.gitignore`**

```gitignore
__pycache__/
*.pyc
.pytest_cache/
.env
.repocache/
output/
*.sqlite3
*.db
.venv/
```

- [ ] **Step 2: Create `requirements.txt`**

```
deepagents
langchain
langchain-anthropic
langsmith
tavily-python
GitPython
rich
jinja2
fastapi
uvicorn
pytest
```

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "project-reviewer"
version = "0.1.0"
requires-python = ">=3.11"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

- [ ] **Step 4: Create package init files**

`app/__init__.py`, `app/agent/__init__.py`, `tests/__init__.py` — each an empty file.

- [ ] **Step 5: Create `tests/conftest.py`**

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

- [ ] **Step 6: Create conda env and install deps**

Run:
```
conda create -y -n project-reviewer python=3.13
conda run -n project-reviewer uv pip install -r requirements.txt
```
Expected: install completes without error.

- [ ] **Step 7: Verify pytest collects nothing yet**

Run: `conda run -n project-reviewer python -m pytest`
Expected: `no tests ran` (exit 5) — acceptable; scaffolding works.

- [ ] **Step 8: Commit**

```
git add .gitignore requirements.txt pyproject.toml app tests
git commit -m "chore: scaffold project-reviewer package and deps"
```

---

## Task 2: SQLite layer — schema & connection

**Files:**
- Create: `app/db.py`
- Test: `tests/test_db_schema.py`

- [ ] **Step 1: Write the failing test**

`tests/test_db_schema.py`:
```python
from app.db import connect, init_schema


def test_init_schema_creates_tables(tmp_path):
    db_path = tmp_path / "r.sqlite3"
    conn = connect(str(db_path))
    init_schema(conn)
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"repos", "evaluations", "file_findings"} <= names


def test_init_schema_is_idempotent(tmp_path):
    conn = connect(str(tmp_path / "r.sqlite3"))
    init_schema(conn)
    init_schema(conn)  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n project-reviewer python -m pytest tests/test_db_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: app.db`.

- [ ] **Step 3: Write minimal implementation**

`app/db.py`:
```python
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  remote_url TEXT UNIQUE NOT NULL,
  local_path TEXT NOT NULL,
  default_branch TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evaluations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id INTEGER NOT NULL REFERENCES repos(id),
  commit_sha TEXT NOT NULL,
  parent_eval_id INTEGER REFERENCES evaluations(id),
  mode TEXT NOT NULL,
  created_at TEXT NOT NULL,
  overall_json TEXT,
  langsmith_run_url TEXT,
  total_input_tokens INTEGER DEFAULT 0,
  total_output_tokens INTEGER DEFAULT 0,
  total_cost_usd REAL DEFAULT 0,
  duration_sec REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS file_findings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id INTEGER NOT NULL REFERENCES repos(id),
  evaluation_id INTEGER NOT NULL REFERENCES evaluations(id),
  file_path TEXT NOT NULL,
  file_hash TEXT NOT NULL,
  criterion TEXT NOT NULL,
  findings_json TEXT NOT NULL,
  criterion_score INTEGER,
  verified INTEGER DEFAULT 0,
  verify_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_ff_eval ON file_findings(evaluation_id);
CREATE INDEX IF NOT EXISTS idx_eval_repo ON evaluations(repo_id);
"""


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n project-reviewer python -m pytest tests/test_db_schema.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```
git add app/db.py tests/test_db_schema.py
git commit -m "feat(db): SQLite schema and connection"
```

---

## Task 3: SQLite layer — repo & evaluation CRUD

**Files:**
- Modify: `app/db.py`
- Test: `tests/test_db_crud.py`

- [ ] **Step 1: Write the failing test**

`tests/test_db_crud.py`:
```python
from app.db import (
    connect,
    init_schema,
    upsert_repo,
    get_repo_by_url,
    create_evaluation,
    latest_evaluation,
)


def _conn(tmp_path):
    c = connect(str(tmp_path / "r.sqlite3"))
    init_schema(c)
    return c


def test_upsert_repo_is_idempotent(tmp_path):
    c = _conn(tmp_path)
    rid = upsert_repo(c, "https://x/y.git", "/tmp/y", "main")
    rid2 = upsert_repo(c, "https://x/y.git", "/tmp/y", "main")
    assert rid == rid2
    assert get_repo_by_url(c, "https://x/y.git")["id"] == rid


def test_latest_evaluation_returns_most_recent(tmp_path):
    c = _conn(tmp_path)
    rid = upsert_repo(c, "u", "p", "main")
    e1 = create_evaluation(c, rid, "sha1", None, "full")
    e2 = create_evaluation(c, rid, "sha2", e1, "incremental")
    assert latest_evaluation(c, rid)["id"] == e2


def test_latest_evaluation_none_when_empty(tmp_path):
    c = _conn(tmp_path)
    rid = upsert_repo(c, "u", "p", "main")
    assert latest_evaluation(c, rid) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n project-reviewer python -m pytest tests/test_db_crud.py -v`
Expected: FAIL — `ImportError: cannot import name 'upsert_repo'`.

- [ ] **Step 3: Write minimal implementation**

Append to `app/db.py`:
```python
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_repo(conn, remote_url: str, local_path: str, default_branch: str) -> int:
    existing = get_repo_by_url(conn, remote_url)
    if existing:
        conn.execute(
            "UPDATE repos SET local_path=?, default_branch=? WHERE id=?",
            (local_path, default_branch, existing["id"]),
        )
        conn.commit()
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO repos (remote_url, local_path, default_branch, created_at) "
        "VALUES (?,?,?,?)",
        (remote_url, local_path, default_branch, _now()),
    )
    conn.commit()
    return cur.lastrowid


def get_repo_by_url(conn, remote_url: str):
    return conn.execute(
        "SELECT * FROM repos WHERE remote_url=?", (remote_url,)
    ).fetchone()


def create_evaluation(conn, repo_id, commit_sha, parent_eval_id, mode) -> int:
    cur = conn.execute(
        "INSERT INTO evaluations (repo_id, commit_sha, parent_eval_id, mode, "
        "created_at) VALUES (?,?,?,?,?)",
        (repo_id, commit_sha, parent_eval_id, mode, _now()),
    )
    conn.commit()
    return cur.lastrowid


def latest_evaluation(conn, repo_id: int):
    return conn.execute(
        "SELECT * FROM evaluations WHERE repo_id=? ORDER BY id DESC LIMIT 1",
        (repo_id,),
    ).fetchone()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n project-reviewer python -m pytest tests/test_db_crud.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```
git add app/db.py tests/test_db_crud.py
git commit -m "feat(db): repo and evaluation CRUD"
```

---

## Task 4: SQLite layer — findings write & cache copy

**Files:**
- Modify: `app/db.py`
- Test: `tests/test_db_findings.py`

- [ ] **Step 1: Write the failing test**

`tests/test_db_findings.py`:
```python
import json
from app.db import (
    connect, init_schema, upsert_repo, create_evaluation,
    insert_finding, findings_for_evaluation, copy_unchanged_findings,
)


def _conn(tmp_path):
    c = connect(str(tmp_path / "r.sqlite3"))
    init_schema(c)
    return c


def test_insert_and_read_findings(tmp_path):
    c = _conn(tmp_path)
    rid = upsert_repo(c, "u", "p", "main")
    eid = create_evaluation(c, rid, "sha1", None, "full")
    insert_finding(c, rid, eid, "a.py", "h1", "deadcode",
                    [{"severity": "low", "msg": "x"}], 80, True, "ok")
    rows = findings_for_evaluation(c, eid)
    assert len(rows) == 1
    assert json.loads(rows[0]["findings_json"]) == [{"severity": "low", "msg": "x"}]
    assert rows[0]["criterion_score"] == 80


def test_copy_unchanged_findings_only_for_listed_files(tmp_path):
    c = _conn(tmp_path)
    rid = upsert_repo(c, "u", "p", "main")
    old = create_evaluation(c, rid, "sha1", None, "full")
    insert_finding(c, rid, old, "keep.py", "h1", "deadcode", [], 90, True, "")
    insert_finding(c, rid, old, "changed.py", "h2", "deadcode", [], 50, True, "")
    new = create_evaluation(c, rid, "sha2", old, "incremental")
    n = copy_unchanged_findings(c, rid, old, new, ["keep.py"])
    rows = findings_for_evaluation(c, new)
    assert n == 1
    assert {r["file_path"] for r in rows} == {"keep.py"}
    assert rows[0]["evaluation_id"] == new
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n project-reviewer python -m pytest tests/test_db_findings.py -v`
Expected: FAIL — `ImportError: cannot import name 'insert_finding'`.

- [ ] **Step 3: Write minimal implementation**

Append to `app/db.py`:
```python
def insert_finding(conn, repo_id, evaluation_id, file_path, file_hash,
                    criterion, findings, score, verified, verify_note) -> int:
    cur = conn.execute(
        "INSERT INTO file_findings (repo_id, evaluation_id, file_path, "
        "file_hash, criterion, findings_json, criterion_score, verified, "
        "verify_note) VALUES (?,?,?,?,?,?,?,?,?)",
        (repo_id, evaluation_id, file_path, file_hash, criterion,
         json.dumps(findings), score, 1 if verified else 0, verify_note),
    )
    conn.commit()
    return cur.lastrowid


def findings_for_evaluation(conn, evaluation_id: int):
    return conn.execute(
        "SELECT * FROM file_findings WHERE evaluation_id=?", (evaluation_id,)
    ).fetchall()


def copy_unchanged_findings(conn, repo_id, src_eval, dst_eval, file_paths) -> int:
    if not file_paths:
        return 0
    placeholders = ",".join("?" * len(file_paths))
    src_rows = conn.execute(
        f"SELECT file_path, file_hash, criterion, findings_json, "
        f"criterion_score, verified, verify_note FROM file_findings "
        f"WHERE evaluation_id=? AND file_path IN ({placeholders})",
        (src_eval, *file_paths),
    ).fetchall()
    for r in src_rows:
        conn.execute(
            "INSERT INTO file_findings (repo_id, evaluation_id, file_path, "
            "file_hash, criterion, findings_json, criterion_score, verified, "
            "verify_note) VALUES (?,?,?,?,?,?,?,?,?)",
            (repo_id, dst_eval, r["file_path"], r["file_hash"], r["criterion"],
             r["findings_json"], r["criterion_score"], r["verified"],
             r["verify_note"]),
        )
    conn.commit()
    return len(src_rows)
```

Add `import json` at top of `app/db.py` if not already present (it is added here; ensure single import).

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n project-reviewer python -m pytest tests/test_db_findings.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```
git add app/db.py tests/test_db_findings.py
git commit -m "feat(db): findings insert and unchanged-findings cache copy"
```

---

## Task 5: Finalize-evaluation writer (overall + cost)

**Files:**
- Modify: `app/db.py`
- Test: `tests/test_db_finalize.py`

- [ ] **Step 1: Write the failing test**

`tests/test_db_finalize.py`:
```python
import json
from app.db import (
    connect, init_schema, upsert_repo, create_evaluation,
    finalize_evaluation, latest_evaluation,
)


def test_finalize_evaluation_writes_overall_and_cost(tmp_path):
    c = connect(str(tmp_path / "r.sqlite3"))
    init_schema(c)
    rid = upsert_repo(c, "u", "p", "main")
    eid = create_evaluation(c, rid, "sha1", None, "full")
    finalize_evaluation(
        c, eid,
        overall={"score": 77, "criteria": {"deadcode": 80}},
        cost={"input_tokens": 100, "output_tokens": 20,
              "cost_usd": 0.01, "run_url": "http://ls/x"},
        duration_sec=12.5,
    )
    row = latest_evaluation(c, rid)
    assert json.loads(row["overall_json"])["score"] == 77
    assert row["total_input_tokens"] == 100
    assert row["total_cost_usd"] == 0.01
    assert row["langsmith_run_url"] == "http://ls/x"
    assert row["duration_sec"] == 12.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n project-reviewer python -m pytest tests/test_db_finalize.py -v`
Expected: FAIL — `ImportError: cannot import name 'finalize_evaluation'`.

- [ ] **Step 3: Write minimal implementation**

Append to `app/db.py`:
```python
def finalize_evaluation(conn, evaluation_id, overall, cost, duration_sec) -> None:
    conn.execute(
        "UPDATE evaluations SET overall_json=?, total_input_tokens=?, "
        "total_output_tokens=?, total_cost_usd=?, langsmith_run_url=?, "
        "duration_sec=? WHERE id=?",
        (json.dumps(overall), cost.get("input_tokens", 0),
         cost.get("output_tokens", 0), cost.get("cost_usd", 0.0),
         cost.get("run_url"), duration_sec, evaluation_id),
    )
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n project-reviewer python -m pytest tests/test_db_finalize.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```
git add app/db.py tests/test_db_finalize.py
git commit -m "feat(db): finalize evaluation with overall and cost"
```

---

## Task 6: Repo manager — clone/pull

**Files:**
- Create: `app/repo_manager.py`
- Test: `tests/test_repo_clone.py`

- [ ] **Step 1: Write the failing test**

`tests/test_repo_clone.py`:
```python
import subprocess
from pathlib import Path
from app.repo_manager import ensure_repo, head_sha


def _make_origin(tmp_path: Path) -> Path:
    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=origin, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=origin, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=origin, check=True)
    (origin / "a.py").write_text("print(1)\n")
    subprocess.run(["git", "add", "."], cwd=origin, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=origin, check=True)
    return origin


def test_ensure_repo_clones_then_reuses(tmp_path):
    origin = _make_origin(tmp_path)
    cache = tmp_path / "cache"
    p1 = ensure_repo(str(origin), str(cache))
    assert (Path(p1) / "a.py").exists()
    sha = head_sha(p1)
    assert len(sha) == 40
    p2 = ensure_repo(str(origin), str(cache))  # second call: pull, no error
    assert p1 == p2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n project-reviewer python -m pytest tests/test_repo_clone.py -v`
Expected: FAIL — `ModuleNotFoundError: app.repo_manager`.

- [ ] **Step 3: Write minimal implementation**

`app/repo_manager.py`:
```python
import hashlib
import re
from pathlib import Path

from git import Repo


def _slug(remote_url: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]", "_", remote_url.rstrip("/"))
    digest = hashlib.sha1(remote_url.encode()).hexdigest()[:8]
    return f"{base[-40:]}_{digest}"


def ensure_repo(remote_url: str, cache_root: str) -> str:
    dest = Path(cache_root) / _slug(remote_url)
    if (dest / ".git").exists():
        repo = Repo(dest)
        repo.remotes.origin.fetch()
        repo.git.reset("--hard", f"origin/{repo.active_branch.name}")
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        Repo.clone_from(remote_url, dest)
    return str(dest)


def head_sha(repo_path: str) -> str:
    return Repo(repo_path).head.commit.hexsha


def default_branch(repo_path: str) -> str:
    return Repo(repo_path).active_branch.name
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n project-reviewer python -m pytest tests/test_repo_clone.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```
git add app/repo_manager.py tests/test_repo_clone.py
git commit -m "feat(repo): clone/pull and head sha"
```

---

## Task 7: Repo manager — file inventory & hashes

**Files:**
- Modify: `app/repo_manager.py`
- Test: `tests/test_repo_inventory.py`

- [ ] **Step 1: Write the failing test**

`tests/test_repo_inventory.py`:
```python
from pathlib import Path
from app.repo_manager import inventory_files, file_hash

CODE_EXT = {".py", ".js", ".ts", ".java", ".go", ".rs"}


def test_inventory_lists_code_files_only(tmp_path):
    (tmp_path / "a.py").write_text("x=1\n")
    (tmp_path / "b.txt").write_text("notcode\n")
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "c.js").write_text("var x\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref\n")
    files = inventory_files(str(tmp_path))
    assert set(files) == {"a.py", "pkg/c.js"}


def test_file_hash_changes_with_content(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x=1\n")
    h1 = file_hash(str(f))
    f.write_text("x=2\n")
    assert h1 != file_hash(str(f))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n project-reviewer python -m pytest tests/test_repo_inventory.py -v`
Expected: FAIL — `ImportError: cannot import name 'inventory_files'`.

- [ ] **Step 3: Write minimal implementation**

Append to `app/repo_manager.py`:
```python
CODE_EXT = {".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs",
            ".rb", ".c", ".cpp", ".h", ".hpp", ".cs", ".kt", ".swift",
            ".php", ".scala"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
             "dist", "build", ".pytest_cache"}


def inventory_files(repo_path: str) -> list[str]:
    root = Path(repo_path)
    out: list[str] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if p.suffix.lower() in CODE_EXT:
            out.append(rel.as_posix())
    return sorted(out)


def file_hash(path: str) -> str:
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n project-reviewer python -m pytest tests/test_repo_inventory.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```
git add app/repo_manager.py tests/test_repo_inventory.py
git commit -m "feat(repo): code-file inventory and content hashing"
```

---

## Task 8: Repo manager — git diff + reverse-import scope

**Files:**
- Modify: `app/repo_manager.py`
- Test: `tests/test_repo_scope.py`

- [ ] **Step 1: Write the failing test**

`tests/test_repo_scope.py`:
```python
from app.repo_manager import reverse_import_expand, changed_files
import subprocess
from pathlib import Path


def test_reverse_import_expand_python(tmp_path):
    (tmp_path / "lib.py").write_text("def f(): return 1\n")
    (tmp_path / "uses.py").write_text("from lib import f\nf()\n")
    (tmp_path / "other.py").write_text("x = 1\n")
    files = ["lib.py", "uses.py", "other.py"]
    expanded = reverse_import_expand(str(tmp_path), {"lib.py"}, files)
    assert expanded == {"lib.py", "uses.py"}


def test_changed_files_between_commits(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    run = lambda *a: subprocess.run(a, cwd=r, check=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (r / "a.py").write_text("1\n")
    run("git", "add", ".")
    run("git", "commit", "-qm", "c1")
    sha1 = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=r, text=True
    ).strip()
    (r / "a.py").write_text("2\n")
    (r / "b.py").write_text("new\n")
    run("git", "add", ".")
    run("git", "commit", "-qm", "c2")
    assert changed_files(str(r), sha1) == {"a.py", "b.py"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n project-reviewer python -m pytest tests/test_repo_scope.py -v`
Expected: FAIL — `ImportError: cannot import name 'reverse_import_expand'`.

- [ ] **Step 3: Write minimal implementation**

Append to `app/repo_manager.py`:
```python
import ast


def changed_files(repo_path: str, since_sha: str) -> set[str]:
    repo = Repo(repo_path)
    out = repo.git.diff("--name-only", f"{since_sha}..HEAD")
    return {line.strip() for line in out.splitlines() if line.strip()}


def _module_to_paths(module: str) -> set[str]:
    parts = module.split(".")
    return {
        "/".join(parts) + ".py",
        "/".join(parts) + "/__init__.py",
        parts[-1] + ".py",
    }


def _python_imports(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                mods.add(n.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def reverse_import_expand(repo_path, seed_files, all_files) -> set[str]:
    root = Path(repo_path)
    seed = set(seed_files)
    target_paths: set[str] = set()
    for sf in seed:
        if sf.endswith(".py"):
            target_paths.add(sf)
    expanded = set(seed)
    for f in all_files:
        if not f.endswith(".py") or f in expanded:
            continue
        try:
            src = (root / f).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        importer_targets: set[str] = set()
        for m in _python_imports(src):
            importer_targets |= _module_to_paths(m)
        if importer_targets & target_paths:
            expanded.add(f)
    return expanded
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n project-reviewer python -m pytest tests/test_repo_scope.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```
git add app/repo_manager.py tests/test_repo_scope.py
git commit -m "feat(repo): git-diff changed files and reverse-import expansion"
```

---

## Task 9: Scope planner — full vs incremental decision

**Files:**
- Create: `app/scope.py`
- Test: `tests/test_scope_planner.py`

- [ ] **Step 1: Write the failing test**

`tests/test_scope_planner.py`:
```python
from app.scope import plan_scope


def test_full_when_no_prior(tmp_path):
    s = plan_scope(prior_sha=None, repo_path=None, all_files=["a.py", "b.py"],
                    force=False)
    assert s.mode == "full"
    assert set(s.in_scope) == {"a.py", "b.py"}


def test_full_when_forced():
    s = plan_scope(prior_sha="abc", repo_path=None, all_files=["a.py"],
                   force=True)
    assert s.mode == "full"


def test_incremental_uses_changed_plus_reverse(monkeypatch):
    import app.scope as scope
    monkeypatch.setattr(scope, "changed_files", lambda p, s: {"lib.py"})
    monkeypatch.setattr(
        scope, "reverse_import_expand",
        lambda p, seed, allf: {"lib.py", "uses.py"},
    )
    s = plan_scope(prior_sha="abc", repo_path="/x",
                   all_files=["lib.py", "uses.py", "other.py"], force=False)
    assert s.mode == "incremental"
    assert set(s.in_scope) == {"lib.py", "uses.py"}
    assert set(s.cached) == {"other.py"}


def test_incremental_falls_back_to_full_on_bad_sha(monkeypatch):
    import app.scope as scope

    def boom(p, s):
        raise RuntimeError("bad sha")

    monkeypatch.setattr(scope, "changed_files", boom)
    s = plan_scope(prior_sha="gone", repo_path="/x",
                   all_files=["a.py"], force=False)
    assert s.mode == "full"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n project-reviewer python -m pytest tests/test_scope_planner.py -v`
Expected: FAIL — `ModuleNotFoundError: app.scope`.

- [ ] **Step 3: Write minimal implementation**

`app/scope.py`:
```python
from dataclasses import dataclass

from app.repo_manager import changed_files, reverse_import_expand


@dataclass
class Scope:
    mode: str            # "full" | "incremental"
    in_scope: list[str]  # files to send to the agent
    cached: list[str]    # unchanged files reused from prior evaluation


def plan_scope(prior_sha, repo_path, all_files, force: bool) -> Scope:
    all_set = list(all_files)
    if force or not prior_sha:
        return Scope("full", all_set, [])
    try:
        changed = changed_files(repo_path, prior_sha)
        expanded = reverse_import_expand(repo_path, changed, all_set)
    except Exception:
        return Scope("full", all_set, [])
    in_scope = sorted(f for f in all_set if f in expanded)
    cached = sorted(f for f in all_set if f not in expanded)
    return Scope("incremental", in_scope, cached)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n project-reviewer python -m pytest tests/test_scope_planner.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```
git add app/scope.py tests/test_scope_planner.py
git commit -m "feat(scope): full vs incremental scope planner with fallback"
```

---

## Task 10: Synthesizer — merge findings & score

**Files:**
- Create: `app/synthesizer.py`
- Test: `tests/test_synthesizer.py`

- [ ] **Step 1: Write the failing test**

`tests/test_synthesizer.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n project-reviewer python -m pytest tests/test_synthesizer.py -v`
Expected: FAIL — `ModuleNotFoundError: app.synthesizer`.

- [ ] **Step 3: Write minimal implementation**

`app/synthesizer.py`:
```python
CRITERIA = ["library", "eng", "deadcode", "techstack"]


def synthesize(finding_rows) -> dict:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n project-reviewer python -m pytest tests/test_synthesizer.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```
git add app/synthesizer.py tests/test_synthesizer.py
git commit -m "feat(synthesizer): per-criterion and overall scoring"
```

---

## Task 11: LangSmith cost aggregation

**Files:**
- Create: `app/langsmith_cost.py`
- Test: `tests/test_langsmith_cost.py`

- [ ] **Step 1: Write the failing test**

`tests/test_langsmith_cost.py`:
```python
import app.langsmith_cost as lc


class _Run:
    def __init__(self, pt, ct, cost):
        self.prompt_tokens = pt
        self.completion_tokens = ct
        self.total_cost = cost


def test_aggregate_sums_runs(monkeypatch):
    runs = [_Run(100, 20, 0.01), _Run(50, 10, 0.005)]

    class FakeClient:
        def list_runs(self, **kw):
            return iter(runs)

        def read_run(self, rid):
            class R:
                url = "http://ls/run/abc"
            return R()

    monkeypatch.setattr(lc, "_client", lambda: FakeClient())
    out = lc.aggregate_cost(trace_id="t1", project="p")
    assert out["input_tokens"] == 150
    assert out["output_tokens"] == 30
    assert round(out["cost_usd"], 4) == 0.015
    assert out["run_url"] == "http://ls/run/abc"


def test_aggregate_returns_zero_when_disabled(monkeypatch):
    monkeypatch.setattr(lc, "_client", lambda: None)
    out = lc.aggregate_cost(trace_id=None, project="p")
    assert out == {"input_tokens": 0, "output_tokens": 0,
                   "cost_usd": 0.0, "run_url": None}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n project-reviewer python -m pytest tests/test_langsmith_cost.py -v`
Expected: FAIL — `ModuleNotFoundError: app.langsmith_cost`.

- [ ] **Step 3: Write minimal implementation**

`app/langsmith_cost.py`:
```python
import os


def _client():
    if not os.getenv("LANGSMITH_API_KEY"):
        return None
    try:
        from langsmith import Client
        return Client()
    except Exception:
        return None


def aggregate_cost(trace_id, project: str) -> dict:
    zero = {"input_tokens": 0, "output_tokens": 0,
            "cost_usd": 0.0, "run_url": None}
    client = _client()
    if client is None or not trace_id:
        return zero
    try:
        runs = list(client.list_runs(project_name=project, trace_id=trace_id))
    except Exception:
        return zero
    inp = sum(getattr(r, "prompt_tokens", 0) or 0 for r in runs)
    out = sum(getattr(r, "completion_tokens", 0) or 0 for r in runs)
    cost = sum(float(getattr(r, "total_cost", 0) or 0) for r in runs)
    run_url = None
    if runs:
        try:
            root = next(r for r in runs)
            run_url = getattr(client.read_run(root.id), "url", None)
        except Exception:
            run_url = None
    return {"input_tokens": inp, "output_tokens": out,
            "cost_usd": cost, "run_url": run_url}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n project-reviewer python -m pytest tests/test_langsmith_cost.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```
git add app/langsmith_cost.py tests/test_langsmith_cost.py
git commit -m "feat(cost): LangSmith trace cost aggregation"
```

---

## Task 12: Agent system prompts

**Files:**
- Create: `app/agent/prompts.py`
- Test: `tests/test_prompts.py`

- [ ] **Step 1: Write the failing test**

`tests/test_prompts.py`:
```python
from app.agent.prompts import (
    ORCHESTRATOR, SCANNER, CRITERIA_PROMPTS, EVALUATOR,
)


def test_all_four_criteria_prompts_present():
    assert set(CRITERIA_PROMPTS) == {"library", "eng", "deadcode", "techstack"}
    for p in CRITERIA_PROMPTS.values():
        assert "JSON" in p


def test_orchestrator_mentions_not_runtime():
    assert "작동 여부" in ORCHESTRATOR
    assert "scanner" in ORCHESTRATOR


def test_evaluator_mentions_websearch_and_hallucination():
    assert "할루시네이션" in EVALUATOR
    assert "tavily" in EVALUATOR.lower()
    assert SCANNER
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n project-reviewer python -m pytest tests/test_prompts.py -v`
Expected: FAIL — `ModuleNotFoundError: app.agent.prompts`.

- [ ] **Step 3: Write minimal implementation**

`app/agent/prompts.py`:
```python
ORCHESTRATOR = """당신은 코드 품질 정성 평가 오케스트레이터다.
코드의 작동 여부는 절대 평가하지 않는다. 빌드/실행/테스트하지 않는다.

작업 순서:
1. write_todos로 계획을 세운다.
2. scanner subagent를 호출해 프로젝트 맵(파일트리/언어/의존성/임포트그래프)을 얻는다.
3. 네 개의 기준 subagent(library, eng, deadcode, techstack)를 각각 호출한다.
   각 subagent에는 in-scope 파일 목록과 프로젝트 맵을 전달한다.
4. evaluator subagent를 호출해 모든 findings를 검증한다.
5. 검증된 결과를 JSON으로 최종 정리해 반환한다.

in-scope 파일만 평가한다. read_repo_file 도구로만 파일을 읽는다."""

SCANNER = """당신은 레포 스캐너다. list_scope_files와 read_repo_file로
프로젝트 구조를 조사한다. 출력 JSON:
{"languages": {...}, "manifests": [...], "entrypoints": [...],
 "import_graph_summary": "..."}
코드 작동 여부는 평가하지 않는다."""

_CRIT_BASE = """대상: in-scope 파일만. read_repo_file로 읽는다.
코드 작동 여부/실행 결과는 평가하지 않는다.
각 파일마다 다음 JSON 배열 항목을 반환한다:
{"file_path": "...", "criterion": "%s",
 "findings": [{"severity": "low|medium|high",
               "location": "path:line", "evidence": "근거", "msg": "지적"}],
 "criterion_score": 0-100}
JSON 외 텍스트를 출력하지 않는다."""

CRITERIA_PROMPTS = {
    "library": "당신은 라이브러리 사용 평가자다. 사용된 라이브러리가 그 "
               "라이브러리의 설계 의도대로 쓰였는지 판단한다.\n" + _CRIT_BASE % "library",
    "eng": "당신은 설계 적정성 평가자다. 문제 규모 대비 오버엔지니어링/"
           "언더엔지니어링을 판단한다.\n" + _CRIT_BASE % "eng",
    "deadcode": "당신은 데드코드 탐지자다. 도달 불가/미사용 코드의 양과 "
                "위치를 판단한다.\n" + _CRIT_BASE % "deadcode",
    "techstack": "당신은 기술 스택 활용 평가자다. 어떤 기술이 쓰였고 "
                 "적절히 잘 활용되었는지 판단한다.\n" + _CRIT_BASE % "techstack",
}

EVALUATOR = """당신은 검증자(critic)다. 다른 subagent의 findings를 검토한다.
각 finding에 대해 판단한다:
- 할루시네이션인가? (실제 코드 근거가 없는가)
- 괜한/사소한 지적인가?
- 기능에 대한 지적이 타당한가?
- LLM이 모르는 신기술/신규 라이브러리라 자체 평가가 불가능한가?
  그렇다면 tavily_search 도구로 해당 라이브러리의 의도된 사용법을 확인한 뒤 판정한다.

각 finding을 다음 JSON으로 반환한다:
{"file_path": "...", "criterion": "...", "verified": true|false,
 "verify_note": "검증 근거 또는 웹검색 결과 요약",
 "kept_findings": [...], "criterion_score": 0-100}
검증 실패(근거 없음/할루시네이션) 항목은 kept_findings에서 제거한다."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n project-reviewer python -m pytest tests/test_prompts.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```
git add app/agent/prompts.py tests/test_prompts.py
git commit -m "feat(agent): system prompts for orchestrator and subagents"
```

---

## Task 13: Agent tools (scoped file access + tavily)

**Files:**
- Create: `app/agent/tools.py`
- Test: `tests/test_agent_tools.py`

- [ ] **Step 1: Write the failing test**

`tests/test_agent_tools.py`:
```python
from app.agent.tools import make_file_tools


def test_read_repo_file_restricted_to_scope(tmp_path):
    (tmp_path / "a.py").write_text("AAA\n")
    (tmp_path / "secret.py").write_text("SECRET\n")
    list_tool, read_tool = make_file_tools(str(tmp_path), ["a.py"])
    assert "a.py" in list_tool.invoke({})
    assert "AAA" in read_tool.invoke({"path": "a.py"})
    blocked = read_tool.invoke({"path": "secret.py"})
    assert "not in scope" in blocked.lower()


def test_read_repo_file_rejects_traversal(tmp_path):
    (tmp_path / "a.py").write_text("AAA\n")
    _, read_tool = make_file_tools(str(tmp_path), ["a.py"])
    out = read_tool.invoke({"path": "../../etc/passwd"})
    assert "not in scope" in out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n project-reviewer python -m pytest tests/test_agent_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: app.agent.tools`.

- [ ] **Step 3: Write minimal implementation**

`app/agent/tools.py`:
```python
import os
from pathlib import Path

from langchain_core.tools import tool


def make_file_tools(repo_path: str, in_scope: list[str]):
    root = Path(repo_path).resolve()
    scope = set(in_scope)

    @tool
    def list_scope_files() -> str:
        """평가 대상(in-scope) 파일 경로 목록을 줄바꿈으로 반환한다."""
        return "\n".join(sorted(scope))

    @tool
    def read_repo_file(path: str) -> str:
        """in-scope인 레포 파일 하나의 텍스트 내용을 반환한다."""
        if path not in scope:
            return f"ERROR: '{path}' is not in scope."
        target = (root / path).resolve()
        if os.path.commonpath([str(root), str(target)]) != str(root):
            return f"ERROR: '{path}' is not in scope (path traversal)."
        try:
            return target.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            return f"ERROR: cannot read '{path}': {e}"

    return list_scope_files, read_repo_file


def make_tavily_tool():
    if not os.getenv("TAVILY_API_KEY"):
        @tool
        def tavily_search(query: str) -> str:
            """웹 검색 (비활성: TAVILY_API_KEY 없음)."""
            return "웹검색 미확인: TAVILY_API_KEY가 설정되지 않음."
        return tavily_search
    from langchain_tavily import TavilySearch
    return TavilySearch(max_results=3)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n project-reviewer python -m pytest tests/test_agent_tools.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```
git add app/agent/tools.py tests/test_agent_tools.py
git commit -m "feat(agent): scoped file-access tools and tavily tool"
```

---

## Task 14: Orchestrator (DeepAgents wiring)

**Files:**
- Create: `app/agent/orchestrator.py`
- Test: `tests/test_orchestrator_build.py`

- [ ] **Step 1: Write the failing test**

`tests/test_orchestrator_build.py`:
```python
import app.agent.orchestrator as orch


def test_build_agent_passes_subagents(monkeypatch):
    captured = {}

    def fake_create(**kw):
        captured.update(kw)
        return "AGENT"

    monkeypatch.setattr(orch, "create_deep_agent", fake_create)
    monkeypatch.setattr(orch, "ChatAnthropic", lambda **kw: "MODEL")
    agent = orch.build_agent(repo_path="/x", in_scope=["a.py"])
    assert agent == "AGENT"
    names = {s["name"] for s in captured["subagents"]}
    assert names == {"scanner", "library", "eng", "deadcode",
                     "techstack", "evaluator"}
    assert captured["model"] == "MODEL"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n project-reviewer python -m pytest tests/test_orchestrator_build.py -v`
Expected: FAIL — `ModuleNotFoundError: app.agent.orchestrator`.

- [ ] **Step 3: Write minimal implementation**

`app/agent/orchestrator.py`:
```python
import os

from deepagents import create_deep_agent
from langchain_anthropic import ChatAnthropic

from app.agent.prompts import (
    ORCHESTRATOR, SCANNER, CRITERIA_PROMPTS, EVALUATOR,
)
from app.agent.tools import make_file_tools, make_tavily_tool

MODEL_NAME = os.getenv("REVIEWER_MODEL", "claude-opus-4-7")


def build_agent(repo_path: str, in_scope: list[str]):
    list_tool, read_tool = make_file_tools(repo_path, in_scope)
    tavily = make_tavily_tool()
    model = ChatAnthropic(model=MODEL_NAME, max_tokens=8000)

    subagents = [
        {"name": "scanner", "description": "프로젝트 구조 스캐너",
         "prompt": SCANNER, "tools": [list_tool, read_tool]},
    ]
    for crit, prompt in CRITERIA_PROMPTS.items():
        subagents.append({
            "name": crit,
            "description": f"{crit} 기준 평가자",
            "prompt": prompt,
            "tools": [list_tool, read_tool],
        })
    subagents.append({
        "name": "evaluator",
        "description": "findings 검증자(critic), 신기술은 웹검색",
        "prompt": EVALUATOR,
        "tools": [read_tool, tavily],
    })

    return create_deep_agent(
        tools=[list_tool, read_tool],
        instructions=ORCHESTRATOR,
        subagents=subagents,
        model=model,
    )


def run_agent(agent, in_scope: list[str]) -> str:
    msg = (
        "다음 in-scope 파일들을 평가하라. 워크플로우대로 scanner → 4기준 → "
        "evaluator 순으로 진행하고, 최종적으로 evaluator가 검증한 결과를 "
        "JSON 배열로만 반환하라.\n파일:\n" + "\n".join(in_scope)
    )
    result = agent.invoke({"messages": [{"role": "user", "content": msg}]})
    return result["messages"][-1].content
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n project-reviewer python -m pytest tests/test_orchestrator_build.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```
git add app/agent/orchestrator.py tests/test_orchestrator_build.py
git commit -m "feat(agent): DeepAgents orchestrator with 6 subagents"
```

---

## Task 15: Findings parser (agent output → rows)

**Files:**
- Create: `app/findings_parser.py`
- Test: `tests/test_findings_parser.py`

- [ ] **Step 1: Write the failing test**

`tests/test_findings_parser.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n project-reviewer python -m pytest tests/test_findings_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: app.findings_parser`.

- [ ] **Step 3: Write minimal implementation**

`app/findings_parser.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n project-reviewer python -m pytest tests/test_findings_parser.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```
git add app/findings_parser.py tests/test_findings_parser.py
git commit -m "feat: parse agent JSON output into finding rows"
```

---

## Task 16: Report — Rich terminal summary + HTML

**Files:**
- Create: `app/report.py`
- Create: `templates/report.html.j2`
- Test: `tests/test_report.py`

- [ ] **Step 1: Write the failing test**

`tests/test_report.py`:
```python
from pathlib import Path
from app.report import render_html, terminal_summary


def _ctx():
    return {
        "repo_url": "https://x/y.git",
        "commit_sha": "abc123",
        "mode": "incremental",
        "overall": {"score": 77,
                    "criteria": {"library": 70, "eng": 80,
                                 "deadcode": 90, "techstack": None}},
        "cost": {"input_tokens": 100, "output_tokens": 20,
                 "cost_usd": 0.012, "run_url": "http://ls/x"},
        "duration_sec": 9.5,
        "findings": [
            {"file_path": "a.py", "criterion": "deadcode", "verified": True,
             "verify_note": "ok",
             "findings": [{"severity": "high", "location": "a.py:3",
                           "evidence": "e", "msg": "dead"}]},
        ],
        "changed_files": ["a.py"],
    }


def test_render_html_writes_self_contained_file(tmp_path):
    out = tmp_path / "r.html"
    render_html(_ctx(), str(out))
    html = out.read_text(encoding="utf-8")
    assert "<style>" in html          # inline CSS, self-contained
    assert "77" in html
    assert "a.py:3" in html
    assert "incremental" in html


def test_terminal_summary_returns_text():
    s = terminal_summary(_ctx())
    assert "77" in s
    assert "deadcode" in s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n project-reviewer python -m pytest tests/test_report.py -v`
Expected: FAIL — `ModuleNotFoundError: app.report`.

- [ ] **Step 3: Write minimal implementation**

`templates/report.html.j2`:
```html
<!doctype html><html><head><meta charset="utf-8">
<title>Project-Reviewer — {{ repo_url }}</title>
<style>
body{font-family:system-ui,sans-serif;margin:2rem;background:#0f1115;color:#e6e6e6}
.card{background:#1a1d24;border-radius:12px;padding:1rem 1.5rem;margin:1rem 0}
.score{font-size:2.5rem;font-weight:700}
.gauge{display:inline-block;margin-right:1.5rem}
.badge{padding:.15rem .5rem;border-radius:6px;background:#2a2f3a;font-size:.8rem}
.high{color:#ff6b6b}.medium{color:#ffd166}.low{color:#8bd450}
table{border-collapse:collapse;width:100%}td,th{padding:.4rem;border-bottom:1px solid #2a2f3a;text-align:left}
</style></head><body>
<h1>Project-Reviewer</h1>
<div class="card">
  <div>{{ repo_url }} @ <code>{{ commit_sha[:10] }}</code>
       <span class="badge">{{ mode }}</span></div>
  <div class="score">{{ overall.score if overall.score is not none else "N/A" }}</div>
  {% for c,v in overall.criteria.items() %}
    <span class="gauge">{{ c }}: <b>{{ v if v is not none else "—" }}</b></span>
  {% endfor %}
  <p>tokens in/out: {{ cost.input_tokens }}/{{ cost.output_tokens }} ·
     ${{ "%.4f"|format(cost.cost_usd) }} · {{ "%.1f"|format(duration_sec) }}s
     {% if cost.run_url %}· <a href="{{ cost.run_url }}">LangSmith</a>{% endif %}</p>
</div>
{% if mode == "incremental" %}
<div class="card"><h2>변경 하이라이트</h2>
<p>이번에 평가된 파일: {{ changed_files|join(", ") }}</p></div>
{% endif %}
{% for f in findings %}
<div class="card">
  <h3>{{ f.file_path }} — {{ f.criterion }}
    {% if f.verified %}✅{% else %}⚠️{% endif %}</h3>
  <p><i>{{ f.verify_note }}</i></p>
  <table><tr><th>severity</th><th>location</th><th>msg</th><th>evidence</th></tr>
  {% for x in f.findings %}
    <tr><td class="{{ x.severity }}">{{ x.severity }}</td>
        <td>{{ x.location }}</td><td>{{ x.msg }}</td><td>{{ x.evidence }}</td></tr>
  {% endfor %}</table>
</div>
{% endfor %}
</body></html>
```

`app/report.py`:
```python
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from rich.console import Console
from rich.table import Table

_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def render_html(ctx: dict, out_path: str) -> None:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(["html"]),
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n project-reviewer python -m pytest tests/test_report.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```
git add app/report.py templates/report.html.j2 tests/test_report.py
git commit -m "feat(report): Rich terminal summary and self-contained HTML"
```

---

## Task 17: CLI pipeline wiring (`main.py`)

**Files:**
- Create: `main.py`
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test (integration smoke with mocked agent)**

`tests/test_pipeline.py`:
```python
import json
import subprocess
from pathlib import Path

import main as cli


def _origin(tmp_path):
    o = tmp_path / "origin"
    o.mkdir()
    run = lambda *a: subprocess.run(a, cwd=o, check=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (o / "a.py").write_text("import os\nx = 1\n")
    (o / "b.py").write_text("y = 2\n")
    run("git", "add", ".")
    run("git", "commit", "-qm", "c1")
    return o


def _fake_agent_output(in_scope):
    return json.dumps([
        {"file_path": f, "criterion": "deadcode",
         "findings": [{"severity": "low", "location": f + ":1",
                       "evidence": "e", "msg": "m"}],
         "criterion_score": 80, "verified": True, "verify_note": "ok"}
        for f in in_scope
    ])


def test_full_then_incremental_reuses_cache(tmp_path, monkeypatch):
    origin = _origin(tmp_path)
    calls = []

    def fake_run_eval(repo_path, in_scope):
        calls.append(list(in_scope))
        return _fake_agent_output(in_scope)

    monkeypatch.setattr(cli, "run_evaluation", fake_run_eval)
    monkeypatch.setattr(
        cli, "aggregate_cost",
        lambda **kw: {"input_tokens": 1, "output_tokens": 1,
                      "cost_usd": 0.0, "run_url": None},
    )
    workdir = tmp_path / "wd"

    cli.review(str(origin), workdir=str(workdir), force=False)
    assert sorted(calls[0]) == ["a.py", "b.py"]   # full

    # modify only b.py and push a new commit
    run = lambda *a: subprocess.run(a, cwd=origin, check=True)
    (origin / "b.py").write_text("y = 3\n")
    run("git", "add", ".")
    run("git", "commit", "-qm", "c2")

    cli.review(str(origin), workdir=str(workdir), force=False)
    assert calls[1] == ["b.py"]                    # incremental: only b.py

    db = cli.db.connect(str(Path(workdir) / "reviewer.sqlite3"))
    repo = cli.db.get_repo_by_url(db, str(origin))
    latest = cli.db.latest_evaluation(db, repo["id"])
    rows = cli.db.findings_for_evaluation(db, latest["id"])
    paths = {r["file_path"] for r in rows}
    assert paths == {"a.py", "b.py"}               # a.py reused from cache
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n project-reviewer python -m pytest tests/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: main`.

- [ ] **Step 3: Write minimal implementation**

`main.py`:
```python
import argparse
import os
import sys
import time
import uuid
from pathlib import Path

from app import db
from app.repo_manager import (
    ensure_repo, head_sha, default_branch, inventory_files, file_hash,
)
from app.scope import plan_scope
from app.synthesizer import synthesize
from app.findings_parser import parse_findings
from app.langsmith_cost import aggregate_cost
from app.report import render_html, terminal_summary


def run_evaluation(repo_path: str, in_scope: list[str]) -> str:
    from app.agent.orchestrator import build_agent, run_agent
    agent = build_agent(repo_path, in_scope)
    return run_agent(agent, in_scope)


def review(remote_url: str, workdir: str, force: bool) -> dict:
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    cache_root = str(work / ".repocache")
    out_dir = work / "output"

    repo_path = ensure_repo(remote_url, cache_root)
    sha = head_sha(repo_path)
    branch = default_branch(repo_path)
    all_files = inventory_files(repo_path)

    conn = db.connect(str(work / "reviewer.sqlite3"))
    db.init_schema(conn)
    repo_id = db.upsert_repo(conn, remote_url, repo_path, branch)
    prior = db.latest_evaluation(conn, repo_id)
    prior_sha = prior["commit_sha"] if prior else None

    scope = plan_scope(prior_sha, repo_path, all_files, force)
    eval_id = db.create_evaluation(
        conn, repo_id, sha, prior["id"] if prior else None, scope.mode
    )

    if scope.mode == "incremental" and prior:
        db.copy_unchanged_findings(
            conn, repo_id, prior["id"], eval_id, scope.cached
        )

    trace_id = str(uuid.uuid4())
    os.environ.setdefault("LANGSMITH_PROJECT", "project-reviewer")
    started = time.time()
    rows = []
    if scope.in_scope:
        raw = run_evaluation(repo_path, scope.in_scope)
        for r in parse_findings(raw):
            h = file_hash(str(Path(repo_path) / r["file_path"])) \
                if (Path(repo_path) / r["file_path"]).exists() else ""
            db.insert_finding(
                conn, repo_id, eval_id, r["file_path"], h, r["criterion"],
                r["findings"], r["criterion_score"], r["verified"],
                r["verify_note"],
            )
        rows = list(db.findings_for_evaluation(conn, eval_id))
    duration = time.time() - started

    all_rows = list(db.findings_for_evaluation(conn, eval_id))
    overall = synthesize(all_rows)
    cost = aggregate_cost(
        trace_id=trace_id, project=os.environ["LANGSMITH_PROJECT"]
    )
    db.finalize_evaluation(conn, eval_id, overall, cost, duration)

    import json as _json
    ctx = {
        "repo_url": remote_url, "commit_sha": sha, "mode": scope.mode,
        "overall": overall, "cost": cost, "duration_sec": duration,
        "changed_files": scope.in_scope,
        "findings": [
            {"file_path": r["file_path"], "criterion": r["criterion"],
             "verified": bool(r["verified"]),
             "verify_note": r["verify_note"] or "",
             "findings": _json.loads(r["findings_json"])}
            for r in all_rows
        ],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"report-{eval_id}.html"
    render_html(ctx, str(html_path))
    print(terminal_summary(ctx))
    print(f"\nHTML report: {html_path}")
    return ctx


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Project-Reviewer")
    parser.add_argument("github_url")
    parser.add_argument("--workdir", default=".reviewer")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    review(args.github_url, args.workdir, args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n project-reviewer python -m pytest tests/test_pipeline.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Run the full suite**

Run: `conda run -n project-reviewer python -m pytest -v`
Expected: ALL PASS.

- [ ] **Step 6: Commit**

```
git add main.py tests/test_pipeline.py
git commit -m "feat: end-to-end CLI pipeline with diff-based cache reuse"
```

---

## Task 18: Optional FastAPI history server (`--serve`)

**Files:**
- Create: `app/server.py`
- Create: `templates/history.html.j2`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing test**

`tests/test_server.py`:
```python
from fastapi.testclient import TestClient
from app import db
from app.server import create_app


def _seed(tmp_path):
    dbp = str(tmp_path / "reviewer.sqlite3")
    c = db.connect(dbp)
    db.init_schema(c)
    rid = db.upsert_repo(c, "https://x/y.git", "/p", "main")
    e = db.create_evaluation(c, rid, "sha1", None, "full")
    db.finalize_evaluation(
        c, e, {"score": 80, "criteria": {"library": 80, "eng": None,
        "deadcode": None, "techstack": None}},
        {"input_tokens": 5, "output_tokens": 2, "cost_usd": 0.01,
         "run_url": None}, 3.0,
    )
    return dbp


def test_index_lists_repos(tmp_path):
    dbp = _seed(tmp_path)
    client = TestClient(create_app(dbp))
    r = client.get("/")
    assert r.status_code == 200
    assert "https://x/y.git" in r.text


def test_repo_history_page(tmp_path):
    dbp = _seed(tmp_path)
    client = TestClient(create_app(dbp))
    r = client.get("/repo/1")
    assert r.status_code == 200
    assert "80" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n project-reviewer python -m pytest tests/test_server.py -v`
Expected: FAIL — `ModuleNotFoundError: app.server`.

- [ ] **Step 3: Write minimal implementation**

`templates/history.html.j2`:
```html
<!doctype html><html><head><meta charset="utf-8"><title>Project-Reviewer</title>
<style>body{font-family:system-ui;margin:2rem;background:#0f1115;color:#eee}
a{color:#8bd450}table{border-collapse:collapse}td,th{padding:.4rem;border-bottom:1px solid #2a2f3a}</style>
</head><body>
{% if repos is defined %}
<h1>Repos</h1><ul>
{% for r in repos %}<li><a href="/repo/{{ r.id }}">{{ r.remote_url }}</a></li>{% endfor %}
</ul>
{% else %}
<h1>{{ repo.remote_url }}</h1>
<table><tr><th>id</th><th>sha</th><th>mode</th><th>score</th><th>cost</th></tr>
{% for e in evals %}<tr><td>{{ e.id }}</td><td>{{ e.commit_sha[:10] }}</td>
<td>{{ e.mode }}</td><td>{{ e.score }}</td><td>${{ "%.4f"|format(e.cost) }}</td></tr>{% endfor %}
</table>{% endif %}
</body></html>
```

`app/server.py`:
```python
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app import db

_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def _env():
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(["html"]),
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
```

- [ ] **Step 4: Add `--serve` to `main.py`**

In `main.py` `main()`, replace the body after `args = parser.parse_args(argv)` with:
```python
    if args.serve:
        from app.server import serve
        serve(str(Path(args.workdir) / "reviewer.sqlite3"))
        return 0
    review(args.github_url, args.workdir, args.force)
    return 0
```
And add to the parser (before `parse_args`):
```python
    parser.add_argument("--serve", action="store_true",
                        help="이력 탐색 웹 서버 실행")
```
Change `github_url` arg to `parser.add_argument("github_url", nargs="?")` so `--serve` can run without a URL.

- [ ] **Step 5: Run server tests and full suite**

Run: `conda run -n project-reviewer python -m pytest -v`
Expected: ALL PASS.

- [ ] **Step 6: Commit**

```
git add app/server.py templates/history.html.j2 tests/test_server.py main.py
git commit -m "feat(server): optional FastAPI history/comparison UI (--serve)"
```

---

## Task 19: README & env documentation

**Files:**
- Modify: `README.md`
- Create: `.env.example`

- [ ] **Step 1: Write `.env.example`**

```
ANTHROPIC_API_KEY=
TAVILY_API_KEY=
LANGSMITH_API_KEY=
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=project-reviewer
REVIEWER_MODEL=claude-opus-4-7
```

- [ ] **Step 2: Write `README.md`**

```markdown
# Project-Reviewer

GitHub 레포를 클론해 코드 품질을 정성 평가하는 멀티 에이전트 시스템.
작동 여부가 아니라 라이브러리 의도성·오버/언더엔지니어링·데드코드·기술
스택 활용을 평가한다. git diff 기반으로 변경분만 재평가해 토큰을 절약한다.

## 설치

    conda create -y -n project-reviewer python=3.13
    conda activate project-reviewer
    uv pip install -r requirements.txt
    cp .env.example .env   # 키 채우기

## 사용

    python main.py https://github.com/owner/repo.git
    python main.py https://github.com/owner/repo.git --force   # 전체 재평가
    python main.py --serve                                     # 이력 웹 UI

결과: 터미널 요약 + `.reviewer/output/report-<id>.html`.

## 테스트

    python -m pytest
```

- [ ] **Step 3: Run full suite once more**

Run: `conda run -n project-reviewer python -m pytest -v`
Expected: ALL PASS.

- [ ] **Step 4: Commit**

```
git add README.md .env.example
git commit -m "docs: README and env example"
```

---

## Self-Review Notes

- **Spec coverage:** 4 criteria → Task 12 prompts + Task 14 subagents. git-diff cache → Tasks 8/9/4/17. SQLite schema → Tasks 2–5. evaluator-critic + Tavily web search → Tasks 12/13/14. Terminal+HTML output → Task 16. Optional FastAPI → Task 18. LangSmith cost → Task 11/17. DeepAgents (planning + subagents + tools) → Task 14. Error handling (no keys, bad sha, parse fail) → Tasks 9/11/13/15. All spec sections mapped.
- **Type consistency:** `Scope(mode,in_scope,cached)` consistent across Tasks 9/17. `synthesize` returns `{"score","criteria"}` consistent in Tasks 10/16/18. finding row dict keys consistent across Tasks 15/17. `aggregate_cost` return shape consistent across Tasks 11/17.
- **Placeholders:** none; every code/test step contains full code.
- **Note for executor:** Task 13 uses `langchain_tavily.TavilySearch` and Task 14 uses `deepagents.create_deep_agent` / `langchain_anthropic.ChatAnthropic`. If the installed `deepagents` version exposes a different `create_deep_agent` signature (e.g., `subagents=` vs `subagent=`), adapt the kwarg names while keeping the 6-subagent structure; tests mock these so unit tests stay green, but verify the real call in a manual smoke run before relying on it.
