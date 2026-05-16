import json
import sqlite3
from datetime import datetime, timezone

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
    """Open a SQLite connection with Row factory and foreign keys enabled."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes if they do not already exist."""
    conn.executescript(SCHEMA)
    # Required: executescript only commits a pending transaction before
    # running the script and performs no implicit commit afterward, so
    # this commit is what persists the DDL. Do not remove.
    conn.commit()


def _now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def upsert_repo(conn, remote_url: str, local_path: str,
                default_branch: str) -> int:
    """Insert the repo or update its mutable fields; return its id.

    Atomic via ON CONFLICT so a repeated URL never duplicates a row.
    created_at is intentionally NOT updated on conflict (it records
    first-seen time).
    """
    conn.execute(
        "INSERT INTO repos (remote_url, local_path, default_branch, "
        "created_at) VALUES (?,?,?,?) "
        "ON CONFLICT(remote_url) DO UPDATE SET "
        "local_path=excluded.local_path, "
        "default_branch=excluded.default_branch",
        (remote_url, local_path, default_branch, _now()),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM repos WHERE remote_url=?", (remote_url,)
    ).fetchone()
    return row["id"]


def get_repo_by_url(conn, remote_url: str):
    """Return the repo row for the given remote URL, or None if absent."""
    return conn.execute(
        "SELECT * FROM repos WHERE remote_url=?", (remote_url,)
    ).fetchone()


def create_evaluation(conn, repo_id: int, commit_sha: str,
                       parent_eval_id: int | None, mode: str) -> int:
    """Insert a new evaluation row and return its id."""
    cur = conn.execute(
        "INSERT INTO evaluations (repo_id, commit_sha, parent_eval_id, mode, "
        "created_at) VALUES (?,?,?,?,?)",
        (repo_id, commit_sha, parent_eval_id, mode, _now()),
    )
    conn.commit()
    return cur.lastrowid


def latest_evaluation(conn, repo_id: int):
    """Return the most recent evaluation row for a repo, or None."""
    return conn.execute(
        # id is autoincrement → newest last
        "SELECT * FROM evaluations WHERE repo_id=? ORDER BY id DESC LIMIT 1",
        (repo_id,),
    ).fetchone()


def insert_finding(conn, repo_id, evaluation_id, file_path, file_hash,
                   criterion, findings, score, verified, verify_note) -> int:
    """Persist a single file-level findings row; return its new row id."""
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
    """Return all file_findings rows for the given evaluation id."""
    return conn.execute(
        "SELECT * FROM file_findings WHERE evaluation_id=?", (evaluation_id,)
    ).fetchall()


def finalize_evaluation(conn, evaluation_id, overall, cost, duration_sec) -> None:
    """Write the final overall/cost/duration onto an existing evaluation row."""
    conn.execute(
        "UPDATE evaluations SET overall_json=?, total_input_tokens=?, "
        "total_output_tokens=?, total_cost_usd=?, langsmith_run_url=?, "
        "duration_sec=? WHERE id=?",
        (json.dumps(overall), cost.get("input_tokens", 0),
         cost.get("output_tokens", 0), cost.get("cost_usd", 0.0),
         cost.get("run_url"), duration_sec, evaluation_id),
    )
    conn.commit()


def copy_unchanged_findings(conn, repo_id, src_eval, dst_eval, file_paths) -> int:
    """Copy findings rows for unchanged files from one evaluation to another.

    Avoids re-running the LLM for files whose content has not changed.
    Returns the number of rows copied.
    """
    if not file_paths:
        return 0
    # Safe f-string: only interpolates '?' characters (parameter count),
    # never user data — all values remain bound via parameterized query.
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
