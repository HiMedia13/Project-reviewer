from pathlib import Path
from app.repo_manager import (
    inventory_files, file_hash, is_frontend_path, exclude_frontend,
)

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


def test_is_frontend_path_inherently_frontend_extensions():
    for f in ["src/App.tsx", "ui/Button.jsx", "comp.vue", "x.svelte",
              "styles/main.css", "a/b.scss", "page.html", "old.htm"]:
        assert is_frontend_path(f) is True, f


def test_is_frontend_path_ambiguous_js_needs_frontend_dir():
    # .js/.ts are frontend only under a frontend-ish directory.
    assert is_frontend_path("frontend/index.js") is True
    assert is_frontend_path("client/store.ts") is True
    assert is_frontend_path("static/app.js") is True
    # ambiguous but NOT in a frontend dir → kept (could be Node backend).
    assert is_frontend_path("src/api/server.js") is False
    assert is_frontend_path("lib/util.ts") is False


def test_is_frontend_path_never_drops_backend_languages():
    # A backend-language file is never frontend, even under web/ etc.
    assert is_frontend_path("web/api.py") is False
    assert is_frontend_path("ui/handler.go") is False
    assert is_frontend_path("app/main.py") is False


def test_exclude_frontend_filters_and_preserves_order():
    files = ["app/main.py", "frontend/index.js", "core/db.py",
             "src/App.tsx", "static/app.js", "service.go"]
    assert exclude_frontend(files) == [
        "app/main.py", "core/db.py", "service.go",
    ]


def test_inventory_then_exclude_frontend(tmp_path):
    (tmp_path / "main.py").write_text("x=1\n")
    fe = tmp_path / "frontend"
    fe.mkdir()
    (fe / "App.tsx").write_text("export default 1\n")
    (fe / "index.js").write_text("console.log(1)\n")
    files = inventory_files(str(tmp_path))
    assert "frontend/App.tsx" in files  # inventory still lists it
    assert exclude_frontend(files) == ["main.py"]


def test_file_hash_changes_with_content(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x=1\n")
    h1 = file_hash(str(f))
    f.write_text("x=2\n")
    assert h1 != file_hash(str(f))
