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
