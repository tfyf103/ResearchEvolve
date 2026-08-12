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
            chunks.append(f"{label}:\n{context}\nâŠ¢ {goal.target}".strip())
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
            if "âŠ¢" not in block:
                continue
            before, target = block.split("âŠ¢", 1)
            lines = [line.strip() for line in before.splitlines() if line.strip()]
            case_name = ""
            if lines and lines[0].startswith("case "):
                case_name = lines.pop(0)[5:].strip()
            goals.append(LeanGoal(target.strip(), tuple(lines), case_name))
        return goals

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
        if not goals and re.search(r"declaration uses ['â€˜]?sorry", output, re.IGNORECASE):
            return TacticTransition(
                "protocol_error", diagnostics="Lean probe used sorry but emitted no parseable proof state; refusing false completion",
                elapsed_seconds=time.monotonic() - started,
            )
        history = list(tactics)
        state = LeanProofState(
            formal_spec_id=spec.id, goals=goals, tactic_history=history, depth=len(history),
            parent_state_id=parent.state_id if pòÚ$z{-®éÜj×æöæRÀ¢¢7FFRçfÆ–FFR‚¢&WGW&âF7F–5G&ç6—F–öâ‚'7V66VVFVB"Â7FFS×7FFRÂF–væ÷7F–73Ö÷WGWBç7G&—‚’ÂVÆ6VE÷6V6öæG3×F–ÖRæÖöæ÷Föæ–2‚’Ò7F'FVB ¢FVb–æ—F–Å÷7FFR‡6VÆbÂ7V3¢f÷&ÖÆ—¦F–öå7V2’ÓâF7F–5G&ç6—F–öã ¢&WGW&â6VÆbå÷&ö&R‡7V2ÂµÒÂæöæR ¢FVbÇ•÷F7F–2‡6VÆbÂ7V3¢f÷&ÖÆ—¦F–öå7V2Â7FFS¢ÆVå&ööe7FFRÂ6æF–FFS¢F7F–46æF–FFR’ÓâF7F–5G&ç6—F–öã ¢7FFRçfÆ–FFR‚¢6æF–FFRçfÆ–FFR‚¢&WGW&â6VÆbå÷&ö&R‡7V2Â²§7FFRçF7F–5ö†—7F÷'’Â6æF–FFRçF7F–2ç7G&—‚•ÒÂ7FFR  ¦6Æ72&ööe6V&6„ÖVÖ÷'“ ¢FVbõö–æ—Eõò‡6VÆbÂFƒ¢7G"ÂF‚’ÓâæöæS ¢6VÆbçF‚ÒF‚‡F‚¢6VÆbçF‚ç&VçBæÖ¶F—"‡&VçG3ÕG'VRÂW†—7Eöö³ÕG'VR¢6VÆbæ6öæâÒ7Æ—FS2æ6öææV7B‡6VÆbçF‚¢6VÆbæ6öæâç&÷uöf7F÷'’Ò7Æ—FS2å&÷p¢6VÆbæ6öæâæW†V7WFW67&—B€¢"" ¢5$TDRD$ÄR”bäõBU„•5E26V&6…÷'Vç2€¢'Våö–BDU…B$”Ô%’´U’Âf÷&ÖÅ÷7V5ö–BDU…BäõBåTÄÂÂ6öæf–uöf–ævW'&–çBDU…BäõBåTÄÂÀ¢7FGW2DU…BäõBåTÄÂÂ7VÖÖ'’DU…BäõBåTÄÂÂ7&VFVEöBDU…BäõBåTÄÂÂWFFVEöBDU…BäõBåTÄÀ¢“°¢5$TDR”äDU‚”bäõBU„•5E2–G…÷6V&6…÷'Vå÷&W7VÖRôâ6V&6…÷'Vç2†f÷&ÖÅ÷7V5ö–BÂ6öæf–uöf–ævW'&–çBÂ7FGW2“°¢5$TDRD$ÄR”bäõBU„•5E2&ööe÷7FFW2€¢7FFUö–BDU…B$”Ô%’´U’Â'Våö–BDU…BäõBåTÄÂÂf–ævW'&–çBDU…BäõBåTÄÂÀ¢7FGW2DU…BäõBåTÄÂÂ&–÷&—G’$TÂäõBåTÄÂÂ÷&F–æÂ”åDTtU"äõBåTÄÂÂ–ÆöBDU…BäõBåTÄÀ¢“°¢5$TDRTä•TR”äDU‚”bäõBU„•5E2–G…÷6V&6…÷7FFUöf–ævW'&–çBôâ&ööe÷7FFW2‡'Våö–BÂf–ævW'&–çB“°¢5$TDRD$ÄR”bäõBU„•5E2F7F–5öGFV×G2€¢'Våö–BDU…BäõBåTÄÂÂ7FFUöf–ævW'&–çBDU…BäõBåTÄÂÂF7F–2DU…BäõBåTÄÂÀ¢7FGW2DU…BäõBåTÄÂÂF–væ÷7F–72DU…BäõBåTÄÂÂ6†–ÆEöf–ævW'&–çBDU…BÀ¢$”Ô%’´U’‡'Våö–BÂ7FFUöf–ævW'&–çBÂF7F–2¢“°¢5$TDRD$ÄR”bäõBU„•5E27FFU÷&WG&–WfÇ2€¢'Våö–BDU…BäõBåTÄÂÂ7FFUö–BDU…BäõBåTÄÂÂ–ÆöBDU…BäõBåTÄÂÀ¢$”Ô%’´U’‡'Våö–BÂ7FFUö–B¢“°¢"" ¢¢6VÆbæ6öæâæ6öÖÖ—B‚ ¢FVb7F'B‡6VÆbÂ7V3¢f÷&ÖÆ—¦F–öå7V2Â6öæf–uöf–ævW'&–çC¢7G"Â&W7VÖS¢&ööÂ’ÓâGWÆUµ&ööe6V&6…7VÖÖ'’Â&ööÅÓ ¢–b&W7VÖS ¢&÷rÒ6VÆbæ6öæâæW†V7WFR€¢%4TÄT5B7VÖÖ'’e$ôÒ6V&6…÷'Vç2t„U$R6öæf–uöf–ævW'&–çCÓòäB7FGW3Òv7F—fRrõ$DU"%’WFFVEöBDU42Ä”Ô•B"À¢†6öæf–uöf–ævW'&–çBÂ’À¢’æfWF6†öæR‚¢–b&÷r—2æ÷BæöæS ¢7VÖÖ'’Ò&ööe6V&6…7VÖÖ'’‚¢¦§6öâæÆöG2‡&÷u²'7VÖÖ'’%Ò’¢7VÖÖ'’æf÷&ÖÅ÷7V5ö–BÒ7V2æ–@¢&WGW&â7VÖÖ'’ÂG'VP¢7VÖÖ'’Ò&ööe6V&6…7VÖÖ'’†b'&ööb×6V&6‚×·WV–BçWV–CB‚’æ†W‡Ò"Â7V2æ–BÂ&7F—fR"¢æ÷rÒ÷WF6æ÷r‚¢6VÆbæ6öæâæW†V7WFR‚$”å4U%B”åDò6V&6…÷'Vç2dÅTU2ƒòÂòÂòÂòÂòÂòÂò’"À¢‡7VÖÖ'’ç'Våö–BÂ7V2æ–BÂ6öæf–uöf–ævW'&–çBÂ&7F—fR"Â§6öâæGV×2‡7VÖÖ'’çFõöF–7B‚’’Âæ÷rÂæ÷r’¢6VÆbæ6öæâæ6öÖÖ—B‚¢&WGW&â7VÖÖ'’ÂfÇ6P ¢FVb6fU÷7VÖÖ'’‡6VÆbÂ7VÖÖ'“¢&ööe6V&6…7VÖÖ'’’ÓâæöæS ¢6VÆbæ6öæâæW†V7WFR‚%UDDR6V&6…÷'Vç24UB7FGW3ÓòÂ7VÖÖ'“ÓòÂWFFVEöCÓòt„U$R'Våö–CÓò"À¢‡7VÖÖ'’ç7FGW2Â§6öâæGV×2‡7VÖÖ'’çFõöF–7B‚’Â6÷'Eö¶W—3ÕG'VR’Â÷WF6æ÷r‚’Â7VÖÖ'’ç'Våö–B’¢6VÆbæ6öæâæ6öÖÖ—B‚ ¢FVbFE÷7FFR‡6VÆbÂ'Våö–C¢7G"Â7FFS¢ÆVå&ööe7FFRÂ7FGW3¢7G"Â&–÷&—G“¢fÆöBÂ÷&F–æÃ¢–çB’Óâ&ööÃ ¢G'“ ¢6VÆbæ6öæâæW†V7WFR‚$”å4U%B”åDò&ööe÷7FFW2dÅTU2ƒòÂòÂòÂòÂòÂòÂò’"À¢‡7FFRç7FFUö–BÂ'Våö–BÂ7FFRæf–ævW'&–çBÂ7FGW2Â&–÷&—G’Â÷&F–æÂÀ¢§6öâæGV×2‡7FFRçFõöF–7B‚’Â6÷'Eö¶W—3ÕG'VR’’¢6VÆbæ6öæâæ6öÖÖ—B‚¢&WGW&âG'VP¢W†6WB7Æ—FS2ä–çFVw&—G”W'&÷# ¢&WGW&âfÇ6P ¢FVb6WE÷7FFU÷7FGW2‡6VÆbÂ7FFUö–C¢7G"Â7FGW3¢7G"’ÓâæöæS ¢6VÆbæ6öæâæW†V7WFR‚%UDDR&ööe÷7FFW24UB7FGW3Óòt„U$R7FFUö–CÓò"Â‡7FGW2Â7FFUö–B’¢6VÆbæ6öæâæ6öÖÖ—B‚ ¢FVbg&öçF–W"‡6VÆbÂ'Våö–C¢7G"’ÓâÆ—7E·GWÆU¶fÆöBÂ–çBÂÆVå&ööe7FFUÕÓ ¢&÷w2Ò6VÆbæ6öæâæW†V7WFR€¢%4TÄT5B&–÷&—G’Â÷&F–æÂÂ–ÆöBe$ôÒ&ööe÷7FFW2t„U$R'Våö–CÓòäB7FGW3Òvg&öçF–W"rõ$DU"%’&–÷&—G’Â÷&F–æÂ"À¢‡'Våö–BÂ’À¢’æfWF6†ÆÂ‚¢&WGW&â²†fÆöB‡&÷u²'&–÷&—G’%Ò’Â–çB‡&÷u²&÷&F–æÂ%Ò’ÂÆVå&ööe7FFRæg&öÕöF–7B†§6öâæÆöG2‡&÷u²'–ÆöB%Ò’’’f÷"&÷r–â&÷w5Ğ ¢FVbGFV×FVB‡6VÆbÂ'Våö–C¢7G"Â7FFUöf–ævW'&–çC¢7G"ÂF7F–3¢7G"’Óâ&ööÃ ¢&WGW&â6VÆbæ6öæâæW†V7WFR€¢%4TÄT5Be$ôÒF7F–5öGFV×G2t„U$R'Våö–CÓòäB7FFUöf–ævW'&–çCÓòäBF7F–3Óò"À¢‡'Våö–BÂ7FFUöf–ævW'&–çBÂF7F–2’À¢’æfWF6†öæR‚’—2æ÷BæöæP ¢FVb&V6÷&EöGFV×B‡6VÆbÂ'Våö–C¢7G"Â7FFS¢ÆVå&ööe7FFRÂ6æF–FFS¢F7F–46æF–FFRÂG&ç6—F–öã¢F7F–5G&ç6—F–öâ’ÓâæöæS ¢6†–ÆBÒG&ç6—F–öâç7FFRæf–ævW'&–çB–bG&ç6—F–öâç7FFRVÇ6RæöæP¢6VÆbæ6öæâæW†V7WFR‚$”å4U%Bõ"$UÄ4R”åDòF7F–5öGFV×G2dÅTU2ƒòÂòÂòÂòÂòÂò’"À¢‡'Våö–BÂ7FFRæf–ævW'&–çBÂ6æF–FFRçF7F–2ç7G&—‚’ÂG&ç6—F–öâç7FGW2À¢G&ç6—F–öâæF–væ÷7F–75²Óc¥ÒÂ6†–ÆB’¢6VÆbæ6öæâæ6öÖÖ—B‚ ¢FVb&V6÷&E÷&WG&–WfÂ‡6VÆbÂ'Våö–C¢7G"Â7FFUö–C¢7G"Â–ÆöC¢F–7E·7G"Âç•Ò’ÓâæöæS ¢6VÆbæ6öæâæW†V7WFR‚$”å4U%Bõ"$UÄ4R”åDò7FFU÷&WG&–WfÇ2dÅTU2ƒòÂòÂò’"À¢‡'Våö–BÂ7FFUö–BÂ§6öâæGV×2‡–ÆöBÂ6÷'Eö¶W—3ÕG'VR’’¢6VÆbæ6öæâæ6öÖÖ—B‚ ¢FVb6Æ÷6R‡6VÆb’ÓâæöæS ¢6VÆbæ6öæâæ6Æ÷6R‚  ¦6Æ72&ööe6V&6„f÷&ÖÆ—¦W# ¢""$&W7BÖf—'7BÂ7FFRÖFVGWÆ–6FVB–çFW&7F—fRÆVâf÷&ÖÆ—¦W"â""  ¢FVbõö–æ—Eõò€¢6VÆbÂv÷&·76S¢7G"ÂF‚Âv÷&¶W#¢ÆVä–çFW&7F–öåv÷&¶W"ÂvVæW&F÷#¢F7F–4vVæW&F÷"À¢¢Â&VÖ—6U÷6VÆV7F÷#¢&VÖ—6U6VÆV7F÷"ÂæöæRÒæöæRÀ¢'VFvWC¢–çFW&7F—fU&ööe6V&6„'VFvWBÂæöæRÒæöæRÂ&W7VÖS¢&ööÂÒfÇ6RÀ¢’ÓâæöæS ¢6VÆbçv÷&·76RÒF‚‡v÷&·76R¢6VÆbçv÷&¶W"Òv÷&¶W ¢6VÆbævVæW&F÷"ÒvVæW&F÷ ¢6VÆbç&VÖ—6U÷6VÆV7F÷"Ò&VÖ—6U÷6VÆV7F÷ ¢6VÆbæ'VFvWBÒ'VFvWB÷"–çFW&7F—fU&ööe6V&6„'VFvWB‚¢6VÆbæ'VFvWBçfÆ–FFR‚¢6VÆbç&W7VÖRÒ&ööÂ‡&W7VÖR¢6VÆbæÖVÖ÷'’Ò&ööe6V&6„ÖVÖ÷'’‡6VÆbçv÷&·76Rò&f÷&ÖÅ÷6V&6‚ç7Æ—FS2" ¢&÷W'G¢FVbæÖR‡6VÆb’Óâ7G# ¢6VÆV7F÷"Ò6VÆbç&VÖ—6U÷6VÆV7F÷"ææÖR–b6VÆbç&VÖ—6U÷6VÆV7F÷"VÇ6R&æöæR ¢f–ævW'&–çBÒ7F&ÆUö§6öåö†6‚‡²'v÷&¶W"#¢6VÆbçv÷&¶W"ææÖRÂ&vVæW&F÷"#¢6VÆbævVæW&F÷"ææÖRÀ¢'6VÆV7F÷"#¢6VÆV7F÷"Â&'VFvWB#¢6F–7B‡6VÆbæ'VFvWB—Ò•³£eĞ¢&WGW&âb'&ööb×6V&6‚Öf÷&ÖÆ—¦W"×c§¶f–ævW'&–çGÒ  ¢FVbö6öæf–uöf–ævW'&–çB‡6VÆbÂ7V3¢f÷&ÖÆ—¦F–öå7V2’Óâ7G# ¢F&vWBÒ7V2çFõöF–7B‚¢F&vWBç÷‚&–B"ÂæöæR¢F&vWBç÷‚&7&VFVEöB"ÂæöæR¢&WGW&â7F&ÆUö§6öåö†6‚‡²&f÷&ÖÅ÷F&vWB#¢F&vWBÂ&f÷&ÖÆ—¦W"#¢6VÆbææÖWÒ ¢7FF–6ÖWF†ö@¢FVb÷&–÷&—G’‡&VçC¢ÆVå&ööe7FFRÂ6†–ÆC¢ÆVå&ööe7FFRÂ6æF–FFS¢F7F–46æF–FFR’ÓâfÆöC ¢&öw&W72ÒÆVâ‡&VçBævöÇ2’ÒÆVâ†6†–ÆBævöÇ2¢&WGW&â&÷VæB†6†–ÆBæFWF‚¢ãRÒ6æF–FFRæ6öæf–FVæ6RÒ&öw&W72¢"ã²ÆVâ†6†–ÆBævöÇ2’¢ãÂ‚ ¢FVböf–æ—6‚‡6VÆbÂ7VÖÖ'“¢&ööe6V&6…7VÖÖ'’Â7FGW3¢7G"Â&V6öã¢7G"Âg&öçF–W%÷6—¦S¢–çB’ÓâæöæS ¢7VÖÖ'’ç7FGW2Ò7FGW22G—S¢–væ÷&U¶76–væÖVçEĞ¢7VÖÖ'’ç&V6öâÒ&V6öà¢7VÖÖ'’æg&öçF–W%÷6—¦RÒg&öçF–W%÷6—¦P¢6VÆbæÖVÖ÷'’ç6fU÷7VÖÖ'’‡7VÖÖ'’ ¢FVbf÷&ÖÆ—¦R‡6VÆbÂ6öçFW‡C¢f÷&ÖÄ6öçFW‡BÂ7V3¢f÷&ÖÆ—¦F–öå7V2’Óâf÷&ÖÄ'F–f7C ¢7V2çfÆ–FFR‚¢7VÖÖ'’Â&W7VÖVBÒ6VÆbæÖVÖ÷'’ç7F'B‡7V2Â6VÆbåö6öæf–uöf–ævW'&–çB‡7V2’Â6VÆbç&W7VÖR¢7F'FVBÒF–ÖRæÖöæ÷Föæ–2‚¢g&öçF–W"Ò6VÆbæÖVÖ÷'’æg&öçF–W"‡7VÖÖ'’ç'Våö–B’–b&W7VÖVBVÇ6RµĞ¢f÷"òÂòÂ7FFR–âg&öçF–W# ¢7FFRæf÷&ÖÅ÷7V5ö–BÒ7V2æ–@¢7FFRçfÆ–FFR‚¢†Væ†V–g’†g&öçF–W"¢÷&F–æÂÒÖ‚‚†—FVÕ³Òf÷"—FVÒ–âg&öçF–W"’ÂFVfVÇCÓ¢6W76–öâÒ6VÆbçv÷&¶W"ç6W76–öâ‡6VÆbçv÷&·76R’–b†6GG"‡6VÆbçv÷&¶W"Â'6W76–öâ"’VÇ6RçVÆÆ6öçFW‡B‚¢G'“ ¢v—F‚6W76–öã ¢–bæ÷B&W7VÖVC ¢–æ—F–ÂÒ6VÆbçv÷&¶W"æ–æ—F–Å÷7FFR‡7V2¢7VÖÖ'’æÆVåö6ÆÇ2³Ò¢–b–æ—F–Âç7FGW2ÓÒ&Vçf—&öæÖVçEöW'&÷"# ¢6VÆbåöf–æ—6‚‡7VÖÖ'’Â&Vçf—&öæÖVçEöW'&÷""Â–æ—F–ÂæF–væ÷7F–72Â¢&—6R&ööe6V&6„Vçf—&öæÖVçDW'&÷"‡7VÖÖ'’¢–b–æ—F–Âç7FGW2Ò'7V66VVFVB"÷"–æ—F–Âç7FFR—2æöæR÷"–æ—F–Âç7FFRæ6ö×ÆWFVC ¢6VÆbåöf–æ—6‚‡7VÖÖ'’Â&–çfÆ–B"Â'v÷&¶W"F–Bæ÷B&WGW&âæöâÖ6Æ÷6VB–æ—F–Â&ööb7FFR"Â¢&—6R'VçF–ÖTW'&÷"‡7VÖÖ'’ç&V6öâ¢7VÖÖ'’ç7FFW5ö7&VFVBÒ¢6VÆbæÖVÖ÷'’æFE÷7FFR‡7VÖÖ'’ç'Våö–BÂ–æ—F–Âç7FFRÂ&g&öçF–W""ÂãÂ÷&F–æÂ¢†Væ†VW6‚†g&öçF–W"ÂƒãÂ÷&F–æÂÂ–æ—F–Âç7FFR’¢6VÆbæÖVÖ÷'’ç6fU÷7VÖÖ'’‡7VÖÖ'’ ¢v†–ÆRg&öçF–W# ¢VÆ6VBÒF–ÖRæÖöæ÷Föæ–2‚’Ò7F'FV@¢'VFvWE÷&V6öâÒ" ¢–b7VÖÖ'’ç7FFW5ö7&VFVBãÒ6VÆbæ'VFvWBæÖ…÷7FFW3 ¢'VFvWE÷&V6öâÒ&Ö…÷7FFW2W††W7FVB ¢VÆ–b7VÖÖ'’æÆVåö6ÆÇ2ãÒ6VÆbæ'VFvWBæÖ…öÆVåö6ÆÇ3 ¢'VFvWE÷&V6öâÒ&Ö…öÆVåö6ÆÇ2W††W7FVB ¢VÆ–b7VÖÖ'’æÖöFVÅö6ÆÇ2ãÒ6VÆbæ'VFvWBæÖ…öÖöFVÅö6ÆÇ3 ¢'VFvWE÷&V6öâÒ&Ö…öÖöFVÅö6ÆÇ2W††W7FVB ¢VÆ–b6VÆbç&VÖ—6U÷6VÆV7F÷"—2æ÷BæöæRæB7VÖÖ'’ç&WG&–WfÅö6ÆÇ2ãÒ6VÆbæ'VFvWBæÖ…÷&WG&–WfÅö6ÆÇ3 ¢'VFvWE÷&V6öâÒ&Ö…÷&WG&–WfÅö6ÆÇ2W††W7FVB ¢VÆ–bVÆ6VBãÒ6VÆbæ'VFvWBæÖ…÷vÆÅ÷6V6öæG3 ¢'VFvWE÷&V6öâÒ&Ö…÷vÆÅ÷6V6öæG2W††W7FVB ¢–b'VFvWE÷&V6öã ¢6VÆbåöf–æ—6‚‡7VÖÖ'’Â'6V&6…öW††W7FVB"Â'VFvWE÷&V6öâÂÆVâ†g&öçF–W"’¢&—6R&ööe6V&6„W††W7FVB‡7VÖÖ'’ ¢òÂòÂ7FFRÒ†Væ†V÷†g&öçF–W"¢6VÆbæÖVÖ÷'’ç6WE÷7FFU÷7FGW2‡7FFRç7FFUö–BÂ&W‡æFVB"¢7VÖÖ'’ç7FFW5öW‡æFVB³Ò¢–b7FFRæFWF‚ãÒ6VÆbæ'VFvWBæÖ…öFWFƒ ¢6öçF–çVP ¢&VÖ—6W3¢Æ—7E¶F–7E·7G"Âç•ÕÒÒµĞ¢–b6VÆbç&VÖ—6U÷6VÆV7F÷"—2æ÷BæöæRæB7VÖÖ'’ç&WG&–WfÅö6ÆÇ2Â6VÆbæ'VFvWBæÖ…÷&WG&–WfÅö6ÆÇ3 ¢6VÆV7F–öâÒ6VÆbç&VÖ—6U÷6VÆV7F÷"ç6VÆV7B€¢f÷&ÖÅ÷7V5ö–C×7V2æ–BÀ¢VW'“Öb'·7V2æ6öæ¦V7GW&U÷7FFVÖVçGÕÆç·7FFRç&VæFW"‚—Ò"À¢vöÅ÷7FFS×7FFRç&VæFW"‚’ÂÆÆ÷vVEöÖöGVÆW3×7V2æ–×÷'G2Â&÷VæC×7FFRæFWF‚À¢¢&VÖ—6W2Ò¶—FVÒçFõöF–7B‚’f÷"—FVÒ–â6VÆV7F–öâç6VÆV7FVEĞ¢7VÖÖ'’ç&WG&–WfÅö6ÆÇ2³Ò¢6VÆbæÖVÖ÷'’ç&V6÷&E÷&WG&–WfÂ‡7VÖÖ'’ç'Våö–BÂ7FFRç7FFUö–BÂ6VÆV7F–öâçFõöF–7B‚’ ¢6æF–FFW2Ò6VÆbævVæW&F÷"ævVæW&FR€¢6öçFW‡BÂ7V2Â7FFRÂ&VÖ—6W2Â6VÆbæ'VFvWBæÖ…÷F7F–75÷W%÷7FFRÀ¢¢7VÖÖ'’æÖöFVÅö6ÆÇ2³Ò¢f÷"6æF–FFR–â6æF–FFW5³¢6VÆbæ'VFvWBæÖ…÷F7F–75÷W%÷7FFUÓ ¢–b7VÖÖ'’æÆVåö6ÆÇ2ãÒ6VÆbæ'VFvWBæÖ…öÆVåö6ÆÇ2÷"7VÖÖ'’ç7FFW5ö7&VFVBãÒ6VÆbæ'VFvWBæÖ…÷7FFW3 ¢'&V°¢–b6VÆbæÖVÖ÷'’æGFV×FVB‡7VÖÖ'’ç'Våö–BÂ7FFRæf–ævW'&–çBÂ6æF–FFRçF7F–2ç7G&—‚’“ ¢6öçF–çVP¢G&ç6—F–öâÒ6VÆbçv÷&¶W"æÇ•÷F7F–2‡7V2Â7FFRÂ6æF–FFR¢7VÖÖ'’æÆVåö6ÆÇ2³Ò¢6VÆbæÖVÖ÷'’ç&V6÷&EöGFV×B‡7VÖÖ'’ç'Våö–BÂ7FFRÂ6æF–FFRÂG&ç6—F–öâ¢–bG&ç6—F–öâç7FGW2ÓÒ&Vçf—&öæÖVçEöW'&÷"# ¢6VÆbåöf–æ—6‚‡7VÖÖ'’Â&Vçf—&öæÖVçEöW'&÷""ÂG&ç6—F–öâæF–væ÷7F–72ÂÆVâ†g&öçF–W"’¢&—6R&ööe6V&6„Vçf—&öæÖVçDW'&÷"‡7VÖÖ'’¢–bG&ç6—F–öâç7FGW2Ò'7V66VVFVB"÷"G&ç6—F–öâç7FFR—2æöæS ¢7VÖÖ'’çF7F–5öf–ÇW&W2³Ò¢6öçF–çVP¢6†–ÆBÒG&ç6—F–öâç7FFP¢–b6†–ÆBæ6ö×ÆWFVC ¢÷&F–æÂ³Ò¢6VÆbæÖVÖ÷'’æFE÷7FFR‡7VÖÖ'’ç'Våö–BÂ6†–ÆBÂ&6Æ÷6VB"ÂÓãÂ÷&F–æÂ¢&ööe÷FW&ÒÒ&'•Æâ"²%Æâ"æ¦ö–â†b"·F7F–7Ò"f÷"F7F–2–â6†–ÆBçF7F–5ö†—7F÷'’¢7VÖÖ'’ç7FFW5ö7&VFVB³Ò¢6VÆbåöf–æ—6‚‡7VÖÖ'’Â&f÷&ÖÆ—¦VB"Â&6Æ÷6VB&ööb7FFS²VæF–ær¶W&æVÂ6W'F–f–6F–öâ"ÂÆVâ†g&öçF–W"’¢&WGW&âf÷&ÖÄ'F–f7B€¢f÷&ÖÅ÷7V5ö–C×7V2æ–BÂ&ööe÷FW&Ó×&ööe÷FW&ÒÀ¢ÖWFFF×²'&ööe÷6V&6‚#¢7VÖÖ'’çFõöF–7B‚’Â'FW&Ö–æÅ÷7FFR#¢6†–ÆBçFõöF–7B‚—ÒÀ¢¢&–÷&—G’Ò6VÆbå÷&–÷&—G’‡7FFRÂ6†–ÆBÂ6æF–FFR¢÷&F–æÂ³Ò¢–bæ÷B6VÆbæÖVÖ÷'’æFE÷7FFR‡7VÖÖ'’ç'Våö–BÂ6†–ÆBÂ&g&öçF–W""Â&–÷&—G’Â÷&F–æÂ“ ¢7VÖÖ'’æGWÆ–6FU÷7FFW2³Ò¢6öçF–çVP¢7VÖÖ'’ç7FFW5ö7&VFVB³Ò¢†Væ†VW6‚†g&öçF–W"Â‡&–÷&—G’Â÷&F–æÂÂ6†–ÆB’¢–bÆVâ†g&öçF–W"’â6VÆbæ'VFvWBæ&VÕ÷v–GFƒ ¢v÷'7BÒÖ‚†g&öçF–W"Â¶W“ÖÆÖ&F—FVÓ¢†—FVÕ³ÒÂ—FVÕ³Ò’¢g&öçF–W"ç&VÖ÷fR‡v÷'7B¢†Væ†V–g’†g&öçF–W"¢6VÆbæÖVÖ÷'’ç6WE÷7FFU÷7FGW2‡v÷'7E³%Òç7FFUö–BÂ''VæVB"¢7VÖÖ'’æg&öçF–W%÷6—¦RÒÆVâ†g&öçF–W"¢6VÆbæÖVÖ÷'’ç6fU÷7VÖÖ'’‡7VÖÖ'’ ¢6VÆbåöf–æ—6‚‡7VÖÖ'’Â'6V&6…öW††W7FVB"Â'&ööb×6V&6‚g&öçF–W"W††W7FVB"Â¢&—6R&ööe6V&6„W††W7FVB‡7VÖÖ'’¢W†6WBÆVåv÷&¶W$Vçf—&öæÖVçDW'&÷"2W†3 ¢6VÆbåöf–æ—6‚‡7VÖÖ'’Â&Vçf—&öæÖVçEöW'&÷""Â7G"†W†2’ÂÆVâ†g&öçF–W"’¢&—6R&ööe6V&6„Vçf—&öæÖVçDW'&÷"‡7VÖÖ'’’g&öÒW†0¢W†6WB…&ööe6V&6„W††W7FVBÂ&ööe6V&6„Vçf—&öæÖVçDW'&÷"“ ¢&—6P¢W†6WBW†6WF–öâ2W†3 ¢6VÆbåöf–æ—6‚‡7VÖÖ'’Â&–çfÆ–B"Â7G"†W†2’ÂÆVâ†g&öçF–W"’¢&—6P ¢FVb6Æ÷6R‡6VÆb’ÓâæöæS ¢6VÆbæÖVÖ÷'’æ6Æ÷6R‚