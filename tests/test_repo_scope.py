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
