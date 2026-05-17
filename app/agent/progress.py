"""Live progress TUI callback handler for the Project-Reviewer run.

The USD figures here are APPROXIMATE live estimates only, computed from
public list prices for a rough in-flight meter. The authoritative cost
for a finished run comes from ``app.langsmith_cost.aggregate_cost``.

This module is isolated: nothing else imports it yet. When the TUI is
disabled (non-TTY), behavior is identical to a plain ``agent.invoke`` —
no callbacks attached, no Rich Live.
"""

import collections
import time

from langchain_core.callbacks import BaseCallbackHandler

PHASES = ["scanner", "library", "eng", "deadcode", "techstack", "evaluator"]

# Approximate USD per 1M tokens (input, output), keyed by model-id prefix.
# Longest matching prefix wins; see _price_for. These are best-effort
# public list prices for a live estimate only.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-opus-4": (15.0, 75.0),
}


def _price_for(model: str) -> tuple[float, float] | None:
    """Return (input, output) price per 1M tokens for *model*.

    Matches by prefix, preferring the longest registered key so a more
    specific entry wins. Returns None when no prefix matches.
    """
    if not model:
        return None
    best_key = None
    for key in MODEL_PRICES:
        if model.startswith(key):
            if best_key is None or len(key) > len(best_key):
                best_key = key
    if best_key is None:
        return None
    return MODEL_PRICES[best_key]


class ReviewProgress(BaseCallbackHandler):
    """Rich-renderable callback handler tracking phases, files, tokens."""

    def __init__(self, total_files: int, model: str, max_log: int = 12):
        self._start = time.monotonic()
        self.total_files = total_files
        self.model = model
        self.phase_status: dict[str, str] = {p: "pending" for p in PHASES}
        self.current: str | None = None
        self.read_paths: set[str] = set()
        self.in_tokens = 0
        self.out_tokens = 0
        self.activity: collections.deque[str] = collections.deque(
            maxlen=max_log)
        # Optional hook invoked after state changes (used to refresh Live).
        self._on_change = None

    # -- helpers ----------------------------------------------------------

    def _notify(self):
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception:
                pass

    @staticmethod
    def _resolve_args(input_str, inputs) -> dict:
        """Best-effort extraction of a tool's argument dict.

        deepagents may pass structured ``inputs=`` (a dict) or a legacy
        ``input_str=``. Handle both; never raise.
        """
        if isinstance(inputs, dict):
            return inputs
        if isinstance(input_str, dict):
            return input_str
        if isinstance(input_str, str) and input_str.strip():
            try:
                import json
                parsed = json.loads(input_str)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        return {}

    # -- callbacks --------------------------------------------------------

    def on_tool_start(self, serialized, input_str=None, *,
                      inputs=None, **kwargs):
        try:
            name = (serialized or {}).get("name")
            args = self._resolve_args(input_str, inputs)

            if name == "task":
                sub = args.get("subagent_type")
                if sub in PHASES:
                    if (self.current is not None
                            and self.phase_status.get(self.current)
                            == "active"):
                        self.phase_status[self.current] = "done"
                    self.phase_status[sub] = "active"
                    self.current = sub
            elif name == "read_repo_file":
                path = args.get("path")
                if path:
                    self.read_paths.add(path)
                    self.activity.append(f"read_repo_file {path}")
                else:
                    self.activity.append("read_repo_file")
            elif name == "tavily_search":
                q = str(args.get("query", ""))[:40]
                self.activity.append(f"tavily: {q}")
            elif name == "list_scope_files":
                self.activity.append("list_scope_files")
            elif name:
                self.activity.append(str(name))
        except Exception:
            pass
        self._notify()

    def on_tool_end(self, output, **kwargs):
        # Intentionally minimal: nothing to record, must not error.
        return None

    def on_llm_end(self, response, **kwargs):
        try:
            inp, out = self._extract_usage(response)
            self.in_tokens += inp
            self.out_tokens += out
        except Exception:
            pass
        self._notify()

    @staticmethod
    def _extract_usage(response) -> tuple[int, int]:
        """Best-effort (input, output) token counts from an LLM result."""
        inp = 0
        out = 0

        llm_output = getattr(response, "llm_output", None)
        if isinstance(llm_output, dict):
            usage = (llm_output.get("usage")
                     or llm_output.get("token_usage")
                     or {})
            if isinstance(usage, dict):
                inp += int(usage.get("input_tokens")
                           or usage.get("prompt_tokens") or 0)
                out += int(usage.get("output_tokens")
                           or usage.get("completion_tokens") or 0)

        generations = getattr(response, "generations", None) or []
        for group in generations:
            try:
                for gen in group:
                    msg = getattr(gen, "message", None)
                    meta = getattr(msg, "usage_metadata", None)
                    if isinstance(meta, dict):
                        inp += int(meta.get("input_tokens") or 0)
                        out += int(meta.get("output_tokens") or 0)
            except TypeError:
                continue

        return inp, out

    # -- derived state ----------------------------------------------------

    def cost_usd(self) -> float | None:
        price = _price_for(self.model)
        if price is None:
            return None
        in_rate, out_rate = price
        return (self.in_tokens / 1_000_000.0) * in_rate + \
               (self.out_tokens / 1_000_000.0) * out_rate

    def elapsed(self) -> float:
        return time.monotonic() - self._start

    # -- rendering --------------------------------------------------------

    def render(self):
        from rich.console import Group
        from rich.panel import Panel
        from rich.text import Text

        secs = max(0.0, self.elapsed())
        mm = int(secs) // 60
        ss = int(secs) % 60

        header = Text()
        header.append("Project-Reviewer  ", style="bold")
        header.append(f"model={self.model}  ", style="cyan")
        header.append(f"elapsed={mm:02d}:{ss:02d}", style="dim")

        glyphs = {"done": "✓", "active": "▶", "pending": "·"}
        styles = {"done": "green", "active": "yellow", "pending": "dim"}
        phase_line = Text()
        for i, p in enumerate(PHASES):
            st = self.phase_status.get(p, "pending")
            if i:
                phase_line.append("  ")
            phase_line.append(f"{glyphs[st]} {p}", style=styles[st])

        total = self.total_files or 0
        files_line = Text(
            f"files: {len(self.read_paths)}/{total}", style="magenta")

        cost = self.cost_usd()
        cost_str = "~$—" if cost is None else f"~${cost:.4f}"
        tokens_line = Text(
            f"tokens in/out: {self.in_tokens}/{self.out_tokens} "
            f"· {cost_str}",
            style="blue")

        if self.activity:
            body = Text("\n".join(self.activity))
        else:
            body = Text("(no activity yet)", style="dim")
        panel = Panel(body, title="recent activity", border_style="dim")

        return Group(header, phase_line, files_line, tokens_line, panel)


def _plain_line(h: "ReviewProgress") -> str:
    m, s = divmod(int(h.elapsed()), 60)
    cost = h.cost_usd()
    cost_s = "—" if cost is None else f"{cost:.4f}"
    last = h.activity[-1] if h.activity else ""
    return (f"[{m:02d}:{s:02d}] phase={h.current or '—'} "
            f"files={len(h.read_paths)}/{h.total_files} "
            f"tok={h.in_tokens}/{h.out_tokens} ~${cost_s} | {last}")


def run_with_progress(agent, payload: dict, *, enabled: bool,
                      total_files: int, model: str,
                      base_config: dict | None = None, plain: bool = False,
                      _live_factory=None) -> str:
    """Invoke *agent* with an optional live progress display.

    When ``enabled`` is False this is exactly ``agent.invoke(payload)``
    (plus ``base_config`` if given) — no handler, no callbacks, no Live
    (the unchanged non-TTY suppression contract).

    ``base_config`` is merged into the invoke config in every path that
    passes one (e.g. ``{"recursion_limit": N}``). ``plain`` renders
    throttled one-line status to stdout instead of a Rich Live TUI, for
    non-TTY logs (background/CI). ``_live_factory`` is a test seam.
    """
    if not enabled:
        if base_config:
            result = agent.invoke(payload, config=base_config)
        else:
            result = agent.invoke(payload)
        return result["messages"][-1].content

    handler = ReviewProgress(total_files, model)
    config = {**(base_config or {}), "callbacks": [handler]}

    if plain:
        state = {"phase": None, "n": 0}

        def _emit():
            state["n"] += 1
            if handler.current != state["phase"] or state["n"] % 10 == 0:
                state["phase"] = handler.current
                print(_plain_line(handler), flush=True)

        handler._on_change = _emit
        try:
            result = agent.invoke(payload, config=config)
        finally:
            print(_plain_line(handler), flush=True)
        return result["messages"][-1].content

    if _live_factory is None:
        from rich.live import Live
        _live_factory = Live

    with _live_factory(handler.render(), refresh_per_second=4,
                       transient=False) as live:
        handler._on_change = lambda: live.update(handler.render())
        try:
            result = agent.invoke(payload, config=config)
        finally:
            live.update(handler.render())
    return result["messages"][-1].content
