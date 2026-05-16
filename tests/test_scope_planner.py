from app.scope import plan_scope


def test_full_when_no_prior(tmp_path):
    s = plan_scope(prior_sha=None, repo_path=None, all_files=["a.py", "b.py"],
                    force=False)
    assert s.mode == "full"
    assert set(s.in_scope) == {"a.py", "b.py"}


def test_full_when_forced():
    s = plan_scope(prior_sha="abc", repo_path=None, all_files=["a.py"],
                   force=True)
    assert s.mode == "full"


def test_incremental_uses_changed_plus_reverse(monkeypatch):
    import app.scope as scope
    monkeypatch.setattr(scope, "changed_files", lambda p, s: {"lib.py"})
    monkeypatch.setattr(
        scope, "reverse_import_expand",
        lambda p, seed, allf: {"lib.py", "uses.py"},
    )
    s = plan_scope(prior_sha="abc", repo_path="/x",
                   all_files=["lib.py", "uses.py", "other.py"], force=False)
    assert s.mode == "incremental"
    assert set(s.in_scope) == {"lib.py", "uses.py"}
    assert set(s.cached) == {"other.py"}


def test_incremental_falls_back_to_full_on_bad_sha(monkeypatch):
    import app.scope as scope

    def boom(p, s):
        raise RuntimeError("bad sha")

    monkeypatch.setattr(scope, "changed_files", boom)
    s = plan_scope(prior_sha="gone", repo_path="/x",
                   all_files=["a.py"], force=False)
    assert s.mode == "full"
