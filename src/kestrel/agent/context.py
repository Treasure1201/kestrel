"""Per-request state: the evidence shown to the model, the searches and calculations it made, and its budget.

One `RequestContext` is created for each question and bound to that question's tools,
so labels (`c1`, `c7.r4`) are stable within the request and never leak between
requests. It is used from one event loop at a time; a lock still guards it because
tools may run in worker threads.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from kestrel.agent.calculator import Calculation
from kestrel.contracts import AnswerStep, Evidence, SearchHit, SearchScope, StepKind, TableRowView
from kestrel.errors import BudgetExceededError


@dataclass
class Budget:
    """One total budget per question: model calls, searches, repair turns and wall-clock time."""

    max_model_calls: int
    max_searches: int
    max_repairs: int
    timeout_s: float
    started: float = field(default_factory=time.monotonic)
    model_calls: int = 0
    searches: int = 0
    repairs: int = 0
    nudges: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def add_tokens(self, input_tokens: int, output_tokens: int) -> None:
        """Record token usage read from one model response."""
        self.input_tokens += max(0, int(input_tokens))
        self.output_tokens += max(0, int(output_tokens))

    @property
    def remaining_s(self) -> float:
        """Seconds left before the wall-clock limit."""
        return self.timeout_s - (time.monotonic() - self.started)

    @property
    def model_calls_left(self) -> int:
        return self.max_model_calls - self.model_calls

    def take_model_call(self) -> None:
        """Count one model call; raise when the budget is spent."""
        if self.model_calls >= self.max_model_calls:
            raise BudgetExceededError(
                f"The question used its limit of {self.max_model_calls} model calls without a final answer. "
                "Try a more specific question.",
            )
        if self.remaining_s <= 0:
            raise BudgetExceededError(f"The question took longer than {self.timeout_s:g} seconds. Try again or ask a more specific question.")
        self.model_calls += 1


def _query_key(query: str) -> str:
    return " ".join(query.casefold().split())


@dataclass
class RequestContext:
    """What the model was shown and did during one question."""

    scope: SearchScope
    budget: Budget
    evidence: dict[str, Evidence] = field(default_factory=dict)
    queries: list[str] = field(default_factory=list)
    calculations: list[Calculation] = field(default_factory=list)
    tables_fetched: dict[str, str] = field(default_factory=dict)
    """section id -> the label whose rows were registered for it."""
    steps: list[AnswerStep] = field(default_factory=list)
    """Every tool call, check and repair, in the order they happened."""
    _by_chunk: dict[str, str] = field(default_factory=dict)
    _next: int = 1
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def seen_query(self, query: str) -> bool:
        """True when the same query (ignoring case and spacing) was already searched."""
        return _query_key(query) in {_query_key(q) for q in self.queries}

    def record_query(self, query: str) -> None:
        with self._lock:
            self.queries.append(query)
            self.budget.searches += 1

    def register_hit(self, hit: SearchHit) -> tuple[str, bool]:
        """Label a search hit. Returns (label, new): a chunk seen earlier keeps its first label."""
        with self._lock:
            existing = self._by_chunk.get(hit.chunk_id)
            if existing is not None:
                return existing, False
            label = f"c{self._next}"
            self._next += 1
            self._by_chunk[hit.chunk_id] = label
            self.evidence[label] = Evidence(label=label, hit=hit)
            return label, True

    def register_row(self, base: str, row: TableRowView, header: tuple[str, ...]) -> str:
        """Label one table row `<base>.r<n>` as citable evidence."""
        label = f"{base}.r{row.row_number}"
        with self._lock:
            if label not in self.evidence:
                self.evidence[label] = Evidence(label=label, hit=self.evidence[base].hit, row=row, header=header)
        return label

    def record_calculation(self, calculation: Calculation) -> None:
        with self._lock:
            self.calculations.append(calculation)

    def record_step(self, kind: StepKind, label: str, detail: str = "") -> None:
        """Log one step of answering (a tool call, the checks, a repair) for display and the result."""
        with self._lock:
            self.steps.append(AnswerStep(kind=kind, label=label, detail=detail))

    def retrieved(self) -> tuple[Evidence, ...]:
        """Every labelled excerpt and row, in the order they were shown."""
        with self._lock:
            return tuple(self.evidence.values())
