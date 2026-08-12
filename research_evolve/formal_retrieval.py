from __future__ import annotations

import json
import math
import re
import sqlite3
import uuid
import builtins
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .formal_project import LeanProjectLock
from .reproducibility import stable_json_hash


_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_']*|\d+")
_DECL_RE = re.compile(r"^\s*(theorem|lemma|def|abbrev)\s+([A-Za-z_][A-Za-z0-9_']*)\b(.*)$")
_NAMESPACE_RE = re.compile(r"^\s*namespace\s+([A-Za-z_][A-Za-z0-9_'.]*)\s*$")
_END_RE = re.compile(r"^\s*end(?:\s+([A-Za-z_][A-Za-z0-9_'.]*))?\s*$")
_LEAN_KEYWORDS = {
    "by", "theorem", "lemma", "def", "abbrev", "where", "fun", "match", "with",
    "let", "in", "if", "then", "else", "have", "show", "from", "exact", "apply",
    "intro", "rfl", "simp", "simpa", "namespace", "end", "true", "false",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tokens(text: str) -> list[str]:
    output: list[str] = []
    for token in _TOKEN_RE.findall(text):
        lowered = token.lower()
        output.append(lowered)
        parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", token)
        output.extend(part.lower() for part in parts if part)
    return output


def _type_features(text: str) -> tuple[str, ...]:
    """Cheap, deterministic Lean-type features; no model or network is trusted here."""
    features = set(_tokens(text))
    structural = {
        "→": "arrow", "->": "arrow", "↔": "iff", "<->": "iff", "∀": "forall",
        "∃": "exists", "≤": "le", ">=": "ge", "≥": "ge", "<": "lt", ">": "gt",
        "=": "eq", "≠": "ne", "∧": "and", "∨": "or", "¬": "not",
    }
    for symbol, name in structural.items():
        if symbol in text:
            features.add(name)
    return tuple(sorted(features))


def _module_for_path(path: str) -> str:
    value = Path(path)
    if value.suffix != ".lean":
        raise ValueError(f"premise source must be a .lean file: {path}")
    return ".".join(value.with_suffix("").parts)


@dataclass(slots=True, frozen=True)
class Premise:
    name: str
    module: str
    statement: str
    kind: str
    source_path: str
    line: int
    tags: tuple[str, ...] = ()
    type_features: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()

    def to_dict(self, *, schema_version: int = 2) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "module": self.module,
            "statement": self.statement,
            "kind": self.kind,
            "source_path": self.source_path,
            "line": self.line,
            "tags": list(self.tags),
        }
        if schema_version >= 2:
            data["type_features"] = list(self.type_features)
            data["dependencies"] = list(self.dependencies)
        return data


@dataclass(slots=True)
class PremiseIndex:
    project_fingerprint: str
    premises: list[Premise]
    fingerprint: str = ""
    schema_version: int = 2

    @classmethod
    def build_from_project(cls, root: str | Path, lock: LeanProjectLock) -> "PremiseIndex":
        lock.verify_project(root)
        project_root = Path(root).resolve()
        parsed: list[dict[str, Any]] = []
        for locked in (item for item in lock.files if item.path.endswith(".lean")):
            module = _module_for_path(locked.path)
            lines = (project_root / locked.path).read_text(encoding="utf-8").splitlines()
            namespaces: list[str] = []
            index = 0
            while index < len(lines):
                raw = lines[index]
                namespace_match = _NAMESPACE_RE.match(raw)
                if namespace_match:
                    namespaces.append(namespace_match.group(1))
                    index += 1
                    continue
                if _END_RE.match(raw):
                    if namespaces:
                        namespaces.pop()
                    index += 1
                    continue
                match = _DECL_RE.match(raw)
                if match is None:
                    index += 1
                    continue
                kind, short_name, tail = match.groups()
                declaration_lines = [tail.strip()]
                cursor = index
                while cursor + 1 < len(lines):
                    continuation = lines[cursor + 1].strip()
                    if _DECL_RE.match(continuation) or _NAMESPACE_RE.match(continuation) or _END_RE.match(continuation):
                        break
                    cursor += 1
                    declaration_lines.append(continuation)
                full = " ".join(part for part in declaration_lines if part).strip()
                statement = full.split(":=", 1)[0].strip()
                statement = re.sub(r"\s+where\s*$", "", statement).strip()
                qualified = ".".join([*namespaces, short_name]) if namespaces else short_name
                parsed.append({
                    "name": qualified,
                    "short_name": short_name,
                    "module": module,
                    "statement": statement,
                    "kind": kind,
                    "source_path": locked.path,
                    "line": index + 1,
                    "body": full.split(":=", 1)[1] if ":=" in full else "",
                })
                index = cursor + 1

        qualified_names = {str(item["name"]) for item in parsed}
        short_names: dict[str, set[str]] = {}
        for name in qualified_names:
            short_names.setdefault(name.rsplit(".", 1)[-1], set()).add(name)
        premises: list[Premise] = []
        for item in parsed:
            dependencies: set[str] = set()
            for token in _TOKEN_RE.findall(str(item["body"])):
                if token.lower() in _LEAN_KEYWORDS:
                    continue
                if token in qualified_names:
                    dependencies.add(token)
                elif len(short_names.get(token, ())) == 1:
                    dependencies.update(short_names[token])
            dependencies.discard(str(item["name"]))
            premises.append(Premise(
                name=str(item["name"]), module=str(item["module"]), statement=str(item["statement"]),
                kind=str(item["kind"]), source_path=str(item["source_path"]), line=int(item["line"]),
                type_features=_type_features(str(item["statement"])), dependencies=tuple(sorted(dependencies)),
            ))
        premises.sort(key=lambda item: (item.module, item.name, item.line))
        instance = cls(project_fingerprint=lock.fingerprint, premises=premises)
        instance.fingerprint = stable_json_hash(instance._stable_payload())
        return instance

    def _stable_payload(self) -> dict[str, Any]:
        return {
            "project_fingerprint": self.project_fingerprint,
            "premises": [item.to_dict(schema_version=self.schema_version) for item in self.premises],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PremiseIndex":
        schema = int(raw.get("schema_version", -1))
        if schema not in {1, 2}:
            raise ValueError("unsupported premise index schema_version")
        raw_premises = raw.get("premises", [])
        if not isinstance(raw_premises, list):
            raise ValueError("premise index premises must be a list")
        premises: list[Premise] = []
        for index, item in enumerate(raw_premises):
            if not isinstance(item, dict):
                raise ValueError(f"premise #{index} must be an object")
            tags = item.get("tags", [])
            type_features = item.get("type_features", [])
            dependencies = item.get("dependencies", [])
            if not all(isinstance(value, list) for value in (tags, type_features, dependencies)):
                raise ValueError(f"premise #{index} tags/type_features/dependencies must be lists")
            statement = str(item.get("statement", ""))
            premises.append(Premise(
                name=str(item.get("name", "")), module=str(item.get("module", "")), statement=statement,
                kind=str(item.get("kind", "")), source_path=str(item.get("source_path", "")),
                line=int(item.get("line", 0)), tags=tuple(str(tag) for tag in tags),
                type_features=tuple(str(value) for value in type_features) if schema >= 2 else (),
                dependencies=tuple(str(value) for value in dependencies) if schema >= 2 else (),
            ))
        instance = cls(str(raw.get("project_fingerprint", "")), premises, str(raw.get("fingerprint", "")), schema)
        instance.validate()
        return instance

    @classmethod
    def read(cls, path: str | Path) -> "PremiseIndex":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("premise index must contain a JSON object")
        return cls.from_dict(raw)

    def validate(self) -> None:
        if self.schema_version not in {1, 2}:
            raise ValueError("unsupported premise index schema_version")
        if not re.fullmatch(r"[0-9a-f]{64}", self.project_fingerprint):
            raise ValueError("premise index project_fingerprint must be a SHA-256-like fingerprint")
        names = [(item.module, item.name, item.line) for item in self.premises]
        if names != sorted(names) or len({item.name for item in self.premises}) != len(self.premises):
            raise ValueError("premise index entries must be sorted and uniquely named")
        known = {item.name for item in self.premises}
        for premise in self.premises:
            if not premise.name or not premise.module or premise.line < 1:
                raise ValueError("premise index contains an incomplete declaration")
            if list(premise.dependencies) != sorted(set(premise.dependencies)):
                raise ValueError("premise dependencies must be sorted and unique")
            if any(dependency not in known for dependency in premise.dependencies):
                raise ValueError("premise dependency graph references an unknown declaration")
        if stable_json_hash(self._stable_payload()) != self.fingerprint:
            raise ValueError("premise index fingerprint does not match its contents")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version, "project_fingerprint": self.project_fingerprint,
            "premises": [item.to_dict(schema_version=self.schema_version) for item in self.premises],
            "fingerprint": self.fingerprint,
        }

    def write(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


@dataclass(slots=True, frozen=True)
class ProofSearchBudget:
    max_candidates: int = 5000
    max_results: int = 12
    max_dependency_expansions: int = 8
    max_context_chars: int = 24000

    def validate(self) -> None:
        if min(self.max_candidates, self.max_results, self.max_context_chars) < 1 or self.max_dependency_expansions < 0:
            raise ValueError("proof search budget values must be positive (dependency expansions may be zero)")


@dataclass(slots=True, frozen=True)
class ScoredPremise:
    premise: Premise
    score: float
    matched_tokens: tuple[str, ...]
    lexical_score: float = 0.0
    type_score: float = 0.0
    dependency_score: float = 0.0
    retrieval_depth: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.premise.to_dict(), "score": self.score, "matched_tokens": list(self.matched_tokens),
            "score_components": {"lexical": self.lexical_score, "type": self.type_score, "dependency": self.dependency_score},
            "retrieval_depth": self.retrieval_depth,
        }


@dataclass(slots=True)
class PremiseSelection:
    formal_spec_id: str
    query: str
    index_fingerprint: str
    selected: list[ScoredPremise]
    goal_state: str = ""
    round: int = 0
    budget: dict[str, int] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"premise-selection-{uuid.uuid4().hex}")
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "formal_spec_id": self.formal_spec_id, "query": self.query,
            "goal_state": self.goal_state, "round": self.round, "index_fingerprint": self.index_fingerprint,
            "budget": self.budget, "stats": self.stats,
            "selected": [item.to_dict() for item in self.selected], "created_at": self.created_at,
        }


class PremiseSelector:
    """Deterministic goal-conditioned lexical/type/dependency retrieval."""

    def __init__(self, index: PremiseIndex, *, limit: int = 12, budget: ProofSearchBudget | None = None) -> None:
        if limit < 1:
            raise ValueError("premise selection limit must be positive")
        index.validate()
        self.index = index
        self.budget = budget or ProofSearchBudget(max_results=limit)
        self.budget.validate()
        self.limit = min(int(limit), self.budget.max_results)
        self._by_name = {premise.name: premise for premise in index.premises}
        document_frequency: dict[str, int] = {}
        for premise in index.premises:
            for token in set(_tokens(f"{premise.name} {premise.statement} {' '.join(premise.tags)}")):
                document_frequency[token] = document_frequency.get(token, 0) + 1
        self._document_frequency = document_frequency

    @property
    def name(self) -> str:
        return f"premise-selector-v2:{self.index.fingerprint[:16]}:limit={self.limit}:budget={stable_json_hash(asdict(self.budget))[:12]}"

    def select(self, *, formal_spec_id: str, query: str, allowed_modules: Sequence[str] | None = None,
               goal_state: str = "", round: int = 0, excluded_names: Sequence[str] = ()) -> PremiseSelection:
        allowed = None if allowed_modules is None else set(allowed_modules)
        excluded = set(excluded_names)
        query_tokens = set(_tokens(query))
        goal_features = set(_type_features(goal_state or query))
        total_docs = max(1, len(self.index.premises))
        scores: list[ScoredPremise] = []
        scanned = 0
        for premise in self.index.premises:
            if scanned >= self.budget.max_candidates:
                break
            if allowed is not None and premise.module not in allowed or premise.name in excluded:
                continue
            scanned += 1
            premise_tokens = set(_tokens(f"{premise.name} {premise.statement} {' '.join(premise.tags)}"))
            matched = sorted(query_tokens & premise_tokens)
            lexical = sum(math.log((total_docs + 1) / (self._document_frequency.get(token, 0) + 1)) + 1.0 for token in matched)
            lexical += 0.75 * len(query_tokens & set(_tokens(premise.name)))
            premise_features = set(premise.type_features or _type_features(premise.statement))
            type_overlap = goal_features & premise_features
            type_score = 1.5 * len(type_overlap) / max(1, math.sqrt(len(goal_features) * len(premise_features)))
            if lexical <= 0 and type_score <= 0:
                continue
            score = lexical + type_score
            scores.append(ScoredPremise(premise, builtins.round(score, 6), tuple(matched), builtins.round(lexical, 6), builtins.round(type_score, 6)))
        scores.sort(key=lambda item: (-item.score, item.premise.name, item.premise.module))

        reserved_dependency_slots = min(self.budget.max_dependency_expansions, max(0, self.limit - 1))
        seed_count = max(1, self.limit - reserved_dependency_slots)
        selected = scores[:seed_count]
        selected_names = {item.premise.name for item in selected}
        expansions = 0
        for parent in list(selected):
            if len(selected) >= self.limit or expansions >= self.budget.max_dependency_expansions:
                break
            for dependency_name in parent.premise.dependencies:
                dependency = self._by_name.get(dependency_name)
                if dependency is None or dependency.name in selected_names or dependency.name in excluded:
                    continue
                if allowed is not None and dependency.module not in allowed:
                    continue
                selected.append(ScoredPremise(dependency, builtins.round(parent.score * 0.35, 6), (), 0.0, 0.0,
                                              builtins.round(parent.score * 0.35, 6), 1))
                selected_names.add(dependency.name)
                expansions += 1
                if len(selected) >= self.limit or expansions >= self.budget.max_dependency_expansions:
                    break

        if len(selected) < self.limit:
            for candidate in scores[seed_count:]:
                if candidate.premise.name in selected_names:
                    continue
                selected.append(candidate)
                selected_names.add(candidate.premise.name)
                if len(selected) >= self.limit:
                    break

        context_chars = 0
        bounded: list[ScoredPremise] = []
        for item in selected:
            size = len(json.dumps(item.to_dict(), ensure_ascii=False))
            if context_chars + size > self.budget.max_context_chars:
                break
            bounded.append(item)
            context_chars += size
        return PremiseSelection(
            formal_spec_id=formal_spec_id, query=query, goal_state=goal_state, round=round,
            index_fingerprint=self.index.fingerprint, selected=bounded, budget=asdict(self.budget),
            stats={"candidates_scanned": scanned, "dependency_expansions": expansions,
                   "context_chars": context_chars, "candidate_budget_exhausted": scanned >= self.budget.max_candidates,
                   "context_budget_exhausted": len(bounded) < len(selected)},
        )


class FormalRetrievalMemory:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS premise_selections (
                id TEXT PRIMARY KEY, formal_spec_id TEXT NOT NULL, index_fingerprint TEXT NOT NULL,
                payload TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_premise_selection_spec ON premise_selections(formal_spec_id);
        """)
        self.conn.commit()

    def record(self, selection: PremiseSelection) -> None:
        self.conn.execute("INSERT OR REPLACE INTO premise_selections VALUES (?, ?, ?, ?, ?)",
                          (selection.id, selection.formal_spec_id, selection.index_fingerprint,
                           json.dumps(selection.to_dict(), sort_keys=True), selection.created_at))
        self.conn.commit()

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT payload FROM premise_selections ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def close(self) -> None:
        self.conn.close()
