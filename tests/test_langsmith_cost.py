import app.langsmith_cost as lc


class _Run:
    def __init__(self, pt, ct, cost):
        self.id = "run-abc"
        self.prompt_tokens = pt
        self.completion_tokens = ct
        self.total_cost = cost


def test_aggregate_sums_runs(monkeypatch):
    runs = [_Run(100, 20, 0.01), _Run(50, 10, 0.005)]

    class FakeClient:
        def list_runs(self, **kw):
            return iter(runs)

        def read_run(self, rid):
            class R:
                url = "http://ls/run/abc"
            return R()

    monkeypatch.setattr(lc, "_client", lambda: FakeClient())
    out = lc.aggregate_cost(trace_id="t1", project="p")
    assert out["input_tokens"] == 150
    assert out["output_tokens"] == 30
    assert round(out["cost_usd"], 4) == 0.015
    assert out["run_url"] == "http://ls/run/abc"


def test_aggregate_returns_zero_when_disabled(monkeypatch):
    monkeypatch.setattr(lc, "_client", lambda: None)
    out = lc.aggregate_cost(trace_id=None, project="p")
    assert out == {"input_tokens": 0, "output_tokens": 0,
                   "cost_usd": 0.0, "run_url": None}


def test_aggregate_zero_when_client_ok_but_no_trace(monkeypatch):
    class FakeClient:
        def list_runs(self, **kw):
            raise AssertionError("must not be called when trace_id is None")

    monkeypatch.setattr(lc, "_client", lambda: FakeClient())
    out = lc.aggregate_cost(trace_id=None, project="p")
    assert out == {"input_tokens": 0, "output_tokens": 0,
                   "cost_usd": 0.0, "run_url": None}
