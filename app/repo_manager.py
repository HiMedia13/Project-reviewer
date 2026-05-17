import ast
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

# Heuristic frontend classification, used to optionally drop UI/web files
# when the reviewer only cares about the backend. Design bias: never drop
# a backend file. Inherently-frontend extensions count as frontend
# anywhere; ambiguous JS/TS only counts as frontend under a frontend-ish
# directory; backend languages (.py/.go/.rs/...) are never frontend.
FRONTEND_EXT = {".jsx", ".tsx", ".vue", ".svelte", ".css", ".scss",
                ".sass", ".less", ".styl", ".html", ".htm"}
WEB_AMBIG_EXT = {".js", ".ts", ".mjs", ".cjs"}
FRONTEND_DIRS = {"frontend", "client", "web", "webapp", "ui", "public",
                 "static", "assets", "www", "styles"}


def is_frontend_path(rel_posix: str) -> bool:
    """True if *rel_posix* looks like a frontend/UI file by the heuristic."""
    p = Path(rel_posix)
    ext = p.suffix.lower()
    if ext in FRONTEND_EXT:
        return True
    if ext in WEB_AMBIG_EXT and any(
        part.lower() in FRONTEND_DIRS for part in p.parts
    ):
        return True
    return False


def exclude_frontend(files: list[str]) -> list[str]:
    """Return *files* with heuristically-frontend paths dropped (order kept)."""
    return [f for f in files if not is_frontend_path(f)]


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


def changed_files(repo_path: str, since_sha: str) -> set[str]:
    """Return the set of file paths changed between since_sha and HEAD."""
    repo = Repo(repo_path)
    out = repo.git.diff("--name-only", f"{since_sha}..HEAD")
    return {line.strip() for line in out.splitlines() if line.strip()}


def _module_to_paths(module: str) -> set[str]:
    """Return candidate relative file paths that could correspond to the given module name."""
    parts = module.split(".")
    return {
        "/".join(parts) + ".py",
        "/".join(parts) + "/__init__.py",
        parts[-1] + ".py",
    }


def _python_imports(source: str) -> set[str]:
    """Parse Python source and return all imported module names."""
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


def reverse_import_expand(repo_path: str, seed_files: set[str], all_files: list[str]) -> set[str]:
    """Expand seed_files to include any .py files in all_files that import them."""
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
