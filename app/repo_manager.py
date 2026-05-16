import hashlib
import re
from pathlib import Path

from git import Repo


def _slug(remote_url: str) -> str:
    # Builds a filesystem-safe, collision-resistant cache dir name from the remote URL.
    base = re.sub(r"[^A-Za-z0-9_.-]", "_", remote_url.rstrip("/"))
    digest = hashlib.sha1(remote_url.encode()).hexdigest()[:8]
    return f"{base[-40:]}_{digest}"


def ensure_repo(remote_url: str, cache_root: str) -> str:
    """Clone the remote into a cache dir, or fetch+hard-reset if already cloned."""
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
    """Return the full 40-character SHA of HEAD in the given repo."""
    return Repo(repo_path).head.commit.hexsha


def default_branch(repo_path: str) -> str:
    """Return the name of the currently active branch in the given repo."""
    return Repo(repo_path).active_branch.name
