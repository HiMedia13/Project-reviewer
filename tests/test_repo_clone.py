import subprocess
from pathlib import Path
from app.repo_manager import ensure_repo, head_sha, default_branch


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
    sha1 = head_sha(p1)
    assert len(sha1) == 40

    # Advance origin with a second commit.
    (origin / "b.py").write_text("print(2)\n")
    subprocess.run(["git", "add", "."], cwd=origin, check=True)
    subprocess.run(["git", "commit", "-qm", "c2"], cwd=origin, check=True)

    p2 = ensure_repo(str(origin), str(cache))  # second call: pull advances clone
    assert p1 == p2
    assert head_sha(p2) != sha1
    assert (Path(p2) / "b.py").exists()


def test_default_branch_returns_branch_name(tmp_path):
    origin = _make_origin(tmp_path)
    cache = tmp_path / "cache"
    p = ensure_repo(str(origin), str(cache))
    origin_branch = subprocess.check_output(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=origin, text=True
    ).strip()
    assert default_branch(p) == origin_branch
    assert default_branch(p)
