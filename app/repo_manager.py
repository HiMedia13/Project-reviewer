import hashlib
import re
import shutil
from pathlib import Path

from git import Repo


def _slug(remote_url: str) -> str:
    # Builds a filesystem-safe, collision-resistant cache dir name from the remote URL.
    base = re.sub(r"[^A-Za-z0-9_.-]", "_", remote_url.rstrip("/"))
    digest = hashlib.sha1(remote_url.encode()).hexdigest()[:8]
    # Total slug length is bounded (~49 chars: 40 base + "_" + 8 digest) to stay
    # well under Windows MAX_PATH given a reasonable cache_root.
    return f"{base[-40:]}_{digest}"


def _branch_name(repo) -> str:
    """Active branch name, falling back to the remote's default or 'main'.

    A cached clone could theoretically end up detached (external
    corruption); we never detach it ourselves, but resolve defensively.
    """
    try:
        return repo.active_branch.name
    except TypeError:
        try:
            ref = repo.remotes.origin.refs["HEAD"].reference.name
            return ref.split("/")[-1]
        except (KeyError, AttributeError, IndexError):
            return "main"


def ensure_repo(remote_url: str, cache_root: str) -> str:
    """Clone the remote into a cache dir, or fetch+hard-reset if already cloned."""
    dest = Path(cache_root) / _slug(remote_url)
    if (dest / ".git").exists():
        repo = Repo(dest)
        repo.remotes.origin.fetch()
        repo.git.reset("--hard", f"origin/{_branch_name(repo)}")
    else:
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        Repo.clone_from(remote_url, dest)
    return str(dest)


def head_sha(repo_path: str) -> str:
    """Return the full 40-character SHA of HEAD in the given repo."""
    return Repo(repo_path).head.commit.hexsha


def default_branch(repo_path: str) -> str:
    """Return the name of the currently active branch in the given repo."""
    return _branch_name(Repo(repo_path))


# Extensions recognised as source code (vendored/generated dirs are excluded via SKIP_DIRS).
CODE_EXT = {".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs",
            ".rb", ".c", ".cpp", ".h", ".hpp", ".cs", ".kt", ".swift",
            ".php", ".scala"}

# Directories to skip entirely when walking the repo tree.
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
             "dist", "build", ".pytest_cache"}


def inventory_files(repo_path: str) -> list[str]:
    """Return sorted posix-relative paths of all code files under repo_path, skipping vendored/build dirs."""
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
    """Return the SHA-1 hex digest of the file's raw bytes."""
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()
