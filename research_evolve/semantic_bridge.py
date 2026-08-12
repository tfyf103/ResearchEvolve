from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

from .candidates import Candidate
from .conjectures import Predicate, ValueRef
from .formal import DEFAULT_ALLOWED_LEAN_AXIOMS
from .reproducibility import stable_json_hash


MathType = Literal["Nat", "Int", "Bool", "Prop"]
ExprKind = Literal["var", "const", "lt", "le", "gt", "ge", "eq", "ne", "forall"]
_BASE_TYPES = {"Nat", "Int", "Bool"}
_COMPARISONS = {"lt", "le", "gt", "ge", "eq", "ne"}


class UnsupportedSemantics(ValueError):
    """The trusted registry cannot assign exact formal meaning to a predicate."""


class SemanticAuditFailure(ValueError):
    """A generated contract failed independent deterministic reconstruction."""


@dataclass(frozen=True, slots=True)
class MathExpr:
    """Small typed mathematical IR used at the empirical/formal trust boundary."""

    kind: ExprKind
    type: MathType
    name: str = ""
    value: Any = None
    args: tuple["MathExpr", ...] = ()
    binder_type: MathType | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"kind": self.kind, "type": self.type}
        if self.name:
            data["name"] = self.name
        if self.kind == "const":
            data["value"] = self.value
        if self.args:
            data["args"] = [item.to_dict() for item in self.args]
        if self.binder_type is not None:
            data["binder_type"] = self.binder_type
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MathExpr":
        def decode(raw: dict[str, Any]) -> "MathExpr":
            return cls(
                kind=str(raw.get("kind", "")),  # type: ignore[arg-type]
                type=str(raw.get("type", "")),  # type: ignore[arg-type]
                name=str(raw.get("name", "")),
                value=raw.get("value"),
                args=tuple(decode(dict(item)) for item in raw.get("args", [])),
                binder_type=(str(raw["binder_type"]) if raw.get("binder_type") is not None else None),  # type: ignore[arg-type]
            )

        expression = decode(data)
        expression.validate()
        return expression

    def validate(self, scope: dict[str, MathType] | None = None) -> None:
        scope = dict(scope or {})
        if self.kind == "var":
            if not self.name or self.type not in _BASE_TYPES:
                raise ValueError("IR variable requires a name and base type")
            if self.name not in scope or scope[self.name] != self.type:
                raise ValueError(f"unbound or mistyped IR variable: {self.name!r}")
            if self.args or self.value is not None or self.binder_type is not None:
                raise ValueError("IR variable contains unexpected fields")
            return
        if self.kind == "const":
            if self.args or self.name or self.binder_type is not None:
                raise ValueError("IR constant contains unexpected fields")
            if self.type == "Nat" and (not isinstance(self.value, int) or isinstance(self.value, bool) or self.value < 0):
                raise ValueError("Nat constant must be a non-negative integer")
            if self.type == "Int" and (not isinstance(self.value, int) or isinstance(self.value, bool)):
                raise ValueError("Int constant must be an integer")
            if self.type == "Bool" and not isinstance(self.value, bool):
                raise ValueError("Bool constant must be boolean")
            if self.type not in _BASE_TYPES:
                raise ValueError(f"unsupported IR constant type: {self.type!r}")
            return
        if self.kind in _COMPARISONS:
            if self.type != "Prop" or len(self.args) != 2 or self.name or self.binder_type is not None:
                raise ValueError(f"IR {self.kind} must be a binary proposition")
            left, right = self.args
            left.validate(scope)
            right.validate(scope)
            if left.type != right.type or left.type not in _BASE_TYPES:
                raise ValueError(f"IR {self.kind} operands must have the same base type")
            if self.kind in {"lt", "le", "gt", "ge"} and left.type == "Bool":
                raise ValueError(f"IR {self.kind} does not order Bool")
            return
        if self.kind == "forall":
            if self.type != "Prop" or not self.name or self.binder_type not in _BASE_TYPES or len(self.args) != 1:
                raise ValueError("IR forall requires one typed proposition body")
            if self.name in scope:
                raise ValueError(f"IR binder shadows existing variable: {self.name!r}")
            body_scope = {**scope, self.name: self.binder_type}
            self.args[0].validate(body_scope)
            if self.args[0].type != "Prop":
                raise ValueError("IR forall body must be a proposition")
            return
        raise ValueError(f"unsupported IR kind: {self.kind!r}")

    @property
    def fingerprint(self) -> str:
        return stable_json_hash(self.to_dict())

    def evaluate(self, environment: dict[str, Any]) -> bool | int:
        if self.kind == "var":
            return environment[self.name]
        if self.kind == "const":
            return self.value
        if self.kind in _COMPARISONS:
            left = self.args[0].evaluate(environment)
            right = self.args[1].evaluate(environment)
            return {
                "lt": lambda: left < right,
                "le": lambda: left <= right,
                "gt": lambda: left > right,
                "ge": lambda: left >= right,
                "eq": lambda: left == right,
                "ne": lambda: left != right,
            }[self.kind]()
        if self.kind == "forall":
            raise ValueError("evaluate a universally quantified IR through evaluate_body with a concrete environment")
        raise ValueError(f"unsupported IR kind: {self.kind!r}")

    def binders_and_body(self) -> tuple[list[tuple[str, MathType]], "MathExpr"]:
        binders: list[tuple[str, MathType]] = []
        current = self
        while current.kind == "forall":
            assert current.binder_type is not None
            binders.append((current.name, current.binder_type))
            current = current.args[0]
        return binders, current

    def to_lean(self) -> str:
        if self.kind == "var":
            return self.name
        if self.kind == "const":
            if self.type == "Bool":
                return "true" if self.value else "false"
            if self.type == "Int" and int(self.value) < 0:
                return f"({self.value} : Int)"
            return str(self.value)
        if self.kind in _COMPARISONS:
            symbols = {"lt": "<", "le": "≤", "gt": ">", "ge": "≥", "eq": "=", "ne": "≠"}
            return f"{self.args[0].to_lean()} {symbols[self.kind]} {self.args[1].to_lean()}"
        if self.kind == "forall":
            binders, body = self.binders_and_body()
            return f"∀ {' '.join(f'({name} : {kind})' for name, kind in binders)}, {body.to_lean()}"
        raise ValueError(f"unsupported IR kind: {self.kind!r}")


@dataclass(frozen=True, slots=True)
class TrustedSymbol:
    source: str
    key: str
    math_type: MathType
    lean_name: str
    imports: tuple[str, ...]
    semantic_version: str
    meaning: str
    test_values: tuple[Any, ...]
    evaluator_sha256: str

    def validate(self) -> None:
        if self.source not in {"score", "payload", "metrics", "behavior"}:
            raise ValueError(f"unsupported trusted symbol source: {self.source!r}")
        if self.source != "score" and not self.key:
            raise ValueError("non-score trusted symbols require a key")
        if self.math_type not in _BASE_TYPES:
            raise ValueError(f"unsupported trusted symbol type: {self.math_type!r}")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_']*", self.lean_name):
            raise ValueError(f"invalid Lean binder name: {self.lean_name!r}")
        if not self.semantic_version.strip() or not self.meaning.strip():
            raise ValueError("trusted symbols require semantic_version and meaning")
        if not self.test_values:
            raise ValueError("trusted symbols require non-empty boundary test_values")
        if not re.fullmatch(r"[0-9a-f]{64}", self.evaluator_sha256):
            raise ValueError("trusted symbols require the SHA-256 of their evaluator implementation")
        for module in self.imports:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", module):
                raise ValueError(f"invalid Lean import module: {module!r}")
        for value in self.test_values:
            _constant(self.math_type, value)

    @property
    def identity(self) -> tuple[str, str]:
        return self.source, self.key

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "key": self.key,
            "math_type": self.math_type,
            "lean_name": self.lean_name,
            "imports": list(self.imports),
            "semantic_version": self.semantic_version,
            "meaning": self.meaning,
            "test_values": list(self.test_values),
            "evaluator_sha256": self.evaluator_sha256,
        }


@dataclass(frozen=True, slots=True)
class SemanticRegistry:
    symbols: tuple[TrustedSymbol, ...]
    backend: str
    toolchain: str
    project_fingerprint: str
    premise_index_fingerprint: str
    allowed_axioms: tuple[str, ...] = tuple(DEFAULT_ALLOWED_LEAN_AXIOMS)
    schema_version: int = 1

    def validate(self) -> None:
        if self.schema_version != 1:
            raise ValueError(f"unsupported semantic registry schema: {self.schema_version}")
        if self.backend != "lean4" or not self.toolchain.strip():
            raise ValueError("semantic registry requires a Lean 4 backend and toolchain")
        if not self.project_fingerprint or not self.premise_index_fingerprint:
            raise ValueError("semantic registry must bind the frozen project and premise index fingerprints")
        if not re.fullmatch(r"[0-9a-f]{64}", self.project_fingerprint):
            raise ValueError("semantic registry project_fingerprint must be a SHA-256-like fingerprint")
        if not re.fullmatch(r"[0-9a-f]{64}", self.premise_index_fingerprint):
            raise ValueError("semantic registry premise_index_fingerprint must be a SHA-256-like fingerprint")
        seen_identity: set[tuple[str, str]] = set()
        seen_lean: set[str] = set()
        for symbol in self.symbols:
            symbol.validate()
            if symbol.identity in seen_identity:
                raise ValueError(f"duplicate trusted symbol: {symbol.identity!r}")
            if symbol.lean_name in seen_lean:
                raise ValueError(f"duplicate trusted Lean binder: {symbol.lean_name!r}")
            seen_identity.add(symbol.identity)
            seen_lean.add(symbol.lean_name)
        if not self.symbols:
            raise ValueError("semantic registry requires at least one trusted symbol")
        if len(set(self.allowed_axioms)) != len(self.allowed_axioms):
            raise ValueError("semantic registry allowed_axioms must be unique")
        for axiom in self.allowed_axioms:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_'.]*", axiom):
                raise ValueError(f"invalid Lean axiom identifier: {axiom!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "backend": self.backend,
            "toolchain": self.toolchain,
            "project_fingerprint": self.project_fingerprint,
            "premise_index_fingerprint": self.premise_index_fingerprint,
            "allowed_axioms": list(self.allowed_axioms),
            "symbols": [item.to_dict() for item in self.symbols],
        }

    @property
    def fingerprint(self) -> str:
        return stable_json_hash(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SemanticRegistry":
        raw_symbols = data.get("symbols", [])
        if not isinstance(raw_symbols, list):
            raise ValueError("semantic registry symbols must be a list")
        symbols: list[TrustedSymbol] = []
        for index, raw in enumerate(raw_symbols):
            if not isinstance(raw, dict):
                raise ValueError(f"semantic registry symbol #{index} must be an object")
            symbols.append(
                TrustedSymbol(
                    source=str(raw.get("source", "")),
                    key=str(raw.get("key", "")),
                    math_type=str(raw.get("math_type", "")),  # type: ignore[arg-type]
                    lean_name=str(raw.get("lean_name", "")),
                    imports=tuple(str(item) for item in raw.get("imports", [])),
                    semantic_version=str(raw.get("semantic_version", "")),
                    meaning=str(raw.get("meaning", "")),
                    test_values=tuple(raw.get("test_values", [])),
                    evaluator_sha256=str(raw.get("evaluator_sha256", "")),
                )
            )
        registry = cls(
            symbols=tuple(symbols),
            backend=str(data.get("backend", "lean4")),
            toolchain=str(data.get("toolchain", "")),
            project_fingerprint=str(data.get("project_fingerprint", "")),
            premise_index_fingerprint=str(data.get("premise_index_fingerprint", "")),
            allowed_axioms=tuple(str(item) for item in data.get("allowed_axioms", DEFAULT_ALLOWED_LEAN_AXIOMS)),
            schema_version=int(data.get("schema_version", 0)),
        )
        registry.validate()
        return registry

    @classmethod
    def read(cls, path: str | Path) -> "SemanticRegistry":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("semantic registry must be a JSON object")
        return cls.from_dict(data)

    def resolve(self, ref: ValueRef) -> TrustedSymbol:
        ref.validate()
        if ref.scale != 1.0 or ref.offset != 0.0:
            raise UnsupportedSemantics("scaled/offset ValueRef semantics are not certified in registry schema 1")
        for symbol in self.symbols:
            if symbol.identity == (ref.source, ref.key):
                return symbol
        raise UnsupportedSemantics(f"unregistered predicate symbol: {ref.source}.{ref.key}")


def _constant(math_type: MathType, value: Any) -> MathExpr:
    expression = MathExpr(kind="const", type=math_type, value=value)
    expression.validate()
    return expression


def _symbol_value(symbol: TrustedSymbol, candidate: Candidate) -> Any | None:
    ref = ValueRef(source=symbol.source, key=symbol.key)  # type: ignore[arg-type]
    return ref.resolve(candidate)


def _coerce_observed_value(math_type: MathType, value: Any) -> Any:
    """Convert JSON evaluator numbers only when the conversion is exact."""
    if math_type in {"Nat", "Int"}:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"observed {math_type} value is not numeric: {value!r}")
        numeric = float(value)
        if not math.isfinite(numeric) or not numeric.is_integer():
            raise ValueError(f"observed {math_type} value is not an exact integer: {value!r}")
        integer = int(numeric)
        if math_type == "Nat" and integer < 0:
            raise ValueError(f"observed Nat value is negative: {value!r}")
        return integer
    if math_type == "Bool" and isinstance(value, bool):
        return value
    raise ValueError(f"observed value does not inhabit {math_type}: {value!r}")


class PredicateIRCompiler:
    """Deterministically translates the safe v0.4 Predicate DSL into typed IR."""

    def __init__(self, registry: SemanticRegistry) -> None:
        registry.validate()
        self.registry = registry

    def compile(self, predicate: Predicate) -> MathExpr:
        predicate.validate()
        left_symbol = self.registry.resolve(predicate.left)
        symbols = [left_symbol]
        left = MathExpr(kind="var", type=left_symbol.math_type, name=left_symbol.lean_name)
        if predicate.right_ref is not None:
            right_symbol = self.registry.resolve(predicate.right_ref)
            symbols.append(right_symbol)
            if right_symbol.math_type != left_symbol.math_type:
                raise UnsupportedSemantics("predicate operands have different trusted mathematical types")
            right = MathExpr(kind="var", type=right_symbol.math_type, name=right_symbol.lean_name)
        else:
            try:
                right = _constant(left_symbol.math_type, predicate.right_constant)
            except ValueError as exc:
                raise UnsupportedSemantics(str(exc)) from exc
        body = MathExpr(kind=predicate.operator, type="Prop", args=(left, right))
        unique = {(item.source, item.key): item for item in symbols}
        ordered = sorted(unique.values(), key=lambda item: (item.source, item.key, item.lean_name))
        result = body
        for symbol in reversed(ordered):
            result = MathExpr(
                kind="forall",
                type="Prop",
                name=symbol.lean_name,
                binder_type=symbol.math_type,
                args=(result,),
            )
        result.validate()
        return result


@dataclass(frozen=True, slots=True)
class SemanticAuditResult:
    passed: bool
    issues: tuple[str, ...]
    checked_vectors: int
    contract_fingerprint: str
    registry_fingerprint: str
    fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"issues": list(self.issues)}


class SemanticContractCompiler:
    """Freezes a Lean target from registry + Predicate without model-authored signatures."""

    VERSION = "1.0"

    def __init__(self, registry: SemanticRegistry) -> None:
        self.registry = registry

    def compile(self, conjecture_id: str, statement: str, predicate: Predicate) -> dict[str, Any]:
        expression = PredicateIRCompiler(self.registry).compile(predicate)
        binders, body = expression.binders_and_body()
        predicate_dict = predicate.to_dict()
        source_fingerprint = stable_json_hash(
            {
                "statement": statement.strip(),
                "predicate": predicate_dict,
                "registry_fingerprint": self.registry.fingerprint,
            }
        )
        theorem_name = f"research_evolve_{source_fingerprint[:20]}"
        binder_text = " ".join(f"({name} : {kind})" for name, kind in binders)
        signature = f"theorem {theorem_name}{(' ' + binder_text) if binder_text else ''} : {body.to_lean()}"
        used = {(ref.source, ref.key) for ref in [predicate.left, predicate.right_ref] if ref is not None}
        imports = sorted({module for symbol in self.registry.symbols if symbol.identity in used for module in symbol.imports})
        contract: dict[str, Any] = {
            "schema_version": 1,
            "status": "candidate",
            "conjecture_statement": statement.strip(),
            "conjecture_predicate": predicate_dict,
            "backend": self.registry.backend,
            "toolchain": self.registry.toolchain,
            "theorem_name": theorem_name,
            "theorem_signature": signature,
            "imports": imports,
            "preamble": "",
            "allowed_axioms": list(self.registry.allowed_axioms),
            "semantic_ir": expression.to_dict(),
            "metadata": {
                "semantic_bridge_version": self.VERSION,
                "registry_fingerprint": self.registry.fingerprint,
                "project_fingerprint": self.registry.project_fingerprint,
                "premise_index_fingerprint": self.registry.premise_index_fingerprint,
                "source_semantics_fingerprint": source_fingerprint,
                "ir_fingerprint": expression.fingerprint,
            },
        }
        contract["fingerprint"] = stable_json_hash(contract)
        return contract


class SemanticAuditor:
    """Independently reconstructs and differentially checks a candidate contract."""

    VERSION = "1.0"

    def __init__(self, registry: SemanticRegistry) -> None:
        self.registry = registry

    def audit(
        self,
        contract: dict[str, Any],
        conjecture_id: str,
        statement: str,
        predicate: Predicate,
        candidates: Iterable[Candidate] = (),
    ) -> SemanticAuditResult:
        issues: list[str] = []
        expected = SemanticContractCompiler(self.registry).compile(conjecture_id, statement, predicate)
        for field in [
            "conjecture_statement",
            "conjecture_predicate",
            "backend",
            "toolchain",
            "theorem_name",
            "theorem_signature",
            "imports",
            "preamble",
            "allowed_axioms",
            "semantic_ir",
            "metadata",
            "fingerprint",
        ]:
            if contract.get(field) != expected.get(field):
                issues.append(f"contract field differs from independent reconstruction: {field}")
        try:
            expression = MathExpr.from_dict(dict(contract.get("semantic_ir") or {}))
        except (TypeError, ValueError) as exc:
            expression = None
            issues.append(f"invalid typed semantic IR: {exc}")

        checked = 0
        if expression is not None:
            binders, body = expression.binders_and_body()
            symbol_by_name = {item.lean_name: item for item in self.registry.symbols}
            used_symbols = [symbol_by_name[name] for name, _ in binders if name in symbol_by_name]
            vectors: list[dict[str, Any]] = [{}]
            for symbol in used_symbols:
                expanded: list[dict[str, Any]] = []
                for vector in vectors:
                    for value in symbol.test_values:
                        expanded.append({**vector, symbol.lean_name: value})
                        if len(expanded) >= 64:
                            break
                    if len(expanded) >= 64:
                        break
                vectors = expanded
            for vector in vectors:
                try:
                    ir_value = bool(body.evaluate(vector))
                    left_symbol = self.registry.resolve(predicate.left)
                    left = vector[left_symbol.lean_name]
                    if predicate.right_ref is not None:
                        right_symbol = self.registry.resolve(predicate.right_ref)
                        right = vector[right_symbol.lean_name]
                    else:
                        right = predicate.right_constant
                    expected_value = {
                        "lt": left < right,
                        "le": left <= right,
                        "gt": left > right,
                        "ge": left >= right,
                        "eq": left == right,
                        "ne": left != right,
                    }[predicate.operator]
                    checked += 1
                    if ir_value != bool(expected_value):
                        issues.append(f"boundary vector semantics mismatch: {vector}")
                except (KeyError, TypeError, ValueError) as exc:
                    issues.append(f"boundary vector could not be audited: {exc}")
            for candidate in candidates:
                environment: dict[str, Any] = {}
                complete = True
                for symbol in used_symbols:
                    value = _symbol_value(symbol, candidate)
                    if value is None:
                        complete = False
                        break
                    try:
                        environment[symbol.lean_name] = _coerce_observed_value(symbol.math_type, value)
                    except ValueError as exc:
                        issues.append(f"candidate {candidate.id} violates registry type: {exc}")
                        complete = False
                        break
                if not complete:
                    continue
                empirical = predicate.evaluate(candidate)
                if empirical is None:
                    continue
                try:
                    ir_value = bool(body.evaluate(environment))
                except (KeyError, TypeError, ValueError) as exc:
                    issues.append(f"candidate {candidate.id} could not be audited: {exc}")
                    continue
                checked += 1
                if ir_value != empirical:
                    issues.append(f"candidate semantics mismatch: {candidate.id}")

        stable = {
            "auditor_version": self.VERSION,
            "passed": not issues,
            "issues": issues,
            "checked_vectors": checked,
            "contract_fingerprint": str(contract.get("fingerprint", "")),
            "registry_fingerprint": self.registry.fingerprint,
        }
        return SemanticAuditResult(
            passed=not issues,
            issues=tuple(issues),
            checked_vectors=checked,
            contract_fingerprint=stable["contract_fingerprint"],
            registry_fingerprint=self.registry.fingerprint,
            fingerprint=stable_json_hash(stable),
        )


class SemanticAuditMemory:
    """Append-only journal of generated contracts and independent audit results."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS semantic_contracts (
                fingerprint TEXT PRIMARY KEY,
                conjecture_id TEXT NOT NULL,
                contract TEXT NOT NULL,
                audit TEXT NOT NULL,
                passed INTEGER NOT NULL
            )
            """
        )
        self.conn.commit()

    def record(self, conjecture_id: str, contract: dict[str, Any], audit: SemanticAuditResult) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO semantic_contracts VALUES (?, ?, ?, ?, ?)",
            (
                str(contract["fingerprint"]),
                conjecture_id,
                json.dumps(contract, sort_keys=True),
                json.dumps(audit.to_dict(), sort_keys=True),
                int(audit.passed),
            ),
        )
        self.conn.commit()

    def list(self, limit: int = 1000) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT conjecture_id, contract, audit, passed FROM semantic_contracts ORDER BY rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "conjecture_id": str(row["conjecture_id"]),
                "contract": json.loads(row["contract"]),
                "audit": json.loads(row["audit"]),
                "passed": bool(row["passed"]),
            }
            for row in rows
        ]

    def close(self) -> None:
        self.conn.close()


class CertifiedSemanticBridge:
    """Compiles, audits, journals, and only then releases a frozen formal contract."""

    def __init__(self, registry: SemanticRegistry, memory: SemanticAuditMemory) -> None:
        self.registry = registry
        self.memory = memory

    @property
    def fingerprint(self) -> str:
        return stable_json_hash(
            {
                "bridge": "certified-semantic-bridge-v1",
                "compiler": SemanticContractCompiler.VERSION,
                "auditor": SemanticAuditor.VERSION,
                "registry": self.registry.fingerprint,
            }
        )

    def compile_and_audit(
        self,
        conjecture_id: str,
        statement: str,
        predicate: Predicate,
        candidates: Iterable[Candidate] = (),
    ) -> dict[str, Any]:
        contract = SemanticContractCompiler(self.registry).compile(conjecture_id, statement, predicate)
        audit = SemanticAuditor(self.registry).audit(contract, conjecture_id, statement, predicate, candidates)
        self.memory.record(conjecture_id, contract, audit)
        if not audit.passed:
            raise SemanticAuditFailure("; ".join(audit.issues))
        certified = dict(contract)
        certified["status"] = "certified_formal_contract"
        certified["metadata"] = {
            **dict(contract["metadata"]),
            "semantic_audit_fingerprint": audit.fingerprint,
            "semantic_audit_checked_vectors": audit.checked_vectors,
        }
        # The candidate fingerprint remains the auditor's immutable subject. The
        # certified envelope gets its own fingerprint to prevent status relabeling.
        certified["certified_fingerprint"] = stable_json_hash(certified)
        return certified

    def close(self) -> None:
        self.memory.close()
