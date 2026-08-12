
from __future__ import annotations

import heapq
import json
import re
import sqlite3
import subprocess
import time
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal, Protocol, Sequence

from .formal import FormalArtifact, FormalizationSpec
from .formal_agents import FormalContext, _CommandFormalActor
from .formal_project import LeanProjectEnvironment
from .formal_retrieval import PremiseSelector
from .reproducibility import stable_json_hash


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True, frozen=True)
class LeanGoal:
    target: str
    local_context: tuple[str, ...] = ()
    case_name: str = ""

    def validate(self) -> None:
        if not self.target.strip():
            raise ValueError("Lean goal target must not be empty")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"target": self.target, "local_context": list(self.local_context), "case_name": self.case_name}


@dataclass(slots=True)
class LeanProofState:
    formal_spec_id: str
    goals: list[LeanGoal]
    tactic_history: list[str] = field(default_factory=list)
    depth: int = 0
    parent_state_id: str | None = None
    state_id: str = field(default_factory=lambda: f"lean-state-{uuid.uuid4().hex}")
    fingerprint: str = ""
    created_at: str = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        if not self.fingerprint:
            self.fingerprint = self.semantic_fingerprint()

    def semantic_fingerprint(self) -> str:
        return stable_json_hash({"goals": [goal.to_dict() for goal in self.goals]})

    def validate(self) -> None:
        if not self.formal_spec_id:
            raise ValueError("Lean proof state requires formal_spec_id")
        if self.depth < 0 or self.depth != len(self.tactic_history):
            raise ValueError("Lean proof state depth must match tactic history")
        for goal in self.goals:
            goal.validate()
        if self.fingerprint != self.semantic_fingerprint():
            raise ValueError("Lean proof state fingerprint mismatch")

    @property
    def completed(self) -> bool:
        return not self.goals

    def render(self) -> str:
        chunks: list[str] = []
        for index, goal in enumerate(self.goals, 1):
            label = f"goal {index}" + (f" ({goal.case_name})" if goal.case_name else "")
            context = "\n".join(goal.local_context)
            chunks.append(f"{label}:\n{context}\n⊢ {goal.target}".strip())
        return "\n\n".join(chunks)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "formal_spec_id": self.formal_spec_id,
            "goals": [goal.to_dict() for goal in self.goals],
            "tactic_history": list(self.tactic_history),
            "depth": self.depth,
            "parent_state_id": self.parent_state_id,
            "state_id": self.state_id,
            "fingerprint": self.fingerprint,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LeanProofState":
        goals = [
            LeanGoal(str(item["target"]), tuple(str(value) for value in item.get("local_context", [])), str(item.get("case_name", "")))
            for item in raw.get("goals", [])
        ]
        state = cls(
            formal_spec_id=str(raw["formal_spec_id"]), goals=goals,
            tactic_history=[str(item) for item in raw.get("tactic_history", [])], depth=int(raw.get("depth", 0)),
            parent_state_id=raw.get("parent_state_id"), state_id=str(raw["state_id"]),
            fingerprint=str(raw.get("fingerprint", "")), created_at=str(raw.get("created_at", _utcnow())),
        )
        state.validate()
        return state


@dataclass(slots=True, frozen=True)
class TacticCandidate:
    tactic: str
    confidence: float = 0.0
    premise_names: tuple[str, ...] = ()
    rationale: str = ""

    _FORBIDDEN = re.compile(
        r"\b(sorry|admit|axiom|unsafe|extern|opaque|run_tac|elab|macro|syntax|theorem|lemma|def|namespace|end)\b|#(?:eval|run)",
        re.IGNORECASE,
    )

    def validate(self) -> None:
        value = self.tactic.strip()
        if not value or len(value) > 4000:
            raise ValueError("tactic candidate must contain 1..4000 characters")
        if "\n" in value or "\r" in value:
            raise ValueError("tactic candidate must be one Lean tactic line")
        if self._FORBIDDEN.search(value):
            raise ValueError("tactic candidate crosses the frozen proof-body boundary")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("tactic confidence must be between zero and one")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"tactic": self.tactic.strip(), "confidence": self.confidence,
                "premise_names": list(self.premise_names), "rationale": self.rationale}


@dataclass(slots=True, frozen=True)
class InteractiveProofSearchBudget:
    max_states: int = 500
    max_depth: int = 24
    max_tactics_per_state: int = 8
    max_lean_calls: int = 2000
    max_model_calls: int = 100
    max_retrieval_calls: int = 500
    max_wall_seconds: float = 900.0
    beam_width: int = 64

    def validate(self) -> None:
        values = (
            self.max_states, self.max_depth, self.max_tactics_per_state, self.max_lean_calls,
            self.max_model_calls, self.max_retrieval_calls, self.beam_width,
        )
        if min(values) < 1 or self.max_wall_seconds <= 0:
            raise ValueError("interactive proof-search budgets must be positive")


@dataclass(slots=True)
class TacticTransition:
    status: Literal["succeeded", "failed", "environment_error", "protocol_error"]
    state: LeanProofState | None = None
    diagnostics: str = ""
    elapsed_seconds: float = 0.0


@dataclass(slots=True)
class ProofSearchSummary:
    run_id: str
    formal_spec_id: str
    status: Literal["active", "formalized", "search_exhausted", "environment_error", "invalid"]
    states_created: int = 0
    states_expanded: int = 0
    duplicate_states: int = 0
    tactic_failures: int = 0
    lean_calls: int = 0
    model_calls: int = 0
    retrieval_calls: int = 0
    frontier_size: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProofSearchExhausted(RuntimeError):
    def __init__(self, summary: ProofSearchSummary) -> None:
        super().__init__(summary.reason or "interactive Lean proof search exhausted its frontier or budget")
        self.summary = summary


class ProofSearchEnvironmentError(RuntimeError):
    def __init__(self, summary: ProofSearchSummary) -> None:
        super().__init__(summary.reason or "interactive Lean worker environment failed")
        self.summary = summary


class LeanWorkerEnvironmentError(RuntimeError):
    pass


class TacticGenerator(Protocol):
    @property
    def name(self) -> str: ...

    def generate(
        self, context: FormalContext, spec: FormalizationSpec, state: LeanProofState,
        retrieved_premises: list[dict[str, Any]], limit: int,
    ) -> list[TacticCandidate]: ...


class LeanInteractionWorker(Protocol):
    @property
    def name(self) -> str: ...

    def session(self, workspace: str | Path) -> Any: ...

    def initial_state(self, spec: FormalizationSpec) -> TacticTransition: ...

    def apply_tactic(self, spec: FormalizationSpec, state: LeanProofState, candidate: TacticCandidate) -> TacticTransition: ...


class CommandTacticGenerator(_CommandFormalActor):
    def __init__(self, command: str | Sequence[str], timeout_seconds: float = 60.0) -> None:
        super().__init__(command, timeout_seconds, role="tactic-generator")

    def generate(
        self, context: FormalContext, spec: FormalizationSpec, state: LeanProofState,
        retrieved_premises: list[dict[str, Any]], limit: int,
    ) -> list[TacticCandidate]:
        raw = self._invoke({
            "schema_version": 1,
            "action": "propose_lean_tactics",
            "context": context.to_dict(),
            "formal_spec": spec.to_dict(),
            "proof_state": state.to_dict(),
            "retrieved_premises": retrieved_premises,
            "max_candidates": limit,
            "response_contract": {"candidates": [{"tactic": "one Lean tactic line", "confidence": 0.0,
                                                      "premise_names": [], "rationale": ""}]},
            "integrity_policy": (
                "Propose one-line tactic commands only. The theorem signature, imports, preamble, project and toolchain are frozen. "
                "Never emit sorry/admit, declarations, metaprogramming, unsafe code or commands."
            ),
        })
        if not isinstance(raw, dict) or not isinstance(raw.get("candidates"), list):
            raise RuntimeError("tactic generator response must contain a candidates list")
        output: list[TacticCandidate] = []
        seen: set[str] = set()
        for item in raw["candidates"]:
            if len(output) >= limit:
                break
            if not isinstance(item, dict):
                raise RuntimeError("each tactic candidate must be a JSON object")
            candidate = TacticCandidate(
                tactic=str(item.get("tactic", "")), confidence=float(item.get("confidence", 0.0)),
                premise_names=tuple(str(value) for value in item.get("premise_names", [])),
                rationale=str(item.get("rationale", "")),
            )
            candidate.validate()
            normalized = candidate.tactic.strip()
            if normalized not in seen:
                output.append(candidate)
                seen.add(normalized)
        return output


class FrozenLeanProofWorker:
    """Runs every cumulative tactic prefix inside one isolated frozen Lake project.

    Worker-authored `all_goals sorry` is instrumentation used only to expose open
    goals. It can never enter the reconstructed artifact or the certification gate.
    """

    GENERATED_MODULE = "ResearchEvolveSearchProbe"
    _DIAGNOSTIC_MARKER_RE = re.compile(
        r"(?m)^[^\n]*?\.lean:\d+:\d+:\s+(?P<severity>info|warning|error):\s*"
    )
    _SORRY_WARNING_RE = re.compile(r"declaration uses ['‘]?sorry", re.IGNORECASE)

    def __init__(self, environment: LeanProjectEnvironment, *, timeout_seconds: float = 60.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Lean interaction timeout must be positive")
        self.environment = environment
        self.timeout_seconds = float(timeout_seconds)
        self._project: Path | None = None

    @property
    def name(self) -> str:
        return f"lean-interaction-worker-v1:{self.environment.fingerprint[:16]}"

    @contextmanager
    def session(self, workspace: str | Path) -> Iterator[None]:
        if self._project is not None:
            raise RuntimeError("Lean interaction worker session is already active")
        with self.environment.materialize(Path(workspace) / "proof_search_projects") as project:
            self._project = project
            try:
                build = subprocess.run(
                    [*self.environment.lake_command, "build", *self.environment.build_targets], cwd=project,
                    text=True, capture_output=True, timeout=self.timeout_seconds, check=False,
                )
                if build.returncode != 0:
                    raise LeanWorkerEnvironmentError(f"frozen Lean project build failed: {build.stderr or build.stdout}")
                yield
            finally:
                self._project = None

    @staticmethod
    def _source(spec: FormalizationSpec, tactics: Sequence[str]) -> str:
        chunks = [*(f"import {module}" for module in spec.imports), ""]
        if spec.preamble.strip():
            chunks.extend([spec.preamble.strip(), ""])
        chunks.append(f"{spec.theorem_signature.strip()} := by")
        chunks.extend(f"  {tactic.strip()}" for tactic in tactics)
        chunks.extend(["  all_goals trace_state", "  all_goals sorry", ""])
        return "\n".join(chunks)

    @classmethod
    def _parse_goals(cls, output: str) -> list[LeanGoal]:
        goals: list[LeanGoal] = []
        markers = list(cls._DIAGNOSTIC_MARKER_RE.finditer(output))
        for index, match in enumerate(markers):
            if match.group("severity") != "info":
                continue
            end = markers[index + 1].start() if index + 1 < len(markers) else len(output)
            block = output[match.end():end].strip()
            if "⊢" not in block:
                continue
            before, target = block.split("⊢", 1)
            lines = [line.strip() for line in before.splitlines() if line.strip()]
            case_name = ""
            if lines and lines[0].startswith("case "):
                case_name = lines.pop(0)[5:].strip()
            goals.append(LeanGoal(target.strip(), tuple(lines), case_name))
        if goals:
            return goals

        # Lean versions and message renderers do not always attach a file/info
        # prefix to `trace_state`. Fall back to the actual turnstile lines while
        # excluding diagnostic headers and sorry warnings.
        raw_lines = output.splitlines()
        for index, raw in enumerate(raw_lines):
            if "⊢" not in raw:
                continue
            before_target, target = raw.split("⊢", 1)
            context: list[str] = []
            inline = before_target.strip()
            if "info:" in inline:
                inline = inline.split("info:", 1)[1].strip()
            if inline:
                context.append(inline)
            cursor = index - 1
            while cursor >= 0:
                line = raw_lines[cursor].strip()
                if not line or "⊢" in line or re.search(r"\b(?:warning|error):", line):
                    break
                if ".lean:" in line and "info:" not in line:
                    break
                if "info:" in line:
                    line = line.split("info:", 1)[1].strip()
                    if line:
                        context.append(line)
                    break
                context.append(line)
                cursor -= 1
            context.reverse()
            case_name = ""
            if context and context[0].startswith("case "):
                case_name = context.pop(0)[5:].strip()
            goals.append(LeanGoal(target.strip(), tuple(context), case_name))
        return goals

    @classmethod
    def _probe_protocol_error(cls, output: str, tactics: Sequence[str], goals: Sequence[LeanGoal]) -> bool:
        return bool(tactics) and not goals and cls._SORRY_WARNING_RE.search(output) is not None

    def _probe(self, spec: FormalizationSpec, tactics: Sequence[str], parent: LeanProofState | None) -> TacticTransition:
        if self._project is None:
            return TacticTransition("environment_error", diagnostics="Lean worker requires an active frozen-project session")
        started = time.monotonic()
        source = self._source(spec, tactics)
        generated = self._project / f"{self.GENERATED_MODULE}.lean"
        generated.write_text(source, encoding="utf-8")
        command = [*self.environment.lake_command, "env", "lean", generated.name]
        try:
            completed = subprocess.run(
                command, cwd=self._project, text=True, capture_output=True,
                timeout=self.timeout_seconds, check=False,
            )
        except FileNotFoundError as exc:
            return TacticTransition("environment_error", diagnostics=str(exc), elapsed_seconds=time.monotonic() - started)
        except subprocess.TimeoutExpired as exc:
            return TacticTransition("environment_error", diagnostics=f"Lean interaction timeout: {exc}", elapsed_seconds=time.monotonic() - started)
        output = f"{completed.stdout}\n{completed.stderr}"
        if completed.returncode != 0:
            return TacticTransition("failed", diagnostics=output.strip(), elapsed_seconds=time.monotonic() - started)
        goals = self._parse_goals(output)
        if self._probe_protocol_error(output, tactics, goals):
            return TacticTransition(
                "protocol_error", diagnostics="Lean probe used sorry but emitted no parseable proof state; refusing false completion",
                elapsed_seconds=time.monotonic() - started,
            )
        history = list(tactics)
        state = LeanProofState(
            formal_spec_id=spec.id, goals=goals, tactic_history=history, depth=len(history),
            parent_state_id=parent.state_id if parent is not None else None,
        )
        state.validate()
        return TacticTransition("succeeded", state=state, diagnostics=output.strip(), elapsed_seconds=time.monotonic() - started)

    def initial_state(self, spec: FormalizationSpec) -> TacticTransition:
        return self._probe(spec, [], None)

    def apply_tactic(self, spec: FormalizationSpec, state: LeanProofState, candidate: TacticCandidate) -> TacticTransition:
        state.validate()
        candidate.validate()
        return self._probe(spec, [*state.tactic_history, candidate.tactic.strip()], state)


class ProofSearchMemory:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS search_runs (
                run_id TEXT PRIMARY KEY, formal_spec_id TEXT NOT NULL, config_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_search_run_resume ON search_runs(formal_spec_id, config_fingerprint, status);
            CREATE TABLE IF NOT EXISTS proof_states (
                state_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                status TEXT NOT NULL, priority REAL NOT NULL, ordinal INTEGER NOT NULL, payload TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_search_state_fingerprint ON proof_states(run_id, fingerprint);
            CREATE TABLE IF NOT EXISTS tactic_attempts (
                run_id TEXT NOT NULL, state_fingerprint TEXT NOT NULL, tactic TEXT NOT NULL,
                status TEXT NOT NULL, diagnostics TEXT NOT NULL, child_fingerprint TEXT,
                PRIMARY KEY (run_id, state_fingerprint, tactic)
            );
            CREATE TABLE IF NOT EXISTS state_retrievals (
                run_id TEXT NOT NULL, state_id TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (run_id, state_id)
            );
            """
        )
        self.conn.commit()

    def start(self, spec: FormalizationSpec, config_fingerprint: str, resume: bool) -> tuple[ProofSearchSummary, bool]:
        if resume:
            row = self.conn.execute(
                "SELECT summary FROM search_runs WHERE config_fingerprint=? AND status='active' ORDER BY updated_at DESC LIMIT 1",
                (config_fingerprint,),
            ).fetchone()
            if row is not None:
                summary = ProofSearchSummary(**json.loads(row["summary"]))
                summary.formal_spec_id = spec.id
                return summary, True
        summary = ProofSearchSummary(f"proof-search-{uuid.uuid4().hex}", spec.id, "active")
        now = _utcnow()
        self.conn.execute("INSERT INTO search_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (summary.run_id, spec.id, config_fingerprint, "active", json.dumps(summary.to_dict()), now, now))
        self.conn.commit()
        return summary, False

    def save_summary(self, summary: ProofSearchSummary) -> None:
        self.conn.execute("UPDATE search_runs SET status=?, summary=?, updated_at=? WHERE run_id=?",
                          (summary.status, json.dumps(summary.to_dict(), sort_keys=True), _utcnow(), summary.run_id))
        self.conn.commit()

    def add_state(self, run_id: str, state: LeanProofState, status: str, priority: float, ordinal: int) -> bool:
        try:
            self.conn.execute("INSERT INTO proof_states VALUES (?, ?, ?, ?, ?, ?, ?)",
                              (state.state_id, run_id, state.fingerprint, status, priority, ordinal,
                               json.dumps(state.to_dict(), sort_keys=True)))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def set_state_status(self, state_id: str, status: str) -> None:
        self.conn.execute("UPDATE proof_states SET status=? WHERE state_id=?", (status, state_id))
        self.conn.commit()

    def frontier(self, run_id: str) -> list[tuple[float, int, LeanProofState]]:
        rows = self.conn.execute(
            "SELECT priority, ordinal, payload FROM proof_states WHERE run_id=? AND status='frontier' ORDER BY priority, ordinal",
            (run_id,),
        ).fetchall()
        return [(float(row["priority"]), int(row["ordinal"]), LeanProofState.from_dict(json.loads(row["payload"]))) for row in rows]

    def attempted(self, run_id: str, state_fingerprint: str, tactic: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM tactic_attempts WHERE run_id=? AND state_fingerprint=? AND tactic=?",
            (run_id, state_fingerprint, tactic),
        ).fetchone() is not None

    def record_attempt(self, run_id: str, state: LeanProofState, candidate: TacticCandidate, transition: TacticTransition) -> None:
        child = transition.state.fingerprint if transition.state else None
        self.conn.execute("INSERT OR REPLACE INTO tactic_attempts VALUES (?, ?, ?, ?, ?, ?)",
                          (run_id, state.fingerprint, candidate.tactic.strip(), transition.status,
                           transition.diagnostics[-16000:], child))
        self.conn.commit()

    def record_retrieval(self, run_id: str, state_id: str, payload: dict[str, Any]) -> None:
        self.conn.execute("INSERT OR REPLACE INTO state_retrievals VALUES (?, ?, ?)",
                          (run_id, state_id, json.dumps(payload, sort_keys=True)))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


class ProofSearchFormalizer:
    """Best-first, state-deduplicated interactive Lean formalizer."""

    def __init__(
        self, workspace: str | Path, worker: LeanInteractionWorker, generator: TacticGenerator,
        *, premise_selector: PremiseSelector | None = None,
        budget: InteractiveProofSearchBudget | None = None, resume: bool = False,
    ) -> None:
        self.workspace = Path(workspace)
        self.worker = worker
        self.generator = generator
        self.premise_selector = premise_selector
        self.budget = budget or InteractiveProofSearchBudget()
        self.budget.validate()
        self.resume = bool(resume)
        self.memory = ProofSearchMemory(self.workspace / "formal_search.sqlite3")

    @property
    def name(self) -> str:
        selector = self.premise_selector.name if self.premise_selector else "none"
        fingerprint = stable_json_hash({"worker": self.worker.name, "generator": self.generator.name,
                                        "selector": selector, "budget": asdict(self.budget)})[:16]
        return f"proof-search-formalizer-v1:{fingerprint}"

    def _config_fingerprint(self, spec: FormalizationSpec) -> str:
        target = spec.to_dict()
        target.pop("id", None)
        target.pop("created_at", None)
        return stable_json_hash({"formal_target": target, "formalizer": self.name})

    @staticmethod
    def _priority(parent: LeanProofState, child: LeanProofState, candidate: TacticCandidate) -> float:
        progress = len(parent.goals) - len(child.goals)
        return round(child.depth * 0.05 - candidate.confidence - progress * 2.0 + len(child.goals) * 0.1, 8)

    def _finish(self, summary: ProofSearchSummary, status: str, reason: str, frontier_size: int) -> None:
        summary.status = status  # type: ignore[assignment]
        summary.reason = reason
        summary.frontier_size = frontier_size
        self.memory.save_summary(summary)

    def formalize(self, context: FormalContext, spec: FormalizationSpec) -> FormalArtifact:
        spec.validate()
        summary, resumed = self.memory.start(spec, self._config_fingerprint(spec), self.resume)
        started = time.monotonic()
        frontier = self.memory.frontier(summary.run_id) if resumed else []
        for _, _, state in frontier:
            state.formal_spec_id = spec.id
            state.validate()
        heapq.heapify(frontier)
        ordinal = max((item[1] for item in frontier), default=0)
        session = self.worker.session(self.workspace) if hasattr(self.worker, "session") else nullcontext()
        try:
            with session:
                if not resumed:
                    initial = self.worker.initial_state(spec)
                    summary.lean_calls += 1
                    if initial.status == "environment_error":
                        self._finish(summary, "environment_error", initial.diagnostics, 0)
                        raise ProofSearchEnvironmentError(summary)
                    if initial.status != "succeeded" or initial.state is None or initial.state.completed:
                        detail = initial.diagnostics[-4000:]
                        self._finish(summary, "invalid", f"worker did not return a non-closed initial proof state: {detail}", 0)
                        raise RuntimeError(summary.reason)
                    summary.states_created = 1
                    self.memory.add_state(summary.run_id, initial.state, "frontier", 0.0, ordinal)
                    heapq.heappush(frontier, (0.0, ordinal, initial.state))
                    self.memory.save_summary(summary)

                while frontier:
                    elapsed = time.monotonic() - started
                    budget_reason = ""
                    if summary.states_created >= self.budget.max_states:
                        budget_reason = "max_states exhausted"
                    elif summary.lean_calls >= self.budget.max_lean_calls:
                        budget_reason = "max_lean_calls exhausted"
                    elif summary.model_calls >= self.budget.max_model_calls:
                        budget_reason = "max_model_calls exhausted"
                    elif self.premise_selector is not None and summary.retrieval_calls >= self.budget.max_retrieval_calls:
                        budget_reason = "max_retrieval_calls exhausted"
                    elif elapsed >= self.budget.max_wall_seconds:
                        budget_reason = "max_wall_seconds exhausted"
                    if budget_reason:
                        self._finish(summary, "search_exhausted", budget_reason, len(frontier))
                        raise ProofSearchExhausted(summary)

                    _, _, state = heapq.heappop(frontier)
                    self.memory.set_state_status(state.state_id, "expanded")
                    summary.states_expanded += 1
                    if state.depth >= self.budget.max_depth:
                        continue

                    premises: list[dict[str, Any]] = []
                    if self.premise_selector is not None and summary.retrieval_calls < self.budget.max_retrieval_calls:
                        selection = self.premise_selector.select(
                            formal_spec_id=spec.id,
                            query=f"{spec.conjecture_statement}\n{state.render()}",
                            goal_state=state.render(), allowed_modules=spec.imports, round=state.depth,
                        )
                        premises = [item.to_dict() for item in selection.selected]
                        summary.retrieval_calls += 1
                        self.memory.record_retrieval(summary.run_id, state.state_id, selection.to_dict())

                    candidates = self.generator.generate(
                        context, spec, state, premises, self.budget.max_tactics_per_state,
                    )
                    summary.model_calls += 1
                    for candidate in candidates[: self.budget.max_tactics_per_state]:
                        if summary.lean_calls >= self.budget.max_lean_calls or summary.states_created >= self.budget.max_states:
                            break
                        if self.memory.attempted(summary.run_id, state.fingerprint, candidate.tactic.strip()):
                            continue
                        transition = self.worker.apply_tactic(spec, state, candidate)
                        summary.lean_calls += 1
                        self.memory.record_attempt(summary.run_id, state, candidate, transition)
                        if transition.status == "environment_error":
                            self._finish(summary, "environment_error", transition.diagnostics, len(frontier))
                            raise ProofSearchEnvironmentError(summary)
                        if transition.status != "succeeded" or transition.state is None:
                            summary.tactic_failures += 1
                            continue
                        child = transition.state
                        if child.completed:
                            ordinal += 1
                            self.memory.add_state(summary.run_id, child, "closed", -1000.0, ordinal)
                            proof_term = "by\n" + "\n".join(f"  {tactic}" for tactic in child.tactic_history)
                            summary.states_created += 1
                            self._finish(summary, "formalized", "closed proof state; pending kernel certification", len(frontier))
                            return FormalArtifact(
                                formal_spec_id=spec.id, proof_term=proof_term,
                                metadata={"proof_search": summary.to_dict(), "terminal_state": child.to_dict()},
                            )
                        priority = self._priority(state, child, candidate)
                        ordinal += 1
                        if not self.memory.add_state(summary.run_id, child, "frontier", priority, ordinal):
                            summary.duplicate_states += 1
                            continue
                        summary.states_created += 1
                        heapq.heappush(frontier, (priority, ordinal, child))
                        if len(frontier) > self.budget.beam_width:
                            worst = max(frontier, key=lambda item: (item[0], item[1]))
                            frontier.remove(worst)
                            heapq.heapify(frontier)
                            self.memory.set_state_status(worst[2].state_id, "pruned")
                    summary.frontier_size = len(frontier)
                    self.memory.save_summary(summary)

                self._finish(summary, "search_exhausted", "proof-search frontier exhausted", 0)
                raise ProofSearchExhausted(summary)
        except LeanWorkerEnvironmentError as exc:
            self._finish(summary, "environment_error", str(exc), len(frontier))
            raise ProofSearchEnvironmentError(summary) from exc
        except (ProofSearchExhausted, ProofSearchEnvironmentError):
            raise
        except Exception as exc:
            self._finish(summary, "invalid", str(exc), len(frontier))
            raise

    def close(self) -> None:
        self.memory.close()

