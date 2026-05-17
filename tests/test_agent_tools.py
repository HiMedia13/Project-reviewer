from app.agent.tools import make_file_tools


def test_read_repo_file_restricted_to_scope(tmp_path):
    (tmp_path / "a.py").write_text("AAA\n")
    (tmp_path / "secret.py").write_text("SECRET\n")
    list_tool, read_tool = make_file_tools(str(tmp_path), ["a.py"])
    assert "a.py" in list_tool.invoke({})
    assert "AAA" in read_tool.invoke({"path": "a.py"})
    blocked = read_tool.invoke({"path": "secret.py"})
    assert "not in scope" in blocked.lower()


def test_read_repo_file_rejects_traversal(tmp_path):
    (tmp_path / "a.py").write_text("AAA\n")
    _, read_tool = make_file_tools(str(tmp_path), ["a.py"])
    out = read_tool.invoke({"path": "../../etc/passwd"})
    assert "not in scope" in out.lower()


def test_read_repo_file_guard_catches_traversal_in_allowlist(tmp_path):
    # A poisoned allowlist entry passes the scope check, so the
    # is_relative_to guard is the boundary that must reject it.
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "secret.py").write_text("SECRET\n")
    _, read_tool = make_file_tools(str(repo), ["../secret.py"])
    out = read_tool.invoke({"path": "../secret.py"})
    assert "not in scope" in out.lower()
    assert "SECRET" not in out
