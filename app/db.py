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
