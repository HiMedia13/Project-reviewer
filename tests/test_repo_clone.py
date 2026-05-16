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
