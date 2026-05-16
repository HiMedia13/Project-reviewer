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
